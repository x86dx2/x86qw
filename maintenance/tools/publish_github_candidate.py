#!/usr/bin/env python3
"""Publish one verified candidate to GitHub without overwriting public bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from .release_candidate import BOUND_METADATA, CandidateError, verify_candidate
    from .validate_catalog import validate_catalog
    from .verify_published_release import PUBLIC_METADATA_NAMES
except ImportError:  # Execucao direta
    from release_candidate import BOUND_METADATA, CandidateError, verify_candidate
    from validate_catalog import validate_catalog
    from verify_published_release import PUBLIC_METADATA_NAMES

from x86qw_runtime.versioning import VersionError, parse_semver


PROJECT = "x86qw"
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
MAX_GH_RESPONSE_BYTES = 4 * 1024 * 1024


class PublisherError(RuntimeError):
    """The candidate or GitHub public state cannot be published safely."""


class GitHubNotFound(PublisherError):
    """The GitHub API returned a genuine 404."""


GhRunner = Callable[[Sequence[str]], str]


def _safe_repository(repository: str) -> str:
    if REPOSITORY_RE.fullmatch(repository) is None:
        raise PublisherError("repositório GitHub inválido")
    return repository


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PublisherError(f"caminho de candidato inválido: {value}")
    return path


def _file_identity(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise PublisherError(f"asset candidato ausente ou inseguro: {path}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return {"size": size, "digest": f"sha256:{digest.hexdigest()}", "path": path}


def _expected_assets(candidate: Path, manifest: Mapping[str, object]) -> dict[str, dict[str, object]]:
    """Map public asset basenames to exact candidate files and digests."""

    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, dict):
        raise PublisherError("manifest do candidato não possui artifacts")
    expected: dict[str, dict[str, object]] = {}
    portable: set[str] = set()

    def add(name: str, path: Path, expected_size: int, expected_sha256: str) -> None:
        if not name or PurePosixPath(name).name != name or "\\" in name:
            raise PublisherError(f"basename de asset inválido: {name!r}")
        key = unicodedata.normalize("NFC", name).casefold()
        if key in portable:
            raise PublisherError(f"basename de asset duplicado: {name}")
        actual = _file_identity(path)
        if actual["size"] != expected_size or actual["digest"] != f"sha256:{expected_sha256}":
            raise PublisherError(f"asset candidato diverge do manifest: {name}")
        portable.add(key)
        expected[name] = actual

    for raw_name, raw_facts in raw_artifacts.items():
        if not isinstance(raw_name, str) or not raw_name.casefold().endswith(".zip"):
            continue
        if not isinstance(raw_facts, dict):
            raise PublisherError("metadado de ZIP do candidato inválido")
        size = raw_facts.get("size")
        sha256 = raw_facts.get("sha256")
        if type(size) is not int or size < 0 or not isinstance(sha256, str) or HEX64.fullmatch(sha256) is None:
            raise PublisherError("pin de ZIP do candidato inválido")
        relative = _safe_relative(raw_name)
        add(relative.name, candidate.joinpath(*relative.parts), size, sha256)

    bound_metadata = manifest.get("metadata")
    if not isinstance(bound_metadata, dict):
        raise PublisherError("manifest não possui metadata pública completa")
    allowed_bound_metadata = {"mirrors.json", *BOUND_METADATA}
    if set(bound_metadata) != set(BOUND_METADATA) and set(bound_metadata) != allowed_bound_metadata:
        raise PublisherError("manifest não possui metadata pública completa")
    metadata_names = list(PUBLIC_METADATA_NAMES)
    if "mirrors.json" in bound_metadata:
        metadata_names.append("mirrors.json")
    for name in metadata_names:
        actual = _file_identity(candidate / name)
        if name in BOUND_METADATA:
            facts = bound_metadata.get(name)
            if (
                not isinstance(facts, dict)
                or facts.get("size") != actual["size"]
                or facts.get("sha256") != str(actual["digest"])[len("sha256:"):]
            ):
                raise PublisherError(f"metadata pública diverge do manifest: {name}")
        key = unicodedata.normalize("NFC", name).casefold()
        if key in portable:
            raise PublisherError(f"basename de asset duplicado: {name}")
        portable.add(key)
        expected[name] = actual
    if not expected:
        raise PublisherError("candidato não possui assets publicáveis")
    return expected


def _asset_identity(value: Mapping[str, object]) -> tuple[object, object]:
    return value.get("size"), value.get("digest")


def _asset_plan(
    expected: Mapping[str, Mapping[str, object]],
    remote: Mapping[str, Mapping[str, object]],
) -> list[str]:
    extras = sorted(set(remote) - set(expected))
    if extras:
        raise PublisherError(f"release GitHub possui assets não autorizados: {', '.join(extras)}")
    for name in sorted(set(expected) & set(remote)):
        if _asset_identity(expected[name]) != _asset_identity(remote[name]):
            raise PublisherError(f"asset GitHub imutável diverge do candidato: {name}")
    return sorted(set(expected) - set(remote))


def _release_create_command(
    *,
    repository: str,
    tag: str,
    title: str,
    notes: str,
    commit: str,
    prerelease: bool,
    latest: bool,
) -> list[str]:
    command = [
        "release", "create", tag,
        "--repo", repository,
        "--target", commit,
        "--title", title,
        "--notes", notes,
    ]
    if prerelease:
        command.append("--prerelease")
    command.append("--latest" if latest and not prerelease else "--latest=false")
    return command


def _github_latest(*, mirror_latest: bool, prerelease: bool) -> bool:
    """Map site-current metadata to GitHub's non-prerelease Latest flag."""

    return mirror_latest and not prerelease


