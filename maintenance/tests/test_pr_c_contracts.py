"""RED tests for the issue #53 contract freeze."""

from __future__ import annotations

import importlib
import json
import tempfile
import unittest
import contextlib
import io
from pathlib import Path

versioning = importlib.import_module("x86qw_runtime.versioning")
try:
    contracts = importlib.import_module("x86qw_runtime.contracts")
except ModuleNotFoundError:
    contracts = None


class SemVerContractTests(unittest.TestCase):
    def test_rc_is_valid_and_orders_before_stable(self) -> None:
        self.assertIsNotNone(getattr(versioning, "SemVer", None))
        self.assertEqual(str(versioning.parse_semver("1.0.0-rc.1")), "1.0.0-rc.1")
        self.assertEqual(versioning.compare_versions("1.0.0-rc.1", "1.0.0"), -1)
        self.assertIsNone(versioning.STABLE_VERSION.fullmatch("1.0.0-rc.1"))

    def test_archive_and_builder_boundaries_use_full_semver(self) -> None:
        archive = importlib.import_module("x86qw_runtime.io.archive")
        builder = importlib.import_module("maintenance.tools.build_installer_bundle")
        self.assertIsNotNone(archive._VERSION_PATTERN.fullmatch("1.0.0-rc.1"))
        self.assertIsNotNone(builder.VERSION_PATTERN.fullmatch("1.0.0-rc.1"))


class SchemaContractTests(unittest.TestCase):
    def test_explicit_schema_versions_require_cli_bound_and_round_trip(self) -> None:
        self.assertIsNotNone(contracts)
        validate_document_versions = contracts.validate_document_versions
        document = {
            "format": 1,
            "project": "x86qw",
            "catalog_version": 1,
            "min_cli_version": "1.0.0-rc.1",
            "packages": [],
        }
        versions = validate_document_versions(document, kind="catalog", allow_legacy=False)
        self.assertTrue(versions.supports("1.0.0-rc.1"))
        with self.assertRaises(Exception):
            validate_document_versions(
                {"catalog_version": 1, "packages": []},
                kind="catalog",
                allow_legacy=False,
            )


class JsonContractTests(unittest.TestCase):
    def test_writer_emits_canonical_envelope_and_redacts(self) -> None:
        self.assertIsNotNone(contracts)
        make_json_output = contracts.make_json_output
        parse_json_output = contracts.parse_json_output
        redact_json = contracts.redact_json
        output = make_json_output(
            "version",
            data={"project": "x86qw", "version": "1.0.0-rc.1"},
        )
        self.assertEqual(
            output.to_json(),
            '{"command":"version","data":{"project":"x86qw","version":"1.0.0-rc.1"},'
            '"dry_run":false,"errors":[],"exit_code":0,"ok":true,"schema_version":1}\n',
        )
        self.assertEqual(parse_json_output(output.to_json()).to_document(), output.to_document())
        self.assertEqual(
            redact_json({"password": "secret", "url": "https://u:p@example.test/x"}),
            {"password": "[REDACTED]", "url": "https://[REDACTED]@example.test/x"},
        )

    def test_invalid_exit_code_and_duplicate_fields_are_rejected(self) -> None:
        self.assertIsNotNone(contracts)
        JsonOutputError = contracts.JsonOutputError
        make_json_output = contracts.make_json_output
        parse_json_output = contracts.parse_json_output
        with self.assertRaises(JsonOutputError):
            make_json_output(
                "verify",
                ok=False,
                exit_code=42,
                errors=({"code": "failure", "message": "failed"},),
            )
        with self.assertRaises(JsonOutputError):
            parse_json_output(
                '{"schema_version":1,"command":"version","command":"version",'
                '"ok":true,"exit_code":0,"dry_run":false,"data":{},"errors":[]}\n'
            )


class WriterContractTests(unittest.TestCase):
    def test_repository_catalog_writers_emit_explicit_catalog_contract(self) -> None:
        root = Path(__file__).resolve().parents[2]
        for relative, collection in (
            ("maintenance/inventory/capabilities.json", "catalog"),
            ("maintenance/inventory/compatibility.json", "catalog"),
            ("maintenance/inventory/components.json", "catalog"),
            ("maintenance/inventory/games.json", "catalog"),
            ("maintenance/inventory/runtimes.json", "catalog"),
        ):
            with self.subTest(relative=relative):
                document = json.loads((root / relative).read_text(encoding="utf-8"))
                self.assertIn("catalog_version", document)
                self.assertIn("min_cli_version", document)
                contracts.validate_document_versions(document, kind=collection, allow_legacy=False)

    def test_cli_receipt_writer_emits_explicit_receipt_contract(self) -> None:
        manager_spec = importlib.util.spec_from_file_location(
            "x86qw_manager_pr_c", Path(__file__).resolve().parents[2] / "dist/installer/bin/manager.py",
        )
        assert manager_spec is not None and manager_spec.loader is not None
        manager = importlib.util.module_from_spec(manager_spec)
        import sys
        sys.modules[manager_spec.name] = manager
        manager_spec.loader.exec_module(manager)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt.json"
            installer = manager.Installer(Path(__file__).resolve().parents[2], Path(temporary))
            installer.write_cli_receipt_record(path, {
                "format": 1,
                "project": "x86qw",
                "version": "1.0.0-rc.1",
            })
            document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(1, document["receipt_version"])
        self.assertEqual("0.7.0", document["min_cli_version"])

    def test_install_state_writer_emits_explicit_state_contract(self) -> None:
        manager_spec = importlib.util.spec_from_file_location(
            "x86qw_manager_state_pr_c", Path(__file__).resolve().parents[2] / "dist/installer/bin/manager.py",
        )
        assert manager_spec is not None and manager_spec.loader is not None
        manager = importlib.util.module_from_spec(manager_spec)
        import sys
        sys.modules[manager_spec.name] = manager
        manager_spec.loader.exec_module(manager)
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            installer = manager.Installer(Path(__file__).resolve().parents[2], target)
            installer.write_install_state("none", [], known=[], capabilities=[])
            document = json.loads((target / manager.INSTALL_STATE).read_text(encoding="utf-8"))
        self.assertEqual(2, document["state_version"])
        self.assertEqual("0.7.0", document["min_cli_version"])

    def test_manager_version_json_writer_emits_one_contract_document(self) -> None:
        manager_spec = importlib.util.spec_from_file_location(
            "x86qw_manager_json_pr_c", Path(__file__).resolve().parents[2] / "dist/installer/bin/manager.py",
        )
        assert manager_spec is not None and manager_spec.loader is not None
        manager = importlib.util.module_from_spec(manager_spec)
        import sys
        sys.modules[manager_spec.name] = manager
        manager_spec.loader.exec_module(manager)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            result = manager.main(["version", "--json"])
        self.assertEqual(0, result)
        document = contracts.parse_json_output(output.getvalue())
        self.assertEqual("version", document.command)
        self.assertEqual("x86qw", document.data["project"])


if __name__ == "__main__":
    unittest.main()
