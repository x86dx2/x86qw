from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from maintenance.tests import trust_support  # Loads the pinned TUF wheels.
from maintenance.tools import tuf_timestamp_renewal
from tuf.api.metadata import Metadata, Timestamp

from maintenance.tools.publish_tuf_metadata import stage_tuf_metadata


ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "maintenance/tools/generate_trust_metadata.py"
SCRIPT = ROOT / "maintenance/tools/tuf_timestamp_renewal.py"


class TufTimestampRenewalTests(unittest.TestCase):
    def _make_repository(self, workspace: Path) -> tuple[Path, Path, Path, str, Path]:
        key_dir = workspace / "keys"
        root = workspace / "root.json"
        catalog = workspace / "catalog.json"
        catalog.write_text(
            '{"format":1,"project":"x86qw","packages":[]}',
            encoding="utf-8",
        )
        initialized = subprocess.run(
            [
                sys.executable,
                str(GENERATOR),
                "init-root",
                "--key-dir",
                str(key_dir),
                "--root",
                str(root),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, initialized.returncode, initialized.stderr)
        repository = workspace / "repository"
        generated = subprocess.run(
            [
                sys.executable,
                str(GENERATOR),
                "generate",
                "--key-dir",
                str(key_dir),
                "--root",
                str(root),
                "--catalog",
                str(catalog),
                "--output",
                str(repository),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, generated.returncode, generated.stderr)
        root_metadata = Metadata.from_bytes(root.read_bytes())
        timestamp_key_id = root_metadata.signed.roles["timestamp"].keyids[0]
        return key_dir, root, catalog, timestamp_key_id, repository

    @staticmethod
    def _files(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def test_renews_only_timestamp_and_keeps_repository_authenticated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            key_dir, root, catalog, key_id, repository = self._make_repository(workspace)
            output = workspace / "renewed"
            report_path = workspace / "renewal.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repository",
                    str(repository),
                    "--root",
                    str(root),
                    "--catalog",
                    str(catalog),
                    "--timestamp-key",
                    str(key_dir / "timestamp-1.pem"),
                    "--key-id",
                    key_id,
                    "--output",
                    str(output),
                    "--report",
                    str(report_path),
                    "--lease-hours",
                    "24",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

            source_files = self._files(repository)
            renewed_files = self._files(output)
            self.assertEqual(set(source_files), set(renewed_files))
            changed = [
                name for name in source_files
                if source_files[name] != renewed_files[name]
            ]
            self.assertEqual(["metadata/timestamp.json"], changed)

            current = Metadata.from_bytes(source_files["metadata/timestamp.json"])
            renewed = Metadata.from_bytes(renewed_files["metadata/timestamp.json"])
            self.assertIsInstance(current.signed, Timestamp)
            self.assertIsInstance(renewed.signed, Timestamp)
            self.assertEqual(current.signed.version + 1, renewed.signed.version)
            self.assertEqual(current.signed.snapshot_meta, renewed.signed.snapshot_meta)
            self.assertGreater(renewed.signed.expires, current.signed.expires)
            self.assertGreater(
                renewed.signed.expires,
                datetime.now(timezone.utc) + timedelta(hours=23),
            )

            staged = stage_tuf_metadata(
                metadata_dir=output,
                catalog=catalog,
                root=root,
                stage_dir=workspace / "verified",
            )
            self.assertEqual("verified-staged", staged["status"])

            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual("timestamp-only", report["mode"])
            self.assertEqual(key_id, report["key_id"])
            self.assertEqual(["metadata/timestamp.json"], report["changed_files"])
            self.assertFalse(report["published"])
            self.assertEqual(
                hashlib.sha256(catalog.read_bytes()).hexdigest(),
                report["target"]["sha256"],
            )

    def test_rejects_a_key_outside_timestamp_role_before_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            key_dir, root, catalog, _key_id, repository = self._make_repository(workspace)
            root_metadata = Metadata.from_bytes(root.read_bytes())
            target_key_id = root_metadata.signed.roles["targets"].keyids[0]
            output = workspace / "must-not-exist"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repository",
                    str(repository),
                    "--root",
                    str(root),
                    "--catalog",
                    str(catalog),
                    "--timestamp-key",
                    str(key_dir / "targets-1.pem"),
                    "--key-id",
                    target_key_id,
                    "--output",
                    str(output),
                    "--lease-hours",
                    "24",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, completed.returncode)
            self.assertIn("timestamp", completed.stderr.lower())
            self.assertFalse(output.exists())

    def test_serialized_renewal_always_extends_the_existing_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            key_dir, root, catalog, key_id, repository = self._make_repository(workspace)
            current = Metadata.from_bytes(
                (repository / "metadata/timestamp.json").read_bytes(),
            ).signed
            output = workspace / "renewed"
            report_path = workspace / "renewal.json"
            fake_now = current.expires - timedelta(hours=24) + timedelta(microseconds=500_000)
            with mock.patch.object(tuf_timestamp_renewal, "datetime") as clock:
                clock.now.return_value = fake_now
                completed = tuf_timestamp_renewal.renew_timestamp(
                    repository=repository,
                    root=root,
                    catalog=catalog,
                    timestamp_key=key_dir / "timestamp-1.pem",
                    key_id=key_id,
                    output=output,
                    report=report_path,
                    lease_hours=24,
                )
            renewed = Metadata.from_bytes(
                (output / "metadata/timestamp.json").read_bytes(),
            ).signed
            self.assertGreater(renewed.expires, current.expires)
            self.assertEqual(2, renewed.version)
            self.assertEqual(completed["renewed"]["expires"], renewed.expires.isoformat().replace("+00:00", "Z"))

    def test_refuses_output_inside_the_source_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            key_dir, root, catalog, key_id, repository = self._make_repository(workspace)
            output = repository / "renewed"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repository",
                    str(repository),
                    "--root",
                    str(root),
                    "--catalog",
                    str(catalog),
                    "--timestamp-key",
                    str(key_dir / "timestamp-1.pem"),
                    "--key-id",
                    key_id,
                    "--output",
                    str(output),
                    "--report",
                    str(workspace / "renewal.json"),
                    "--lease-hours",
                    "24",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, completed.returncode)
            self.assertIn("fonte", completed.stderr.lower())
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
