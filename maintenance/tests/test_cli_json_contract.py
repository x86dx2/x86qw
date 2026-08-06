from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from x86qw_runtime.contracts import (
    ExitCode,
    JsonCommandOutput,
    JsonOutputError,
    make_json_output,
    parse_json_output,
    redact_json,
)


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "x86qw_json_manager_test", ROOT / "dist/installer/bin/manager.py",
)
manager = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = manager
SPEC.loader.exec_module(manager)


class CliJsonContractTests(unittest.TestCase):
    def setUp(self) -> None:
        manager.console.configure(verbose=False, no_color=True)

    def invoke(self, arguments: list[str]) -> tuple[int, object, str]:
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = manager.main(arguments)
        return result, parse_json_output(output.getvalue()), errors.getvalue()

    @staticmethod
    def status_data() -> dict[str, object]:
        return {
            "project": "x86qw",
            "target": "/tmp/x86qw",
            "installation": "present",
            "state": "present",
            "sessions": [
                {"session_id": "session-b", "status": "running", "command": "host"},
                {"session_id": "session-a", "status": "stopped", "command": "play"},
            ],
        }

    @staticmethod
    def hub_data() -> dict[str, object]:
        return {
            "target": "/tmp/x86qw",
            "servers": [
                {
                    "address": "b.example:27500",
                    "title": "B server",
                    "mode": "duel",
                    "map": "dm6",
                    "players": {"humans": 1, "bots": 2},
                    "qtv_stream": "2@qtv.example:28000",
                },
                {
                    "address": "a.example:27500",
                    "title": "A server",
                    "mode": "ctf",
                    "map": "dm3",
                    "players": {"humans": 0, "bots": 0},
                    "qtv_stream": None,
                },
            ],
        }

    @staticmethod
    def plan_data() -> dict[str, object]:
        return {
            "target": "/tmp/x86qw",
            "status": "planned",
            "operations": [{
                "kind": "Componente",
                "item": "KTX",
                "installed": "1.0.0",
                "available": "1.1.0",
                "action": "Atualizar",
                "size": 123,
            }],
        }

    def test_version_json_is_one_document_without_human_diagnostics(self) -> None:
        result, document, errors = self.invoke(["version", "--json"])
        self.assertEqual(0, result)
        self.assertEqual("version", document.command)
        self.assertTrue(document.ok)
        self.assertFalse(document.dry_run)
        self.assertEqual(manager.application_version(), document.data["version"])
        self.assertEqual("x86qw", document.data["project"])
        self.assertEqual("", errors)

    def test_help_and_version_do_not_load_catalogs(self) -> None:
        fail_catalog = mock.Mock(side_effect=AssertionError("catalog loaded during help/version"))
        with mock.patch.object(manager, "load_component_catalog", fail_catalog), mock.patch.object(
            manager, "load_runtime_catalog", fail_catalog,
        ), mock.patch.object(manager, "read_zipapp_json", fail_catalog):
            help_output = io.StringIO()
            with contextlib.redirect_stdout(help_output), self.assertRaises(SystemExit) as raised:
                manager.main(["--help"])
        self.assertEqual(0, raised.exception.code)
        self.assertIn("usage:", help_output.getvalue())

        with mock.patch.object(manager, "load_component_catalog", fail_catalog), mock.patch.object(
            manager, "load_runtime_catalog", fail_catalog,
        ), mock.patch.object(manager, "read_zipapp_json", fail_catalog):
            version_flag_output = io.StringIO()
            with contextlib.redirect_stdout(version_flag_output), self.assertRaises(SystemExit) as raised:
                manager.main(["--version"])
        self.assertEqual(0, raised.exception.code)
        self.assertIn(manager.application_version(), version_flag_output.getvalue())

        with mock.patch.object(manager, "load_component_catalog", fail_catalog), mock.patch.object(
            manager, "load_runtime_catalog", fail_catalog,
        ), mock.patch.object(manager, "read_zipapp_json", fail_catalog):
            version_output = io.StringIO()
            with contextlib.redirect_stdout(version_output):
                result = manager.main(["version"])
        self.assertEqual(0, result)
        self.assertEqual(f"x86QW {manager.application_version()}\n", version_output.getvalue())

    def test_command_data_golden_documents_are_canonical_and_sorted(self) -> None:
        version = make_json_output(
            "version", data={"version": "1.0.0-rc.1", "project": "x86qw"},
        )
        self.assertEqual(
            '{"command":"version","data":{"project":"x86qw","version":"1.0.0-rc.1"},'
            '"dry_run":false,"errors":[],"exit_code":0,"ok":true,"schema_version":1}\n',
            version.to_json(),
        )

        status = make_json_output("status", data=self.status_data())
        self.assertEqual(["session-a", "session-b"], [
            item["session_id"] for item in status.data["sessions"]  # type: ignore[index]
        ])
        self.assertEqual(status.to_json(), make_json_output("status", data=self.status_data()).to_json())

        hub = make_json_output("hub", data=self.hub_data())
        self.assertEqual(["a.example:27500", "b.example:27500"], [
            item["address"] for item in hub.data["servers"]  # type: ignore[index]
        ])
        self.assertEqual(
            parse_json_output(hub.to_json()).to_document(),
            hub.to_document(),
        )

        verify = make_json_output(
            "verify", data={"target": "/tmp/x86qw", "verified": True},
        )
        self.assertEqual({"target": "/tmp/x86qw", "verified": True}, verify.data)

    def test_application_version_accepts_semver_prerelease_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            version_file = Path(temporary) / "VERSION"
            version_file.write_text("1.0.0-rc.1\n", encoding="ascii")
            with mock.patch.object(manager, "ZIPAPP_PATH", None), mock.patch.object(
                manager, "DEVELOPMENT_VERSION_FILE", version_file,
            ):
                self.assertEqual("1.0.0-rc.1", manager.application_version())

    def test_stable_release_comparison_obeys_semver_prerelease_order(self) -> None:
        self.assertTrue(manager.Installer.release_is_newer(
            "1.0.0", "1.0.0-rc.1", "stable",
        ))
        self.assertFalse(manager.Installer.release_is_newer(
            "1.0.0-rc.1", "1.0.0", "stable",
        ))

    def test_status_json_reports_missing_target_without_loading_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "installation"
            result, document, errors = self.invoke([
                "status", "--json", str(target),
            ])
        self.assertEqual(0, result)
        self.assertEqual("status", document.command)
        self.assertTrue(document.ok)
        self.assertEqual("missing", document.data["installation"])
        self.assertEqual(str(target.resolve()), document.data["target"])
        self.assertEqual("", errors)

    def test_status_json_preserves_closed_session_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "installation"
            sessions = target / ".x86qw" / "sessions" / "session-1"
            sessions.mkdir(parents=True)
            (sessions / "session.json").write_text(json.dumps({
                "session_id": "session-1",
                "status": "running",
                "command": "host",
            }), encoding="utf-8")
            result, document, errors = self.invoke(["status", "--json", str(target)])

        self.assertEqual(0, result)
        self.assertEqual([{
            "session_id": "session-1", "status": "running", "command": "host",
        }], document.data["sessions"])  # type: ignore[index]
        self.assertEqual("", errors)

    def test_command_data_rejects_missing_extra_and_invalid_fields(self) -> None:
        invalid = (
            ("version", {"project": "x86qw", "version": "1.0.0", "extra": True}),
            ("version", {"project": "x86qw", "version": "not-semver"}),
            ("version", {"project": "x86qw", "version": "1.0.0\x7f"}),
            ("status", {"project": "x86qw", "target": "/tmp/x", "installation": "present", "state": "present"}),
            ("status", {**self.status_data(), "installation": "unknown"}),
            ("status", {**self.status_data(), "state": []}),
            ("verify", {"target": "/tmp/x"}),
            ("verify", {"target": "/tmp/x", "verified": False}),
        )
        for command, data in invalid:
            with self.subTest(command=command, data=data), self.assertRaises(JsonOutputError):
                make_json_output(command, data=data)

    def test_status_and_hub_reject_adversarial_nested_shapes(self) -> None:
        duplicate_session = self.status_data()
        duplicate_session["sessions"] = [
            {"session_id": "same", "status": "running", "command": "host"},
            {"session_id": "same", "status": "stopped", "command": "play"},
        ]
        extra_session = self.status_data()
        extra_session["sessions"] = [{
            "session_id": "session-a", "status": "running", "command": "host", "pid": 42,
        }]
        extra_server = self.hub_data()
        extra_server["servers"] = [{**self.hub_data()["servers"][0], "token": "secret"}]  # type: ignore[index]
        raw_server = self.hub_data()
        raw_server["servers"] = [{
            "address": "a.example:27500",
            "players": [],
            "mode": "duel",
            "settings": {"map": "dm6", "hostname": "raw"},
            "qtv_stream": {"url": "2@qtv.example:28000"},
        }]
        invalid_players = self.hub_data()
        invalid_players["servers"] = [{
            **self.hub_data()["servers"][0],
            "players": {"humans": True, "bots": 0},
        }]  # type: ignore[index]
        invalid_address = self.hub_data()
        invalid_address["servers"] = [{
            **self.hub_data()["servers"][0], "address": "+exec bad.cfg",
        }]  # type: ignore[index]
        invalid_qtv = self.hub_data()
        invalid_qtv["servers"] = [{
            **self.hub_data()["servers"][0], "qtv_stream": "https://user:pass@example.invalid/stream",
        }]  # type: ignore[index]
        for command, data in (
            ("status", duplicate_session),
            ("status", extra_session),
            ("hub", extra_server),
            ("hub", raw_server),
            ("hub", invalid_players),
            ("hub", invalid_address),
            ("hub", invalid_qtv),
        ):
            with self.subTest(command=command), self.assertRaises(JsonOutputError):
                make_json_output(command, data=data)

    def test_hub_endpoint_grammar_and_unicode_controls_are_closed(self) -> None:
        valid = self.hub_data()
        valid["servers"] = [{
            **self.hub_data()["servers"][0],  # type: ignore[index]
            "address": "[2001:db8:0:0:0:0:0:1]:27500",
            "title": "São Paulo 🏁",
            "qtv_stream": "2@[2001:db8::2]:28000",
        }]
        output = make_json_output("hub", data=valid)
        server = output.data["servers"][0]  # type: ignore[index]
        self.assertEqual("[2001:db8::1]:27500", server["address"])
        self.assertEqual("2@[2001:db8::2]:28000", server["qtv_stream"])
        self.assertEqual("São Paulo 🏁", server["title"])

        for address in (
            ":::::27500", "[]:27500", "host::27500", ":27500",
            "2001:db8::1:27500", "[not-an-ip]:27500",
            "server.example:0", "server.example:65536",
            "999.999.999.999:27500", "256.0.0.1:27500",
            "01.02.03.04:27500", "123.456:27500",
        ):
            invalid = self.hub_data()
            invalid["servers"] = [{
                **self.hub_data()["servers"][0],  # type: ignore[index]
                "address": address,
            }]
            with self.subTest(address=address), self.assertRaises(JsonOutputError):
                make_json_output("hub", data=invalid)

        for stream in ("@qtv.example:28000", "1@", "1@[]:28000", "1@host:0", "1@a@b:28000"):
            invalid = self.hub_data()
            invalid["servers"] = [{
                **self.hub_data()["servers"][0],  # type: ignore[index]
                "qtv_stream": stream,
            }]
            with self.subTest(stream=stream), self.assertRaises(JsonOutputError):
                make_json_output("hub", data=invalid)

        for field in ("title", "mode", "map"):
            for character in ("\u202e", "\u2066", "\u200b", "\u200d", "\ufffd", "\ud800"):
                invalid = self.hub_data()
                invalid["servers"] = [{
                    **self.hub_data()["servers"][0],  # type: ignore[index]
                    field: f"safe{character}text",
                }]
                with self.subTest(field=field, character=hex(ord(character))):
                    with self.assertRaises(JsonOutputError):
                        make_json_output("hub", data=invalid)

    def test_failed_output_has_empty_data_and_a_stable_error(self) -> None:
        failed = make_json_output(
            "verify",
            ok=False,
            data={},
            exit_code=ExitCode.FAILURE,
            errors=({"code": "integrity", "message": "hash mismatch"},),
        )
        self.assertEqual({}, failed.data)
        self.assertEqual(ExitCode.FAILURE, failed.exit_code)
        with self.assertRaises(JsonOutputError):
            make_json_output(
                "verify",
                ok=False,
                data={"target": "/tmp/x86qw", "verified": False},
                exit_code=ExitCode.FAILURE,
                errors=({"code": "integrity", "message": "hash mismatch"},),
            )

    def test_generic_redaction_still_masks_sensitive_fields(self) -> None:
        self.assertEqual(
            {"password": "[REDACTED]", "session_id": "[REDACTED]"},
            redact_json({"password": "secret", "session_id": "session-secret"}),
        )

    def test_dry_run_plan_contract_is_closed_for_every_maintenance_command(self) -> None:
        for command in ("repair", "update", "upgrade"):
            with self.subTest(command=command):
                output = make_json_output(command, dry_run=True, data=self.plan_data())
                self.assertEqual("planned", output.data["status"])

                extra_operation = self.plan_data()
                extra_operation["operations"] = [{
                    **self.plan_data()["operations"][0], "secret": "never-emit",
                }]  # type: ignore[index]
                with self.assertRaises(JsonOutputError):
                    make_json_output(command, dry_run=True, data=extra_operation)

                with self.assertRaises(JsonOutputError):
                    make_json_output(
                        command,
                        dry_run=True,
                        data={"target": "/tmp/x86qw", "status": "planned", "operations": []},
                    )

    def test_verify_json_returns_nonzero_envelope_for_invalid_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "installation"
            result, document, errors = self.invoke([
                "verify", "--json", str(target),
            ])
        self.assertNotEqual(0, result)
        self.assertEqual("verify", document.command)
        self.assertFalse(document.ok)
        self.assertNotEqual(0, document.exit_code)
        self.assertTrue(document.errors)
        self.assertEqual("", errors)

    def test_hub_json_projects_raw_api_rows_to_the_public_shape(self) -> None:
        raw_server = {
            "address": "server.example:27500",
            "players": [{"is_bot": False}, {"is_bot": True}, {"is_bot": False}],
            "mode": "duel",
            "settings": {"map": "dm6", "hostname": "Raw Hub Title"},
            "qtv_stream": {"url": "2@qtv.example:28000"},
            "token": "must-not-cross-boundary",
        }
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            manager.Installer, "validate_target", return_value=None,
        ), mock.patch.object(
            manager.Installer, "hub_servers", return_value=[raw_server],
        ):
            result, document, errors = self.invoke([
                "hub", "--json", str(Path(temporary) / "installation"),
            ])

        self.assertEqual(0, result)
        self.assertTrue(document.ok)
        self.assertEqual(
            {
                "address": "server.example:27500",
                "title": "Raw Hub Title",
                "mode": "duel",
                "map": "dm6",
                "players": {"humans": 2, "bots": 1},
                "qtv_stream": "2@qtv.example:28000",
            },
            document.data["servers"][0],  # type: ignore[index]
        )
        self.assertNotIn("token", document.to_json())
        self.assertEqual("", errors)

    def test_dry_run_commands_are_declared_and_json_is_deterministic(self) -> None:
        for command in ("repair", "update", "upgrade"):
            with self.subTest(command=command):
                namespace = manager.parse_arguments(
                    [command, "--dry-run", "--json"], ROOT,
                )
                self.assertTrue(namespace.dry_run)
                self.assertTrue(namespace.json)

    def test_dry_run_json_uses_structured_operations_without_human_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "installation"

            def fake_action(
                options: object,
                project_root: Path,
                *,
                plan_sink: list[dict[str, object]] | None = None,
            ) -> int:
                assert plan_sink is not None
                plan_sink.append({
                    "kind": "Componente",
                    "item": "KTX",
                    "installed": "1.0.0",
                    "available": "1.1.0",
                    "action": "Atualizar",
                    "size": 123,
                })
                return 0

            with mock.patch.object(manager, "execute_manager_action", side_effect=fake_action):
                result, document, errors = self.invoke([
                    "repair", "--dry-run", "--json", str(target),
                ])

        self.assertEqual(0, result)
        self.assertTrue(document.ok)
        self.assertTrue(document.dry_run)
        self.assertEqual("planned", document.data["status"])
        self.assertEqual(1, len(document.data["operations"]))
        self.assertNotIn("plan_output", document.data)
        self.assertNotIn("Atualizar", document.to_json().split('"operations"', 1)[0])
        self.assertEqual("", errors)

    def test_json_flag_is_rejected_for_gameplay_actions(self) -> None:
        with self.assertRaises(SystemExit):
            manager.parse_arguments(["play", "--json"], ROOT)

    def test_malformed_json_constructor_values_fail_with_typed_errors(self) -> None:
        with self.assertRaises(JsonOutputError):
            JsonCommandOutput(command=["status"], ok=True, data={})  # type: ignore[arg-type]
        with self.assertRaises(JsonOutputError):
            JsonCommandOutput(
                command="repair",
                ok=True,
                dry_run=True,
                data={"target": "/tmp/x", "status": [], "operations": []},  # type: ignore[dict-item]
            )
        with self.assertRaises(JsonOutputError):
            JsonCommandOutput(command="status", ok=True, data={}, errors=None)  # type: ignore[arg-type]

    def test_json_maintenance_output_requires_dry_run(self) -> None:
        with self.assertRaises(JsonOutputError):
            JsonCommandOutput(command="repair", ok=True, data={})

    def test_json_document_with_non_text_extra_field_has_typed_error(self) -> None:
        document = {
            "schema_version": 1,
            "command": "status",
            "ok": True,
            "exit_code": 0,
            "dry_run": False,
            "data": {},
            "errors": [],
            7: "unexpected",
        }
        with self.assertRaises(JsonOutputError):
            JsonCommandOutput.from_document(document)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
