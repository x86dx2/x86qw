from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from x86qw_runtime.contracts import JsonOutputError, make_json_output, redact_json
from x86qw_runtime.doctor import CHECK_IDS, diagnose, render_doctor_report


def _check(report: dict[str, object], check_id: str) -> dict[str, object]:
    matches = [item for item in report["checks"] if item["id"] == check_id]
    if len(matches) != 1:
        raise AssertionError(f"expected one {check_id} check, got {matches!r}")
    return matches[0]


class DiagnoseTests(unittest.TestCase):
    def test_missing_target_is_unhealthy_and_creates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "missing-install"
            report = diagnose(target)

        self.assertFalse(target.exists())
        self.assertFalse(report["healthy"])
        self.assertEqual("owner-only", report["audience"])
        self.assertEqual(str(target), report["target"])
        self.assertEqual(list(CHECK_IDS), [item["id"] for item in report["checks"]])
        self.assertEqual("fail", _check(report, "installation")["status"])
        self.assertEqual("skip", _check(report, "network")["status"])

    def test_present_state_marks_installation_ok(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            state = target / ".x86qw" / "state.json"
            state.parent.mkdir(parents=True)
            state.write_text("{}", encoding="utf-8")
            before = {path.relative_to(target) for path in target.rglob("*")}
            report = diagnose(target, catalog_commands=("play", "doctor"))
            after = {path.relative_to(target) for path in target.rglob("*")}

        self.assertEqual(before, after)
        self.assertEqual("ok", _check(report, "installation")["status"])
        self.assertEqual("ok", _check(report, "catalog")["status"])

    def test_expired_local_trust_cache_is_a_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            state = target / ".x86qw" / "state.json"
            state.parent.mkdir(parents=True)
            state.write_text("{}", encoding="utf-8")
            timestamp = Path(temporary) / "timestamp.json"
            timestamp.write_text(
                json.dumps({
                    "signed": {
                        "_type": "timestamp",
                        "expires": "2020-01-01T00:00:00Z",
                        "version": 18,
                    },
                    "signatures": [],
                }),
                encoding="utf-8",
            )
            report = diagnose(
                target,
                catalog_commands=("play", "doctor"),
                trust_timestamp_path=timestamp,
                now=datetime(2026, 8, 16, tzinfo=timezone.utc),
            )

        self.assertEqual("warn", _check(report, "trust")["status"])
        self.assertIn("cache TUF local", _check(report, "trust")["summary"])
        self.assertIn("expirado", _check(report, "trust")["summary"])
        self.assertTrue(report["healthy"])

    def test_injected_network_status_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = diagnose(
                Path(temporary) / "missing",
                network=("ok", "endpoint alcançável"),
            )
        self.assertEqual("ok", _check(report, "network")["status"])
        self.assertEqual("endpoint alcançável", _check(report, "network")["summary"])


class DoctorContractTests(unittest.TestCase):
    def test_json_envelope_accepts_a_closed_doctor_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = diagnose(Path(temporary) / "missing")
        output = make_json_output("doctor", data=report)
        self.assertEqual("doctor", output.command)
        self.assertTrue(output.ok)
        self.assertFalse(output.dry_run)
        self.assertEqual(report["target"], output.data["target"])
        self.assertEqual(list(CHECK_IDS), [item["id"] for item in output.data["checks"]])

    def test_json_contract_uses_the_same_check_ids(self) -> None:
        from x86qw_runtime.contracts.output import COMMAND_DATA_SCHEMAS

        self.assertEqual(CHECK_IDS, COMMAND_DATA_SCHEMAS["doctor"]["check_ids"])

    def test_unknown_check_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = diagnose(Path(temporary) / "missing")
        report["checks"][0]["id"] = "telemetry"
        with self.assertRaises(JsonOutputError):
            make_json_output("doctor", data=report)

    def test_summaries_are_redacted_in_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = diagnose(
                Path(temporary) / "missing",
                network=("skip", "token=super-secret"),
            )
        output = make_json_output("doctor", data=report)
        self.assertNotIn("super-secret", output.to_json())
        self.assertIn("[REDACTED]", output.to_json())

    def test_human_report_names_the_failed_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            text = render_doctor_report(diagnose(Path(temporary) / "missing"))
        self.assertIn("installation", text)
        self.assertIn("Problemas encontrados", text)
        self.assertNotIn("password", text)
