"""Verify signed release metadata against one immutable candidate.

This command is intentionally read-only.  It is the last gate before a
protected publication job may move signed metadata; it never edits the
catalog, bootstrap, tag or release assets.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools.release_candidate import CandidateError, verify_candidate
from x86qw_runtime.trust import (
    MAX_METADATA_BYTES,
    TrustError,
    parse_json_bytes,
    verify_release_metadata,
)


class ReleaseMetadataError(RuntimeError):
    """Signed metadata does not authenticate the candidate being promoted."""


def _read_regular(path: Path) -> bytes:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ReleaseMetadataError(f"arquivo de metadata ausente ou inseguro: {path}")
    try:
        with path.open("rb") as stream:
            payload = stream.read(MAX_METADATA_BYTES + 1)
        if len(payload) > MAX_METADATA_BYTES:
            raise ReleaseMetadataError(
                f"metadata excede o limite de {MAX_METADATA_BYTES} bytes: {path}"
            )
        return payload
    except OSError as error:
        raise ReleaseMetadataError(f"não foi possível ler metadata: {path}") from error


def _catalog_record(catalog_bytes: bytes, version: str) -> dict[str, Any]:
    try:
        value = parse_json_bytes(catalog_bytes)
    except TrustError as error:
        raise ReleaseMetadataError("catálogo do candidato não é JSON válido") from error
    packages = value.get("packages") if isinstance(value, dict) else None
    if not isinstance(packages, list):
        raise ReleaseMetadataError("catálogo do candidato não possui packages")
    records = [
        item for item in packages
        if isinstance(item, dict)
        and item.get("package") == "x86qw-installer"
        and item.get("version") == version
        and item.get("current") is True
    ]
    if len(records) != 1:
        raise ReleaseMetadataError(
            f"catálogo não identifica exatamente um instalador current {version}"
        )
    return records[0]


def verify_candidate_metadata(
    *,
    candidate: Path,
    root: Path,
    current: Path,
    snapshot: Path,
    catalog: Path,
    expected_release: str | None = None,
) -> dict[str, object]:
    """Verify trust chain, release identity and installer digest read-only."""

    # Trust metadata is an independent publication boundary.  Candidate
    # verification accepts the Mac/local flow without native evidence; an
    # explicitly supplied legacy evidence file is still validated structurally.
    manifest = verify_candidate(Path(candidate))
    version = str(manifest["version"])
    if expected_release is not None and version != expected_release:
        raise ReleaseMetadataError(
            f"candidato {version} diverge da versão esperada {expected_release}"
        )
    catalog_bytes = _read_regular(catalog)
    try:
        verified = verify_release_metadata(
            _read_regular(root),
            _read_regular(current),
            _read_regular(snapshot),
            catalog_bytes,
        )
    except TrustError as error:
        raise ReleaseMetadataError(f"cadeia de trust rejeitada: {error}") from error
    if verified.release != version:
        raise ReleaseMetadataError(
            f"metadata current aponta para {verified.release}, candidato é {version}"
        )
    record = _catalog_record(catalog_bytes, version)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ReleaseMetadataError("manifest do candidato não possui artifacts")
    installer_name = f"installer/x86qw-installer-{version}.zip"
    installer = artifacts.get(installer_name)
    if not isinstance(installer, dict):
        raise ReleaseMetadataError(
            f"candidato não contém o bundle do instalador em {installer_name}"
        )
    if (
        record.get("filename") != f"x86qw-installer-{version}.zip"
        or record.get("size") != installer.get("size")
        or record.get("sha256") != installer.get("sha256")
    ):
        raise ReleaseMetadataError(
            "digest ou tamanho do instalador diverge entre candidato e catálogo"
        )
    return {
        "format": 1,
        "project": "x86qw",
        "release": version,
        "commit": manifest["commit"],
        "catalog_release": verified.release,
        "catalog_sha256": verified.catalog.sha256,
        "installer": {
            "path": installer_name,
            "size": installer["size"],
            "sha256": installer["sha256"],
        },
        "status": "verified",
    }


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="verifica metadata assinada contra candidato imutável")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--expected-release")
    options = parser.parse_args(arguments)
    try:
        result = verify_candidate_metadata(
            candidate=options.candidate,
            root=options.root,
            current=options.current,
            snapshot=options.snapshot,
            catalog=options.catalog,
            expected_release=options.expected_release,
        )
    except (CandidateError, ReleaseMetadataError, OSError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
