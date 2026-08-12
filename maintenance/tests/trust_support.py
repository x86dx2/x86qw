"""Ephemeral TUF repository support for trust-boundary tests only."""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
for wheel in sorted((ROOT / "maintenance/vendor/wheels").glob("*.whl")):
    sys.path.insert(0, str(wheel))

from securesystemslib._vendor.ed25519.ed25519 import (  # noqa: E402
    publickey_unsafe,
    signature_unsafe,
)
from securesystemslib.signer import Key, Signature, Signer, SSlibKey  # noqa: E402
from securesystemslib.signer._utils import compute_default_keyid  # noqa: E402
from tuf.api import exceptions  # noqa: E402
from tuf.api.metadata import (  # noqa: E402
    MetaFile,
    Metadata,
    Root,
    Snapshot,
    TargetFile,
    Targets,
    Timestamp,
)
from tuf.ngclient.fetcher import FetcherInterface  # noqa: E402


METADATA_URL = "https://metadata.invalid/"
TARGET_URL = "https://targets.invalid/"
CATALOG_TARGET = "catalog/catalog.json"
THRESHOLDS = {"root": 2, "targets": 2, "snapshot": 1, "timestamp": 1}
KEY_COUNTS = {"root": 3, "targets": 3, "snapshot": 2, "timestamp": 2}


class EphemeralSigner(Signer):
    """Unsafe reference signing is confined to random, process-local test keys."""

    def __init__(self) -> None:
        self._secret = os.urandom(32)
        public = publickey_unsafe(self._secret).hex()
        keyval = {"public": public}
        keyid = compute_default_keyid("ed25519", "ed25519", keyval)
        self._public_key = SSlibKey(
            keyid, "ed25519", "ed25519", keyval,
        )

    @property
    def public_key(self) -> Key:
        return self._public_key

    def sign(self, payload: bytes) -> Signature:
        public = bytes.fromhex(self._public_key.keyval["public"])
        signature = signature_unsafe(payload, self._secret, public)
        return Signature(self._public_key.keyid, signature.hex())

    @classmethod
    def from_priv_key_uri(
        cls,
        priv_key_uri: str,
        public_key: Key,
        secrets_handler=None,
    ) -> "EphemeralSigner":
        del priv_key_uri, public_key, secrets_handler
        raise NotImplementedError("test keys are generated in memory")


def new_keyset() -> dict[str, tuple[EphemeralSigner, ...]]:
    return {
        role: tuple(EphemeralSigner() for _ in range(count))
        for role, count in KEY_COUNTS.items()
    }


def signed_root(
    keys: dict[str, tuple[EphemeralSigner, ...]],
    *,
    version: int,
    previous_root: tuple[EphemeralSigner, ...] = (),
) -> bytes:
    root = Metadata(Root(
        version=version,
        expires=datetime.now(timezone.utc) + timedelta(days=365),
        consistent_snapshot=True,
    ))
    for role, signers in keys.items():
        for signer in signers:
            root.signed.add_key(signer.public_key, role)
        root.signed.roles[role].threshold = THRESHOLDS[role]
    for index, signer in enumerate(keys["root"][:2]):
        root.sign(signer, append=index > 0)
    for signer in previous_root[:2]:
        root.sign(signer, append=True)
    return root.to_bytes()


@dataclass(frozen=True)
class TestRepository:
    bootstrap_root: bytes
    files: dict[str, bytes]


def build_repository(
    keys: dict[str, tuple[EphemeralSigner, ...]],
    *,
    version: int,
    catalog: bytes = b'{"format":1,"project":"x86qw","packages":[]}',
    expired: str | None = None,
    bootstrap_root: bytes | None = None,
    root_updates: tuple[bytes, ...] = (),
) -> TestRepository:
    now = datetime.now(timezone.utc)

    def expiry(role: str, days: int) -> datetime:
        if expired == role:
            return now - timedelta(seconds=1)
        return now + timedelta(days=days)

    targets = Metadata(Targets(version=version, expires=expiry("targets", 30)))
    target_info = TargetFile.from_data(CATALOG_TARGET, catalog)
    targets.signed.targets[CATALOG_TARGET] = target_info
    for index, signer in enumerate(keys["targets"][:2]):
        targets.sign(signer, append=index > 0)
    targets_bytes = targets.to_bytes()

    snapshot = Metadata(Snapshot(version=version, expires=expiry("snapshot", 7)))
    snapshot.signed.meta["targets.json"] = MetaFile.from_data(
        version, targets_bytes, ["sha256"],
    )
    snapshot.sign(keys["snapshot"][0])
    snapshot_bytes = snapshot.to_bytes()

    timestamp = Metadata(Timestamp(version=version, expires=expiry("timestamp", 1)))
    timestamp.signed.snapshot_meta = MetaFile.from_data(
        version, snapshot_bytes, ["sha256"],
    )
    timestamp.sign(keys["timestamp"][0])
    timestamp_bytes = timestamp.to_bytes()

    root = bootstrap_root or signed_root(keys, version=1)
    target_hash = target_info.hashes["sha256"]
    files = {
        f"{METADATA_URL}timestamp.json": timestamp_bytes,
        f"{METADATA_URL}{version}.snapshot.json": snapshot_bytes,
        f"{METADATA_URL}{version}.targets.json": targets_bytes,
        f"{TARGET_URL}catalog/{target_hash}.catalog.json": catalog,
    }
    for update in root_updates:
        update_version = Metadata.from_bytes(update).signed.version
        files[f"{METADATA_URL}{update_version}.root.json"] = update
    return TestRepository(root, files)


class MappingFetcher(FetcherInterface):
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def _fetch(self, url: str):
        try:
            yield self.files[url]
        except KeyError as error:
            raise exceptions.DownloadHTTPError("not found", 404) from error


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
