#!/usr/bin/env python3
"""Run an offline TUF renewal, expiry, and recovery drill.

The drill requires the operator's private signing directory explicitly.  It
never writes to the supplied repository, never publishes metadata, and refuses
to replace an existing report.  The generated repository is temporary and is
authenticated against the same incorporated root before the report is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


ROLES = ("timestamp", "snapshot", "targets")
UTC = timezone.utc
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_OPERATOR_TEXT = 128


class TufDrillError(RuntimeError):
    """The renewal/recovery drill could not prove its bounded contract."""


def operation_context(
    *,
    operator: str,
    custody_host: str,
    sla_hours: int,
) -> dict[str, object]:
    """Validate the non-secret operational accountability attached to a drill."""

    for value, label in ((operator, "operator"), (custody_host, "custody_host")):
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or len(value) > MAX_OPERATOR_TEXT
            or any(ord(char) < 0x20 for char in value)
        ):
            raise TufDrillError(f"{label} inválido")
    if type(sla_hours) is not int or not 1 <= sla_hours <= 8760:
        raise TufDrillError("sla_hours deve estar entre 1 e 8760")
    return {
        "operator": operator,
        "custody_host": custody_host,
        "timestamp_sla_hours": sla_hours,
        "key_scope": "root-and-targets-offline",
    }


def _regular_file(path: Path, label: str, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise TufDrillError(f"{label} ausente ou inseguro: {path}")
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise TufDrillError(f"{label} não pôde ser lido: {path}") from error
    if not payload or len(payload) > maximum:
        raise TufDrillError(f"{label} excede o limite: {path}")
    return payload


def _json(path: Path, label: str) -> tuple[bytes, dict[str, Any]]:
    payload = _regular_file(path, label, MAX_METADATA_BYTES)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TufDrillError(f"{label} não é JSON UTF-8 válido") from error
    if not isinstance(value, dict):
        raise TufDrillError(f"{label} precisa ser um objeto JSON")
    signed = value.get("signed")
    if not isinstance(signed, dict):
        raise TufDrillError(f"{label} não contém signed")
    return payload, value


def _role_path(repository: Path, role: str) -> Path:
    metadata = Path(repository) / "metadata"
    if role == "timestamp":
        candidates = [metadata / "timestamp.json"]
    else:
        candidates = sorted(
            path for path in metadata.glob(f"*.{role}.json")
            if path.is_file() and not path.is_symlink()
        )
    if len(candidates) != 1:
        raise TufDrillError(
            f"metadata TUF {role} precisa conter exatamente um arquivo: {repository}"
        )
    return candidates[0]


def _expires(document: Mapping[str, Any], role: str) -> tuple[int, datetime]:
    signed = document.get("signed")
    if not isinstance(signed, dict):
        raise TufDrillError(f"metadata TUF {role} sem signed")
    version = signed.get("version")
    expires = signed.get("expires")
    if type(version) is not int or version < 1 or not isinstance(expires, str):
        raise TufDrillError(f"metadata TUF {role} possui versão/expiração inválida")
    try:
        when = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    except ValueError as error:
        raise TufDrillError(f"metadata TUF {role} possui expiração inválida") from error
    if when.tzinfo is None:
        raise TufDrillError(f"metadata TUF {role} precisa de expiração timezone-aware")
    return version, when.astimezone(UTC)


def lease_status(
    repository: Path,
    *,
    warning_hours: int = 6,
    now: datetime | None = None,
) -> dict[str, object]:
    if type(warning_hours) is not int or not 1 <= warning_hours <= 8760:
        raise TufDrillError("warning_hours deve estar entre 1 e 8760")
    observed = (now or datetime.now(UTC)).astimezone(UTC)
    versions: dict[str, int] = {}
    expires: dict[str, str] = {}
    for role in ROLES:
        _, document = _json(_role_path(Path(repository), role), f"metadata TUF {role}")
        version, expiry = _expires(document, role)
        versions[role] = version
        expires[role] = expiry.isoformat().replace("+00:00", "Z")
        if expiry <= observed + timedelta(hours=warning_hours):
            raise TufDrillError(
                f"lease TUF {role} expira dentro da janela de {warning_hours} horas"
            )
    return {
        "status": "healthy",
        "versions": versions,
        "expires": expires,
        "warning_hours": warning_hours,
    }


def target_identity(repository: Path) -> dict[str, object]:
    targets = Path(repository) / "targets"
    candidates = sorted(
        path for path in targets.rglob("*.catalog.json")
        if path.is_file() and not path.is_symlink()
    )
    if len(candidates) != 1:
        raise TufDrillError("repositório TUF precisa conter exatamente um target catalog")
    payload = _regular_file(candidates[0], "target catalog", MAX_CATALOG_BYTES)
    return {
        "path": candidates[0].relative_to(targets).as_posix(),
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def target_unchanged(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    return left.get("size") == right.get("size") and left.get("sha256") == right.get("sha256")


def _root_identity(repository: Path) -> bytes:
    metadata = Path(repository) / "metadata"
    candidates = sorted(
        path for path in metadata.glob("*.root.json")
        if path.is_file() and not path.is_symlink()
    )
    if len(candidates) != 1:
        raise TufDrillError("repositório TUF precisa conter exatamente uma root versionada")
    return _regular_file(candidates[0], "root TUF", 512 * 1024)


def _rewrite_expired_timestamp(source: Path, destination: Path, *, now: datetime) -> None:
    shutil.copytree(source, destination)
    timestamp = destination / "metadata" / "timestamp.json"
    payload, document = _json(timestamp, "timestamp TUF de teste")
    del payload
    signed = document["signed"]
    assert isinstance(signed, dict)
    signed["expires"] = (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    timestamp.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")


def run_drill(
    *,
    key_dir: Path,
    root: Path,
    catalog: Path,
    repository: Path,
    output: Path,
    operator: str,
    custody_host: str,
    sla_hours: int,
    warning_hours: int = 6,
) -> dict[str, object]:
    """Renew metadata in isolation and record the recovery proof."""

    key_dir = Path(key_dir)
    root = Path(root)
    catalog = Path(catalog)
    repository = Path(repository)
    output = Path(output)
    operation = operation_context(
        operator=operator,
        custody_host=custody_host,
        sla_hours=sla_hours,
    )
    if output.exists() or output.is_symlink():
        raise TufDrillError(f"relatório do drill já existe: {output}")
    root_bytes = _regular_file(root, "root TUF incorporada", 512 * 1024)
    catalog_bytes = _regular_file(catalog, "catálogo", MAX_CATALOG_BYTES)
    current_target = target_identity(repository)
    if current_target["size"] != len(catalog_bytes) or current_target["sha256"] != hashlib.sha256(catalog_bytes).hexdigest():
        raise TufDrillError("target corrente não corresponde ao catálogo fornecido")
    current_lease = lease_status(repository, warning_hours=warning_hours)
    current_root = _root_identity(repository)
    current_version = max(current_lease["versions"].values())
    now = datetime.now(UTC)

    # Imports stay behind the explicit operator boundary: normal repository
    # inspection and report validation do not load signing code or private keys.
    from maintenance.tools.generate_trust_metadata import generate_repository
    from maintenance.tools.publish_tuf_metadata import stage_tuf_metadata

    with tempfile.TemporaryDirectory(prefix="x86qw-tuf-drill-") as temporary:
        workspace = Path(temporary)
        renewed = workspace / "renewed"
        generate_repository(
            key_dir,
            root,
            catalog,
            renewed,
            version=current_version + 1,
        )
        renewed_target = target_identity(renewed)
        renewed_lease = lease_status(renewed, warning_hours=warning_hours, now=now)
        if not target_unchanged(current_target, renewed_target):
            raise TufDrillError("renovação TUF alterou o target catalog")
        if _root_identity(renewed) != current_root or _root_identity(renewed) != root_bytes:
            raise TufDrillError("renovação TUF alterou a root incorporada")
        if any(renewed_lease["versions"][role] <= current_lease["versions"][role] for role in ROLES):
            raise TufDrillError("renovação TUF não avançou todas as versões de role")
        stage_tuf_metadata(
            metadata_dir=repository,
            catalog=catalog,
            root=root,
            stage_dir=workspace / "current-verified",
        )
        stage_tuf_metadata(
            metadata_dir=renewed,
            catalog=catalog,
            root=root,
            stage_dir=workspace / "renewed-verified",
        )
        expired = workspace / "expired"
        _rewrite_expired_timestamp(renewed, expired, now=now)
        expiry_failure_detected = False
        try:
            lease_status(expired, warning_hours=warning_hours, now=now)
        except TufDrillError:
            expiry_failure_detected = True
        if not expiry_failure_detected:
            raise TufDrillError("drill não detectou timestamp expirado")
        recovery = lease_status(renewed, warning_hours=warning_hours, now=now)

    report = {
        "format": 1,
        "project": "x86qw",
        "status": "drill-passed",
        "mode": "offline-renewal-expiry-recovery",
        "operation": operation,
        "current_metadata_version": current_version,
        "renewed_metadata_version": current_version + 1,
        "target": renewed_target,
        "target_unchanged": target_unchanged(current_target, renewed_target),
        "root_unchanged": True,
        "expiry_failure_detected": True,
        "recovery_verified": recovery["status"] == "healthy",
        "published": False,
        "checked_at": now.isoformat().replace("+00:00", "Z"),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    return report


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-dir", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--custody-host", required=True)
    parser.add_argument("--sla-hours", type=int, required=True)
    parser.add_argument("--warning-hours", type=int, default=6)
    options = parser.parse_args(arguments)
    try:
        result = run_drill(
            key_dir=options.key_dir,
            root=options.root,
            catalog=options.catalog,
            repository=options.repository,
            output=options.output,
            operator=options.operator,
            custody_host=options.custody_host,
            sla_hours=options.sla_hours,
            warning_hours=options.warning_hours,
        )
    except (OSError, TufDrillError, ValueError) as error:
        print(f"[ERRO] Drill TUF falhou: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
