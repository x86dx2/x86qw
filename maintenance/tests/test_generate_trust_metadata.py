from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from maintenance.tests import trust_support  # Loads the pinned TUF wheels.
from tuf.api import exceptions
from tuf.api.metadata import Metadata, Root

from x86qw_runtime.trust import BoundedTufFetcher, load_trusted_catalog


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "maintenance/tools/generate_trust_metadata.py"


class LocalFetcher:
    def __init__(self, repository: Path) -> None:
        self.repository = repository

    def get(self, url: str, **_options: object) -> bytes:
        marker = ".invalid/"
        relative = url.split(marker, 1)[1]
        try:
            return (self.repository / relative).read_bytes()
        except FileNotFoundError as error:
            raise exceptions.DownloadHTTPError("not found", 404) from error


class GenerateTrustMetadataTests(unittest.TestCase):
    def run_tool(self, *arguments: object) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *(str(value) for value in arguments)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_initializes_root_and_generates_refreshable_catalog_repository(self) -> None:
        catalog = b'{"format":1,"project":"x86qw","packages":[]}\n'
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            key_dir = workspace / "keys"
            root_path = workspace / "root.json"
            catalog_path = workspace / "catalog.json"
            catalog_path.write_bytes(catalog)

            initialized = self.run_tool(
                "init-root", "--key-dir", key_dir, "--root", root_path,
            )
            self.assertEqual(0, initialized.returncode, initialized.stderr)
            root_bytes = root_path.read_bytes()
            root = Metadata.from_bytes(root_bytes)
            self.assertIsInstance(root.signed, Root)
            self.assertTrue(root.signed.consistent_snapshot)
            self.assertEqual(
                {"root": (3, 2), "targets": (3, 2), "snapshot": (2, 1), "timestamp": (2, 1)},
                {
                    role: (len(value.keyids), value.threshold)
                    for role, value in root.signed.roles.items()
                },
            )
            self.assertEqual(10, len(root.signed.keys))
            self.assertLessEqual(
                root.signed.expires,
                datetime.now(timezone.utc) + timedelta(days=365, minutes=1),
            )
            self.assertEqual(10, len(list(key_dir.glob("*.pem"))))
            if os.name != "nt":
                self.assertEqual(0, key_dir.stat().st_mode & 0o077)
                for key in key_dir.glob("*.pem"):
                    self.assertEqual(0, key.stat().st_mode & 0o077)

            output = workspace / "repository"
            generated = self.run_tool(
                "generate",
                "--key-dir", key_dir,
                "--root", root_path,
                "--catalog", catalog_path,
                "--output", output,
            )
            self.assertEqual(0, generated.returncode, generated.stderr)
            digest = hashlib.sha256(catalog).hexdigest()
            expected = {
                "metadata/1.root.json",
                "metadata/1.targets.json",
                "metadata/1.snapshot.json",
                "metadata/timestamp.json",
                f"targets/catalog/{digest}.catalog.json",
            }
            self.assertEqual(
                expected,
                {
                    path.relative_to(output).as_posix()
                    for path in output.rglob("*")
                    if path.is_file()
                },
            )
            limits = {
                "1.targets.json": timedelta(days=90, minutes=1),
                "1.snapshot.json": timedelta(days=7, minutes=1),
                "timestamp.json": timedelta(days=1, minutes=1),
            }
            for name, limit in limits.items():
                metadata = Metadata.from_bytes((output / "metadata" / name).read_bytes())
                self.assertLessEqual(metadata.signed.expires, datetime.now(timezone.utc) + limit)

            cache = workspace / "cache"
            cache.mkdir(mode=0o700)
            refreshed = load_trusted_catalog(
                bootstrap_root=root_bytes,
                metadata_dir=cache / "metadata",
                target_dir=cache / "targets",
                metadata_base_url="https://repository.invalid/metadata/",
                target_base_url="https://repository.invalid/targets/",
                fetcher=BoundedTufFetcher(LocalFetcher(output).get),
            )
            self.assertEqual(json.loads(catalog), refreshed)

    def test_refuses_to_overwrite_existing_keys_or_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            key_dir = workspace / "keys"
            root_path = workspace / "root.json"
            self.assertEqual(
                0,
                self.run_tool(
                    "init-root", "--key-dir", key_dir, "--root", root_path,
                ).returncode,
            )
            repeated = self.run_tool(
                "init-root", "--key-dir", key_dir, "--root", root_path,
            )
            self.assertNotEqual(0, repeated.returncode)

            catalog_path = workspace / "catalog.json"
            catalog_path.write_text(
                '{"format":1,"project":"x86qw","packages":[]}\n',
                encoding="utf-8",
            )
            output = workspace / "repository"
            arguments = (
                "generate", "--key-dir", key_dir, "--root", root_path,
                "--catalog", catalog_path, "--output", output,
            )
            self.assertEqual(0, self.run_tool(*arguments).returncode)
            self.assertNotEqual(0, self.run_tool(*arguments).returncode)

    def test_generates_monotonic_metadata_versions_for_renewal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            key_dir = workspace / "keys"
            root_path = workspace / "root.json"
            catalog_path = workspace / "catalog.json"
            catalog_path.write_text(
                '{"format":1,"project":"x86qw","packages":[]}\n',
                encoding="utf-8",
            )
            self.assertEqual(
                0,
                self.run_tool(
                    "init-root", "--key-dir", key_dir, "--root", root_path,
                ).returncode,
            )
            output = workspace / "repository-v2"
            completed = self.run_tool(
                "generate", "--key-dir", key_dir, "--root", root_path,
                "--catalog", catalog_path, "--output", output, "--version", 2,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertTrue((output / "metadata/1.root.json").is_file())
            for name in ("2.targets.json", "2.snapshot.json", "timestamp.json"):
                metadata = Metadata.from_bytes((output / "metadata" / name).read_bytes())
                self.assertEqual(2, metadata.signed.version)


if __name__ == "__main__":
    unittest.main()
