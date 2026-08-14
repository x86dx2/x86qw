#!/usr/bin/env python3
"""Verify an unpublished TUF timestamp-only renewal before deployment."""

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

from tuf.api.metadata import Metadata, Root, Timestamp  # noqa: E402

from maintenance.tools.publish_tuf_metadata import stage_tuf_metadata  # noqa: E402
from x86qw_runtime.trust import TrustError, validate_bootstrap_policy  # noqa: E402


PROJECT = "x86qw"
MAX_FILE_BYTES = 2 * 1024 * 1024
HEX64 = re.compile(r"^[0-9a-f]{64}$")
UTC = timezone.utc


class TimestampRenewalVerificationError(RuntimeError):
    """The renewal handoff is unsafe or does not match its report."""


def _regular_file(path: Path, label: str, maximum: int = MAX_FILE_BYTES) -> bytes:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise TimestampRenewalVerificationError(f"{label} ausente ou inseguro: {path}")
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise TimestampRenewalVerificationError(f"{label} não pôde ser lido: {path}") from error
    if not payload or len(payload) > maximum:
        raise TimestampRenewalVerificationError(f"{label} excede o limite: {path}")
    return payload


def _tree_files(root: Path) -> dict[str, Path]:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise TimestampRenewalVerificationError(f"repositório TUF ausente ou inseguro: {root}")
    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise TimestampRenewalVerificationError(f"repositório TUF contém symlink: {path}")
        if path.is_file():
            if path.stat().st_size > MAX_FILE_BYTES:
                raise TimestampRenewalVerificationError(f"arquivo TUF excede o limite: {path.name}")
            files[path.relative_to(root).as_posix()] = path
        elif not path.is_dir():
            raise TimestampRenewalVerificationError(f"repositório TUF contém tipo especial: {path}")
    return files


