#!/usr/bin/env python3
"""Verify the durable JSON receipt produced by public M3 acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools.public_install_smoke import _is_candidate_version  # noqa: E402
from x86qw_runtime.io.metadata import read_bounded_regular_file  # noqa: E402


MAX_RECORD_BYTES = 256 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_OPERATIONS = (
    "version",
    "changes",
    "migrate_dry_run",
    "update_dry_run",
    "update_apply",
    "update_idempotent",
    "verify",
    "uninstall",
    "uninstall_purge",
)
REQUIRED_MIGRATION_FIELDS = frozenset({
    "source_version",
    "source_bundle_sha256",
    "migrate_apply",
    "target_version",
    "upgrade_to_candidate",
    "verify_after_upgrade",
    "uninstall_preserved_personal_data",
    "uninstall_preserved_paks",
    "uninstall_exit_code",
})


class PublicAcceptanceError(RuntimeError):
    """The public acceptance receipt is absent, stale, or incomplete."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = read_bounded_regular_file(path, maximum_size=MAX_RECORD_BYTES)
    except OSError as error:
        raise PublicAcceptanceError(f"recibo público ausente ou inseguro: {path}") from error
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicAcceptanceError("recibo público não é JSON UTF-8 válido") from error
    if not isinstance(value, dict):
        raise PublicAcceptanceError("recibo público precisa ser um objeto JSON")
    return value


def _require_expected_digest(value: str | None, actual: str, label: str) -> None:
    if value is None:
        return
    if HEX64.fullmatch(value) is None:
        raise PublicAcceptanceError(f"digest esperado inválido: {label}")
    if value != actual:
        raise PublicAcceptanceError(f"{label} diverge do recibo público")


def verify_record(
    path: Path,
    *,
    expected_version: str,
    expected_receipt_sha256: str | None = None,
    expected_bundle_sha256: str | None = None,
    expected_catalog_sha256: str | None = None,
) -> dict[str, object]:
    if not _is_candidate_version(expected_version):
        raise PublicAcceptanceError("versão esperada do recibo não é SemVer válida")
    record = _read_json(Path(path))
    record_format = record.get("format")
    if record_format not in {1, 2} or record.get("project") != "x86qw":
        raise PublicAcceptanceError("identidade do recibo público inválida")
    if record.get("candidate_version") != expected_version:
        raise PublicAcceptanceError("recibo público pertence a outra versão")
    if record.get("platform") != "macos" or record.get("channel") != "stable":
        raise PublicAcceptanceError("recibo público não é a aceitação macOS stable")
    if record.get("release") != "latest" or record.get("profile") != "complete":
        raise PublicAcceptanceError("recibo público não usou o perfil completo corrente")
    if record.get("verified") is not True:
        raise PublicAcceptanceError("recibo público não está marcado como verificado")
    digest = record.get("bundle_sha256")
    if not isinstance(digest, str) or HEX64.fullmatch(digest) is None:
        raise PublicAcceptanceError("recibo público não possui digest do bundle")
    catalog_digest = record.get("catalog_sha256")
    if not isinstance(catalog_digest, str) or HEX64.fullmatch(catalog_digest) is None:
        raise PublicAcceptanceError("recibo público não possui digest do catálogo TUF")
    receipt_digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    _require_expected_digest(expected_receipt_sha256, receipt_digest, "receipt_sha256")
    _require_expected_digest(expected_bundle_sha256, digest, "bundle_sha256")
    _require_expected_digest(expected_catalog_sha256, catalog_digest, "catalog_sha256")

    lifecycle = record.get("full_lifecycle")
    if not isinstance(lifecycle, dict):
        raise PublicAcceptanceError("recibo público não contém full_lifecycle")
    launcher = lifecycle.get("launcher")
    if launcher not in {"x86qw.sh", "x86qw.cmd"}:
        raise PublicAcceptanceError("recibo público não identifica launcher instalado")
    operations = lifecycle.get("operations")
    if not isinstance(operations, dict) or set(operations) != set(REQUIRED_OPERATIONS):
        raise PublicAcceptanceError("recibo público não contém todas as operações obrigatórias")
    if any(type(operations[name]) is not bool or operations[name] is not True for name in REQUIRED_OPERATIONS):
        raise PublicAcceptanceError("recibo público contém operação não comprovada")
    if lifecycle.get("personal_data_preserved_by_uninstall") is not True:
        raise PublicAcceptanceError("recibo público não comprovou uninstall conservador")
    if lifecycle.get("purge_removed_personal_data") is not True:
        raise PublicAcceptanceError("recibo público não comprovou purge")
    if record_format == 2:
        migration = lifecycle.get("migration")
        if not isinstance(migration, dict) or set(migration) != REQUIRED_MIGRATION_FIELDS:
            raise PublicAcceptanceError(
                "recibo público v2 não contém a migração real 0.7.13"
            )
        if migration.get("source_version") != "0.7.13":
            raise PublicAcceptanceError("migração pública não partiu de 0.7.13")
        source_digest = migration.get("source_bundle_sha256")
        if not isinstance(source_digest, str) or HEX64.fullmatch(source_digest) is None:
            raise PublicAcceptanceError("migração pública não possui digest do instalador 0.7.13")
        if migration.get("target_version") != expected_version:
            raise PublicAcceptanceError("migração pública não convergiu para o candidato")
        for field in (
            "migrate_apply",
            "upgrade_to_candidate",
            "verify_after_upgrade",
            "uninstall_preserved_personal_data",
            "uninstall_preserved_paks",
        ):
            if migration.get(field) is not True:
                raise PublicAcceptanceError(f"migração pública não comprovou {field}")
        if type(migration.get("uninstall_exit_code")) is not int or migration["uninstall_exit_code"] != 0:
            raise PublicAcceptanceError("uninstall pós-migração pública terminou com erro")
    return {
        "format": 1,
        "project": "x86qw",
        "status": "verified-public-acceptance",
        "candidate_version": expected_version,
        "receipt_sha256": receipt_digest,
        "bundle_sha256": digest,
        "catalog_sha256": catalog_digest,
        "operations": list(REQUIRED_OPERATIONS),
        "migration_source_version": (
            "0.7.13" if record_format == 2 else None
        ),
    }


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--expected-receipt-sha256")
    parser.add_argument("--expected-bundle-sha256")
    parser.add_argument("--expected-catalog-sha256")
    options = parser.parse_args(arguments)
    try:
        result = verify_record(
            options.record,
            expected_version=options.version,
            expected_receipt_sha256=options.expected_receipt_sha256,
            expected_bundle_sha256=options.expected_bundle_sha256,
            expected_catalog_sha256=options.expected_catalog_sha256,
        )
    except (OSError, PublicAcceptanceError) as error:
        print(f"[ERRO] Aceitação pública inválida: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
