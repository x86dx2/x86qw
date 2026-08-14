from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from maintenance.tools import build_soak_report


class BuildSoakReportTests(unittest.TestCase):
    def _evidence(self, *, extra: str = "") -> str:
        payload = (
            '{"2026-08-01":"https://github.com/x86dx2/x86qw/issues/143#day-1",'
            '"2026-08-02":"https://github.com/x86dx2/x86qw/issues/143#day-2"'
            f"{extra}}}"
        )
        return base64.b64encode(payload.encode("utf-8")).decode("ascii")

    def _gates(self) -> dict[str, bool]:
        return {
            "p0_p1_clear": True,
            "tuf_healthy": True,
            "public_install": True,
            "gameplay": True,
            "hosting": True,
        }

    def test_builds_format_two_report_with_daily_evidence(self) -> None:
        report = build_soak_report.build_report(
            candidate_commit="a" * 40,
            candidate_version="1.0.0-rc.2",
            candidate_sha256="b" * 64,
            bundle_sha256="c" * 64,
            started_at="2026-08-01T00:00:00Z",
            completed_at="2026-08-08T00:00:00Z",
            issue_number="143",
            issue_state="closed",
            issue_url="https://github.com/x86dx2/x86qw/issues/143",
            observed_dates="2026-08-01,2026-08-02",
            hardware="MacBook Pro Mac15,6 / Apple M3 Pro",
            observation_evidence_b64=self._evidence(),
            gates=self._gates(),
        )

        self.assertEqual(2, report["format"])
        self.assertEqual("macos-arm64", report["environment"]["platform"])
        self.assertEqual(2, len(report["observations"]))
        self.assertTrue(report["observations"][0]["evidence"].startswith("https://"))

    def test_rejects_duplicate_json_evidence_keys(self) -> None:
        with self.assertRaisesRegex(build_soak_report.SoakReportBuildError, "duplicada"):
            build_soak_report.build_report(
                candidate_commit="a" * 40,
                candidate_version="1.0.0-rc.2",
                candidate_sha256="b" * 64,
                bundle_sha256="c" * 64,
                started_at="2026-08-01T00:00:00Z",
                completed_at="2026-08-08T00:00:00Z",
                issue_number="143",
                issue_state="closed",
                issue_url="https://github.com/x86dx2/x86qw/issues/143",
                observed_dates="2026-08-01,2026-08-02",
                hardware="MacBook Pro Mac15,6 / Apple M3 Pro",
                observation_evidence_b64=self._evidence(
                    extra=',"2026-08-01":"https://github.com/x86dx2/x86qw/issues/143#duplicate"'
                ),
                gates=self._gates(),
            )

    def test_rejects_evidence_dates_that_do_not_match_observations(self) -> None:
        with self.assertRaisesRegex(build_soak_report.SoakReportBuildError, "corresponder"):
            build_soak_report.build_report(
                candidate_commit="a" * 40,
                candidate_version="1.0.0-rc.2",
                candidate_sha256="b" * 64,
                bundle_sha256="c" * 64,
                started_at="2026-08-01T00:00:00Z",
                completed_at="2026-08-08T00:00:00Z",
                issue_number="143",
                issue_state="closed",
                issue_url="https://github.com/x86dx2/x86qw/issues/143",
                observed_dates="2026-08-01",
                hardware="MacBook Pro Mac15,6 / Apple M3 Pro",
                observation_evidence_b64=self._evidence(),
                gates=self._gates(),
            )

    def test_cli_writes_the_same_canonical_report_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report.json"
            environment = {
                "CANDIDATE_COMMIT": "a" * 40,
                "CANDIDATE_VERSION": "1.0.0-rc.2",
                "CANDIDATE_SHA256": "b" * 64,
                "BUNDLE_SHA256": "c" * 64,
                "STARTED_AT": "2026-08-01T00:00:00Z",
                "COMPLETED_AT": "2026-08-08T00:00:00Z",
                "ISSUE_NUMBER": "143",
                "ISSUE_STATE": "closed",
                "ISSUE_URL": "https://github.com/x86dx2/x86qw/issues/143",
                "OBSERVED_DATES": "2026-08-01,2026-08-02",
                "HARDWARE": "MacBook Pro Mac15,6 / Apple M3 Pro",
                "OBSERVATION_EVIDENCE_B64": self._evidence(),
                **{key.upper(): str(value).lower() for key, value in self._gates().items()},
            }
            with mock.patch.dict(build_soak_report.os.environ, environment, clear=False):
                self.assertEqual(0, build_soak_report.main(["--output", str(output)]))
            self.assertEqual(2, json.loads(output.read_text(encoding="utf-8"))["format"])


if __name__ == "__main__":
    unittest.main()
