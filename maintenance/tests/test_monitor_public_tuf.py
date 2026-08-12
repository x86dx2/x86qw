from __future__ import annotations

import json
import unittest

from maintenance.tools import monitor_public_tuf


class PublicTufMonitorTests(unittest.TestCase):
    def test_default_warning_window_fits_the_timestamp_lease_policy(self) -> None:
        self.assertEqual(6, monitor_public_tuf.DEFAULT_WARNING_HOURS)

    def test_metadata_payload_selects_consistent_snapshot_role(self) -> None:
        records = {
            "https://example.invalid/metadata/timestamp.json": b"timestamp",
            "https://example.invalid/metadata/11.snapshot.json": b"snapshot",
            "https://example.invalid/metadata/11.targets.json": b"targets",
        }
        self.assertEqual(b"snapshot", monitor_public_tuf._metadata_payload(records, "snapshot"))

    def test_metadata_payload_rejects_ambiguous_role(self) -> None:
        records = {
            "https://example.invalid/metadata/11.snapshot.json": b"one",
            "https://example.invalid/metadata/12.snapshot.json": b"two",
        }
        with self.assertRaises(monitor_public_tuf.PublicTufMonitorError):
            monitor_public_tuf._metadata_payload(records, "snapshot")

    def test_expiry_parser_requires_version_and_timezone(self) -> None:
        payload = json.dumps({
            "signed": {"version": 11, "expires": "2026-08-17T23:10:15Z"},
        }).encode("utf-8")
        version, expiry = monitor_public_tuf._expires(payload, "snapshot")
        self.assertEqual(11, version)
        self.assertEqual("2026-08-17T23:10:15+00:00", expiry.isoformat())

        invalid = json.dumps({
            "signed": {"version": 11, "expires": "2026-08-17T23:10:15"},
        }).encode("utf-8")
        with self.assertRaises(monitor_public_tuf.PublicTufMonitorError):
            monitor_public_tuf._expires(invalid, "snapshot")


if __name__ == "__main__":
    unittest.main()
