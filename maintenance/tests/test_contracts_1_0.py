"""Positive, negative and adversarial coverage for the PR7 contracts."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from x86qw_runtime.contracts import (
    CATALOG_SCHEMA_VERSION,
    CURRENT_RECEIPT_VERSION,
    CURRENT_STATE_VERSION,
    ContractError,
    ContractVersions,
    CatalogSnapshot,
    DeprecationPolicy,
    DeprecationState,
    ExitCode,
    IncompatibleCliError,
    JsonOutputError,
    ReleaseChannel,
    add_contract_versions,
    classify_release,
    make_json_output,
    parse_json_output,
    render_json_output,
    snapshot_catalog,
    validate_document_versions,
    validate_release_version,
)
from x86qw_runtime.versioning import (
    ReleaseStage,
    STABLE_VERSION,
    SemVer,
    compare_versions,
    parse_semver,
)


class SemVerContractTests(unittest.TestCase):
    def test_all_release_stages_parse_and_order(self) -> None:
        values = [
            parse_semver("1.0.0-alpha.1"),
            parse_semver("1.0.0-beta.1"),
            parse_semver("1.0.0-rc.1"),
            parse_semver("1.0.0"),
        ]
        self.assertEqual(
            [item.stage for item in values],
            [ReleaseStage.ALPHA, ReleaseStage.BETA, ReleaseStage.RC, ReleaseStage.STABLE],
        )
        self.assertEqual(values, sorted(values))
        self.assertEqual(compare_versions("1.0.0-rc.1", "1.0.0"), -1)

    def test_semver_precedence_ignores_build_metadata(self) -> None:
        self.assertEqual(parse_semver("1.2.3+build.1"), parse_semver("1.2.3+build.2"))
        self.assertEqual(str(parse_semver("1.2.3-rc.4+candidate")), "1.2.3-rc.4+candidate")

    def test_semver_official_precedence_table(self) -> None:
        values = [
            parse_semver("1.0.0-alpha"),
            parse_semver("1.0.0-alpha.1"),
            parse_semver("1.0.0-alpha.beta"),
            parse_semver("1.0.0-beta"),
            parse_semver("1.0.0-beta.2"),
            parse_semver("1.0.0-beta.11"),
            parse_semver("1.0.0-rc.1"),
            parse_semver("1.0.0"),
        ]
        self.assertEqual(values, sorted(values))
        self.assertEqual(hash(parse_semver("1.0.0+build.1")), hash(parse_semver("1.0.0+build.2")))

    def test_semver_rejects_v_prefix_leading_zeroes_and_malformed_values(self) -> None:
        for value in (
            "v1.0.0",
            "01.0.0",
            "1.01.0",
            "1.0.01",
            "1.0",
            "1.0.0-",
            "1.0.0+",
            "1.0.0-alpha..1",
            "1.0.0-01",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_semver(value)

    def test_direct_construction_is_validated(self) -> None:
        with self.assertRaises(ValueError):
            SemVer(1, 0, 0, ("01",))
        with self.assertRaises(ValueError):
            SemVer(1, 0, 0, "alpha")


class SchemaContractTests(unittest.TestCase):
    def test_legacy_documents_remain_readable_and_new_metadata_is_explicit(self) -> None:
        legacy_catalog = {"format": 1, "project": "x86qw", "packages": []}
        versions = validate_document_versions(legacy_catalog, kind="catalog")
        self.assertEqual(versions.catalog_version, CATALOG_SCHEMA_VERSION)
        self.assertEqual(versions.state_version, CURRENT_STATE_VERSION)
        self.assertEqual(versions.receipt_version, CURRENT_RECEIPT_VERSION)
        expanded = add_contract_versions(legacy_catalog, versions, kind="catalog")
        self.assertEqual(expanded["catalog_version"], 1)
        self.assertNotIn("state_version", expanded)
        self.assertEqual(legacy_catalog, {"format": 1, "project": "x86qw", "packages": []})

    def test_cli_range_is_inclusive_and_max_is_optional(self) -> None:
        versions = ContractVersions(
            catalog_version=1,
            state_version=2,
            receipt_version=1,
            min_cli_version="1.0.0-beta.1",
            max_cli_version="1.0.0-rc.2",
        )
        self.assertTrue(versions.supports("1.0.0-beta.1"))
        self.assertTrue(versions.supports("1.0.0-rc.2"))
        self.assertFalse(versions.supports("1.0.0"))
        with self.assertRaises(IncompatibleCliError):
            versions.require_compatible("1.0.0")
        with self.assertRaises(ContractError):
            ContractVersions(min_cli_version="1.0.0", max_cli_version="0.9.9")

    def test_schema_version_and_cli_fields_reject_wrong_types(self) -> None:
        with self.assertRaises(ContractError):
            validate_document_versions(
                {"catalog_version": True},
                kind="catalog",
                allow_legacy=False,
            )
        with self.assertRaises(ContractError):
            validate_document_versions(
                {"catalog_version": 1},
                kind="catalog",
                allow_legacy=False,
            )
        with self.assertRaises(ContractError):
            validate_document_versions(
                {"catalog_version": 1, "min_cli_version": "v1.0.0"},
                kind="catalog",
            )
        with self.assertRaises(ContractError):
            validate_document_versions(
                {"catalog_version": 2, "catalog_schema_version": 1},
                kind="catalog",
            )
        with self.assertRaises(ContractError):
            validate_document_versions(
                {"catalog_version": None, "format": 1, "min_cli_version": "0.7.0"},
                kind="catalog",
            )

        for field in ("catalog_version", "state_version", "receipt_version"):
            with self.subTest(field=field), self.assertRaises(ContractError):
                validate_document_versions(
                    {field: 999, "min_cli_version": "0.7.0"},
                    kind=field.removesuffix("_version"),
                )

    def test_explicit_schema_requires_cli_bounds_and_rejects_other_schema_fields(self) -> None:
        with self.assertRaises(ContractError):
            validate_document_versions(
                {"catalog_version": 1},
                kind="catalog",
            )
        with self.assertRaises(ContractError):
            validate_document_versions(
                {"catalog_version": 1, "min_cli_version": "0.7.0", "state_version": 2},
                kind="catalog",
            )

    def test_catalog_snapshot_validates_explicit_document_versions(self) -> None:
        with self.assertRaises(ContractError):
            snapshot_catalog(
                {
                    "format": 1,
                    "project": "x86qw",
                    "catalog_version": True,
                    "min_cli_version": "0.7.0",
                    "packages": [],
                },
                coordinate="refs/tags/catalog-1",
            )

    def test_string_legacy_receipt_format_remains_compatible(self) -> None:
        versions = validate_document_versions(
            {"format": "1", "component": "ktx"},
            kind="receipt",
        )
        self.assertEqual(versions.receipt_version, 1)

    def test_boolean_legacy_format_is_not_accepted_as_schema_version(self) -> None:
        with self.assertRaises(ContractError):
            validate_document_versions(
                {"receipt_version": 1, "format": True},
                kind="receipt",
            )

    def test_public_catalog_is_legacy_until_signed_metadata_is_regenerated(self) -> None:
        catalog_path = Path(__file__).resolve().parents[2] / "site/public/api/v1/catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        validate_document_versions(catalog, kind="catalog", allow_legacy=True)
        with self.assertRaises(ContractError):
            validate_document_versions(catalog, kind="catalog", allow_legacy=False)


class CatalogSnapshotTests(unittest.TestCase):
    def test_snapshot_is_immutable_and_content_addressed(self) -> None:
        source = {"format": 1, "project": "x86qw", "packages": [{"version": "1.0.0"}]}
        snapshot = snapshot_catalog(source, coordinate="refs/tags/catalog-1")
        source["packages"].append({"version": "2.0.0"})
        self.assertEqual(len(snapshot["packages"]), 1)
        with self.assertRaises(TypeError):
            snapshot.document["format"] = 2  # type: ignore[index]
        self.assertTrue(snapshot.verify(snapshot.snapshot_id))
        self.assertEqual(
            snapshot,
            CatalogSnapshot.from_document(
                snapshot.to_document(),
                digest=snapshot.digest,
                coordinate=snapshot.coordinate,
            ),
        )

    def test_mutable_catalog_coordinates_and_wrong_digest_are_rejected(self) -> None:
        with self.assertRaises(ContractError):
            snapshot_catalog({"format": 1}, coordinate="main")
        with self.assertRaises(ContractError):
            snapshot_catalog({"format": 1}, coordinate="refs/heads/main")
        with self.assertRaises(ContractError):
            snapshot_catalog(
                {"format": 1},
                coordinate="https://example.invalid/x86qw/main/catalog.json",
            )
        for coordinate in ("refs/tags/../catalog-1", "refs/tags/catalog/1", "snapshot/not-a-digest"):
            with self.subTest(coordinate=coordinate), self.assertRaises(ContractError):
                snapshot_catalog({"format": 1}, coordinate=coordinate)
        self.assertTrue(snapshot_catalog({"format": 1}, coordinate="commit/" + "a" * 40).coordinate)
        self.assertTrue(snapshot_catalog({"format": 1}, coordinate="snapshot/" + "b" * 64).coordinate)
        with self.assertRaises(ContractError):
            snapshot_catalog({"format": 1}, digest="0" * 64)


class ReleaseAndDeprecationPolicyTests(unittest.TestCase):
    def test_channels_require_matching_versions(self) -> None:
        self.assertEqual(classify_release("1.0.0-alpha.1"), ReleaseChannel.ALPHA)
        self.assertEqual(classify_release("20260804-120000_abcdef1"), ReleaseChannel.NIGHTLY)
        validate_release_version("1.0.0-rc.1", ReleaseChannel.RC)
        with self.assertRaises(ContractError):
            validate_release_version("1.0.0", ReleaseChannel.RC)

    def test_deprecation_is_monotonic(self) -> None:
        policy = DeprecationPolicy(deprecated_in="1.0.0-beta.1", removed_in="1.0.0")
        self.assertEqual(policy.state_at("0.9.9"), DeprecationState.ACTIVE)
        self.assertEqual(policy.state_at("1.0.0-rc.1"), DeprecationState.DEPRECATED)
        self.assertEqual(policy.state_at("1.0.0"), DeprecationState.REMOVED)
        with self.assertRaises(ContractError):
            DeprecationPolicy(removed_in="1.0.0")


class JsonOutputContractTests(unittest.TestCase):
    def test_json_output_is_deterministic_and_redacted(self) -> None:
        output = make_json_output(
            "status",
            data={
                "project": "x86qw",
                "target": "/tmp/x86qw",
                "installation": "present",
                "state": "present",
                "sessions": [],
            },
        )
        expected = (
            '{"command":"status","data":{"installation":"present","project":"x86qw",'
            '"sessions":[],"state":"present","target":"/tmp/x86qw"},'
            '"dry_run":false,"errors":[],"exit_code":0,"ok":true,"schema_version":1}\n'
        )
        self.assertEqual(render_json_output(output), expected)
        self.assertEqual(parse_json_output(expected).to_document(), output.to_document())

    def test_failed_and_dry_run_outputs_have_stable_codes(self) -> None:
        failed = make_json_output(
            "verify",
            ok=False,
            exit_code=ExitCode.FAILURE,
            errors=({"code": "integrity", "message": "hash mismatch"},),
        )
        self.assertEqual(parse_json_output(failed.to_json()).exit_code, ExitCode.FAILURE)
        dry_run = make_json_output(
            "upgrade",
            dry_run=True,
            data={"target": "/tmp/x86qw", "status": "noop", "operations": []},
        )
        self.assertTrue(parse_json_output(dry_run.to_json()).dry_run)
        with self.assertRaises(JsonOutputError):
            make_json_output("repair --dry-run")
        with self.assertRaises(JsonOutputError):
            make_json_output("version", dry_run=True)

    def test_duplicate_fields_and_unknown_commands_fail(self) -> None:
        with self.assertRaises(JsonOutputError):
            parse_json_output(
                '{"schema_version":1,"command":"status","command":"status",'
                '"ok":true,"exit_code":0,"dry_run":false,"data":{},"errors":[]}\n'
            )
        with self.assertRaises(JsonOutputError):
            make_json_output("unknown")

    def test_json_exit_codes_are_limited_to_the_public_contract(self) -> None:
        with self.assertRaises(JsonOutputError):
            make_json_output(
                "verify",
                ok=False,
                exit_code=42,
                errors=({"code": "failure", "message": "failed"},),
            )

    def test_json_errors_are_closed_and_match_success_state(self) -> None:
        with self.assertRaises(JsonOutputError):
            make_json_output("status", data={}, errors=({"code": "x", "message": "nope"},))
        with self.assertRaises(JsonOutputError):
            make_json_output("status", ok=False, exit_code=ExitCode.FAILURE)
        with self.assertRaises(JsonOutputError):
            make_json_output(
                "status",
                ok=False,
                exit_code=ExitCode.FAILURE,
                errors=({"code": "", "message": "failure"},),
            )
        with self.assertRaises(JsonOutputError):
            make_json_output(
                "status",
                ok=False,
                exit_code=ExitCode.FAILURE,
                errors=({"code": "failure", "message": "failure", "extra": True},),
            )

    def test_canonical_json_rejects_non_string_object_keys(self) -> None:
        with self.assertRaises(JsonOutputError):
            make_json_output("status", data={"nested": {1: "not a JSON object key"}})


class StableVersionRegexTests(unittest.TestCase):
    def test_legacy_stable_boundary_is_strict_semver(self) -> None:
        self.assertTrue(STABLE_VERSION.fullmatch("0.0.0"))
        self.assertTrue(STABLE_VERSION.fullmatch("1.2.3"))
        for value in ("01.2.3", "1.02.3", "1.2.03", "1.2.3-rc.1"):
            with self.subTest(value=value):
                self.assertIsNone(STABLE_VERSION.fullmatch(value))


if __name__ == "__main__":
    unittest.main()
