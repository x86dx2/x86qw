from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from maintenance.tools import verify_public_acceptance


def record(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "format": 1,
        "project": "x86qw",
        "candidate_version": "1.0.0-rc.1",
        "platform": "macos",
        "channel": "stable",
        "release": "latest",
        "profile": "complete",
        "catalog_sha256": "b" * 64,
        "bundle_sha256": "a" * 64,
        "verified": True,
        "full_lifecycle": {
            "launcher": "x86qw.sh",
            "operations": {
                "version": True,
                "changes": True,
                "migrate_dry_run": True,
                "update_dry_run": True,
                "update_apply": True,
                "update_idempotent": True,
                "verify": True,
                "uninstall": True,
                "uninstall_purge": True,
            },
            "personal_data_preserved_by_uninstall": True,
            "purge_removed_personal_data": True,
        },
    }
    value.update(overrides)
    return value


class VerifyPublicAcceptanceTests(unittest.TestCase):
    def write(self, value: object) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "public-acceptance.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_accepts_complete_record_for_the_expected_rc(self):
        path = self.write(record())
        result = verify_public_acceptance.verify_record(
            path,
            expected_version="1.0.0-rc.1",
            expected_receipt_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            expected_bundle_sha256="a" * 64,
            expected_catalog_sha256="b" * 64,
        )
        self.assertEqual("verified-public-acceptance", result["status"])
        self.assertEqual("a" * 64, result["bundle_sha256"])
        self.assertEqual("b" * 64, result["catalog_sha256"])

    def test_rejects_public_bytes_that_do_not_match_the_handoff(self):
        path = self.write(record())
        with self.assertRaises(verify_public_acceptance.PublicAcceptanceError):
            verify_public_acceptance.verify_record(
                path,
                expected_version="1.0.0-rc.1",
                expected_receipt_sha256="c" * 64,
                expected_bundle_sha256="a" * 64,
                expected_catalog_sha256="b" * 64,
            )
        with self.assertRaises(verify_public_acceptance.PublicAcceptanceError):
            verify_public_acceptance.verify_record(
                path,
                expected_version="1.0.0-rc.1",
                expected_bundle_sha256="c" * 64,
                expected_catalog_sha256="b" * 64,
            )
        with self.assertRaises(verify_public_acceptance.PublicAcceptanceError):
            verify_public_acceptance.verify_record(
                path,
                expected_version="1.0.0-rc.1",
                expected_bundle_sha256="a" * 64,
                expected_catalog_sha256="c" * 64,
            )

    def test_rejects_missing_or_false_lifecycle_operation(self):
        value = record()
        operations = value["full_lifecycle"]["operations"]
        assert isinstance(operations, dict)
        operations["update_idempotent"] = False
        with self.assertRaises(verify_public_acceptance.PublicAcceptanceError):
            verify_public_acceptance.verify_record(
                self.write(value), expected_version="1.0.0-rc.1",
            )

    def test_rejects_wrong_version_or_non_macos_record(self):
        for overrides in (
            {"candidate_version": "1.0.0"},
            {"platform": "linux"},
            {"verified": False},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(verify_public_acceptance.PublicAcceptanceError):
                    verify_public_acceptance.verify_record(
                        self.write(record(**overrides)), expected_version="1.0.0-rc.1",
                    )


if __name__ == "__main__":
    unittest.main()
