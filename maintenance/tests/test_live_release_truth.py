from __future__ import annotations

import unittest
from datetime import datetime, timezone

from maintenance.tools.verify_live_release_truth import validate_release_truth


class LiveReleaseTruthTests(unittest.TestCase):
    def document(self) -> dict:
        return {
            "snapshot_commit": "d" * 40,
            "status": {
                "main": "GREEN",
                "tuf": "HEALTHY",
                "external_public": "NO-GO",
            },
            "authorities": {
                "candidate_release": {
                    "audience": "owner-only",
                    "external_public_authorized": False,
                },
                "deployment": {
                    "tuf": {
                        "operational_status": "HEALTHY",
                        "timestamp_expiry": "2026-09-27T02:15:12Z",
                    }
                },
            },
        }

    def test_accepts_fresh_owner_only_no_go_truth(self) -> None:
        summary = validate_release_truth(
            self.document(),
            now=datetime(2026, 8, 28, tzinfo=timezone.utc),
        )
        self.assertEqual("d" * 40, summary["snapshot_commit"])
        self.assertEqual("owner-only", summary["release_audience"])
        self.assertEqual("NO-GO", summary["external_public"])
        self.assertGreater(summary["tuf_seconds_remaining"], 0)

    def test_rejects_expired_tuf_truth(self) -> None:
        with self.assertRaisesRegex(ValueError, "expired"):
            validate_release_truth(
                self.document(),
                now=datetime(2026, 9, 28, tzinfo=timezone.utc),
            )

    def test_rejects_external_public_authorization(self) -> None:
        document = self.document()
        document["authorities"]["candidate_release"][
            "external_public_authorized"
        ] = True
        with self.assertRaisesRegex(ValueError, "external-public"):
            validate_release_truth(
                document,
                now=datetime(2026, 8, 28, tzinfo=timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
