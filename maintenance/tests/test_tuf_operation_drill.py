from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
