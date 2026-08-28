#!/usr/bin/env python3
"""Verify an unpublished TUF snapshot+timestamp renewal before deployment."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
for wheel in sorted((PROJECT_ROOT / "maintenance/vendor/wheels").glob("*.whl")):
    sys.path.insert(0, str(wheel))

from tuf.api.metadata import Metadata, Root, Snapshot, Timestamp  # noqa: E402

from maintenance.tools.publish_tuf_metadata import stage_tuf_metadata  # noqa: E402
from x86qw_runtime.trust import TrustError, validate_bootstrap_policy  # noqa: E402


PROJECT = "x86qw"
MAX_FILE_BYTES = 2 * 1024 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SNAPSHOT_FILE = re.compile(r"^metadata/[1-9][0-9]*\.snapshot\.json$")
UTC = timezone.utc


class SnapshotRenewalVerificationError(RuntimeError):
    """The snapshot renewal handoff is unsafe or does not match its report."""


def _regular_file(path: Path, label: str, maximum: int = MAX_FILE_BYTES) -> bytes:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise SnapshotRenewalVerificationError(f"{label} ausente ou inseguro: {path}")
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise SnapshotRenewalVerificationError(f"{label} não pôde ser lido: {path}") from error
    if not payload or len(payload) > maximum:
        raise SnapshotRenewalVerificationError(f"{label} excede o limite: {path}")
    return payload


def _tree_files(root: Path) -> dict[str, Path]:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise SnapshotRenewalVerificationError(f"repositório TUF ausente ou inseguro: {root}")
    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SnapshotRenewalVerificationError(f"repositório TUF contém symlink: {path}")
        if path.is_file():
            if path.stat().st_size > MAX_FILE_BYTES:
                raise SnapshotRenewalVerificationError(f"arquivo TUF excede o limite: {path.name}")
            files[path.relative_to(root).as_posix()] = path
        elif not path.is_dir():
            raise SnapshotRenewalVerificationError(f"repositório TUF contém tipo especial: {path}")
    return files


def _json(path: Path) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise SnapshotRenewalVerificationError(f"relatório contém chave duplicada: {key}")
            value[key] = item
        return value

    try:
        value = json.loads(
            _regular_file(path, "relatório").decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except SnapshotRenewalVerificationError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SnapshotRenewalVerificationError("relatório de renovação não é JSON válido") from error
    if not isinstance(value, dict):
        raise SnapshotRenewalVerificationError("relatório de renovação precisa ser um objeto JSON")
    return value


def _metadata(path: Path, label: str, role_type: type) -> Metadata:
    try:
        metadata = Metadata.from_bytes(_regular_file(path, label))
    except (OSError, ValueError, TypeError) as error:
        raise SnapshotRenewalVerificationError(f"{label} é metadata TUF inválida") from error
    if not isinstance(metadata.signed, role_type):
        raise SnapshotRenewalVerificationError(f"{label} não contém a role esperada")
    return metadata


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _target_identity(repository: Path, catalog: Path) -> dict[str, object]:
    catalog_bytes = _regular_file(catalog, "catálogo")
    target_root = Path(repository) / "targets"
    candidates = [
        path for path in sorted(target_root.rglob("*.catalog.json"))
        if path.is_file() and not path.is_symlink()
    ]
    if len(candidates) != 1:
        raise SnapshotRenewalVerificationError(
            "repositório TUF precisa conter exatamente um target catalog"
        )
    target_bytes = _regular_file(candidates[0], "target catalog")
    if target_bytes != catalog_bytes:
        raise SnapshotRenewalVerificationError("target TUF diverge do catálogo fornecido")
    return {
        "path": candidates[0].relative_to(target_root).as_posix(),
        "size": len(target_bytes),
        "sha256": hashlib.sha256(target_bytes).hexdigest(),
    }


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SnapshotRenewalVerificationError(f"{label} inválido")
    return value


def _key_id(value: object, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise SnapshotRenewalVerificationError(f"{label} inválido")
    return value


def _snapshot_changed_files(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) != 2 or not all(isinstance(item, str) for item in value):
        raise SnapshotRenewalVerificationError("changed_files inválido para renovação de snapshot")
    files = list(value)
    if files != sorted(files):
        raise SnapshotRenewalVerificationError("changed_files precisa estar ordenado")
    if "metadata/timestamp.json" not in files:
        raise SnapshotRenewalVerificationError("renovação de snapshot precisa alterar timestamp.json")
    snapshot_files = [name for name in files if SNAPSHOT_FILE.fullmatch(name)]
    if len(snapshot_files) != 1:
        raise SnapshotRenewalVerificationError("renovação de snapshot precisa adicionar exatamente um snapshot versionado")
    return files


def _validate_report(
    report: Mapping[str, object],
    *,
    source_snapshot: Metadata,
    renewed_snapshot: Metadata,
    source_timestamp: Metadata,
    renewed_timestamp: Metadata,
    source_snapshot_bytes: bytes,
    renewed_snapshot_bytes: bytes,
    source_timestamp_bytes: bytes,
    renewed_timestamp_bytes: bytes,
    root_bytes: bytes,
    target: Mapping[str, object],
    expected_snapshot_key_id: str | None,
    expected_timestamp_key_id: str | None,
    snapshot_keyids: set[str],
    timestamp_keyids: set[str],
    changed_files: list[str],
) -> None:
    expected_fields = {
        "format", "project", "status", "mode", "snapshot_key_id", "timestamp_key_id",
        "key_scope", "source", "renewed", "changed_files", "root_sha256", "target",
        "published", "checked_at", "policy",
    }
    if set(report) != expected_fields:
        raise SnapshotRenewalVerificationError("relatório de renovação possui campos inválidos")
    if (
        report.get("format") != 1
        or report.get("project") != PROJECT
        or report.get("status") != "snapshot-renewed"
        or report.get("mode") != "snapshot-timestamp"
        or report.get("key_scope") != "snapshot-and-timestamp"
        or report.get("published") is not False
        or not isinstance(report.get("checked_at"), str)
        or not report["checked_at"]
    ):
        raise SnapshotRenewalVerificationError(
            "renovação precisa ser um handoff snapshot-timestamp ainda não publicado"
        )
    snapshot_key_id = _key_id(report.get("snapshot_key_id"), "snapshot_key_id")
    timestamp_key_id = _key_id(report.get("timestamp_key_id"), "timestamp_key_id")
    if expected_snapshot_key_id is not None and snapshot_key_id != expected_snapshot_key_id:
        raise SnapshotRenewalVerificationError("key ID snapshot diverge do despacho")
    if expected_timestamp_key_id is not None and timestamp_key_id != expected_timestamp_key_id:
        raise SnapshotRenewalVerificationError("key ID timestamp diverge do despacho")
    if snapshot_key_id not in snapshot_keyids:
        raise SnapshotRenewalVerificationError("key ID declarado não pertence à role snapshot")
    if timestamp_key_id not in timestamp_keyids:
        raise SnapshotRenewalVerificationError("key ID declarado não pertence à role timestamp")
    if snapshot_key_id == timestamp_key_id:
        raise SnapshotRenewalVerificationError("snapshot e timestamp não podem compartilhar key ID")
    try:
        checked_at = datetime.fromisoformat(str(report["checked_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise SnapshotRenewalVerificationError("checked_at do relatório é inválido") from error
    if checked_at.tzinfo is None:
        raise SnapshotRenewalVerificationError("checked_at do relatório precisa conter timezone")

    if _snapshot_changed_files(report.get("changed_files")) != changed_files:
        raise SnapshotRenewalVerificationError("changed_files do relatório diverge do repositório")

    identity_fields = {
        "snapshot_version", "snapshot_expires", "snapshot_sha256",
        "timestamp_version", "timestamp_expires", "timestamp_sha256",
    }
    for item, snapshot, timestamp, snapshot_payload, timestamp_payload, label in (
        (
            _mapping(report.get("source"), "source"),
            source_snapshot, source_timestamp,
            source_snapshot_bytes, source_timestamp_bytes, "source",
        ),
        (
            _mapping(report.get("renewed"), "renewed"),
            renewed_snapshot, renewed_timestamp,
            renewed_snapshot_bytes, renewed_timestamp_bytes, "renewed",
        ),
    ):
        if set(item) != identity_fields:
            raise SnapshotRenewalVerificationError(f"{label} do relatório possui campos inválidos")
        if item.get("snapshot_version") != snapshot.signed.version:
            raise SnapshotRenewalVerificationError(f"versão snapshot diverge no relatório: {label}")
        if item.get("timestamp_version") != timestamp.signed.version:
            raise SnapshotRenewalVerificationError(f"versão timestamp diverge no relatório: {label}")
        if item.get("snapshot_expires") != _iso(snapshot.signed.expires):
            raise SnapshotRenewalVerificationError(f"expiração snapshot diverge no relatório: {label}")
        if item.get("timestamp_expires") != _iso(timestamp.signed.expires):
            raise SnapshotRenewalVerificationError(f"expiração timestamp diverge no relatório: {label}")
        if item.get("snapshot_sha256") != hashlib.sha256(snapshot_payload).hexdigest():
            raise SnapshotRenewalVerificationError(f"digest snapshot diverge no relatório: {label}")
        if item.get("timestamp_sha256") != hashlib.sha256(timestamp_payload).hexdigest():
            raise SnapshotRenewalVerificationError(f"digest timestamp diverge no relatório: {label}")

    if renewed_snapshot.signed.version != source_snapshot.signed.version + 1:
        raise SnapshotRenewalVerificationError("versão snapshot não avançou exatamente uma unidade")
    if renewed_timestamp.signed.version != source_timestamp.signed.version + 1:
        raise SnapshotRenewalVerificationError("versão timestamp não avançou exatamente uma unidade")
    if renewed_snapshot.signed.expires <= source_snapshot.signed.expires:
        raise SnapshotRenewalVerificationError("lease snapshot não foi estendida")
    if renewed_timestamp.signed.expires <= source_timestamp.signed.expires:
        raise SnapshotRenewalVerificationError("lease timestamp não foi estendida")
    if renewed_timestamp.signed.expires >= renewed_snapshot.signed.expires:
        raise SnapshotRenewalVerificationError(
            "timestamp não pode expirar no mesmo instante ou depois do snapshot"
        )
    if renewed_snapshot.signed.meta != source_snapshot.signed.meta:
        raise SnapshotRenewalVerificationError("snapshot renovada alterou a meta de targets")
    if renewed_timestamp.signed.snapshot_meta.version != renewed_snapshot.signed.version:
        raise SnapshotRenewalVerificationError("timestamp renovado não aponta para a nova snapshot")

    policy = _mapping(report.get("policy"), "policy")
    if set(policy) != {"snapshot_lease_days", "timestamp_lease_days", "role_expiry_days"}:
        raise SnapshotRenewalVerificationError("policy do relatório possui campos inválidos")
    for field in ("snapshot_lease_days", "timestamp_lease_days"):
        days = policy.get(field)
        if type(days) is not int or not 1 <= days <= 3650:
            raise SnapshotRenewalVerificationError(f"{field} inválido")
    if not isinstance(policy.get("role_expiry_days"), Mapping):
        raise SnapshotRenewalVerificationError("role_expiry_days inválido")

    if report.get("root_sha256") != hashlib.sha256(root_bytes).hexdigest():
        raise SnapshotRenewalVerificationError("digest da root diverge no relatório")
    if report.get("target") != dict(target):
        raise SnapshotRenewalVerificationError("target diverge no relatório")
    if snapshot_key_id not in renewed_snapshot.signatures:
        raise SnapshotRenewalVerificationError("assinatura snapshot não corresponde ao key ID declarado")
    if timestamp_key_id not in renewed_timestamp.signatures:
        raise SnapshotRenewalVerificationError("assinatura timestamp não corresponde ao key ID declarado")


def verify_snapshot_renewal(
    *,
    source_repository: Path,
    renewed_repository: Path,
    report: Path,
    root: Path,
    catalog: Path,
    expected_snapshot_key_id: str | None = None,
    expected_timestamp_key_id: str | None = None,
) -> dict[str, object]:
    """Authenticate a renewal and prove that only snapshot+timestamp changed."""

    source_repository = Path(source_repository)
    renewed_repository = Path(renewed_repository)
    if source_repository.resolve() == renewed_repository.resolve():
        raise SnapshotRenewalVerificationError("fonte e renovação precisam ser diretórios distintos")
    source_files = _tree_files(source_repository)
    renewed_files = _tree_files(renewed_repository)
    added = sorted(set(renewed_files) - set(source_files))
    removed = sorted(set(source_files) - set(renewed_files))
    if removed:
        raise SnapshotRenewalVerificationError(f"renovação removeu arquivos TUF: {removed}")
    changed_existing = [
        relative for relative in sorted(source_files)
        if source_files[relative].read_bytes() != renewed_files[relative].read_bytes()
    ]
    if changed_existing != ["metadata/timestamp.json"] or len(added) != 1 or SNAPSHOT_FILE.fullmatch(added[0]) is None:
        raise SnapshotRenewalVerificationError(
            f"renovação não é snapshot-timestamp: added={added} changed={changed_existing}"
        )
    changed_files = sorted([*added, *changed_existing])

    root_bytes = _regular_file(Path(root), "root TUF", 512 * 1024)
    try:
        validate_bootstrap_policy(root_bytes)
        root_metadata = Metadata.from_bytes(root_bytes)
    except (OSError, TrustError, ValueError, TypeError) as error:
        raise SnapshotRenewalVerificationError("root TUF inválida") from error
    if not isinstance(root_metadata.signed, Root):
        raise SnapshotRenewalVerificationError("root TUF não contém uma role root")
    snapshot_role = root_metadata.signed.roles.get("snapshot")
    timestamp_role = root_metadata.signed.roles.get("timestamp")
    if snapshot_role is None or timestamp_role is None:
        raise SnapshotRenewalVerificationError("root TUF não contém snapshot e timestamp")

    source_timestamp = _metadata(
        source_repository / "metadata/timestamp.json", "timestamp fonte", Timestamp,
    )
    renewed_timestamp = _metadata(
        renewed_repository / "metadata/timestamp.json", "timestamp renovado", Timestamp,
    )
    source_snapshot_relative = f"metadata/{source_timestamp.signed.snapshot_meta.version}.snapshot.json"
    renewed_snapshot_relative = added[0]
    if source_snapshot_relative not in source_files:
        raise SnapshotRenewalVerificationError("snapshot fonte versionada ausente")
    if source_files[source_snapshot_relative].read_bytes() != renewed_files[source_snapshot_relative].read_bytes():
        raise SnapshotRenewalVerificationError("snapshot fonte versionada foi alterada")
    source_snapshot = _metadata(
        source_repository / source_snapshot_relative, "snapshot fonte", Snapshot,
    )
    renewed_snapshot = _metadata(
        renewed_repository / renewed_snapshot_relative, "snapshot renovada", Snapshot,
    )
    if f"metadata/{renewed_snapshot.signed.version}.snapshot.json" != renewed_snapshot_relative:
        raise SnapshotRenewalVerificationError("nome do snapshot renovado diverge da versão assinada")

    target = _target_identity(renewed_repository, Path(catalog))
    source_target = _target_identity(source_repository, Path(catalog))
    if source_target != target:
        raise SnapshotRenewalVerificationError("renovação alterou o target catalog")
    report_value = _json(Path(report))
    _validate_report(
        report_value,
        source_snapshot=source_snapshot,
        renewed_snapshot=renewed_snapshot,
        source_timestamp=source_timestamp,
        renewed_timestamp=renewed_timestamp,
        source_snapshot_bytes=_regular_file(source_repository / source_snapshot_relative, "snapshot fonte"),
        renewed_snapshot_bytes=_regular_file(renewed_repository / renewed_snapshot_relative, "snapshot renovada"),
        source_timestamp_bytes=_regular_file(source_repository / "metadata/timestamp.json", "timestamp fonte"),
        renewed_timestamp_bytes=_regular_file(renewed_repository / "metadata/timestamp.json", "timestamp renovado"),
        root_bytes=root_bytes,
        target=target,
        expected_snapshot_key_id=expected_snapshot_key_id,
        expected_timestamp_key_id=expected_timestamp_key_id,
        snapshot_keyids=set(snapshot_role.keyids),
        timestamp_keyids=set(timestamp_role.keyids),
        changed_files=changed_files,
    )

    try:
        with tempfile.TemporaryDirectory(prefix="x86qw-snapshot-verify-") as temporary:
            stage_tuf_metadata(
                metadata_dir=renewed_repository,
                catalog=Path(catalog),
                root=Path(root),
                stage_dir=Path(temporary) / "verified",
            )
    except (OSError, TrustError, ValueError, TypeError) as error:
        raise SnapshotRenewalVerificationError(
            f"metadata snapshot renovada não autentica o catálogo: {error}"
        ) from error
    return {
        "format": 1,
        "project": PROJECT,
        "status": "verified-snapshot-renewal",
        "changed_files": changed_files,
        "published": False,
        "snapshot_key_id": report_value["snapshot_key_id"],
        "timestamp_key_id": report_value["timestamp_key_id"],
        "target": dict(target),
    }


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repository", type=Path, required=True)
    parser.add_argument("--renewed-repository", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--expected-snapshot-key-id")
    parser.add_argument("--expected-timestamp-key-id")
    options = parser.parse_args(arguments)
    try:
        result = verify_snapshot_renewal(
            source_repository=options.source_repository,
            renewed_repository=options.renewed_repository,
            report=options.report,
            root=options.root,
            catalog=options.catalog,
            expected_snapshot_key_id=options.expected_snapshot_key_id,
            expected_timestamp_key_id=options.expected_timestamp_key_id,
        )
    except (OSError, SnapshotRenewalVerificationError, ValueError) as error:
        print(f"[ERRO] Verificação de renovação TUF snapshot falhou: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
