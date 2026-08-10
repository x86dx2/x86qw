#!/usr/bin/env python3
"""Initialize x86QW TUF keys and generate one atomic public repository."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
for wheel in sorted((PROJECT_ROOT / "maintenance/vendor/wheels").glob("*.whl")):
    sys.path.insert(0, str(wheel))

from securesystemslib.signer import CryptoSigner  # noqa: E402
from tuf.api.metadata import (  # noqa: E402
    MetaFile,
    Metadata,
    Root,
    Snapshot,
    TargetFile,
    Targets,
    Timestamp,
)

from x86qw_runtime.trust import (  # noqa: E402
    CATALOG_TARGET,
    EXPECTED_ROLE_POLICY,
    validate_bootstrap_policy,
)


ROLE_EXPIRY_DAYS = {"root": 365, "targets": 90, "snapshot": 7, "timestamp": 1}


def _regular_bytes(path: Path, label: str, maximum: int = 2 * 1024 * 1024) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} deve ser arquivo regular sem symlink: {path}")
    payload = path.read_bytes()
    if not payload or len(payload) > maximum:
        raise ValueError(f"{label} ausente ou excede {maximum} bytes: {path}")
    return payload


def _write_new(path: Path, payload: bytes, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
    except BaseException:
        if path.exists() and not path.is_symlink():
            path.unlink()
        raise


def initialize_root(key_dir: Path, root_path: Path) -> None:
    if key_dir.is_symlink() or key_dir.exists():
        raise ValueError(f"diretório de chaves já existe: {key_dir}")
    if root_path.is_symlink() or root_path.exists():
        raise ValueError(f"root já existe: {root_path}")
    root_path.parent.mkdir(parents=True, exist_ok=True)
    key_dir.mkdir(parents=True, mode=0o700)
    if os.name != "nt":
        key_dir.chmod(0o700)

    signers: dict[str, list[CryptoSigner]] = {}
    for role, (count, _threshold) in EXPECTED_ROLE_POLICY.items():
        role_signers = []
        for index in range(1, count + 1):
            signer = CryptoSigner.generate_ed25519()
            _write_new(key_dir / f"{role}-{index}.pem", signer.private_bytes, 0o600)
            role_signers.append(signer)
        signers[role] = role_signers

    now = datetime.now(timezone.utc)
    metadata = Metadata(Root(
        version=1,
        expires=now + timedelta(days=ROLE_EXPIRY_DAYS["root"]),
        consistent_snapshot=True,
    ))
    for role, role_signers in signers.items():
        for signer in role_signers:
            metadata.signed.add_key(signer.public_key, role)
        metadata.signed.roles[role].threshold = EXPECTED_ROLE_POLICY[role][1]
    for index, signer in enumerate(signers["root"][:2]):
        metadata.sign(signer, append=index > 0)
    payload = metadata.to_bytes()
    validate_bootstrap_policy(payload)
    _write_new(root_path, payload, 0o644)


def _load_signers(key_dir: Path, root: Root, role: str) -> list[CryptoSigner]:
    if key_dir.is_symlink() or not key_dir.is_dir():
        raise ValueError(f"diretório de chaves inválido: {key_dir}")
    canonical_dir = key_dir.resolve(strict=True)
    signers = []
    for index, keyid in enumerate(root.roles[role].keyids, start=1):
        path = key_dir / f"{role}-{index}.pem"
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"chave privada ausente ou insegura: {path}")
        canonical = path.resolve(strict=True)
        if canonical.parent != canonical_dir:
            raise ValueError(f"chave privada fora do diretório aprovado: {path}")
        signer = CryptoSigner.from_priv_key_uri(f"file2:{canonical}", root.keys[keyid])
        probe = signer.sign(b"x86qw signer identity")
        root.keys[keyid].verify_signature(probe, b"x86qw signer identity")
        signers.append(signer)
    return signers


def _sign(metadata: Metadata, signers: list[CryptoSigner], threshold: int) -> bytes:
    for index, signer in enumerate(signers[:threshold]):
        metadata.sign(signer, append=index > 0)
    return metadata.to_bytes()


def generate_repository(
    key_dir: Path,
    root_path: Path,
    catalog_path: Path,
    output: Path,
    version: int = 1,
) -> None:
    if type(version) is not int or version <= 0:
        raise ValueError("versão de metadata deve ser inteira e positiva")
    if output.is_symlink() or output.exists():
        raise ValueError(f"repositório de saída já existe: {output}")
    root_bytes = _regular_bytes(root_path, "root TUF", 512 * 1024)
    validate_bootstrap_policy(root_bytes)
    root_metadata = Metadata.from_bytes(root_bytes)
    assert isinstance(root_metadata.signed, Root)
    root = root_metadata.signed
    catalog = _regular_bytes(catalog_path, "catálogo")
    now = datetime.now(timezone.utc)

    targets = Metadata(Targets(
        version=version,
        expires=now + timedelta(days=ROLE_EXPIRY_DAYS["targets"]),
    ))
    target = TargetFile.from_data(CATALOG_TARGET, catalog)
    targets.signed.targets[CATALOG_TARGET] = target
    targets_bytes = _sign(
        targets,
        _load_signers(key_dir, root, "targets"),
        root.roles["targets"].threshold,
    )

    snapshot = Metadata(Snapshot(
        version=version,
        expires=now + timedelta(days=ROLE_EXPIRY_DAYS["snapshot"]),
    ))
    snapshot.signed.meta["targets.json"] = MetaFile.from_data(
        version, targets_bytes, ["sha256"],
    )
    snapshot_bytes = _sign(
        snapshot,
        _load_signers(key_dir, root, "snapshot"),
        root.roles["snapshot"].threshold,
    )

    timestamp = Metadata(Timestamp(
        version=version,
        expires=now + timedelta(days=ROLE_EXPIRY_DAYS["timestamp"]),
    ))
    timestamp.signed.snapshot_meta = MetaFile.from_data(
        version, snapshot_bytes, ["sha256"],
    )
    timestamp_bytes = _sign(
        timestamp,
        _load_signers(key_dir, root, "timestamp"),
        root.roles["timestamp"].threshold,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        metadata_dir = staging / "metadata"
        target_dir = staging / "targets/catalog"
        metadata_dir.mkdir(parents=True)
        target_dir.mkdir(parents=True)
        _write_new(metadata_dir / f"{root.version}.root.json", root_bytes, 0o644)
        _write_new(metadata_dir / f"{version}.targets.json", targets_bytes, 0o644)
        _write_new(metadata_dir / f"{version}.snapshot.json", snapshot_bytes, 0o644)
        _write_new(metadata_dir / "timestamp.json", timestamp_bytes, 0o644)
        digest = hashlib.sha256(catalog).hexdigest()
        _write_new(target_dir / f"{digest}.catalog.json", catalog, 0o644)
        os.replace(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init-root")
    initialize.add_argument("--key-dir", type=Path, required=True)
    initialize.add_argument("--root", type=Path, required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--key-dir", type=Path, required=True)
    generate.add_argument("--root", type=Path, required=True)
    generate.add_argument("--catalog", type=Path, required=True)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--version", type=int, default=1)
    return parser


def main(arguments: list[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    if options.command == "init-root":
        initialize_root(options.key_dir, options.root)
    else:
        generate_repository(
            options.key_dir,
            options.root,
            options.catalog,
            options.output,
            options.version,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        print(f"trust metadata generation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