def _json(path: Path) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise TimestampRenewalVerificationError(f"relatório contém chave duplicada: {key}")
            value[key] = item
        return value

    try:
        value = json.loads(
            _regular_file(path, "relatório").decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except TimestampRenewalVerificationError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise TimestampRenewalVerificationError("relatório de renovação não é JSON válido") from error
    if not isinstance(value, dict):
        raise TimestampRenewalVerificationError("relatório de renovação precisa ser um objeto JSON")
    return value


def _metadata_timestamp(path: Path, label: str) -> Metadata:
    try:
        metadata = Metadata.from_bytes(_regular_file(path, label))
    except (OSError, ValueError, TypeError) as error:
        raise TimestampRenewalVerificationError(f"{label} é metadata TUF inválida") from error
    if not isinstance(metadata.signed, Timestamp):
        raise TimestampRenewalVerificationError(f"{label} não contém a role timestamp")
    return metadata


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _identity(payload: bytes) -> dict[str, object]:
    return {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _target_identity(repository: Path, catalog: Path) -> dict[str, object]:
    catalog_bytes = _regular_file(catalog, "catálogo")
    target_root = Path(repository) / "targets"
    candidates = [
        path for path in sorted(target_root.rglob("*.catalog.json"))
        if path.is_file() and not path.is_symlink()
    ]
    if len(candidates) != 1:
        raise TimestampRenewalVerificationError(
            "repositório TUF precisa conter exatamente um target catalog"
        )
    target_bytes = _regular_file(candidates[0], "target catalog")
    if target_bytes != catalog_bytes:
        raise TimestampRenewalVerificationError("target TUF diverge do catálogo fornecido")
    return {
        "path": candidates[0].relative_to(target_root).as_posix(),
        "size": len(target_bytes),
        "sha256": hashlib.sha256(target_bytes).hexdigest(),
    }


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TimestampRenewalVerificationError(f"{label} inválido")
    return value


def _validate_report(
    report: Mapping[str, object],
    *,
    source: Metadata,
    renewed: Metadata,
    source_timestamp: bytes,
    renewed_timestamp: bytes,
    root_bytes: bytes,
    target: Mapping[str, object],
    expected_key_id: str | None,
    timestamp_keyids: set[str],
) -> None:
    expected_fields = {
        "format", "project", "status", "mode", "key_id", "key_scope",
        "source", "renewed", "changed_files", "root_sha256", "target",
        "published", "checked_at",
    }
    if set(report) != expected_fields:
        raise TimestampRenewalVerificationError("relatório de renovação possui campos inválidos")
    if (
        report.get("format") != 1
        or report.get("project") != PROJECT
        or report.get("status") != "timestamp-renewed"
        or report.get("mode") != "timestamp-only"
        or report.get("key_scope") != "timestamp-only"
        or report.get("published") is not False
        or not isinstance(report.get("checked_at"), str)
        or not report["checked_at"]
    ):
        raise TimestampRenewalVerificationError(
            "renovação precisa ser um handoff timestamp-only ainda não publicado"
        )
    key_id = report.get("key_id")
    if not isinstance(key_id, str) or HEX64.fullmatch(key_id) is None:
        raise TimestampRenewalVerificationError("key ID timestamp inválido")
    if expected_key_id is not None and key_id != expected_key_id:
        raise TimestampRenewalVerificationError("key ID timestamp diverge do despacho")
    if key_id not in timestamp_keyids:
        raise TimestampRenewalVerificationError("key ID declarado não pertence à role timestamp")
    try:
        checked_at = datetime.fromisoformat(str(report["checked_at"]).replace("Z", "+00:00"))
    except ValueError as error:
        raise TimestampRenewalVerificationError("checked_at do relatório é inválido") from error
    if checked_at.tzinfo is None:
        raise TimestampRenewalVerificationError("checked_at do relatório precisa conter timezone")

    changed = report.get("changed_files")
    if changed != ["metadata/timestamp.json"]:
        raise TimestampRenewalVerificationError(
            "renovação não é timestamp-only: changed_files inválido"
        )
    source_report = _mapping(report.get("source"), "source")
    renewed_report = _mapping(report.get("renewed"), "renewed")
    for item, metadata, payload, label in (
        (source_report, source, source_timestamp, "source"),
        (renewed_report, renewed, renewed_timestamp, "renewed"),
    ):
        if set(item) != {"timestamp_version", "expires", "sha256"}:
            raise TimestampRenewalVerificationError(f"{label} do relatório possui campos inválidos")
        if item.get("timestamp_version") != metadata.signed.version:
            raise TimestampRenewalVerificationError(f"versão timestamp diverge no relatório: {label}")
        if item.get("expires") != _iso(metadata.signed.expires):
            raise TimestampRenewalVerificationError(f"expiração timestamp diverge no relatório: {label}")
        if item.get("sha256") != hashlib.sha256(payload).hexdigest():
            raise TimestampRenewalVerificationError(f"digest timestamp diverge no relatório: {label}")
    if renewed.signed.version != source.signed.version + 1:
        raise TimestampRenewalVerificationError("versão timestamp não avançou exatamente uma unidade")
    if renewed.signed.expires <= source.signed.expires:
        raise TimestampRenewalVerificationError("lease timestamp não foi estendida")

    if report.get("root_sha256") != hashlib.sha256(root_bytes).hexdigest():
        raise TimestampRenewalVerificationError("digest da root diverge no relatório")
    if report.get("target") != dict(target):
        raise TimestampRenewalVerificationError("target diverge no relatório")
    if key_id not in renewed.signatures:
        raise TimestampRenewalVerificationError("assinatura timestamp não corresponde ao key ID declarado")


def verify_timestamp_renewal(
    *,
    source_repository: Path,
    renewed_repository: Path,
    report: Path,
    root: Path,
    catalog: Path,
    expected_key_id: str | None = None,
) -> dict[str, object]:
    """Authenticate a renewal and prove that only timestamp metadata changed."""

    source_repository = Path(source_repository)
    renewed_repository = Path(renewed_repository)
    if source_repository.resolve() == renewed_repository.resolve():
        raise TimestampRenewalVerificationError("fonte e renovação precisam ser diretórios distintos")
    source_files = _tree_files(source_repository)
    renewed_files = _tree_files(renewed_repository)
    if set(source_files) != set(renewed_files):
        raise TimestampRenewalVerificationError("renovação alterou o conjunto de arquivos TUF")
    changed = [
        relative for relative in sorted(source_files)
        if source_files[relative].read_bytes() != renewed_files[relative].read_bytes()
    ]
    if changed != ["metadata/timestamp.json"]:
        raise TimestampRenewalVerificationError(
            f"renovação não é timestamp-only: arquivos alterados {changed}"
        )

    root_bytes = _regular_file(Path(root), "root TUF", 512 * 1024)
    try:
        validate_bootstrap_policy(root_bytes)
        root_metadata = Metadata.from_bytes(root_bytes)
    except (OSError, TrustError, ValueError, TypeError) as error:
        raise TimestampRenewalVerificationError("root TUF inválida") from error
    if not isinstance(root_metadata.signed, Root):
        raise TimestampRenewalVerificationError("root TUF não contém uma role root")
    timestamp_role = root_metadata.signed.roles.get("timestamp")
    if timestamp_role is None:
        raise TimestampRenewalVerificationError("root TUF não contém a role timestamp")
    source_timestamp_path = source_repository / "metadata/timestamp.json"
    renewed_timestamp_path = renewed_repository / "metadata/timestamp.json"
    source_timestamp = _regular_file(source_timestamp_path, "timestamp fonte")
    renewed_timestamp = _regular_file(renewed_timestamp_path, "timestamp renovado")
    source_metadata = _metadata_timestamp(source_timestamp_path, "timestamp fonte")
    renewed_metadata = _metadata_timestamp(renewed_timestamp_path, "timestamp renovado")
    target = _target_identity(renewed_repository, Path(catalog))
    source_target = _target_identity(source_repository, Path(catalog))
    if source_target != target:
        raise TimestampRenewalVerificationError("renovação alterou o target catalog")
    report_value = _json(Path(report))
    _validate_report(
        report_value,
        source=source_metadata,
        renewed=renewed_metadata,
        source_timestamp=source_timestamp,
        renewed_timestamp=renewed_timestamp,
        root_bytes=root_bytes,
        target=target,
        expected_key_id=expected_key_id,
        timestamp_keyids=set(timestamp_role.keyids),
    )

    # This is the cryptographic gate: it verifies root, timestamp, snapshot,
    # targets and the catalog target using the same production verifier used
    # by the publication path.
    try:
        with tempfile.TemporaryDirectory(prefix="x86qw-timestamp-verify-") as temporary:
            stage_tuf_metadata(
                metadata_dir=renewed_repository,
                catalog=Path(catalog),
                root=Path(root),
                stage_dir=Path(temporary) / "verified",
            )
    except (OSError, TrustError, ValueError, TypeError) as error:
        raise TimestampRenewalVerificationError(
            f"metadata timestamp renovada não autentica o catálogo: {error}"
        ) from error
    return {
        "format": 1,
        "project": PROJECT,
        "status": "verified-timestamp-renewal",
        "changed_files": changed,
        "published": False,
        "key_id": report_value["key_id"],
        "target": dict(target),
    }


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-repository", type=Path, required=True)
    parser.add_argument("--renewed-repository", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--expected-key-id")
    options = parser.parse_args(arguments)
    try:
        result = verify_timestamp_renewal(
            source_repository=options.source_repository,
            renewed_repository=options.renewed_repository,
            report=options.report,
            root=options.root,
            catalog=options.catalog,
            expected_key_id=options.expected_key_id,
        )
    except (OSError, TimestampRenewalVerificationError, ValueError) as error:
        print(f"[ERRO] Verificação de renovação TUF falhou: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
