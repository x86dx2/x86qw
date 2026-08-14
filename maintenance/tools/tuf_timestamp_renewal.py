#!/usr/bin/env python3
"""Renew only TUF ``timestamp`` metadata without publishing it.

This operation is deliberately narrower than the full metadata generator:
the supplied private key must belong to the timestamp role, the root/targets
keys are never loaded, and the resulting repository may differ from the
source only at ``metadata/timestamp.json``.  The output is an un-published
handoff for a separately protected publication ceremony.
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
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
for wheel in sorted((PROJECT_ROOT / "maintenance/vendor/wheels").glob("*.whl")):
    sys.path.insert(0, str(wheel))

from tuf.api.metadata import Metadata, Root, Timestamp  # noqa: E402

from maintenance.tools.generate_trust_metadata import (  # noqa: E402
    Ed25519FileSigner,
)
from maintenance.tools.publish_tuf_metadata import (  # noqa: E402
    stage_tuf_metadata,
)
from x86qw_runtime.trust import TrustError, validate_bootstrap_policy  # noqa: E402


MAX_FILE_BYTES = 2 * 1024 * 1024
UTC = timezone.utc


class TimestampRenewalError(RuntimeError):
    """The timestamp-only renewal contract could not be proven."""


def _regular_file(path: Path, label: str, maximum: int = MAX_FILE_BYTES) -> bytes:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise TimestampRenewalError(f"{label} ausente ou inseguro: {path}")
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise TimestampRenewalError(f"{label} não pôde ser lido: {path}") from error
    if not payload or len(payload) > maximum:
        raise TimestampRenewalError(f"{label} excede o limite: {path}")
    return payload


def _tree_files(root: Path) -> dict[str, Path]:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise TimestampRenewalError(f"repositório TUF ausente ou inseguro: {root}")
    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise TimestampRenewalError(f"repositório TUF contém symlink: {path}")
        if path.is_file():
            if path.stat().st_size > MAX_FILE_BYTES:
                raise TimestampRenewalError(f"arquivo TUF excede o limite: {path}")
            files[path.relative_to(root).as_posix()] = path
        elif not path.is_dir():
            raise TimestampRenewalError(f"repositório TUF contém tipo especial: {path}")
    return files


def _read_root(path: Path) -> tuple[bytes, Metadata]:
    payload = _regular_file(path, "root TUF", 512 * 1024)
    try:
        validate_bootstrap_policy(payload)
        metadata = Metadata.from_bytes(payload)
    except (OSError, TrustError, ValueError) as error:
        raise TimestampRenewalError(f"root TUF inválida: {error}") from error
    if not isinstance(metadata.signed, Root):
        raise TimestampRenewalError("root TUF não contém uma role root")
    return payload, metadata


def _target_identity(repository: Path, catalog: Path) -> dict[str, object]:
    catalog_bytes = _regular_file(catalog, "catálogo")
    target_root = Path(repository) / "targets"
    candidates = [
        path for path in sorted(target_root.rglob("*.catalog.json"))
        if path.is_file() and not path.is_symlink()
    ]
    if len(candidates) != 1:
        raise TimestampRenewalError(
            "repositório TUF precisa conter exatamente um target catalog"
        )
    target_bytes = _regular_file(candidates[0], "target catalog")
    if target_bytes != catalog_bytes:
        raise TimestampRenewalError("target TUF corrente diverge do catálogo fornecido")
    return {
        "path": candidates[0].relative_to(target_root).as_posix(),
        "size": len(target_bytes),
        "sha256": hashlib.sha256(target_bytes).hexdigest(),
    }


def _timestamp_path(repository: Path) -> Path:
    path = Path(repository) / "metadata" / "timestamp.json"
    _regular_file(path, "timestamp TUF")
    return path


def _load_timestamp_signer(
    *, key_path: Path, key_id: str, root: Root,
) -> Ed25519FileSigner:
    role = root.roles.get("timestamp")
    if role is None or key_id not in role.keyids:
        raise TimestampRenewalError(
            "a chave fornecida não pertence à role timestamp; root/targets não são aceitas"
        )
    key = root.keys.get(key_id)
    if key is None:
        raise TimestampRenewalError("key id timestamp não existe na root incorporada")
    key_path = Path(key_path)
    _regular_file(key_path, "chave privada timestamp", 16 * 1024)
    if os.name != "nt" and key_path.stat().st_mode & 0o077:
        raise TimestampRenewalError("chave privada timestamp precisa ser 0600")
    try:
        return Ed25519FileSigner.from_priv_key_uri(
            f"file2:{key_path.resolve(strict=True)}",
            key,
        )
    except (OSError, ValueError) as error:
        raise TimestampRenewalError(
            f"chave privada timestamp não corresponde ao key id: {error}"
        ) from error


def _copy_tree(source: Path, destination: Path) -> None:
    files = _tree_files(source)
    destination.mkdir(mode=0o700)
    for relative, path in files.items():
        target = destination / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copyfile(path, target, follow_symlinks=False)
        os.chmod(target, 0o644)


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        if path.exists() and not path.is_symlink():
            path.unlink()
        raise


def _inside(path: Path, parent: Path) -> bool:
    try:
        Path(path).resolve(strict=False).relative_to(Path(parent).resolve(strict=False))
    except (FileNotFoundError, ValueError):
        return False
    return True


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def renew_timestamp(
    *,
    repository: Path,
    root: Path,
    catalog: Path,
    timestamp_key: Path,
    key_id: str,
    output: Path,
    report: Path,
    lease_hours: int,
) -> dict[str, object]:
    """Create an authenticated repository differing only in timestamp.json."""

    if type(lease_hours) is not int or not 1 <= lease_hours <= 8760:
        raise TimestampRenewalError("lease_hours deve estar entre 1 e 8760")
    repository = Path(repository)
    output = Path(output)
    report = Path(report)
    if _inside(output, repository):
        raise TimestampRenewalError("destino de saída não pode ficar dentro do repositório-fonte")
    if _inside(report, repository):
        raise TimestampRenewalError("relatório não pode ficar dentro do repositório-fonte")
    if _inside(report, output):
        raise TimestampRenewalError("relatório não pode ficar dentro do destino TUF")
    if output.exists() or output.is_symlink():
        raise TimestampRenewalError(f"destino TUF já existe: {output}")
    if report.exists() or report.is_symlink():
        raise TimestampRenewalError(f"relatório já existe: {report}")
    source_files = _tree_files(repository)
    root_bytes, root_metadata = _read_root(root)
    _target_identity(repository, catalog)
    signer = _load_timestamp_signer(
        key_path=timestamp_key,
        key_id=key_id,
        root=root_metadata.signed,
    )

    timestamp_path = _timestamp_path(repository)
    try:
        current = Metadata.from_bytes(timestamp_path.read_bytes())
    except (OSError, ValueError) as error:
        raise TimestampRenewalError(f"timestamp TUF inválido: {error}") from error
    if not isinstance(current.signed, Timestamp):
        raise TimestampRenewalError("timestamp.json não contém a role timestamp")

    now = datetime.now(UTC)
    expires = now + timedelta(hours=lease_hours)
    if expires <= current.signed.expires:
        raise TimestampRenewalError("a renovação encurtaria a lease corrente")
    renewed = Metadata(
        Timestamp(version=current.signed.version + 1, expires=expires),
    )
    renewed.signed.snapshot_meta = current.signed.snapshot_meta
    renewed.sign(signer)
    renewed_bytes = renewed.to_bytes()

    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staged = Path(temporary) / "repository"
        _copy_tree(repository, staged)
        staged_timestamp = staged / "metadata/timestamp.json"
        staged_timestamp.unlink()
        _write_new(staged_timestamp, renewed_bytes)
        staged_files = _tree_files(staged)
        changed = [
            relative for relative in sorted(source_files)
            if source_files[relative].read_bytes() != staged_files[relative].read_bytes()
        ]
        if changed != ["metadata/timestamp.json"]:
            raise TimestampRenewalError(
                f"renovação alterou arquivos além de timestamp.json: {changed}"
            )
        stage_tuf_metadata(
            metadata_dir=staged,
            catalog=Path(catalog),
            root=Path(root),
            stage_dir=Path(temporary) / "verified",
        )
        os.replace(staged, output)

    target = _target_identity(output, catalog)
    report_value: dict[str, Any] = {
        "format": 1,
        "project": "x86qw",
        "status": "timestamp-renewed",
        "mode": "timestamp-only",
        "key_id": key_id,
        "key_scope": "timestamp-only",
        "source": {
            "timestamp_version": current.signed.version,
            "expires": _iso(current.signed.expires),
            "sha256": hashlib.sha256(timestamp_path.read_bytes()).hexdigest(),
        },
        "renewed": {
            "timestamp_version": renewed.signed.version,
            "expires": _iso(renewed.signed.expires),
            "sha256": hashlib.sha256(renewed_bytes).hexdigest(),
        },
        "changed_files": ["metadata/timestamp.json"],
        "root_sha256": hashlib.sha256(root_bytes).hexdigest(),
        "target": target,
        "published": False,
        "checked_at": _iso(now),
    }
    _write_new(
        report,
        (json.dumps(report_value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
    )
    return report_value


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--timestamp-key", type=Path, required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--lease-hours", type=int, required=True)
    options = parser.parse_args(arguments)
    try:
        result = renew_timestamp(
            repository=options.repository,
            root=options.root,
            catalog=options.catalog,
            timestamp_key=options.timestamp_key,
            key_id=options.key_id,
            output=options.output,
            report=options.report,
            lease_hours=options.lease_hours,
        )
    except (OSError, TimestampRenewalError, ValueError) as error:
        print(f"[ERRO] Renovação TUF timestamp falhou: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
