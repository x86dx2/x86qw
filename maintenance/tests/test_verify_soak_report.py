from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from maintenance.tools import verify_soak_report


class VerifySoakReportTests(unittest.TestCase):
    def _report(self, *, completed_at: str = "2026-08-08T00:00:00Z", issue_state: str = "closed") -> dict[str, object]:
        return {
            "format": 1,
            "project": "x86qw",
            "status": "passed",
            "candidate": {
                "commit": "a" * 40,
                "version": "1.0.0-rc.2",
                "candidate_json_sha256": "b" * 64,
                "bundle_sha256": "c" * 64,
            },
            "period": {
                "started_at": "2026-08-01T00:00:00Z",
                "completed_at": completed_at,
                "minimum_days": 7,
            },
            "issue": {
                "number": 143,
                "state": issue_state,
                "url": "https://github.com/x86dx2/x86qw/issues/143",
            },
            "gates": {
                "p0_p1_clear": True,
                "tuf_healthy": True,
                "public_install": True,
                "gameplay": True,
                "hosting": True,
            },
            "observations": [
                {"date": f"2026-08-{day:02d}", "status": "green"}
                for day in range(1, 9)
            ],
        }

    def _write_report(self, root: Path, report: dict[str, object]) -> Path:
        path = root / "report.json"
        path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def _verify(self, report: Path) -> dict[str, object]:
        return verify_soak_report.verify_report(
            report,
            expected_commit="a" * 40,
            expected_version="1.0.0-rc.2",
            expected_candidate_json_sha256="b" * 64,
            expected_bundle_sha256="c" * 64,
            expected_issue_number=143,
            minimum_days=7,
        )

    def test_accepts_closed_green_soak_bound_to_exact_rc(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = self._write_report(Path(temporary), self._report())
            verified = self._verify(report)

        self.assertEqual("passed", verified["status"])
        self.assertEqual(8, len(verified["observations"]))

    def test_rejects_soak_shorter_than_minimum_duration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = self._write_report(
                Path(temporary),
                self._report(completed_at="2026-08-07T23:59:59Z"),
            )
            with self.assertRaisesRegex(verify_soak_report.SoakReportError, "duração"):
                self._verify(report)

    def test_rejects_soak_that_claims_a_future_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = self._write_report(
                Path(temporary),
                self._report(completed_at="2099-08-08T00:00:00Z"),
            )
            with self.assertRaisesRegex(verify_soak_report.SoakReportError, "futuro"):
                self._verify(report)

    def test_rejects_soak_with_a_missing_daily_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = self._report()
            payload["observations"] = [
                {"date": f"2026-08-{day:02d}", "status": "green"}
                for day in (1, 2, 3, 4, 5, 6, 8)
            ]
            report = self._write_report(Path(temporary), payload)
            with self.assertRaisesRegex(verify_soak_report.SoakReportError, "ausentes"):
                self._verify(report)

    def test_rejects_open_soak_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = self._write_report(
                Path(temporary), self._report(issue_state="open"),
            )
            with self.assertRaisesRegex(verify_soak_report.SoakReportError, "issue"):
                self._verify(report)

    def test_rejects_failed_operational_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = self._report()
            gates = dict(payload["gates"])
            gates["tuf_healthy"] = False
            payload["gates"] = gates
            report = self._write_report(Path(temporary), payload)
            with self.assertRaisesRegex(verify_soak_report.SoakReportError, "TUF"):
                self._verify(report)
