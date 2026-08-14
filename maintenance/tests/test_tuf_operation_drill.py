from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from maintenance.tests import trust_support  # Loads the pinned TUF wheels.
from maintenance.tools import tuf_operation_drill


def write_role(path: Path, *, version: int, expires: str) -> None:
    path.write_text(json.dumps({
        "signed": {"version": version, "expires": expires},
        "signatures": [],
    }), encoding="utf-8")


class TufOperationDrillTests(unittest.TestCase):
    def repo(self, expires: str) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "repository"
        (root / "metadata").mkdir(parents=True)
        (root / "targets" / "catalog").mkdir(parents=True)
        for role in ("targets", "snapshot"):
            write_role(root / "metadata" / f"7.{role}.json", version=7, expires=expires)
        write_role(root / "metadata" / "timestamp.json", version=7, expires=expires)
        (root / "metadata" / "1.root.json").write_text("{}", encoding="utf-8")
        (root / "targets" / "catalog" / "abc.catalog.json").write_text(
            '{"project":"x86qw"}', encoding="utf-8",
        )
        return root

    def test_lease_status_accepts_a_future_timestamp(self):
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        result = tuf_operation_drill.lease_status(
            self.repo(future), warning_hours=6,
        )
        self.assertEqual("healthy", result["status"])
        self.assertEqual(7, result["versions"]["timestamp"])

    def test_lease_status_detects_expired_metadata(self):
        expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        with self.assertRaises(tuf_operation_drill.TufDrillError):
            tuf_operation_drill.lease_status(self.repo(expired), warning_hours=6)

    def test_target_identity_requires_exact_unchanged_bytes(self):
        left = {"size": 3, "sha256": "abc"}
        right = {"size": 3, "sha256": "abc"}
        self.assertTrue(tuf_operation_drill.target_unchanged(left, right))
        self.assertFalse(tuf_operation_drill.target_unchanged(left, {"size": 4, "sha256": "abc"}))

    def test_operation_context_requires_custodian_host_and_sla(self):
        result = tuf_operation_drill.operation_context(
            operator="release-operator",
            custody_host="offline-signer-01",
            sla_hours=6,
        )
        self.assertEqual(
            {
                "operator": "release-operator",
                "custody_host": "offline-signer-01",
                "timestamp_sla_hours": 6,
                "key_scope": "root-and-targets-offline",
            },
            result,
        )
        for kwargs in (
            {"operator": "", "custody_host": "offline-signer-01", "sla_hours": 6},
            {"operator": "release-operator", "custody_host": "", "sla_hours": 6},
            {"operator": "release-operator", "custody_host": "offline-signer-01", "sla_hours": 0},
            {"operator": "release-operator", "custody_host": "offline-signer-01", "sla_hours": 8761},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(tuf_operation_drill.TufDrillError):
                    tuf_operation_drill.operation_context(**kwargs)

    def test_run_drill_records_each_role_version(self) -> None:
        from maintenance.tools.generate_trust_metadata import (
            generate_repository,
            initialize_root,
        )

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            key_dir = workspace / "keys"
            root = workspace / "root.json"
            catalog = workspace / "catalog.json"
            repository = workspace / "repository"
            output = workspace / "report.json"
            catalog.write_bytes(
                (Path(__file__).resolve().parents[2] / "site/public/api/v1/catalog.json").read_bytes()
            )
            initialize_root(key_dir, root)
            generate_repository(key_dir, root, catalog, repository, version=1)

            report = tuf_operation_drill.run_drill(
                key_dir=key_dir,
                root=root,
                catalog=catalog,
                repository=repository,
                output=output,
                operator="release-operator",
                custody_host="offline-signer-01",
                sla_hours=6,
            )

            self.assertEqual(2, report["format"])
            self.assertEqual(
                {
                    role: {"current": 1, "renewed": 2}
                    for role in ("timestamp", "snapshot", "targets")
                },
                report["role_versions"],
            )


if __name__ == "__main__":
    unittest.main()