def _execute_gh(arguments: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            ["gh", *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PublisherError("não foi possível executar o cliente GitHub") from error
    if result.returncode != 0:
        if "404" in result.stderr or "Not Found" in result.stderr:
            raise GitHubNotFound("objeto GitHub não encontrado")
        raise PublisherError("comando GitHub falhou")
    if len(result.stdout.encode("utf-8")) > MAX_GH_RESPONSE_BYTES:
        raise PublisherError("resposta GitHub excede o limite")
    return result.stdout


def _json_document(payload: str, label: str) -> dict[str, object]:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise PublisherError(f"{label} contém chave duplicada")
            result[key] = value
        return result

    try:
        value = json.loads(payload, object_pairs_hook=no_duplicates)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PublisherError(f"{label} não é JSON válido") from error
    if not isinstance(value, dict):
        raise PublisherError(f"{label} precisa ser objeto JSON")
    return value


def _api_json(endpoint: str, *, runner: GhRunner = _execute_gh) -> dict[str, object] | None:
    try:
        return _json_document(runner(("api", endpoint)), "resposta GitHub")
    except GitHubNotFound:
        return None


def _catalog_record(candidate: Path, version: str) -> dict[str, object]:
    path = candidate / "catalog.json"
    if path.is_symlink() or not path.is_file():
        raise PublisherError("catálogo do candidato ausente")
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PublisherError("catálogo do candidato inválido") from error
    try:
        validate_catalog(catalog)
    except (TypeError, ValueError) as error:
        raise PublisherError("catálogo do candidato não passou a validação") from error
    packages = catalog.get("packages")
    assert isinstance(packages, list)
    records = [
        package for package in packages
        if isinstance(package, dict)
        and package.get("component") == "installer"
        and package.get("package") == "x86qw-installer"
        and package.get("version") == version
    ]
    if len(records) != 1:
        raise PublisherError(f"catálogo não possui um instalador único para {version}")
    record = records[0]
    if (
        not isinstance(record.get("release_title"), str)
        or not str(record["release_title"]).strip()
        or not isinstance(record.get("release_notes"), str)
        or not str(record["release_notes"]).strip()
        or not isinstance(record.get("current"), bool)
        or not isinstance(record.get("mirror_latest"), bool)
    ):
        raise PublisherError("metadados de release do instalador estão incompletos")
    return record


def _remote_assets(release: Mapping[str, object]) -> dict[str, dict[str, object]]:
    raw_assets = release.get("assets")
    if not isinstance(raw_assets, list):
        raise PublisherError("release GitHub não possui lista de assets")
    result: dict[str, dict[str, object]] = {}
    for raw in raw_assets:
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            raise PublisherError("asset GitHub inválido")
        name = raw["name"]
        if name in result:
            raise PublisherError(f"asset GitHub duplicado: {name}")
        result[name] = {
            "size": raw.get("size"),
            "digest": raw.get("digest"),
        }
    return result


def _validate_ref(ref: Mapping[str, object], *, tag: str, commit: str) -> None:
    target = ref.get("object")
    if (
        ref.get("ref") != f"refs/tags/{tag}"
        or not isinstance(target, dict)
        or target.get("type") != "commit"
        or target.get("sha") != commit
    ):
        raise PublisherError("tag GitHub não aponta diretamente para o commit candidato")


def _validate_release(
    release: Mapping[str, object],
    *,
    tag: str,
    title: str,
    notes: str,
    prerelease: bool,
) -> None:
    if (
        release.get("tag_name") != tag
        or release.get("name") != title
        or release.get("body") != notes
        or release.get("draft") is not False
        or release.get("prerelease") is not prerelease
    ):
        raise PublisherError("release GitHub existente diverge dos metadados aprovados")


def _endpoint(repository: str, suffix: str) -> str:
    return f"repos/{quote(repository, safe='/')}/{suffix}"


def publish_candidate(
    *,
    candidate: Path,
    repository: str,
    publish: bool = False,
    runner: GhRunner = _execute_gh,
) -> dict[str, object]:
    repository = _safe_repository(repository)
    candidate = Path(candidate)
    try:
        manifest = verify_candidate(candidate)
    except CandidateError as error:
        raise PublisherError(f"candidato inválido: {error}") from error
    version = manifest.get("version")
    commit = manifest.get("commit")
    if not isinstance(version, str) or not isinstance(commit, str) or HEX40.fullmatch(commit) is None:
        raise PublisherError("identidade do candidato inválida")
    try:
        parsed_version = parse_semver(version)
    except (TypeError, VersionError) as error:
        raise PublisherError("versão do candidato inválida") from error
    record = _catalog_record(candidate, version)
    expected = _expected_assets(candidate, manifest)
    tag = f"x86qw-installer-{version}"
    prerelease = parsed_version.is_prerelease
    latest = _github_latest(mirror_latest=record["mirror_latest"] is True, prerelease=prerelease)
    title = str(record["release_title"])
    notes = str(record["release_notes"])
    if not publish:
        return {
            "format": 1,
            "project": PROJECT,
            "status": "planned",
            "release": tag,
            "assets": sorted(expected),
            "latest": latest,
        }

    ref_endpoint = _endpoint(repository, f"git/ref/tags/{quote(tag, safe='')}")
    release_endpoint = _endpoint(repository, f"releases/tags/{quote(tag, safe='')}")
    ref = _api_json(ref_endpoint, runner=runner)
    release = _api_json(release_endpoint, runner=runner)
    if (ref is None) != (release is None):
        raise PublisherError("estado GitHub assimétrico: tag ou release ausente")
    if ref is None and release is None:
        try:
            runner(_release_create_command(
                repository=repository,
                tag=tag,
                title=title,
                notes=notes,
                commit=commit,
                prerelease=prerelease,
                latest=latest,
            ))
        except PublisherError:
            # A criação pode ter vencido uma corrida; só aceitamos a corrida
            # quando o estado público subsequente for exatamente o mesmo.
            ref = _api_json(ref_endpoint, runner=runner)
            release = _api_json(release_endpoint, runner=runner)
            if ref is None or release is None:
                raise
        else:
            ref = _api_json(ref_endpoint, runner=runner)
            release = _api_json(release_endpoint, runner=runner)
    assert ref is not None and release is not None
    _validate_ref(ref, tag=tag, commit=commit)
    _validate_release(
        release,
        tag=tag,
        title=title,
        notes=notes,
        prerelease=prerelease,
    )
    missing = _asset_plan(expected, _remote_assets(release))
    if missing:
        runner((
            "release", "upload", tag,
            *[os.fspath(expected[name]["path"]) for name in missing],
            "--repo", repository,
        ))
        release = _api_json(release_endpoint, runner=runner)
        if release is None:
            raise PublisherError("release desapareceu após o upload")
        _validate_release(
            release,
            tag=tag,
            title=title,
            notes=notes,
            prerelease=prerelease,
        )
        if _asset_plan(expected, _remote_assets(release)):
            raise PublisherError("upload GitHub não convergiu para os assets do candidato")

    latest_release = _api_json(_endpoint(repository, "releases/latest"), runner=runner)
    if latest:
        if latest_release is None or latest_release.get("tag_name") != tag:
            raise PublisherError("release current não é a latest pública")
    elif latest_release is not None and latest_release.get("tag_name") == tag:
        raise PublisherError("release prerelease foi marcada como latest pública")
    return {
        "format": 1,
        "project": PROJECT,
        "status": "published",
        "release": tag,
        "assets": sorted(expected),
        "latest": latest,
    }


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", "x86dx2/x86qw"))
    parser.add_argument("--publish", action="store_true")
    options = parser.parse_args(arguments)
    try:
        result = publish_candidate(
            candidate=options.candidate,
            repository=options.repository,
            publish=options.publish,
        )
    except (OSError, PublisherError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
