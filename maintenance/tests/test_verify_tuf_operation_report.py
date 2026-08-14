from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from maintenance.tools import verify_tuf_operation_report


class VerifyTufOperationReportTests(unittest.TestCase):
    def _candidate(self, root: Path) -> Path:
        candidate = root / "candidate"
        candidate.mkdir()
        catalog = candidate / "catalog.json"
        catalog.write_bytes(b'{"project":"x86qw","version":"1.0.0"}\n')
        (candidate / "candidate.json").write_text(
            json.dumps({
                "project": "x86qw",
                "version": "1.0.0",
                "commit": "a" * 40,
            }) + "\n",
            encoding="utf-8",
        )
        return candidate

    def _report(self, candidate: Path) -> dict[str, object]:
        catalog = candidate / "catalog.json"
        digest = hashlib.sha256(catalog.read_bytes()).hexdigest()
        return {
            "checked_at": "2026-08-14T08:10:11.227270Z",
            "current_metadata_version": 1,
            "expiry_failure_detected": True,
            "format": 1,
            "mode": "offline-renewal-expiry-recovery",
            "operation": {
                "custody_host": "offline-signer-01",
                "key_scope": "root-and-targets-offline",
                "operator": "release-operator",
                "timestamp_sla_hours": 6,
            },
            "project": "x86qw",
            "published": False,
            "recovery_verified": True,
            "renewed_metadata_version": 2,
            "root_unchanged": True,
            "status": "drill-passed",
            "target": {
                "path": f"catalog/{digest}.catalog.json",
                "sha256": digest,
                "size": catalog.stat().st_size,
            },
            "target_unchanged": True,
        }

    def test_accepts_report_bound_to_candidate_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            report = root / "report.json"
            report.write_text(json.dumps(self._report(candidate)), encoding="utf-8")

            result = verify_tuf_operation_report.verify_report(
                candidate=candidate,
                report=report,
            )

            self.assertEqual("drill-passed", result["status"])
            self.assertEqual("offline-signer-01", result["operation"]["custody_host"])

    def test_rejects_historical_report_without_operation_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            value = self._report(candidate)
            del value["operation"]
            report = root / "report.json"
            report.write_text(json.dumps(value), encoding="utf-8")

            with self.assertRaises(verify_tuf_operation_report.TufOperationReportError):
                verify_tuf_operation_report.verify_report(candidate=candidate, report=report)

    def test_rejects_report_when_target_digest_differs_from_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._candidate(root)
            value = self._report(candidate)
            value["target"]["sha256"] = "0" * 64  # type: ignore[index]
            report = root / "report.json"
            report.write_text(json.dumps(value), encoding="utf-8")

            with self.assertRaises(verify_tuf_operation_report.TufOperationReportError):
                verify_tuf_operation_report.verify_report(candidate=candidate, report=report)


if __name__ == "__main__":
    unittest.main()
