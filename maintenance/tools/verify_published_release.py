#!/usr/bin/env python3
"""Classify the public release state without mutating GitHub or its assets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools.release_candidate import (
    BOUND_METADATA_NAMES,
    CandidateError,
    verify_candidate,
)
from maintenance.tools.release_receipt import ReleaseReceiptError, validate_durable_assets
from x86qw_runtime.io.downloader import (
    BoundedMetadata,
    DownloadError,
    DownloadHTTPError,
    RetryPolicy,
    download,
    validate_https_url,
)
from x86qw_runtime.versioning import VersionError, parse_semver


DEFAULT_API = "https://api.github.com"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEADLINE_SECONDS = 30.0
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ASSET_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
# Native evidence and its public root/receipt are release assets.  The
# promotion gate authenticates them before publication and the public verifier
# requires the same immutable set after deployment.
PUBLIC_METADATA_NAMES = (
    "candidate.json",
    *BOUND_METADATA_NAMES,
    "release-evidence.json",
    "evidence-root.json",
    "release-receipt.json",
)
INTERNAL_CANDIDATE_PREFIXES = ("runtime/native-smoke/",)


class PublishedReleaseError(RuntimeError):
    """The public release cannot be classified safely."""


class PublishedReleaseNotFound(PublishedReleaseError):
    """The GitHub API returned a genuine 404 for one public object."""


FetchJson = Callable[[str, float], Mapping[str, object]]
VerifyCandidate = Callable[..., dict[str, object]]


def _require_final_public_acceptance(receipt: Mapping[str, object], version: str) -> None:
    if version != "1.0.0":
        return
    acceptance = receipt.get("public_acceptance")
    if (
        not isinstance(acceptance, Mapping)
        or set(acceptance) != {"commit", "run_id", "artifact_id", "artifact_name", "version"}
    ):
        raise PublishedReleaseError(
            "recibo durável da versão final não contém o handoff de aceitação pública"
        )


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise PublishedReleaseError("JSON público contém chaves duplicadas")
        value[key] = item
    return value


def _validate_identity(repository: str, version: str, commit: str) -> None:
    if REPOSITORY_RE.fullmatch(repository) is None:
        raise PublishedReleaseError("repositório GitHub inválido")
    try:
        parse_semver(version)
    except (TypeError, VersionError) as error:
        raise PublishedReleaseError("versão pública inválida") from error
    if HEX40.fullmatch(commit) is None:
        raise PublishedReleaseError("commit público inválido")


def _portable(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _asset_basename(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PublishedReleaseError("nome de asset público inválido")
    path = PurePosixPath(value)
    if path.is_absolute() or path.name != value or any(
        part in {"", ".", ".."} for part in path.parts
    ) or any(ord(char) < 0x20 for char in value):
        raise PublishedReleaseError("nome de asset público inválido")
    return value


def _file_identity(candidate: Path, name: str) -> dict[str, object]:
    path = candidate / name
    if path.is_symlink() or not path.is_file():
        raise PublishedReleaseError(f"metadata público ausente ou inseguro: {name}")
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
    except OSError as error:
        raise PublishedReleaseError(f"não foi possível ler metadata público: {name}") from error
    return {"size": size, "digest": f"sha256:{digest.hexdigest()}"}


def _candidate_assets(manifest: Mapping[str, object], candidate: Path) -> dict[str, dict[str, object]]:
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, dict):
        raise PublishedReleaseError("manifest verificado sem artefatos")
    assets: dict[str, dict[str, object]] = {}
    portable_names: set[str] = set()

    def add(name: str, metadata: Mapping[str, object]) -> None:
        basename = _asset_basename(name)
        portable_name = _portable(basename)
        if portable_name in portable_names:
            raise PublishedReleaseError("colisão de basename entre assets do candidato")
        portable_names.add(portable_name)
        assets[basename] = {"size": metadata["size"], "digest": metadata["digest"]}

    for raw_name, raw_metadata in raw_artifacts.items():
        if (
            not isinstance(raw_name, str)
            or not raw_name.casefold().endswith(".zip")
            or raw_name.startswith(INTERNAL_CANDIDATE_PREFIXES)
        ):
            continue
        if (
            not isinstance(raw_metadata, dict)
            or type(raw_metadata.get("size")) is not int
            or raw_metadata.get("size", -1) < 0
            or not isinstance(raw_metadata.get("sha256"), str)
            or HEX64.fullmatch(raw_metadata["sha256"]) is None
        ):
            raise PublishedReleaseError(f"metadados de asset inválidos: {raw_name}")
        add(PurePosixPath(raw_name).name, {
            "size": raw_metadata["size"],
            "digest": f"sha256:{raw_metadata['sha256']}",
        })
    if not assets:
        raise PublishedReleaseError("candidato não possui ZIP publicável")

    bound_metadata = manifest.get("metadata")
    if not isinstance(bound_metadata, dict) or set(bound_metadata) != set(BOUND_METADATA_NAMES):
        raise PublishedReleaseError("manifest sem metadata público fechado")
    for name in PUBLIC_METADATA_NAMES:
        actual = _file_identity(Path(candidate), name)
        if name in BOUND_METADATA_NAMES:
            expected = bound_metadata.get(name)
            if (
                not isinstance(expected, dict)
                or expected.get("size") != actual["size"]
                or expected.get("sha256") != str(actual["digest"])[len("sha256:"):]
            ):
                raise PublishedReleaseError(f"metadata público diverge do manifest: {name}")
        add(name, actual)
    return assets


def _fetch_json_with_downloader(
    url: str,
    timeout: float,
    *,
    token: str,
) -> Mapping[str, object]:
    contract = BoundedMetadata(
        url=url,
        maximum_size=MAX_RESPONSE_BYTES,
        deadline_seconds=timeout,
        retry=RetryPolicy(attempts=1),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "x86qw-release-gate",
        },
        label="metadata de release pública",
    )
    try:
        result = download(contract)
    except DownloadHTTPError as error:
        if error.status == 404:
            raise PublishedReleaseNotFound() from error
        raise PublishedReleaseError("API pública de release retornou erro HTTP") from error
    except DownloadError as error:
        raise PublishedReleaseError("não foi possível consultar a API pública de release") from error
    try:
        payload = json.loads(
            (result.data or b"").decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublishedReleaseError("JSON da API pública de release inválido") from error
    if not isinstance(payload, dict):
        raise PublishedReleaseError("schema da API pública de release inválido")
    return payload


def _load_ref(ref: Mapping[str, object], *, expected_ref: str, commit: str) -> None:
    if ref.get("ref") != expected_ref:
        raise PublishedReleaseError("ref pública diverge da tag candidata")
    target = ref.get("object")
    if (
        not isinstance(target, dict)
        or target.get("type") != "commit"
        or target.get("sha") != commit
    ):
        raise PublishedReleaseError("tag pública não aponta diretamente para o commit candidato")


def _load_release(
    release: Mapping[str, object],
    *,
    tag: str,
    expected_assets: Mapping[str, Mapping[str, object]],
    expected_prerelease: bool,
) -> None:
    if (
        release.get("tag_name") != tag
        or release.get("draft") is not False
        or release.get("prerelease") is not expected_prerelease
    ):
        raise PublishedReleaseError("release pública é draft, prerelease ou usa tag divergente")
    raw_assets = release.get("assets")
    if not isinstance(raw_assets, list):
        raise PublishedReleaseError("release pública sem lista de assets")
    seen_names: set[str] = set()
    seen_portable: set[str] = set()
    seen_ids: set[int] = set()
    actual: dict[str, dict[str, object]] = {}
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, dict):
            raise PublishedReleaseError("asset público inválido")
        name = _asset_basename(raw_asset.get("name"))
        portable_name = _portable(name)
        asset_id = raw_asset.get("id")
        if (
            isinstance(asset_id, bool)
            or not isinstance(asset_id, int)
            or asset_id <= 0
            or asset_id in seen_ids
        ):
            raise PublishedReleaseError("asset público sem ID único")
        seen_ids.add(asset_id)
        if name in seen_names or portable_name in seen_portable:
            raise PublishedReleaseError("assets públicos duplicados")
        seen_names.add(name)
        seen_portable.add(portable_name)
        if (
            raw_asset.get("state") != "uploaded"
            or type(raw_asset.get("size")) is not int
            or raw_asset.get("size", -1) < 0
            or not isinstance(raw_asset.get("digest"), str)
            or ASSET_DIGEST_RE.fullmatch(raw_asset["digest"]) is None
        ):
            raise PublishedReleaseError("asset público incompleto ou sem digest SHA-256")
        actual[name] = {
            "size": raw_asset["size"],
            "digest": raw_asset["digest"],
        }
    if set(actual) != set(expected_assets):
        raise PublishedReleaseError("conjunto de assets públicos diverge do candidato")
    for name, expected in expected_assets.items():
        if actual[name] != dict(expected):
            raise PublishedReleaseError(f"asset público diverge do candidato: {name}")


def classify_published_release(
    candidate: Path,
    *,
    trust_root: Path | None = None,
    repository: str,
    version: str,
    commit: str,
    token: str | None = None,
    api_root: str = DEFAULT_API,
    verify: VerifyCandidate | None = None,
    fetch_json: FetchJson | None = None,
    deadline_seconds: float = DEADLINE_SECONDS,
) -> str:
    """Return ``absent`` or ``exact``; reject every partial/divergent state."""
    _validate_identity(repository, version, commit)
    try:
        validate_https_url(api_root, "endpoint da API pública")
    except DownloadError as error:
        raise PublishedReleaseError("endpoint da API pública precisa ser HTTPS") from error
    if deadline_seconds <= 0:
        raise PublishedReleaseError("prazo da consulta pública inválido")
    verifier = verify or verify_candidate
    try:
        verification_kwargs = {}
        if trust_root is not None:
            verification_kwargs["trust_root"] = Path(trust_root)
        manifest = verifier(Path(candidate), **verification_kwargs)
    except CandidateError as error:
        raise PublishedReleaseError("candidato aprovado não pôde ser verificado") from error
    if manifest.get("version") != version or manifest.get("commit") != commit:
        raise PublishedReleaseError("manifest verificado diverge da identidade solicitada")
    try:
        durable_receipt = validate_durable_assets(Path(candidate), trust_root=trust_root)
        _require_final_public_acceptance(durable_receipt, version)
    except ReleaseReceiptError as error:
        raise PublishedReleaseError(f"evidência durável pública inválida: {error}") from error
    expected_assets = _candidate_assets(manifest, Path(candidate))
    tag = f"x86qw-installer-{version}"
    encoded_repo = urllib.parse.quote(repository, safe="/")
    encoded_tag = urllib.parse.quote(tag, safe="")
    base = f"{api_root.rstrip('/')}/repos/{encoded_repo}"
    ref_url = f"{base}/git/ref/tags/{encoded_tag}"
    release_url = f"{base}/releases/tags/{encoded_tag}"
    if fetch_json is None:
        if token is None or not token or any(char.isspace() or ord(char) < 0x20 for char in token):
            raise PublishedReleaseError("token GitHub ausente ou inválido")

        def fetch_json(url: str, timeout: float) -> Mapping[str, object]:
            return _fetch_json_with_downloader(url, timeout, token=token)
    deadline = time.monotonic() + deadline_seconds

    def query(url: str) -> Mapping[str, object] | None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PublishedReleaseError("prazo total da consulta pública excedido")
        try:
            return fetch_json(url, min(10.0, remaining))
        except PublishedReleaseNotFound:
            return None
        except PublishedReleaseError:
            raise
        except Exception as error:
            raise PublishedReleaseError("resposta da API pública inconclusiva") from error

    ref = query(ref_url)
    release = query(release_url)
    if ref is None and release is None:
        return "absent"
    if ref is None or release is None:
        raise PublishedReleaseError("estado público assimétrico: tag ou release ausente")
    _load_ref(ref, expected_ref=f"refs/tags/{tag}", commit=commit)
    _load_release(
        release,
        tag=tag,
        expected_assets=expected_assets,
        expected_prerelease=parse_semver(version).is_prerelease,
    )
    return "exact"


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--trust-root", type=Path)
    parser.add_argument("--allow-absent", action="store_true")
    options = parser.parse_args(arguments)
    try:
        state = classify_published_release(
            options.candidate,
            trust_root=options.trust_root,
            repository=options.repository,
            version=options.version,
            commit=options.commit,
            token=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"),
        )
    except PublishedReleaseError as error:
        print(f"[ERRO] Estado público inconclusivo: {error}", file=sys.stderr)
        return 2
    print(state)
    if state == "absent" and not options.allow_absent:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
