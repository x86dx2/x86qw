#!/usr/bin/env python3
"""Project verified candidate and deployment facts into release-truth JSON."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HERO = re.compile(r'<p\s+class="kicker"[^>]*>(.*?)</p>', re.DOTALL)


class ReleaseTruthProjectionError(ValueError):
    """The projection cannot be produced without carrying stale evidence."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ReleaseTruthProjectionError(f"{label} ausente ou inseguro: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseTruthProjectionError(f"{label} inválido: {path}") from error
    if not isinstance(value, dict):
        raise ReleaseTruthProjectionError(f"{label} precisa ser um objeto JSON")
    return value


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseTruthProjectionError(f"{label} ausente ou inválido")
    return value


def _metadata_version(repository: Path, suffix: str) -> tuple[int, dict[str, Any]]:
    metadata = repository / "metadata"
    if repository.is_symlink() or not repository.is_dir() or metadata.is_symlink() or not metadata.is_dir():
        raise ReleaseTruthProjectionError(f"repositório TUF ausente ou inseguro: {repository}")
    versions: list[tuple[int, dict[str, Any]]] = []
    for path in sorted(metadata.glob(f"*.{suffix}.json")):
        document = _read_json(path, f"metadata TUF {suffix}")
        signed = _require_mapping(document.get("signed"), f"metadata TUF {suffix}.signed")
        version = signed.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ReleaseTruthProjectionError(f"versão TUF inválida: {path.name}")
        versions.append((version, signed))
    if not versions:
        raise ReleaseTruthProjectionError(f"metadata TUF {suffix} ausente")
    return max(versions, key=lambda item: item[0])


def _timestamp_metadata(repository: Path) -> tuple[int, dict[str, Any]]:
    path = repository / "metadata/timestamp.json"
    document = _read_json(path, "metadata TUF timestamp")
    signed = _require_mapping(document.get("signed"), "metadata TUF timestamp.signed")
    version = signed.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ReleaseTruthProjectionError("versão TUF timestamp inválida")
    return version, signed


def _root_version(repository: Path) -> int:
    metadata = repository / "metadata"
    versions: list[int] = []
    for path in sorted(metadata.glob("*.root.json")):
        encoded = path.name.removesuffix(".root.json")
        if not encoded.isdecimal():
            raise ReleaseTruthProjectionError(f"root TUF possui nome inválido: {path.name}")
        document = _read_json(path, "root TUF versionada")
        signed = _require_mapping(document.get("signed"), "root TUF.signed")
        version = signed.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version != int(encoded):
            raise ReleaseTruthProjectionError(f"root TUF diverge do nome: {path.name}")
        versions.append(version)
    if not versions or sorted(versions) != list(range(1, max(versions) + 1)):
        raise ReleaseTruthProjectionError("cadeia de roots TUF não é contínua a partir da v1")
    return max(versions)


