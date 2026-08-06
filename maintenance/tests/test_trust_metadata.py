from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from maintenance.tests.trust_support import (
    METADATA_URL,
    TARGET_URL,
    MappingFetcher,
    build_repository,
    new_keyset,
    signed_root,
)


class TrustMetadataTests(unittest.TestCase):
    def runtime(self):
        try:
            from x86qw_runtime import trust
        except ImportError as error:
            self.fail(f"trust runtime boundary is missing: {error}")
        return trust

    def load(self, repository, root: Path):
        return self.runtime().load_trusted_catalog(
            bootstrap_root=repository.bootstrap_root,
            metadata_dir=root / "metadata",
            target_dir=root / "targets",
            metadata_base_url=METADATA_URL,
            target_base_url=TARGET_URL,
            fetcher=MappingFetcher(repository.files),
        )

    def test_valid_threshold_signed_catalog_is_returned(self) -> None:
        keys = new_keyset()
        catalog = b'{"format":1,"project":"x86qw","packages":[]}'
        repository = build_repository(keys, version=1, catalog=catalog)
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(
                json.loads(catalog),
                self.load(repository, Path(temporary)),
            )

    def test_modified_catalog_is_rejected_before_use(self) -> None:
        repository = build_repository(new_keyset(), version=1)
        target_url = next(url for url in repository.files if url.startswith(TARGET_URL))
        repository.files[target_url] = b'{"format":1,"project":"attacker","packages":[]}'
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(self.runtime().TrustError, "trust|assin|hash|autentic"):
                self.load(repository, Path(temporary))

    def test_expired_timestamp_blocks_a_refresh(self) -> None:
        repository = build_repository(new_keyset(), version=1, expired="timestamp")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(self.runtime().TrustError, "trust|expir|freeze"):
                self.load(repository, Path(temporary))

    def test_rollback_to_an_older_timestamp_is_rejected(self) -> None:
        keys = new_keyset()
        newer = build_repository(keys, version=2)
        older = build_repository(keys, version=1, bootstrap_root=newer.bootstrap_root)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.load(newer, root)
            with self.assertRaisesRegex(self.runtime().TrustError, "trust|rollback|vers"):
                self.load(older, root)

    def test_same_version_timestamp_equivocation_is_rejected(self) -> None:
        keys = new_keyset()
        original = build_repository(keys, version=1)
        equivocation = build_repository(
            keys,
            version=1,
            catalog=b'{"format":1,"project":"x86qw","packages":[{"attacker":true}]}',
            bootstrap_root=original.bootstrap_root,
        )
        for url, payload in original.files.items():
            if url.startswith(TARGET_URL):
                equivocation.files.setdefault(url, payload)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.load(original, root)
            with self.assertRaisesRegex(
                self.runtime().TrustError, "equivoca|mesma versão|trust",
            ):
                self.load(equivocation, root)

    def test_root_rotation_requires_old_and_new_thresholds(self) -> None:
        old_keys = new_keyset()
        new_keys = new_keyset()
        root_v1 = signed_root(old_keys, version=1)
        root_v2 = signed_root(
            new_keys, version=2, previous_root=old_keys["root"],
        )
        rotated = build_repository(
            new_keys,
            version=2,
            bootstrap_root=root_v1,
            root_updates=(root_v2,),
        )
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual("x86qw", self.load(rotated, Path(temporary))["project"])

        unanchored_root = signed_root(new_keys, version=2)
        unanchored = build_repository(
            new_keys,
            version=2,
            bootstrap_root=root_v1,
            root_updates=(unanchored_root,),
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(self.runtime().TrustError, "trust|root|assin"):
                self.load(unanchored, Path(temporary))

    def test_bootstrap_policy_rejects_non_ed25519_keys(self) -> None:
        keys = new_keyset()
        root = json.loads(signed_root(keys, version=1))
        first_key = next(iter(root["signed"]["keys"].values()))
        first_key["keytype"] = "rsa"
        first_key["scheme"] = "rsassa-pss-sha256"
        with self.assertRaisesRegex(self.runtime().TrustError, "Ed25519"):
            self.runtime().validate_bootstrap_policy(json.dumps(root).encode())

    def test_metadata_directories_are_private(self) -> None:
        repository = build_repository(new_keyset(), version=1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.load(repository, root)
            if __import__("os").name != "nt":
                for path in root.rglob("*"):
                    with self.subTest(path=path.relative_to(root)):
                        self.assertEqual(0, path.stat().st_mode & 0o077)

    def test_tuf_fetcher_uses_the_existing_bounded_remote_boundary(self) -> None:
        calls = []

        def get(url: str, **options: object) -> bytes:
            calls.append((url, options))
            return b"metadata"

        fetcher = self.runtime().BoundedTufFetcher(get)
        self.assertEqual(
            b"metadata",
            fetcher.download_bytes(f"{METADATA_URL}timestamp.json", 128 * 1024),
        )
        self.assertEqual(METADATA_URL + "timestamp.json", calls[0][0])
        self.assertEqual(64 * 1024, calls[0][1]["maximum_size"])
        self.assertEqual(1, calls[0][1]["attempts"])


if __name__ == "__main__":
    unittest.main()
