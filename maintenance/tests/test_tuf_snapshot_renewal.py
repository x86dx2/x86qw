from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from maintenance.tests import trust_support  # Loads the pinned TUF wheels.
from maintenance.tools import tuf_snapshot_renewal
from tuf.api.metadata import Metadata, Snapshot, Timestamp

from maintenance.tools.publish_tuf_metadata import stage_tuf_metadata


ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "maintenance/tools/generate_trust_metadata.py"
SCRIPT = ROOT / "maintenance/tools/tuf_snapshot_renewal.py"


class TufSnapshotRenewalTests(unittest.TestCase):
    def _make_repository(self, workspace: Path) -> tuple[Path, Path, Path, str, str, Path]:
        key_dir = workspace / "keys"
        root = workspace / "root.json"
        catalog = workspace / "catalog.json"
        catalog.write_text(
            '{"format":1,"project":"x86qw","packages":[]}',
            encoding="utf-8",
        )
        initialized = subprocess.run(
            [
                sys.executable, str(GENERATOR), "init-root",
                "--key-dir", str(key_dir), "--root", str(root),
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
                sys.executable, str(GENERATOR), "generate",
                "--key-dir", str(key_dir), "--root", str(root),
                "--catalog", str(catalog), "--output", str(repository),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, generated.returncode, generated.stderr)
        root_metadata = Metadata.from_bytes(root.read_bytes())
        snapshot_key_id = root_metadata.signed.roles["snapshot"].keyids[0]
        timestamp_key_id = root_metadata.signed.roles["timestamp"].keyids[0]
        return key_dir, root, catalog, snapshot_key_id, timestamp_key_id, repository

    @staticmethod
    def _files(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def test_renews_snapshot_and_timestamp_without_changing_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            key_dir, root, catalog, snapshot_key_id, timestamp_key_id, repository = (
                self._make_repository(workspace)
            )
            output = workspace / "renewed"
            report_path = workspace / "renewal.json"
            completed = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--repository", str(repository),
                    "--root", str(root),
                    "--catalog", str(catalog),
                    "--snapshot-key", str(key_dir / "snapshot-1.pem"),
                    "--snapshot-key-id", snapshot_key_id,
                    "--timestamp-key", str(key_dir / "timestamp-1.pem"),
                    "--timestamp-key-id", timestamp_key_id,
                    "--output", str(output),
                    "--report", str(report_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

            source_files = self._files(repository)
            renewed_files = self._files(output)
            added = sorted(set(renewed_files) - set(source_files))
            changed = [
                name for name in sorted(source_files)
                if source_files[name] != renewed_files[name]
            ]
            self.assertEqual(["metadata/2.snapshot.json"], added)
            self.assertEqual(["metadata/timestamp.json"], changed)
            self.assertEqual(
                source_files["metadata/1.snapshot.json"],
                renewed_files["metadata/1.snapshot.json"],
            )

            source_snapshot = Metadata.from_bytes(source_files["metadata/1.snapshot.json"])
            renewed_snapshot = Metadata.from_bytes(renewed_files["metadata/2.snapshot.json"])
            source_timestamp = Metadata.from_bytes(source_files["metadata/timestamp.json"])
            renewed_timestamp = Metadata.from_bytes(renewed_files["metadata/timestamp.json"])
            self.assertIsInstance(source_snapshot.signed, Snapshot)
            self.assertIsInstance(renewed_snapshot.signed, Snapshot)
            self.assertIsInstance(source_timestamp.signed, Timestamp)
            self.assertIsInstance(renewed_timestamp.signed, Timestamp)
            self.assertEqual(source_snapshot.signed.version + 1, renewed_snapshot.signed.version)
            self.assertEqual(source_timestamp.signed.version + 1, renewed_timestamp.signed.version)
            self.assertEqual(source_snapshot.signed.meta, renewed_snapshot.signed.meta)
            self.assertEqual(2, renewed_timestamp.signed.snapshot_meta.version)
            self.assertGreater(renewed_snapshot.signed.expires, source_snapshot.signed.expires)
            self.assertGreater(renewed_timestamp.signed.expires, source_timestamp.signed.expires)
            self.assertGreater(renewed_snapshot.signed.expires, renewed_timestamp.signed.expires)

            staged = stage_tuf_metadata(
                metadata_dir=output,
                catalog=catalog,
                root=root,
                stage_dir=workspace / "verified",
            )
            self.assertEqual("verified-staged", staged["status"])

            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual("snapshot-timestamp", report["mode"])
            self.assertEqual(snapshot_key_id, report["snapshot_key_id"])
            self.assertEqual(timestamp_key_id, report["timestamp_key_id"])
            self.assertEqual(
                ["metadata/2.snapshot.json", "metadata/timestamp.json"],
                report["changed_files"],
            )
            self.assertFalse(report["published"])
            self.assertEqual(
                hashlib.sha256(catalog.read_bytes()).hexdigest(),
                report["target"]["sha256"],
            )

    def test_rejects_a_key_outside_snapshot_role_before_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            key_dir, root, catalog, _snapshot_key_id, timestamp_key_id, repository = (
                self._make_repository(workspace)
            )
            root_metadata = Metadata.from_bytes(root.read_bytes())
            target_key_id = root_metadata.signed.roles["targets"].keyids[0]
            output = workspace / "must-not-exist"
            completed = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--repository", str(repository),
                    "--root", str(root),
                    "--catalog", str(catalog),
                    "--snapshot-key", str(key_dir / "targets-1.pem"),
                    "--snapshot-key-id", target_key_id,
                    "--timestamp-key", str(key_dir / "timestamp-1.pem"),
                    "--timestamp-key-id", timestamp_key_id,
                    "--output", str(output),
                    "--report", str(workspace / "renewal.json"),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, completed.returncode)
            self.assertIn("snapshot", completed.stderr.lower())
            self.assertFalse(output.exists())

    def test_rejects_timestamp_lease_that_would_outlive_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            key_dir, root, catalog, snapshot_key_id, timestamp_key_id, repository = (
                self._make_repository(workspace)
            )
            current_snapshot = Metadata.from_bytes(
                (repository / "metadata/1.snapshot.json").read_bytes(),
            ).signed
            fake_now = current_snapshot.expires - timedelta(days=1)
            output = workspace / "must-not-exist"
            with mock.patch.object(tuf_snapshot_renewal, "datetime") as clock:
                clock.now.return_value = fake_now
                with self.assertRaisesRegex(tuf_snapshot_renewal.SnapshotRenewalError, "timestamp"):
                    tuf_snapshot_renewal.renew_snapshot(
                        repository=repository,
                        root=root,
                        catalog=catalog,
                        snapshot_key=key_dir / "snapshot-1.pem",
                        snapshot_key_id=snapshot_key_id,
                        timestamp_key=key_dir / "timestamp-1.pem",
                        timestamp_key_id=timestamp_key_id,
                        output=output,
                        report=workspace / "renewal.json",
                        snapshot_lease_days=7,
                        timestamp_lease_days=30,
                    )
            self.assertFalse(output.exists())

    def test_refuses_output_inside_the_source_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            key_dir, root, catalog, snapshot_key_id, timestamp_key_id, repository = (
                self._make_repository(workspace)
            )
            output = repository / "renewed"
            completed = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--repository", str(repository),
                    "--root", str(root),
                    "--catalog", str(catalog),
                    "--snapshot-key", str(key_dir / "snapshot-1.pem"),
                    "--snapshot-key-id", snapshot_key_id,
                    "--timestamp-key", str(key_dir / "timestamp-1.pem"),
                    "--timestamp-key-id", timestamp_key_id,
                    "--output", str(output),
                    "--report", str(workspace / "renewal.json"),
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
