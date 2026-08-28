#!/usr/bin/env python3
"""Renew TUF snapshot and timestamp without changing targets.

The supplied private keys must belong to the snapshot and timestamp roles.
Targets and root keys are never loaded.  The resulting repository adds one
versioned snapshot file and rewrites ``metadata/timestamp.json``.  The output
is an un-published handoff for a separately protected publication ceremony.
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

from tuf.api.metadata import Metadata, MetaFile, Root, Snapshot, Timestamp  # noqa: E402

from maintenance.tools.generate_trust_metadata import (  # noqa: E402
    ROLE_EXPIRY_DAYS,
    Ed25519FileSigner,
)
from maintenance.tools.publish_tuf_metadata import (  # noqa: E402
    stage_tuf_metadata,
)
from x86qw_runtime.trust import TrustError, validate_bootstrap_policy  # noqa: E402


MAX_FILE_BYTES = 2 * 1024 * 1024
UTC = timezone.utc


class SnapshotRenewalError(RuntimeError):
    """The snapshot renewal contract could not be proven."""


def _regular_file(path: Path, label: str, maximum: int = MAX_FILE_BYTES) -> bytes:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise SnapshotRenewalError(f"{label} ausente ou inseguro: {path}")
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise SnapshotRenewalError(f"{label} não pôde ser lido: {path}") from error
    if not payload or len(payload) > maximum:
        raise SnapshotRenewalError(f"{label} excede o limite: {path}")
    return payload


def _tree_files(root: Path) -> dict[str, Path]:
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise SnapshotRenewalError(f"repositório TUF ausente ou inseguro: {root}")
    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise SnapshotRenewalError(f"repositório TUF contém symlink: {path}")
        if path.is_file():
            if path.stat().st_size > MAX_FILE_BYTES:
                raise SnapshotRenewalError(f"arquivo TUF excede o limite: {path}")
            files[path.relative_to(root).as_posix()] = path
        elif not path.is_dir():
            raise SnapshotRenewalError(f"repositório TUF contém tipo especial: {path}")
    return files


def _read_root(path: Path) -> tuple[bytes, Metadata]:
    payload = _regular_file(path, "root TUF", 512 * 1024)
    try:
        validate_bootstrap_policy(payload)
        metadata = Metadata.from_bytes(payload)
    except (OSError, TrustError, ValueError) as error:
        raise SnapshotRenewalError(f"root TUF inválida: {error}") from error
    if not isinstance(metadata.signed, Root):
        raise SnapshotRenewalError("root TUF não contém uma role root")
    return payload, metadata


def _target_identity(repository: Path, catalog: Path) -> dict[str, object]:
    catalog_bytes = _regular_file(catalog, "catálogo")
    target_root = Path(repository) / "targets"
    candidates = [
        path for path in sorted(target_root.rglob("*.catalog.json"))
        if path.is_file() and not path.is_symlink()
    ]
    if len(candidates) != 1:
        raise SnapshotRenewalError(
            "repositório TUF precisa conter exatamente um target catalog"
        )
    target_bytes = _regular_file(candidates[0], "target catalog")
    if target_bytes != catalog_bytes:
        raise SnapshotRenewalError("target TUF corrente diverge do catálogo fornecido")
    return {
        "path": candidates[0].relative_to(target_root).as_posix(),
        "size": len(target_bytes),
        "sha256": hashlib.sha256(target_bytes).hexdigest(),
    }


def _load_role_signer(
    *, key_path: Path, key_id: str, root: Root, role: str,
) -> Ed25519FileSigner:
    role_obj = root.roles.get(role)
    if role_obj is None or key_id not in role_obj.keyids:
        raise SnapshotRenewalError(
            f"a chave fornecida não pertence à role {role}; root/targets não são aceitas"
        )
    key = root.keys.get(key_id)
    if key is None:
        raise SnapshotRenewalError(f"key id {role} não existe na root incorporada")
    key_path = Path(key_path)
    _regular_file(key_path, f"chave privada {role}", 16 * 1024)
    if os.name != "nt" and key_path.stat().st_mode & 0o077:
        raise SnapshotRenewalError(f"chave privada {role} precisa ser 0600")
    try:
        return Ed25519FileSigner.from_priv_key_uri(
            f"file2:{key_path.resolve(strict=True)}",
            key,
        )
    except (OSError, ValueError) as error:
        raise SnapshotRenewalError(
            f"chave privada {role} não corresponde ao key id: {error}"
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


def _extend_expiry(current: datetime, *, now: datetime, lease: timedelta) -> datetime:
    requested = now + lease
    expires = max(requested, current + timedelta(seconds=1)).replace(microsecond=0)
    if expires <= current.replace(microsecond=0):
        expires = current.replace(microsecond=0) + timedelta(seconds=1)
    return expires


def _current_snapshot(repository: Path, timestamp: Timestamp) -> tuple[Path, Metadata]:
    version = timestamp.snapshot_meta.version
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise SnapshotRenewalError("timestamp não aponta para uma snapshot versionada")
    path = Path(repository) / "metadata" / f"{version}.snapshot.json"
    payload = _regular_file(path, "snapshot TUF")
    try:
        metadata = Metadata.from_bytes(payload)
    except (OSError, ValueError) as error:
        raise SnapshotRenewalError(f"snapshot TUF inválida: {error}") from error
    if not isinstance(metadata.signed, Snapshot):
        raise SnapshotRenewalError(f"{path.name} não contém a role snapshot")
    if metadata.signed.version != version:
        raise SnapshotRenewalError("versão da snapshot diverge do timestamp")
    return path, metadata


def renew_snapshot(
    *,
    repository: Path,
    root: Path,
    catalog: Path,
    snapshot_key: Path,
    snapshot_key_id: str,
    timestamp_key: Path,
    timestamp_key_id: str,
    output: Path,
    report: Path,
    snapshot_lease_days: int,
    timestamp_lease_days: int,
) -> dict[str, object]:
    """Create an authenticated repository with a new snapshot and timestamp."""

    if type(snapshot_lease_days) is not int or not 1 <= snapshot_lease_days <= 3650:
        raise SnapshotRenewalError("snapshot_lease_days deve estar entre 1 e 3650")
    if type(timestamp_lease_days) is not int or not 1 <= timestamp_lease_days <= 3650:
        raise SnapshotRenewalError("timestamp_lease_days deve estar entre 1 e 3650")
    repository = Path(repository)
    output = Path(output)
    report = Path(report)
    if _inside(output, repository):
        raise SnapshotRenewalError("destino de saída não pode ficar dentro do repositório-fonte")
    if _inside(report, repository):
        raise SnapshotRenewalError("relatório não pode ficar dentro do repositório-fonte")
    if _inside(report, output):
        raise SnapshotRenewalError("relatório não pode ficar dentro do destino TUF")
    if output.exists() or output.is_symlink():
        raise SnapshotRenewalError(f"destino TUF já existe: {output}")
    if report.exists() or report.is_symlink():
        raise SnapshotRenewalError(f"relatório já existe: {report}")
    source_files = _tree_files(repository)
    root_bytes, root_metadata = _read_root(root)
    _target_identity(repository, catalog)
    if snapshot_key_id == timestamp_key_id:
        raise SnapshotRenewalError("snapshot e timestamp não podem usar o mesmo key id")
    snapshot_signer = _load_role_signer(
        key_path=snapshot_key,
        key_id=snapshot_key_id,
        root=root_metadata.signed,
        role="snapshot",
    )
    timestamp_signer = _load_role_signer(
        key_path=timestamp_key,
        key_id=timestamp_key_id,
        root=root_metadata.signed,
        role="timestamp",
    )

    timestamp_path = Path(repository) / "metadata" / "timestamp.json"
    try:
        current_timestamp = Metadata.from_bytes(_regular_file(timestamp_path, "timestamp TUF"))
    except (OSError, ValueError) as error:
        raise SnapshotRenewalError(f"timestamp TUF inválido: {error}") from error
    if not isinstance(current_timestamp.signed, Timestamp):
        raise SnapshotRenewalError("timestamp.json não contém a role timestamp")
    current_snapshot_path, current_snapshot = _current_snapshot(
        repository, current_timestamp.signed,
    )

    now = datetime.now(UTC)
    snapshot_expires = _extend_expiry(
        current_snapshot.signed.expires,
        now=now,
        lease=timedelta(days=snapshot_lease_days),
    )
    timestamp_expires = _extend_expiry(
        current_timestamp.signed.expires,
        now=now,
        lease=timedelta(days=timestamp_lease_days),
    )
    if timestamp_expires >= snapshot_expires:
        raise SnapshotRenewalError(
            "timestamp não pode expirar no mesmo instante ou depois do snapshot"
        )

    new_snapshot_version = current_snapshot.signed.version + 1
    renewed_snapshot = Metadata(
        Snapshot(version=new_snapshot_version, expires=snapshot_expires),
    )
    renewed_snapshot.signed.meta.update(current_snapshot.signed.meta)
    renewed_snapshot.sign(snapshot_signer)
    snapshot_bytes = renewed_snapshot.to_bytes()

    renewed_timestamp = Metadata(
        Timestamp(
            version=current_timestamp.signed.version + 1,
            expires=timestamp_expires,
        ),
    )
    renewed_timestamp.signed.snapshot_meta = MetaFile.from_data(
        new_snapshot_version, snapshot_bytes, ["sha256"],
    )
    renewed_timestamp.sign(timestamp_signer)
    timestamp_bytes = renewed_timestamp.to_bytes()

    snapshot_relative = f"metadata/{new_snapshot_version}.snapshot.json"
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staged = Path(temporary) / "repository"
        _copy_tree(repository, staged)
        _write_new(staged / snapshot_relative, snapshot_bytes)
        staged_timestamp = staged / "metadata/timestamp.json"
        staged_timestamp.unlink()
        _write_new(staged_timestamp, timestamp_bytes)
        staged_files = _tree_files(staged)
        added = sorted(set(staged_files) - set(source_files))
        changed_existing = [
            relative for relative in sorted(source_files)
            if source_files[relative].read_bytes() != staged_files[relative].read_bytes()
        ]
        if added != [snapshot_relative] or changed_existing != ["metadata/timestamp.json"]:
            raise SnapshotRenewalError(
                f"renovação alterou o conjunto errado: added={added} changed={changed_existing}"
            )
        stage_tuf_metadata(
            metadata_dir=staged,
            catalog=Path(catalog),
            root=Path(root),
            stage_dir=Path(temporary) / "verified",
        )
        os.replace(staged, output)

    target = _target_identity(output, catalog)
    changed_files = sorted([*added, *changed_existing])
    report_value: dict[str, Any] = {
        "format": 1,
        "project": "x86qw",
        "status": "snapshot-renewed",
        "mode": "snapshot-timestamp",
        "snapshot_key_id": snapshot_key_id,
        "timestamp_key_id": timestamp_key_id,
        "key_scope": "snapshot-and-timestamp",
        "source": {
            "snapshot_version": current_snapshot.signed.version,
            "snapshot_expires": _iso(current_snapshot.signed.expires),
            "snapshot_sha256": hashlib.sha256(current_snapshot_path.read_bytes()).hexdigest(),
            "timestamp_version": current_timestamp.signed.version,
            "timestamp_expires": _iso(current_timestamp.signed.expires),
            "timestamp_sha256": hashlib.sha256(timestamp_path.read_bytes()).hexdigest(),
        },
        "renewed": {
            "snapshot_version": renewed_snapshot.signed.version,
            "snapshot_expires": _iso(renewed_snapshot.signed.expires),
            "snapshot_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
            "timestamp_version": renewed_timestamp.signed.version,
            "timestamp_expires": _iso(renewed_timestamp.signed.expires),
            "timestamp_sha256": hashlib.sha256(timestamp_bytes).hexdigest(),
        },
        "changed_files": changed_files,
        "root_sha256": hashlib.sha256(root_bytes).hexdigest(),
        "target": target,
        "published": False,
        "checked_at": _iso(now),
        "policy": {
            "snapshot_lease_days": snapshot_lease_days,
            "timestamp_lease_days": timestamp_lease_days,
            "role_expiry_days": dict(ROLE_EXPIRY_DAYS),
        },
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
    parser.add_argument("--snapshot-key", type=Path, required=True)
    parser.add_argument("--snapshot-key-id", required=True)
    parser.add_argument("--timestamp-key", type=Path, required=True)
    parser.add_argument("--timestamp-key-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--snapshot-lease-days", type=int, default=ROLE_EXPIRY_DAYS["snapshot"])
    parser.add_argument("--timestamp-lease-days", type=int, default=ROLE_EXPIRY_DAYS["timestamp"])
    options = parser.parse_args(arguments)
    try:
        result = renew_snapshot(
            repository=options.repository,
            root=options.root,
            catalog=options.catalog,
            snapshot_key=options.snapshot_key,
            snapshot_key_id=options.snapshot_key_id,
            timestamp_key=options.timestamp_key,
            timestamp_key_id=options.timestamp_key_id,
            output=options.output,
            report=options.report,
            snapshot_lease_days=options.snapshot_lease_days,
            timestamp_lease_days=options.timestamp_lease_days,
        )
    except (OSError, SnapshotRenewalError, ValueError) as error:
        print(f"[ERRO] Renovação TUF snapshot falhou: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
