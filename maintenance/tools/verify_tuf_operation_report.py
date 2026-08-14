#!/usr/bin/env python3
"""Verify one accountable, non-secret TUF operation drill report."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools.tuf_operation_drill import (  # noqa: E402
    ROLES,
    TufDrillError,
    operation_context,
)


PROJECT = "x86qw"
FORMAT = 2
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_CATALOG_BYTES = 2 * 1024 * 1024
UTC = timezone.utc
UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TARGET_PATH = re.compile(r"^catalog/[0-9a-f]{64}\.catalog\.json$")
REPORT_FIELDS = frozenset({
    "checked_at",
    "current_metadata_version",
    "expiry_failure_detected",
    "format",
    "mode",
    "operation",
    "project",
    "published",
    "recovery_verified",
    "renewed_metadata_version",
    "role_versions",
    "root_unchanged",
    "status",
    "target",
    "target_unchanged",
})
TARGET_FIELDS = frozenset({"path", "sha256", "size"})
ROLE_VERSION_FIELDS = frozenset({"current", "renewed"})
OPERATION_FIELDS = frozenset({
    "custody_host",
    "key_scope",
    "operator",
    "timestamp_sla_hours",
})


class TufOperationReportError(RuntimeError):
    """The report is incomplete, unsafe, or not bound to the candidate."""


def _read_json(path: Path, *, label: str, maximum: int) -> dict[str, Any]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise TufOperationReportError(f"{label} ausente ou inseguro: {path}")
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise TufOperationReportError(f"{label} não pôde ser lido: {path}") from error
    if not payload or len(payload) > maximum:
        raise TufOperationReportError(f"{label} excede o limite permitido")

    def no_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise TufOperationReportError(f"{label} contém chave duplicada: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=no_duplicate_pairs)
    except TufOperationReportError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise TufOperationReportError(f"{label} não é JSON UTF-8 válido") from error
    if not isinstance(value, dict):
        raise TufOperationReportError(f"{label} precisa ser um objeto JSON")
    return value


def _regular_bytes(path: Path, *, label: str, maximum: int) -> bytes:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise TufOperationReportError(f"{label} ausente ou inseguro: {path}")
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise TufOperationReportError(f"{label} não pôde ser lido: {path}") from error
    if not payload or len(payload) > maximum:
        raise TufOperationReportError(f"{label} excede o limite permitido")
    return payload


def _utc_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or UTC_TIMESTAMP.fullmatch(value) is None:
        raise TufOperationReportError("checked_at precisa ser timestamp UTC canônico")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TufOperationReportError("checked_at não é timestamp válido") from error
    if parsed.tzinfo is None or parsed.astimezone(UTC) != parsed:
        raise TufOperationReportError("checked_at precisa ser timezone-aware")
    return parsed


def verify_report(*, candidate: Path, report: Path) -> dict[str, object]:
    """Verify a drill report and bind its target to ``candidate/catalog.json``."""

    candidate = Path(candidate)
    report_value = _read_json(Path(report), label="relatório TUF", maximum=MAX_REPORT_BYTES)
    if set(report_value) != REPORT_FIELDS:
        raise TufOperationReportError("relatório TUF possui campos ausentes ou extras")
    if (
        report_value.get("format") != FORMAT
        or report_value.get("project") != PROJECT
        or report_value.get("status") != "drill-passed"
        or report_value.get("mode") != "offline-renewal-expiry-recovery"
        or report_value.get("published") is not False
        or report_value.get("target_unchanged") is not True
        or report_value.get("root_unchanged") is not True
        or report_value.get("expiry_failure_detected") is not True
        or report_value.get("recovery_verified") is not True
    ):
        raise TufOperationReportError("relatório TUF não comprova drill fechado")
    checked_at = _utc_timestamp(report_value.get("checked_at"))
    if checked_at > datetime.now(UTC):
        raise TufOperationReportError("checked_at do drill TUF está no futuro")

    current = report_value.get("current_metadata_version")
    renewed = report_value.get("renewed_metadata_version")
    if (
        type(current) is not int
        or current < 1
        or type(renewed) is not int
        or renewed <= current
    ):
        raise TufOperationReportError("versões de metadata TUF não avançaram monotonicamente")

    role_versions = report_value.get("role_versions")
    if not isinstance(role_versions, Mapping) or set(role_versions) != set(ROLES):
        raise TufOperationReportError("versões TUF por role ausentes ou inválidas")
    normalized_role_versions: dict[str, dict[str, int]] = {}
    for role in ROLES:
        values = role_versions.get(role)
        if not isinstance(values, Mapping) or set(values) != ROLE_VERSION_FIELDS:
            raise TufOperationReportError(f"versões TUF inválidas para {role}")
        role_current = values.get("current")
        role_renewed = values.get("renewed")
        if (
            type(role_current) is not int
            or role_current < 1
            or type(role_renewed) is not int
            or role_renewed <= role_current
        ):
            raise TufOperationReportError(f"versões TUF não avançaram para {role}")
        normalized_role_versions[role] = {
            "current": role_current,
            "renewed": role_renewed,
        }
    if (
        current != max(values["current"] for values in normalized_role_versions.values())
        or renewed != max(values["renewed"] for values in normalized_role_versions.values())
    ):
        raise TufOperationReportError("versões agregadas divergem das versões por role")

    operation = report_value.get("operation")
    if not isinstance(operation, Mapping) or set(operation) != OPERATION_FIELDS:
        raise TufOperationReportError("contexto operacional TUF ausente ou inválido")
    try:
        normalized_operation = operation_context(
            operator=operation["operator"],
            custody_host=operation["custody_host"],
            sla_hours=operation["timestamp_sla_hours"],
        )
    except (KeyError, TufDrillError, TypeError) as error:
        raise TufOperationReportError("contexto operacional TUF inválido") from error
    if operation["key_scope"] != normalized_operation["key_scope"]:
        raise TufOperationReportError("escopo de chaves TUF diverge do contrato")

    target = report_value.get("target")
    if not isinstance(target, Mapping) or set(target) != TARGET_FIELDS:
        raise TufOperationReportError("target TUF ausente ou inválido")
    target_size = target.get("size")
    target_sha256 = target.get("sha256")
    target_path = target.get("path")
    if (
        type(target_size) is not int
        or target_size <= 0
        or target_size > MAX_CATALOG_BYTES
        or not isinstance(target_sha256, str)
        or HEX64.fullmatch(target_sha256) is None
        or not isinstance(target_path, str)
        or TARGET_PATH.fullmatch(target_path) is None
        or target_path != f"catalog/{target_sha256}.catalog.json"
    ):
        raise TufOperationReportError("identidade do target TUF inválida")

    catalog = _regular_bytes(
        candidate / "catalog.json",
        label="catálogo do candidato",
        maximum=MAX_CATALOG_BYTES,
    )
    catalog_sha256 = hashlib.sha256(catalog).hexdigest()
    if target_size != len(catalog) or target_sha256 != catalog_sha256:
        raise TufOperationReportError("target TUF não corresponde ao catálogo do candidato")
    return dict(report_value)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        result = verify_report(candidate=options.candidate, report=options.report)
    except (OSError, TufOperationReportError) as error:
        print(f"[ERRO] Relatório TUF inválido: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["TufOperationReportError", "main", "verify_report"]
