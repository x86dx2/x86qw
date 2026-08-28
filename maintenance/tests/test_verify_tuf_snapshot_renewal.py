from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from maintenance.tests import trust_support  # Loads the pinned TUF wheels.
from maintenance.tools.tuf_snapshot_renewal import renew_snapshot


ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "maintenance/tools/generate_trust_metadata.py"


class VerifyTufSnapshotRenewalTests(unittest.TestCase):
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
        from tuf.api.metadata import Metadata

        root_metadata = Metadata.from_bytes(root.read_bytes())
        return (
            key_dir,
            root,
            catalog,
            root_metadata.signed.roles["snapshot"].keyids[0],
            root_metadata.signed.roles["timestamp"].keyids[0],
            repository,
        )

    def _renew(self, workspace: Path) -> tuple[Path, Path, Path, Path]:
        key_dir, root, catalog, snapshot_key_id, timestamp_key_id, repository = (
            self._make_repository(workspace)
        )
        renewed = workspace / "renewed"
        report = workspace / "report.json"
        renew_snapshot(
            repository=repository,
            root=root,
            catalog=catalog,
            snapshot_key=key_dir / "snapshot-1.pem",
            snapshot_key_id=snapshot_key_id,
            timestamp_key=key_dir / "timestamp-1.pem",
            timestamp_key_id=timestamp_key_id,
            output=renewed,
            report=report,
            snapshot_lease_days=90,
            timestamp_lease_days=30,
        )
        return root, catalog, repository, renewed

    def test_valid_renewal_is_authenticated_and_reports_snapshot_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root, catalog, source, renewed = self._renew(workspace)
            from maintenance.tools.verify_tuf_snapshot_renewal import (
                verify_snapshot_renewal,
            )

            result = verify_snapshot_renewal(
                source_repository=source,
                renewed_repository=renewed,
                report=workspace / "report.json",
                root=root,
                catalog=catalog,
            )

            self.assertEqual("verified-snapshot-renewal", result["status"])
            self.assertEqual(
                ["metadata/2.snapshot.json", "metadata/timestamp.json"],
                result["changed_files"],
            )
            self.assertFalse(result["published"])

    def test_changed_target_is_rejected_even_when_report_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root, catalog, source, renewed = self._renew(workspace)
            target = next((renewed / "targets").rglob("*.catalog.json"))
            target.write_bytes(target.read_bytes() + b"\n")
            from maintenance.tools.verify_tuf_snapshot_renewal import (
                SnapshotRenewalVerificationError,
                verify_snapshot_renewal,
            )

            with self.assertRaisesRegex(SnapshotRenewalVerificationError, "target"):
                verify_snapshot_renewal(
                    source_repository=source,
                    renewed_repository=renewed,
                    report=workspace / "report.json",
                    root=root,
                    catalog=catalog,
                )

    def test_published_report_is_not_accepted_by_handoff_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            root, catalog, source, renewed = self._renew(workspace)
            report = workspace / "report.json"
            payload = json.loads(report.read_text(encoding="utf-8"))
            payload["published"] = True
            report.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            from maintenance.tools.verify_tuf_snapshot_renewal import (
                SnapshotRenewalVerificationError,
                verify_snapshot_renewal,
            )

            with self.assertRaisesRegex(SnapshotRenewalVerificationError, "publicado"):
                verify_snapshot_renewal(
                    source_repository=source,
                    renewed_repository=renewed,
                    report=report,
                    root=root,
                    catalog=catalog,
                )


if __name__ == "__main__":
    unittest.main()
