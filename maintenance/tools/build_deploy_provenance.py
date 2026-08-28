#!/usr/bin/env python3
"""Write static /version, /api/v1/version and /api/v1/health documents."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
VERSION_PATHS = ("version", "api/v1/version")
HEALTH_PATH = "api/v1/health"


class DeployProvenanceError(ValueError):
    """The deploy provenance documents cannot be written."""


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise DeployProvenanceError(f"catálogo ausente ou inseguro: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_deploy_provenance(
    *,
    commit: str,
    validate_run_id: int,
    deploy_run_id: int,
    catalog_sha256: str,
) -> dict[str, dict[str, object]]:
    if not HEX40.fullmatch(commit):
        raise DeployProvenanceError("commit precisa ser SHA-1 de 40 hex")
    if isinstance(validate_run_id, bool) or validate_run_id < 1:
        raise DeployProvenanceError("validate_run_id inválido")
    if isinstance(deploy_run_id, bool) or deploy_run_id < 1:
        raise DeployProvenanceError("deploy_run_id inválido")
    if not HEX64.fullmatch(catalog_sha256):
        raise DeployProvenanceError("catalog_sha256 inválido")
    version: dict[str, object] = {
        "catalog_sha256": catalog_sha256,
        "commit": commit,
        "deploy_run_id": deploy_run_id,
        "external_public": False,
        "format": 1,
        "project": "x86qw",
        "release_audience": "owner-only",
        "validate_run_id": validate_run_id,
    }
    health: dict[str, object] = {
        "commit": commit,
        "external_public": False,
        "format": 1,
        "project": "x86qw",
        "release_audience": "owner-only",
        "status": "ok",
        "validate_run_id": validate_run_id,
        "deploy_run_id": deploy_run_id,
    }
    return {"version": version, "health": health}


def write_deploy_provenance(directory: Path, documents: dict[str, dict[str, object]]) -> None:
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise DeployProvenanceError(f"destino ausente ou inseguro: {directory}")
    encoded_version = json.dumps(documents["version"], ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    encoded_health = json.dumps(documents["health"], ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    for relative in VERSION_PATHS:
        path = directory.joinpath(*Path(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(encoded_version, encoding="utf-8")
    health_path = directory.joinpath(*Path(HEALTH_PATH).parts)
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_path.write_text(encoded_health, encoding="utf-8")


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--validate-run-id", type=int, required=True)
    parser.add_argument("--deploy-run-id", type=int, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        documents = build_deploy_provenance(
            commit=options.commit,
            validate_run_id=options.validate_run_id,
            deploy_run_id=options.deploy_run_id,
            catalog_sha256=_sha256_file(options.catalog),
        )
        write_deploy_provenance(options.directory, documents)
    except (OSError, DeployProvenanceError) as error:
        print(f"[ERRO] {error}")
        return 1
    print(json.dumps(documents["version"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