def _parse_utc(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReleaseTruthProjectionError(f"{label} inválido") from error
    if parsed.tzinfo is None:
        raise ReleaseTruthProjectionError(f"{label} precisa informar timezone")
    return parsed.astimezone(timezone.utc)


def _hero(site_source: Path) -> str | None:
    index = site_source / "index.html"
    if index.is_symlink() or not index.is_file():
        raise ReleaseTruthProjectionError(f"index.html do candidato ausente ou inseguro: {index}")
    try:
        match = HERO.search(index.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as error:
        raise ReleaseTruthProjectionError("index.html do candidato inválido") from error
    if not match:
        return None
    text = html.unescape(re.sub(r"<[^>]+>", "", match.group(1)))
    return " ".join(text.split()) or None


def _current_installer(catalog: dict[str, Any]) -> dict[str, Any]:
    packages = catalog.get("packages")
    if not isinstance(packages, list):
        raise ReleaseTruthProjectionError("catálogo candidato não possui packages")
    records = [
        item for item in packages
        if isinstance(item, dict)
        and item.get("package") == "x86qw-installer"
        and item.get("current") is True
    ]
    if len(records) != 1:
        raise ReleaseTruthProjectionError("catálogo candidato não possui um instalador current único")
    return records[0]


def _immutable_match(current: object, expected: object, label: str) -> None:
    if current is not None and current != expected:
        raise ReleaseTruthProjectionError(
            f"{label} diverge da evidência já registrada; não é permitido reutilizá-la"
        )


def project_release_truth(
    *,
    source: Path,
    candidate: Path,
    trust_repository: Path,
    site_source: Path,
    release_code_commit: str,
    development_validate_run: int,
    observed_at: str,
    output: Path,
    renewal_run_id: int | None = None,
    publication_run_id: int | None = None,
) -> dict[str, Any]:
    if HEX40.fullmatch(release_code_commit) is None:
        raise ReleaseTruthProjectionError("release_code_commit inválido")
    if isinstance(development_validate_run, bool) or development_validate_run < 1:
        raise ReleaseTruthProjectionError("development_validate_run inválido")
    observed = _parse_utc(observed_at, "observed_at")

    truth = deepcopy(_read_json(Path(source), "release-truth fonte"))
    candidate = Path(candidate)
    manifest = _read_json(candidate / "candidate.json", "candidate.json")
    catalog = _read_json(candidate / "catalog.json", "catálogo candidato")
    product = _read_json(candidate / "product.json", "product candidato")
    if manifest.get("project") != "x86qw" or manifest.get("version") != product.get("version"):
        raise ReleaseTruthProjectionError("candidate e product divergem")
    candidate_commit = manifest.get("commit")
    candidate_version = manifest.get("version")
    if HEX40.fullmatch(candidate_commit or "") is None or not isinstance(candidate_version, str):
        raise ReleaseTruthProjectionError("identidade do candidato inválida")
    if product.get("release_audience") != "owner-only" or product.get("external_public") is not False:
        raise ReleaseTruthProjectionError("projeção só pode manter a audiência owner-only")
    installer = _current_installer(catalog)
    if installer.get("version") != candidate_version:
        raise ReleaseTruthProjectionError("instalador current diverge da versão do candidato")
    artifacts = _require_mapping(manifest.get("artifacts"), "artifacts do candidato")
    installer_key = f"installer/x86qw-installer-{candidate_version}.zip"
    artifact = _require_mapping(artifacts.get(installer_key), "artifact do instalador")
    installer_size = installer.get("size")
    installer_sha256 = installer.get("sha256")
    if (
        type(installer_size) is not int
        or installer_size <= 0
        or type(artifact.get("size")) is not int
        or artifact.get("size") != installer_size
        or not isinstance(installer_sha256, str)
        or HEX64.fullmatch(installer_sha256) is None
        or artifact.get("sha256") != installer_sha256
    ):
        raise ReleaseTruthProjectionError("pin do instalador diverge entre candidato e catálogo")
    candidate_sha256 = hashlib.sha256((candidate / "candidate.json").read_bytes()).hexdigest()

    authorities = _require_mapping(truth.get("authorities"), "authorities da verdade de release")
    candidate_release = _require_mapping(authorities.get("candidate_release"), "candidate_release")
    _immutable_match(candidate_release.get("version"), candidate_version, "versão do candidate_release")
    _immutable_match(candidate_release.get("target_commit"), candidate_commit, "target_commit do candidate_release")
    _immutable_match(candidate_release.get("candidate_sha256"), candidate_sha256, "candidate_sha256")
    _immutable_match(candidate_release.get("installer_size_bytes"), installer_size, "tamanho do instalador")
    _immutable_match(candidate_release.get("installer_sha256"), installer_sha256, "SHA-256 do instalador")
    _immutable_match(candidate_release.get("tag"), f"x86qw-installer-{candidate_version}", "tag do candidate_release")
    candidate_release.update({
        "tag": f"x86qw-installer-{candidate_version}",
        "version": candidate_version,
        "target_commit": candidate_commit,
        "installer_size_bytes": installer_size,
        "installer_sha256": installer_sha256,
        "candidate_sha256": candidate_sha256,
    })

    root_version = _root_version(Path(trust_repository))
    timestamp_version, timestamp = _timestamp_metadata(Path(trust_repository))
    snapshot_version, _snapshot = _metadata_version(Path(trust_repository), "snapshot")
    targets_version, _targets = _metadata_version(Path(trust_repository), "targets")
    expires_text = timestamp.get("expires")
    if not isinstance(expires_text, str):
        raise ReleaseTruthProjectionError("timestamp TUF não declara expires")
    expires = _parse_utc(expires_text, "expiry do timestamp TUF")
    seconds_to_expiry = int((expires - observed).total_seconds())
    if seconds_to_expiry <= 0:
        raise ReleaseTruthProjectionError("timestamp TUF já expirou na observação")

    deployment = _require_mapping(authorities.get("deployment"), "deployment")
    live = _require_mapping(deployment.get("live_observation"), "live_observation")
    tuf = _require_mapping(deployment.get("tuf"), "deployment.tuf")
    live.update({
        "observed_at_utc": observed_at,
        "product_version": candidate_version,
        "catalog_current_installer": candidate_version,
        "release_truth_endpoint": "200",
        "product_release_audience": "owner-only",
        "root_mentions_owner_only": True,
        "state": "CONVERGED_CANDIDATE_DEPLOYMENT",
    })
    hero = _hero(Path(site_source))
    if hero is not None:
        live["root_site_hero"] = hero
    tuf.update({
        "root_version": root_version,
        "timestamp_version": timestamp_version,
        "snapshot_version": snapshot_version,
        "targets_version": targets_version,
        "packages_observed": len(catalog.get("packages", [])),
        "timestamp_expiry": expires_text,
        "seconds_to_expiry_at_observation": seconds_to_expiry,
        "warning_hours": 6,
        "monitor_warning_6h": "healthy" if seconds_to_expiry > 6 * 3600 else "warning",
        "monitor_warning_1h": "healthy" if seconds_to_expiry > 3600 else "warning",
        "technical_chain": "authenticated",
        "operational_status": "HEALTHY",
        "catalog_sha256": hashlib.sha256((candidate / "catalog.json").read_bytes()).hexdigest(),
    })
    if renewal_run_id is not None:
        tuf["renewal_run_id"] = renewal_run_id
    if publication_run_id is not None:
        tuf["publication_run_id"] = publication_run_id
    for stale in ("publication_artifact_id", "publication_artifact_sha256"):
        tuf.pop(stale, None)

    development = _require_mapping(authorities.get("development"), "development")
    development.update({"head": release_code_commit, "validate_run": development_validate_run})
    truth.update({
        "observed_at_utc": observed_at,
        "snapshot_commit": release_code_commit,
    })
    status = _require_mapping(truth.get("status"), "status")
    status.update({
        "main": "GREEN",
        "tuf": "HEALTHY",
        "owner_only_release": "VALID_FOR_SINGLE_USER_M3",
        "external_public": "NO-GO",
        "feature_work": "ALLOWED",
    })
    if output.exists() or output.is_symlink():
        raise ReleaseTruthProjectionError(f"destino do projection overlay já existe: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(truth, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return truth


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--trust-repository", type=Path, required=True)
    parser.add_argument("--site-source", type=Path, required=True)
    parser.add_argument("--release-code-commit", required=True)
    parser.add_argument("--development-validate-run", type=int, required=True)
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--renewal-run-id", type=int)
    parser.add_argument("--publication-run-id", type=int)
    options = parser.parse_args(arguments)
    try:
        result = project_release_truth(
            source=options.source,
            candidate=options.candidate,
            trust_repository=options.trust_repository,
            site_source=options.site_source,
            release_code_commit=options.release_code_commit,
            development_validate_run=options.development_validate_run,
            observed_at=options.observed_at,
            output=options.output,
            renewal_run_id=options.renewal_run_id,
            publication_run_id=options.publication_run_id,
        )
    except (OSError, ReleaseTruthProjectionError) as error:
        print(f"[ERRO] {error}")
        return 1
    print(json.dumps({"format": 1, "project": "x86qw", "status": "projected", "output": str(options.output)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
