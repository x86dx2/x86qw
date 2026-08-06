from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from x86qw_runtime import migrations
from x86qw_runtime import receipts, state
from x86qw_runtime.io.archive import scan_archive


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "maintenance" / "tests" / "fixtures" / "migrations"
PUBLIC_ARCHIVES = {
    "0.7.0": {
        "url": "https://github.com/x86dx2/x86qw/releases/download/x86qw-installer-0.7.0/x86qw-installer-0.7.0.zip",
        "size": 155094,
        "sha256": "abdf43ef026d25b9eecae327a559ba81dcd65844e20412d8f367f12d91a6f4af",
    },
    "0.7.1": {
        "url": "https://github.com/x86dx2/x86qw/releases/download/x86qw-installer-0.7.1/x86qw-installer-0.7.1.zip",
        "size": 157113,
        "sha256": "a0946ffcc8a4e1181dbc55ea08caf54691b18b12e901d12069eb2064b38c0d80",
    },
    "0.7.2": {
        "url": "https://github.com/x86dx2/x86qw/releases/download/x86qw-installer-0.7.2/x86qw-installer-0.7.2.zip",
        "size": 286103,
        "sha256": "7f14caadce174665a24431eab4615833c657ad71a8cbe82f34b66c413857b6f0",
    },
    "0.7.3": {
        "url": "https://github.com/x86dx2/x86qw/releases/download/x86qw-installer-0.7.3/x86qw-installer-0.7.3.zip",
        "size": 286137,
        "sha256": "41ecb4d82d41c6d4733c6990c5baf40a9062f85ce9faf098d8e8822ad66784d6",
    },
}


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_component(root: Path, component: str, *, payload: bytes = b"managed") -> None:
    metadata = root / ".x86qw"
    metadata.mkdir(parents=True, exist_ok=True)
    inventory = f"qw/{component}.pk3\t{_digest(payload)}\n".encode("utf-8")
    receipt = (
        b"format\t1\n"
        + f"component\t{component}\n".encode()
        + b"selection\t0.7.3\n"
        + b"source\thttps://example.invalid/component.zip\n"
        + f"inventory_sha256\t{_digest(inventory)}\n".encode()
    )
    (metadata / f"{component}.receipt").write_bytes(receipt)
    (metadata / f"{component}.inventory").write_bytes(inventory)


def _write_state(root: Path, *, format: int = 1) -> None:
    metadata = root / ".x86qw"
    metadata.mkdir(parents=True, exist_ok=True)
    document = {
        "format": format,
        "project": "x86qw",
        "profile": "custom",
        "requested_components": ["ktx"],
        "recorded_components": ["ktx"],
        "known_components": ["ktx", "qtv"],
    }
    if format == 2:
        document.update({
            "capabilities": [],
            "component_fingerprint": state.profile_fingerprint(["ktx"]),
        })
    (metadata / "state.json").write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class MigrationOnePointZeroTests(unittest.TestCase):
    def test_migration_fixture_manifests_use_one_explicit_schema(self) -> None:
        expected_public = {"0.7.0", "0.7.1", "0.7.2", "0.7.3"}
        expected_prospective = {"0.8.x", "0.9.x"}
        self.assertEqual(
            expected_public | expected_prospective,
            {path.name for path in FIXTURES.iterdir() if path.is_dir()},
        )
        for version in sorted(expected_public | expected_prospective):
            with self.subTest(version=version):
                fixture = FIXTURES / version
                manifest_path = fixture / "manifest.json"
                self.assertTrue(manifest_path.is_file())
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertIsInstance(manifest, dict)
                self.assertEqual(version, manifest.get("source_version", version))
                if version in expected_public:
                    self.assertEqual("public-release-backed", manifest.get("fixture_kind"))
                    self.assertEqual("public-installer-archive-plus-legacy-layout", manifest.get("payload_kind"))
                    self.assertEqual(f"x86qw-installer-{version}", manifest.get("source_tag"))
                    self.assertRegex(
                        str(manifest.get("source_commit", "")),
                        r"^[0-9a-f]{40}$",
                    )
                    self.assertEqual("dist/installer/VERSION", manifest.get("source_path"))
                    self.assertEqual("legacy-metadata-with-format-1-state", manifest.get("layout"))
                    self.assertTrue((fixture / "VERSION").is_file())
                    self.assertTrue((fixture / ".x86qw/state.json").is_file())
                    archive = manifest.get("source_archive")
                    self.assertIsInstance(archive, dict)
                    expected_archive = PUBLIC_ARCHIVES[version]
                    self.assertEqual(expected_archive["url"], archive.get("url"))
                    self.assertEqual(expected_archive["size"], archive.get("size"))
                    self.assertEqual(expected_archive["sha256"], archive.get("sha256"))
                    self.assertEqual("archive/x86qw-installer-" + version + ".zip", archive.get("path"))
                    self.assertEqual("bundle", archive.get("extracted_path"))
                else:
                    self.assertEqual("prospective-contract", manifest.get("fixture_kind"))
                    self.assertEqual("synthetic-contract-only", manifest.get("payload_kind"))
                    self.assertEqual(version, manifest.get("source_family"))
                    self.assertIs(manifest.get("public_release_available"), False)
                    self.assertTrue(str(manifest.get("reason", "")).strip())
                    self.assertFalse((fixture / "VERSION").exists())

    def test_public_fixture_archives_and_materialized_members_are_exact(self) -> None:
        for version, expected_archive in PUBLIC_ARCHIVES.items():
            with self.subTest(version=version):
                fixture = FIXTURES / version
                manifest = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))
                archive = manifest["source_archive"]
                archive_path = fixture / archive["path"]
                payload = archive_path.read_bytes()
                self.assertEqual(expected_archive["size"], len(payload))
                self.assertEqual(expected_archive["sha256"], _digest(payload))
                plan = scan_archive(archive_path)
                bundle_root = fixture / archive["extracted_path"]
                expected_names = {member.name for member in plan.members if not member.is_dir}
                actual_names = {
                    path.relative_to(bundle_root).as_posix()
                    for path in bundle_root.rglob("*")
                    if path.is_file()
                }
                self.assertEqual(expected_names, actual_names)
                for member in plan.members:
                    path = bundle_root / member.name
                    if member.is_dir:
                        self.assertTrue(path.is_dir())
                        continue
                    self.assertTrue(path.is_file())
                    member_bytes = path.read_bytes()
                    self.assertEqual(member.size, len(member_bytes), member.name)
                    self.assertEqual(member.sha256, _digest(member_bytes), member.name)

    def test_state_version_marker_is_optional_but_validated(self) -> None:
        document = {
            "format": 2,
            "project": "x86qw",
            "profile": "none",
            "requested_components": [],
            "recorded_components": [],
            "known_components": [],
            "capabilities": [],
            "component_fingerprint": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "installation_version": "1.0.0",
        }
        parsed = state.parse_install_state(
            document,
            allowed_profiles=state.INSTALLATION_PROFILES,
            allowed_capabilities=frozenset(),
        )
        self.assertEqual("1.0.0", parsed.installation_version)
        document["installation_version"] = "0.6.0"
        with self.assertRaises(state.StateError):
            state.parse_install_state(
                document,
                allowed_profiles=state.INSTALLATION_PROFILES,
                allowed_capabilities=frozenset(),
            )

    def test_state_version_marker_accepts_semver_prerelease_and_build(self) -> None:
        document = {
            "format": 2,
            "project": "x86qw",
            "profile": "none",
            "requested_components": [],
            "recorded_components": [],
            "known_components": [],
            "capabilities": [],
            "component_fingerprint": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "installation_version": "1.0.0-rc.1+build.42",
        }

        parsed = state.parse_install_state(
            document,
            allowed_profiles=state.INSTALLATION_PROFILES,
            allowed_capabilities=frozenset(),
        )

        self.assertEqual("1.0.0-rc.1+build.42", parsed.installation_version)

    def test_state_v1_legacy_component_ids_use_the_historical_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / ".x86qw"
            metadata.mkdir()
            original = {
                "format": 1,
                "project": "x86qw",
                "profile": "custom",
                "requested_components": ["nquake-ktx", "nquake-sounds", "ktx"],
                "recorded_components": ["nquake-ktx", "nquake-sounds", "ktx"],
                "known_components": ["nquake-ktx", "nquake-sounds", "ktx", "qtv"],
            }
            state_path = metadata / "state.json"
            state_path.write_text(json.dumps(original), encoding="utf-8")

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertTrue(plan.executable, plan.conflicts)
            self.assertEqual(("state",), tuple(item.key for item in plan.operations))
            migrated = json.loads(plan.operations[0].payload)
            self.assertEqual(2, migrated["format"])
            self.assertEqual(["ktx"], migrated["requested_components"])
            self.assertEqual(["ktx"], migrated["recorded_components"])
            self.assertEqual(["ktx", "qtv"], migrated["known_components"])
            self.assertNotIn("nquake-ktx", json.dumps(migrated))
            self.assertNotIn("nquake-sounds", json.dumps(migrated))
            self.assertEqual(json.dumps(original), state_path.read_text(encoding="utf-8"))

    def test_state_v2_legacy_component_ids_are_normalized_and_fingerprinted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / ".x86qw"
            metadata.mkdir()
            original = {
                "format": 2,
                "project": "x86qw",
                "profile": "custom",
                "requested_components": ["nquake-ktx"],
                "recorded_components": ["nquake-ktx"],
                "known_components": ["nquake-ktx", "ktx"],
                "capabilities": [],
                "component_fingerprint": state.profile_fingerprint(["nquake-ktx"]),
                "installation_version": "0.7.3",
            }
            state_path = metadata / "state.json"
            state_path.write_text(json.dumps(original), encoding="utf-8")

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertTrue(plan.executable, plan.conflicts)
            self.assertEqual(("state",), tuple(item.key for item in plan.operations))
            migrated = json.loads(plan.operations[0].payload)
            self.assertEqual(["ktx"], migrated["requested_components"])
            self.assertEqual(["ktx"], migrated["recorded_components"])
            self.assertEqual(state.profile_fingerprint(["ktx"]), migrated["component_fingerprint"])
            self.assertEqual("1.0.0", migrated["installation_version"])
            self.assertEqual(2, migrated["format"])

            migrations.execute_migration(plan)
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(migrated, persisted)
            self.assertEqual((), migrations.plan_migration(root, source_version="0.7.3").operations)

    def test_state_v2_without_installation_marker_gets_target_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root, format=2)
            state_path = root / ".x86qw" / "state.json"
            document = json.loads(state_path.read_text(encoding="utf-8"))
            document.pop("installation_version", None)
            state_path.write_text(json.dumps(document), encoding="utf-8")

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertTrue(plan.executable, plan.conflicts)
            self.assertFalse(any(item.code == "corrupt-state" for item in plan.conflicts))
            self.assertEqual(("state",), tuple(item.key for item in plan.operations))
            migrated = json.loads(plan.operations[0].payload)
            self.assertEqual("1.0.0", migrated["installation_version"])
            self.assertEqual(state.profile_fingerprint(["ktx"]), migrated["component_fingerprint"])

    def test_state_version_alias_is_canonicalized_during_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / ".x86qw"
            metadata.mkdir()
            document = {
                "format": 1,
                "project": "x86qw",
                "profile": "none",
                "requested_components": [],
                "recorded_components": [],
                "known_components": [],
                "version": "0.7.3",
            }
            state_path = metadata / "state.json"
            state_path.write_text(json.dumps(document), encoding="utf-8")

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertTrue(plan.executable, plan.conflicts)
            migrated = json.loads(plan.operations[0].payload)
            self.assertNotIn("version", migrated)
            self.assertNotIn("installer_version", migrated)
            self.assertEqual("1.0.0", migrated["installation_version"])

    def test_format_two_retired_component_is_normalized_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root, format=2)
            state_path = root / ".x86qw/state.json"
            state_document = json.loads(state_path.read_text(encoding="utf-8"))
            state_document.update({
                "requested_components": ["nquake-sounds"],
                "recorded_components": ["nquake-sounds"],
                "known_components": ["nquake-sounds", "ktx"],
                "component_fingerprint": state.profile_fingerprint(["nquake-sounds"]),
            })
            state_path.write_text(json.dumps(state_document), encoding="utf-8")
            _write_component(root, "nquake-sounds")
            before = (root / ".x86qw/nquake-sounds.receipt").read_bytes(), (
                root / ".x86qw/nquake-sounds.inventory"
            ).read_bytes()

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertTrue(plan.executable, plan.conflicts)
            self.assertEqual(("nquake-sounds",), plan.retired_components)
            self.assertEqual(("state",), tuple(item.key for item in plan.operations))
            migrations.execute_migration(plan)
            migrated = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(2, migrated["format"])
            self.assertNotIn("nquake-sounds", json.dumps(migrated))
            self.assertEqual(before[0], (root / ".x86qw/nquake-sounds.receipt").read_bytes())
            self.assertEqual(before[1], (root / ".x86qw/nquake-sounds.inventory").read_bytes())

            rerun = migrations.plan_migration(root, source_version="0.7.3")

            self.assertTrue(rerun.executable, rerun.conflicts)
            self.assertEqual((), rerun.operations)
            self.assertEqual(("nquake-sounds",), rerun.retired_components)

    def test_authenticated_source_markers_reject_a_conflicting_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / ".x86qw"
            metadata.mkdir()
            (metadata / "cli.receipt").write_text(
                json.dumps({"format": 1, "project": "x86qw", "version": "0.8.0"}),
                encoding="utf-8",
            )
            (metadata / "state.json").write_text(json.dumps({
                "format": 2,
                "project": "x86qw",
                "profile": "none",
                "requested_components": [],
                "recorded_components": [],
                "known_components": [],
                "capabilities": [],
                "component_fingerprint": state.profile_fingerprint([]),
                "installation_version": "0.8.0",
            }), encoding="utf-8")

            observed = migrations.plan_migration(root)
            overridden = migrations.plan_migration(root, source_version="0.7.3")

            self.assertEqual("0.8.0", observed.source.version)
            self.assertFalse(observed.executable)
            self.assertTrue(any(item.code == "prospective-source" for item in observed.conflicts))
            self.assertEqual("0.8.0", overridden.source.version)
            self.assertFalse(overridden.executable)
            self.assertTrue(any(item.code == "source-version-mismatch" for item in overridden.conflicts))
            self.assertTrue(any(item.code == "prospective-source" for item in overridden.conflicts))
            self.assertEqual((), overridden.operations)

    def test_state_only_target_marker_allows_original_override_on_idempotent_replan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root, format=2)
            first = migrations.execute_migration(
                migrations.plan_migration(root, source_version="0.7.3")
            )
            self.assertEqual("committed", first.status)

            rerun = migrations.plan_migration(root, source_version="0.7.3")

            self.assertTrue(rerun.executable, rerun.conflicts)
            self.assertEqual("1.0.0", rerun.source.version)
            self.assertEqual((), rerun.operations)

    def test_complete_aggregate_metadata_is_validated_and_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            metadata = root / ".x86qw"
            inventory = b"qw/ktx.pk3\t" + b"a" * 64 + b"\n"
            (metadata / "nquake.inventory").write_bytes(inventory)
            (metadata / "nquake.receipt").write_bytes(
                b"format\t1\n"
                + b"distfiles_commit\t" + b"b" * 40 + b"\n"
                + f"inventory_sha256\t{_digest(inventory)}\n".encode()
            )

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertTrue(plan.executable, plan.conflicts)
            self.assertEqual(("state",), tuple(item.key for item in plan.operations))
            migrations.execute_migration(plan)
            self.assertTrue((metadata / "nquake.receipt").is_file())
            self.assertTrue((metadata / "nquake.inventory").is_file())
            rerun = migrations.plan_migration(root, source_version="0.7.3")
            self.assertTrue(rerun.executable, rerun.conflicts)
            self.assertEqual((), rerun.operations)

    def test_aggregate_inventory_rejects_unsafe_paths_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            metadata = root / ".x86qw"
            inventory = b"../../outside\t" + b"a" * 64 + b"\n"
            (metadata / "nquake.inventory").write_bytes(inventory)
            (metadata / "nquake.receipt").write_bytes(
                b"format\t1\n"
                + b"distfiles_commit\t" + b"b" * 40 + b"\n"
                + f"inventory_sha256\t{_digest(inventory)}\n".encode()
            )
            state_before = (metadata / "state.json").read_bytes()

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "corrupt-receipt" for item in plan.conflicts))
            self.assertTrue(any("portable" in item.detail for item in plan.conflicts))
            self.assertEqual((), plan.operations)
            self.assertEqual(state_before, (metadata / "state.json").read_bytes())

    def test_format_two_inventory_rejects_unsafe_path_before_move(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root, format=2)
            metadata = root / ".x86qw"
            payload = b"../../outside\t" + ("a" * 64).encode() + b"\n"
            receipt = (
                b"format\t1\ncomponent\tktx\nselection\t0.7.3\n"
                b"source\thttps://example.invalid/component.zip\n"
                + f"inventory_sha256\t{_digest(payload)}\n".encode()
            )
            (metadata / "ktx.inventory").write_bytes(payload)
            (metadata / "ktx.receipt").write_bytes(receipt)

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "corrupt-receipt" for item in plan.conflicts))
            self.assertTrue(any("portable" in item.detail for item in plan.conflicts))
            self.assertEqual((), plan.operations)
            self.assertEqual(payload, (metadata / "ktx.inventory").read_bytes())

    def test_corrupt_canonical_component_metadata_blocks_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            canonical = root / ".x86qw" / "components" / "ktx"
            canonical.mkdir(parents=True)
            (canonical / "receipt").write_bytes(b"not a receipt\n")
            (canonical / "inventory").write_bytes(b"not an inventory\n")

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "corrupt-receipt" for item in plan.conflicts))
            self.assertEqual((), plan.operations)

    def test_corrupt_canonical_client_receipt_blocks_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            canonical = root / ".x86qw/clients/ezquake/linux/stable.receipt"
            canonical.parent.mkdir(parents=True)
            canonical.write_bytes(b"not a receipt\n")

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "corrupt-receipt" for item in plan.conflicts))
            self.assertEqual((), plan.operations)
            self.assertEqual(b"not a receipt\n", canonical.read_bytes())

    def test_partial_aggregate_nquake_metadata_blocks_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            (root / ".x86qw" / "nquake.receipt").write_bytes(b"partial\n")

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "partial-receipt" for item in plan.conflicts))
            self.assertEqual((), plan.operations)

    def test_legacy_state_mapping_is_idempotent_after_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / ".x86qw"
            metadata.mkdir()
            state_path = metadata / "state.json"
            state_path.write_text(json.dumps({
                "format": 1,
                "project": "x86qw",
                "profile": "custom",
                "requested_components": ["nquake-ktx", "nquake-sounds"],
                "recorded_components": ["nquake-ktx", "nquake-sounds"],
                "known_components": ["nquake-ktx", "nquake-sounds", "ktx"],
            }), encoding="utf-8")

            first = migrations.execute_migration(
                migrations.plan_migration(root, source_version="0.7.3")
            )
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            rerun = migrations.plan_migration(root, source_version="0.7.3")

            self.assertEqual("committed", first.status)
            self.assertEqual(["ktx"], persisted["requested_components"])
            self.assertEqual(["ktx"], persisted["recorded_components"])
            self.assertEqual(["ktx"], persisted["known_components"])
            self.assertEqual((), rerun.operations)
            self.assertFalse(rerun.conflicts, rerun.conflicts)

    def test_legacy_state_dry_run_preserves_personal_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / ".x86qw"
            metadata.mkdir()
            state_path = metadata / "state.json"
            state_path.write_text(json.dumps({
                "format": 1,
                "project": "x86qw",
                "profile": "custom",
                "requested_components": ["nquake-ktx"],
                "recorded_components": ["nquake-ktx"],
                "known_components": ["nquake-ktx", "ktx"],
            }), encoding="utf-8")
            personal = root / "qw" / "config.cfg"
            personal.parent.mkdir()
            personal.write_text("seta name player\n", encoding="utf-8")
            before = sorted(
                (path.relative_to(root).as_posix(), path.read_bytes())
                for path in root.rglob("*")
                if path.is_file()
            )

            plan = migrations.plan_migration(
                root, source_version="0.7.3", dry_run=True,
            )

            after = sorted(
                (path.relative_to(root).as_posix(), path.read_bytes())
                for path in root.rglob("*")
                if path.is_file()
            )
            self.assertTrue(plan.executable, plan.conflicts)
            self.assertIn("qw/config.cfg", plan.preserved_paths)
            self.assertEqual(before, after)

    def test_legacy_state_mapping_still_blocks_a_personal_managed_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            state_path = root / ".x86qw/state.json"
            state_document = json.loads(state_path.read_text(encoding="utf-8"))
            state_document.update({
                "requested_components": ["nquake-ktx"],
                "recorded_components": ["nquake-ktx"],
                "known_components": ["nquake-ktx", "ktx"],
            })
            state_path.write_text(json.dumps(state_document), encoding="utf-8")
            _write_component(root, "ktx")
            destination = root / ".x86qw/components/ktx/receipt"
            destination.parent.mkdir(parents=True)
            destination.write_text("player-owned\n", encoding="utf-8")
            before = state_path.read_bytes(), destination.read_bytes()

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "destination-occupied" for item in plan.conflicts))
            self.assertEqual((), plan.operations)
            self.assertEqual(before, (state_path.read_bytes(), destination.read_bytes()))

    def test_legacy_nquake_ktx_pair_is_normalized_to_the_canonical_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "nquake-ktx")

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertTrue(plan.executable, plan.conflicts)
            receipt_operation = next(
                operation for operation in plan.operations
                if operation.key == "component:ktx:receipt"
            )
            self.assertEqual(
                ".x86qw/components/ktx/receipt",
                receipt_operation.destination,
            )
            self.assertIn(b"component\tktx\n", receipt_operation.payload)
            self.assertNotIn(b"component\tnquake-ktx\n", receipt_operation.payload)

            result = migrations.execute_migration(plan)

            self.assertEqual("committed", result.status)
            canonical = root / ".x86qw/components/ktx/receipt"
            self.assertEqual("ktx", receipts.inspect_receipt(canonical.read_bytes()).subject)
            canonical_inventory = root / ".x86qw/components/ktx/inventory"
            self.assertIn(b"qw/ktx.pk3\t", canonical_inventory.read_bytes())
            receipts.validate_receipt_inventory(
                canonical.read_bytes(), canonical_inventory.read_bytes(), component="ktx",
            )
            self.assertFalse((root / ".x86qw/nquake-ktx.receipt").exists())
            self.assertFalse((root / ".x86qw/nquake-ktx.inventory").exists())
            rerun = migrations.plan_migration(root, source_version="0.7.3")
            self.assertEqual((), rerun.operations)
            self.assertFalse(rerun.conflicts, rerun.conflicts)

    def test_retired_nquake_sounds_is_removed_from_state_but_preserved_for_diagnosis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            state_path = root / ".x86qw/state.json"
            state_path.write_text(json.dumps({
                "format": 1,
                "project": "x86qw",
                "profile": "custom",
                "requested_components": ["nquake-sounds"],
                "recorded_components": ["nquake-sounds"],
                "known_components": ["nquake-sounds", "ktx"],
            }), encoding="utf-8")
            _write_component(root, "nquake-sounds")
            receipt_before = (root / ".x86qw/nquake-sounds.receipt").read_bytes()
            inventory_before = (root / ".x86qw/nquake-sounds.inventory").read_bytes()

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertTrue(plan.executable, plan.conflicts)
            self.assertEqual(("nquake-sounds",), plan.retired_components)
            self.assertIn(".x86qw/nquake-sounds.receipt", plan.preserved_paths)
            self.assertIn(".x86qw/nquake-sounds.inventory", plan.preserved_paths)
            self.assertNotIn(
                ".x86qw/components/nquake-sounds/receipt",
                {operation.destination for operation in plan.operations},
            )
            migrations.execute_migration(plan)

            migrated = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertNotIn("nquake-sounds", json.dumps(migrated))
            self.assertEqual(receipt_before, (root / ".x86qw/nquake-sounds.receipt").read_bytes())
            self.assertEqual(inventory_before, (root / ".x86qw/nquake-sounds.inventory").read_bytes())
            rerun = migrations.plan_migration(root, source_version="0.7.3")
            self.assertEqual((), rerun.operations)
            self.assertEqual(("nquake-sounds",), rerun.retired_components)

    def test_legacy_and_canonical_pairs_deduplicate_only_when_normalized_bytes_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx", payload=b"same")
            _write_component(root, "nquake-ktx", payload=b"same")

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertTrue(plan.executable, plan.conflicts)
            self.assertIn(
                "component:ktx:retire:nquake-ktx:receipt",
                {operation.key for operation in plan.operations},
            )
            migrations.execute_migration(plan)
            self.assertTrue((root / ".x86qw/components/ktx/receipt").is_file())
            self.assertFalse((root / ".x86qw/ktx.receipt").exists())
            self.assertFalse((root / ".x86qw/nquake-ktx.receipt").exists())
            rerun = migrations.plan_migration(root, source_version="0.7.3")
            self.assertEqual((), rerun.operations)

    def test_deduplicated_pair_result_can_roll_back_the_retired_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx", payload=b"same")
            _write_component(root, "nquake-ktx", payload=b"same")
            before = sorted(
                (path.relative_to(root).as_posix(), path.read_bytes())
                for path in root.rglob("*")
                if path.is_file()
            )

            result = migrations.execute_migration(
                migrations.plan_migration(root, source_version="0.7.3")
            )
            result.rollback()

            after = sorted(
                (path.relative_to(root).as_posix(), path.read_bytes())
                for path in root.rglob("*")
                if path.is_file()
            )
            self.assertEqual(before, after)

    def test_legacy_and_canonical_pairs_with_different_bytes_block_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx", payload=b"current")
            _write_component(root, "nquake-ktx", payload=b"legacy")
            before = sorted(
                (path.relative_to(root).as_posix(), path.read_bytes())
                for path in root.rglob("*")
                if path.is_file()
            )

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "component-collision" for item in plan.conflicts))
            self.assertEqual((), plan.operations)
            after = sorted(
                (path.relative_to(root).as_posix(), path.read_bytes())
                for path in root.rglob("*")
                if path.is_file()
            )
            self.assertEqual(before, after)

    def test_identical_destination_without_a_journal_is_still_an_ownership_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx")
            source = root / ".x86qw/ktx.receipt"
            destination = root / ".x86qw/components/ktx/receipt"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(source.read_bytes())

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "destination-occupied" for item in plan.conflicts))
            self.assertEqual((), plan.operations)
            self.assertTrue(source.exists())

    def test_destination_created_after_planning_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx")
            plan = migrations.plan_migration(root, source_version="0.7.3")
            destination = root / ".x86qw/components/ktx/receipt"
            destination.parent.mkdir(parents=True)
            destination.write_text("created concurrently\n", encoding="utf-8")

            with self.assertRaises(migrations.MigrationError):
                migrations.execute_migration(plan)

            self.assertEqual("created concurrently\n", destination.read_text(encoding="utf-8"))
            self.assertTrue((root / ".x86qw/ktx.receipt").exists())

    def test_receipt_inspection_requires_a_valid_component_inventory_binding(self) -> None:
        inventory = b"qw/ktx.pk3\t" + b"a" * 64 + b"\n"
        receipt = (
            b"format\t1\ncomponent\tktx\nselection\t1.0.0\n"
            b"source\thttps://example.invalid/ktx.zip\n"
            + f"inventory_sha256\t{_digest(inventory)}\n".encode()
        )
        identity = receipts.inspect_receipt(receipt)
        self.assertEqual("component", identity.kind)
        receipts.validate_receipt_inventory(receipt, inventory, component="ktx")
        with self.assertRaises(receipts.ReceiptError):
            receipts.validate_receipt_inventory(receipt, inventory + b"tamper", component="ktx")

    def test_public_0_7_fixtures_match_tagged_metadata_contract(self) -> None:
        for version in ("0.7.0", "0.7.1", "0.7.2", "0.7.3"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                fixture = FIXTURES / version
                root = Path(directory) / "quake world"
                shutil.copytree(fixture, root)
                manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
                tagged_commit = subprocess.run(
                    ["git", "rev-parse", manifest["source_tag"]],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                tagged_version = subprocess.run(
                    ["git", "show", f"{manifest['source_tag']}:{manifest['source_path']}"],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                plan = migrations.plan_migration(
                    root,
                    source_version=manifest["source_version"],
                    dry_run=True,
                )
                self.assertEqual("public-release-backed", manifest["fixture_kind"])
                self.assertEqual(
                    "public-installer-archive-plus-legacy-layout",
                    manifest["payload_kind"],
                )
                self.assertEqual(version, manifest["source_version"])
                self.assertEqual(manifest["source_commit"], tagged_commit)
                self.assertEqual(version, tagged_version)
                archive = manifest["source_archive"]
                archive_path = root / archive["path"]
                self.assertEqual(archive["size"], archive_path.stat().st_size)
                self.assertEqual(archive["sha256"], _digest(archive_path.read_bytes()))
                self.assertTrue((root / archive["extracted_path"]).is_dir())
                self.assertTrue(plan.executable, plan.conflicts)
                self.assertGreaterEqual(len(plan.operations), 3)

    def test_public_0_7_fixtures_execute_and_converge_without_touching_payloads(self) -> None:
        for version in ("0.7.0", "0.7.1", "0.7.2", "0.7.3"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                fixture = FIXTURES / version
                root = Path(directory) / "quake world"
                shutil.copytree(fixture, root)
                preserved_snapshot = {
                    relative: (root / relative).read_bytes()
                    for relative in (
                        f"archive/x86qw-installer-{version}.zip",
                        f"bundle/x86qw-installer-{version}/x86qw.pyz",
                    )
                }
                payloads = {
                    "qw/ktx.pk3": b"player-owned KTX payload\n",
                    "qw/config.cfg": b"seta name player\n",
                    "demos/local.dem": b"demo bytes\n",
                    "qconsole.log": b"log bytes\n",
                }
                for relative, payload in payloads.items():
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(payload)

                manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
                result = migrations.migrate_installation(
                    root,
                    source_version=manifest["source_version"],
                    dry_run=False,
                )

                self.assertEqual("committed", result.status)
                self.assertEqual(
                    "1.0.0",
                    json.loads((root / ".x86qw/state.json").read_text(encoding="utf-8"))["installation_version"],
                )
                for relative, payload in payloads.items():
                    self.assertEqual(payload, (root / relative).read_bytes())
                for relative, payload in preserved_snapshot.items():
                    self.assertEqual(payload, (root / relative).read_bytes())
                rerun = migrations.plan_migration(
                    root,
                    source_version=manifest["source_version"],
                )
                self.assertTrue(rerun.executable, rerun.conflicts)
                self.assertEqual((), rerun.operations)

    def test_plan_is_dry_run_and_preserves_unknown_personal_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx")
            personal = root / "qw" / "config.cfg"
            personal.parent.mkdir()
            personal.write_text("bind mouse1 +attack\n", encoding="utf-8")
            unknown = root / "README.personal"
            unknown.write_text("owned by the player\n", encoding="utf-8")
            before = sorted(
                (path.relative_to(root).as_posix(), path.read_bytes())
                for path in root.rglob("*")
                if path.is_file()
            )

            plan = migrations.plan_migration(
                root,
                source_version="0.7.3",
                target_version="1.0.0",
                dry_run=True,
            )

            self.assertTrue(plan.executable)
            self.assertTrue(plan.dry_run)
            self.assertTrue(any(item.destination.endswith("components/ktx/receipt") for item in plan.operations))
            self.assertIn("qw/config.cfg", plan.preserved_paths)
            self.assertIn("README.personal", plan.preserved_paths)
            after = sorted(
                (path.relative_to(root).as_posix(), path.read_bytes())
                for path in root.rglob("*")
                if path.is_file()
            )
            self.assertEqual(before, after)
            document = plan.to_document()
            self.assertEqual(
                ["preflight", "stage", "verify", "commit", "finalize"],
                document["phases"],
            )
            self.assertEqual("0.7.x", document["source_family"])

    def test_execute_migrates_state_and_component_metadata_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx")
            plan = migrations.plan_migration(root, source_version="0.7.3")

            result = migrations.execute_migration(plan)

            self.assertEqual("committed", result.status)
            self.assertTrue((root / ".x86qw/state.json").is_file())
            self.assertEqual(2, json.loads((root / ".x86qw/state.json").read_text())["format"])
            self.assertTrue((root / ".x86qw/components/ktx/receipt").is_file())
            self.assertTrue((root / ".x86qw/components/ktx/inventory").is_file())
            self.assertFalse((root / ".x86qw/ktx.receipt").exists())
            self.assertFalse((root / ".x86qw/ktx.inventory").exists())

    def test_stable_and_nightly_receipts_are_migrated_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / ".x86qw"
            metadata.mkdir()
            stable = (
                b"format\t1\nplatform\tlinux\narchitecture\tx86_64\nchannel\tstable\n"
                b"selection\t3.6.9\ninstall_name\tezquake-stable-x86_64.AppImage\n"
                b"bundle_version\t3.6.9\nartifact_name\tezQuake-linux-x86_64.zip\n"
                b"artifact_url\thttps://example.invalid/ezQuake-linux-x86_64.zip\n"
                + b"artifact_sha256\t" + b"a" * 64 + b"\n"
                + b"binary_sha256\t" + b"b" * 64 + b"\n"
            )
            nightly_version = b"20260804-120000_abcdef1"
            nightly = (
                b"format\t1\nplatform\tlinux\narchitecture\tx86_64\nchannel\tnightly\n"
                + b"selection\t" + nightly_version + b"\n"
                + b"install_name\tezquake-nightly-x86_64.AppImage\n"
                + b"bundle_version\t" + nightly_version + b"\n"
                + b"artifact_name\t" + nightly_version + b"_ezQuake-x86_64.AppImage\n"
                + b"artifact_url\thttps://example.invalid/" + nightly_version + b"_ezQuake-x86_64.AppImage\n"
                + b"artifact_sha256\t" + b"c" * 64 + b"\n"
                + b"binary_sha256\t" + b"d" * 64 + b"\n"
            )
            (metadata / "ezquake-linux-stable.receipt").write_bytes(stable)
            (metadata / "ezquake-linux-nightly.receipt").write_bytes(nightly)
            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertTrue(plan.executable, plan.conflicts)
            destinations = {operation.destination for operation in plan.operations}
            self.assertIn(".x86qw/clients/ezquake/linux/stable.receipt", destinations)
            self.assertIn(".x86qw/clients/ezquake/linux/nightly.receipt", destinations)
            result = migrations.execute_migration(plan)
            self.assertEqual("committed", result.status)
            self.assertEqual(
                stable,
                (root / ".x86qw/clients/ezquake/linux/stable.receipt").read_bytes(),
            )
            self.assertEqual(
                nightly,
                (root / ".x86qw/clients/ezquake/linux/nightly.receipt").read_bytes(),
            )
            self.assertFalse((metadata / "ezquake-linux-stable.receipt").exists())
            self.assertFalse((metadata / "ezquake-linux-nightly.receipt").exists())
            rerun = migrations.plan_migration(root, source_version="0.7.3")
            self.assertTrue(rerun.executable, rerun.conflicts)
            self.assertEqual((), rerun.operations)

    def test_client_receipt_filename_must_match_its_embedded_platform(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / ".x86qw"
            metadata.mkdir()
            payload = (
                b"format\t1\nplatform\twindows\narchitecture\tx86_64\nchannel\tstable\n"
                b"selection\t3.6.9\ninstall_name\tezquake-stable-x86_64.AppImage\n"
                b"bundle_version\t3.6.9\nartifact_name\tezQuake-windows-x86_64.zip\n"
                b"artifact_url\thttps://example.invalid/ezQuake-windows-x86_64.zip\n"
                + b"artifact_sha256\t" + b"a" * 64 + b"\n"
                + b"binary_sha256\t" + b"b" * 64 + b"\n"
            )
            (metadata / "ezquake-linux-stable.receipt").write_bytes(payload)

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "corrupt-receipt" for item in plan.conflicts))

    def test_state_marker_identifies_source_without_a_cli_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            state_path = root / ".x86qw/state.json"
            document = json.loads(state_path.read_text(encoding="utf-8"))
            document["installation_version"] = "0.7.3"
            state_path.write_text(json.dumps(document), encoding="utf-8")

            plan = migrations.plan_migration(root)

            self.assertEqual("0.7.x", plan.source.family)
            self.assertTrue(plan.executable, plan.conflicts)

    def test_directory_symlink_is_preserved_and_binds_the_plan_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx")
            outside = root / "personal-target"
            outside.mkdir()
            (outside / "config.cfg").write_text("personal\n", encoding="utf-8")
            link = root / "personal-link"
            link.symlink_to(outside, target_is_directory=True)

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertIn("personal-link", plan.preserved_paths)
            link.unlink()
            link.symlink_to(root / "other-target", target_is_directory=True)
            with self.assertRaises(migrations.MigrationError):
                migrations.execute_migration(plan)


    def test_rerun_is_idempotent_after_a_successful_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx")
            migrations.execute_migration(migrations.plan_migration(root, source_version="0.7.3"))

            rerun = migrations.plan_migration(root, source_version="0.7.3")
            result = migrations.execute_migration(rerun)

            self.assertTrue(rerun.executable)
            self.assertEqual((), rerun.operations)
            self.assertEqual("noop", result.status)

    def test_committed_result_can_be_rolled_back_byte_for_byte(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx")
            before = sorted(
                (path.relative_to(root).as_posix(), path.read_bytes())
                for path in root.rglob("*")
                if path.is_file()
            )
            result = migrations.execute_migration(
                migrations.plan_migration(root, source_version="0.7.3")
            )

            result.rollback()

            after = sorted(
                (path.relative_to(root).as_posix(), path.read_bytes())
                for path in root.rglob("*")
                if path.is_file()
            )
            self.assertEqual(before, after)

    def test_rollback_does_not_overwrite_a_new_personal_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx")
            result = migrations.execute_migration(
                migrations.plan_migration(root, source_version="0.7.3")
            )
            destination = root / ".x86qw/components/ktx/receipt"
            destination.write_text("player-owned\n", encoding="utf-8")

            with self.assertRaises(migrations.MigrationError):
                result.rollback()

            self.assertEqual("player-owned\n", destination.read_text(encoding="utf-8"))

    def test_disk_full_during_commit_rolls_back_and_exposes_phase(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx")
            plan = migrations.plan_migration(root, source_version="0.7.3")

            with mock.patch.object(
                migrations, "atomic_create_bytes", side_effect=OSError("ENOSPC"),
            ):
                with self.assertRaises(migrations.MigrationExecutionError) as raised:
                    migrations.execute_migration(plan)

            self.assertEqual(migrations.MigrationPhase.COMMIT, raised.exception.phase)
            self.assertTrue(raised.exception.rolled_back)
            self.assertTrue((root / ".x86qw/ktx.receipt").is_file())

    def test_symlink_at_legacy_receipt_path_is_not_owned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / ".x86qw"
            metadata.mkdir()
            target = root / "personal-receipt"
            target.write_text("player-owned\n", encoding="utf-8")
            (metadata / "ktx.receipt").symlink_to(target)
            (metadata / "ktx.inventory").write_text("not used\n", encoding="utf-8")

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "corrupt-receipt" for item in plan.conflicts))

    def test_symlink_in_managed_destination_parent_blocks_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_component(root, "ktx")
            outside = root / "outside"
            outside.mkdir()
            (root / ".x86qw/components").symlink_to(outside, target_is_directory=True)

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "unsafe-destination" for item in plan.conflicts))
            self.assertFalse((outside / "ktx" / "receipt").exists())

    def test_canonical_components_file_blocks_without_a_planner_crash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            canonical_root = root / ".x86qw/components"
            canonical_root.write_text("player-owned metadata\n", encoding="utf-8")

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "unsafe-metadata" for item in plan.conflicts))
            self.assertEqual((), plan.operations)
            self.assertEqual("player-owned metadata\n", canonical_root.read_text(encoding="utf-8"))

    def test_canonical_components_symlink_blocks_even_without_legacy_moves(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            outside = root / "outside"
            outside.mkdir()
            (outside / "ktx").mkdir()
            canonical_root = root / ".x86qw/components"
            canonical_root.symlink_to(outside, target_is_directory=True)

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "unsafe-metadata" for item in plan.conflicts))
            self.assertEqual((), plan.operations)
            self.assertTrue(canonical_root.is_symlink())

    def test_symlink_metadata_root_blocks_state_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            (outside / "state.json").write_text("{}", encoding="utf-8")
            (root / ".x86qw").symlink_to(outside, target_is_directory=True)

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "unsafe-metadata" for item in plan.conflicts))

    def test_symlink_metadata_root_does_not_authenticate_external_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            (outside / "state.json").write_text(json.dumps({
                "format": 1,
                "project": "x86qw",
                "profile": "none",
                "requested_components": [],
                "recorded_components": [],
                "known_components": [],
                "installation_version": "0.7.3",
            }), encoding="utf-8")
            (root / ".x86qw").symlink_to(outside, target_is_directory=True)

            source = migrations.inspect_migration_source(root)
            plan = migrations.plan_migration(root)

            self.assertIsNone(source.version)
            self.assertIsNone(source.family)
            self.assertIsNone(source.state_format)
            self.assertFalse(plan.executable)
            self.assertEqual((), plan.operations)
            self.assertTrue(any(item.code == "unsafe-metadata" for item in plan.conflicts))
            self.assertTrue(any(item.code == "unknown-source" for item in plan.conflicts))
            self.assertEqual("0.7.3", json.loads((outside / "state.json").read_text())["installation_version"])

    def test_symlink_metadata_root_does_not_authenticate_external_cli_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            (outside / "cli.receipt").write_text(
                '{"format": 1, "project": "x86qw", "version": "0.7.3"}\n',
                encoding="utf-8",
            )
            (root / ".x86qw").symlink_to(outside, target_is_directory=True)

            source = migrations.inspect_migration_source(root)
            plan = migrations.plan_migration(root)

            self.assertIsNone(source.version)
            self.assertIsNone(source.family)
            self.assertEqual((), source.managed_paths)
            self.assertFalse(plan.executable)
            self.assertEqual((), plan.operations)
            self.assertTrue(any(item.code == "unsafe-metadata" for item in plan.conflicts))
            self.assertTrue(any(item.code == "unknown-source" for item in plan.conflicts))
            self.assertIn('"version": "0.7.3"', (outside / "cli.receipt").read_text())

    def test_symlink_cli_parent_does_not_authenticate_external_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / ".x86qw"
            metadata.mkdir()
            outside = root / "outside"
            outside.mkdir()
            (outside / "receipt").write_text(
                '{"format": 1, "project": "x86qw", "version": "0.7.3"}\n',
                encoding="utf-8",
            )
            (metadata / "cli").symlink_to(outside, target_is_directory=True)

            source = migrations.inspect_migration_source(root)
            plan = migrations.plan_migration(root)
            overridden = migrations.plan_migration(root, source_version="0.7.3")

            self.assertIsNone(source.version)
            self.assertIsNone(source.family)
            self.assertEqual((), source.managed_paths)
            self.assertFalse(plan.executable)
            self.assertEqual((), plan.operations)
            self.assertTrue(any(item.code == "unknown-source" for item in plan.conflicts))
            self.assertFalse(overridden.executable)
            self.assertTrue(any(item.code == "unsafe-metadata" for item in overridden.conflicts))

    def test_symlink_metadata_root_does_not_scan_external_migration_journal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            journal = outside / ".x86qw/migrations/1.0/tx-0123456789abcdef01234567/journal.json"
            journal.parent.mkdir(parents=True)
            journal.write_bytes(b"external journal\n")
            (root / ".x86qw").symlink_to(outside / ".x86qw", target_is_directory=True)

            pending = migrations.inspect_pending_migration(root)
            plan = migrations.plan_migration(root)

            self.assertIsNone(pending)
            self.assertFalse(plan.executable)
            self.assertEqual((), plan.operations)
            self.assertTrue(any(item.code == "unsafe-metadata" for item in plan.conflicts))
            self.assertFalse(any(item.code == "recovery-required" for item in plan.conflicts))
            self.assertEqual(b"external journal\n", journal.read_bytes())

    def test_symlink_installation_root_does_not_scan_external_migration_journal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            outside = base / "outside"
            journal = outside / ".x86qw/migrations/1.0/tx-0123456789abcdef01234567/journal.json"
            journal.parent.mkdir(parents=True)
            journal.write_bytes(b"external journal\n")
            root = base / "installation"
            root.symlink_to(outside, target_is_directory=True)

            pending = migrations.inspect_pending_migration(root)
            recovered = migrations.recover_migration(root)

            self.assertIsNone(pending)
            self.assertFalse(recovered)
            self.assertEqual(b"external journal\n", journal.read_bytes())

    def test_corrupt_receipt_and_partial_pair_block_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / ".x86qw"
            metadata.mkdir()
            (metadata / "ktx.receipt").write_bytes(b"not a receipt")
            before = (metadata / "ktx.receipt").read_bytes()

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code in {"corrupt-receipt", "partial-metadata"} for item in plan.conflicts))
            self.assertEqual(before, (metadata / "ktx.receipt").read_bytes())

    def test_orphan_component_inventory_is_a_partial_pair_not_unowned_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / ".x86qw"
            metadata.mkdir()
            inventory = metadata / "ktx.inventory"
            inventory.write_bytes(b"qw/ktx.pk3\t" + b"a" * 64 + b"\n")
            before = inventory.read_bytes()

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "partial-metadata" for item in plan.conflicts))
            self.assertEqual((), plan.operations)
            self.assertEqual(before, inventory.read_bytes())

    def test_corrupt_state_blocks_metadata_migration_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx")
            state_path = root / ".x86qw/state.json"
            state_path.write_bytes(b"{not-json")
            before = state_path.read_bytes()

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "corrupt-state" for item in plan.conflicts))
            self.assertEqual((), plan.operations)
            self.assertEqual(before, state_path.read_bytes())

    def test_personal_file_at_managed_destination_is_a_hard_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_component(root, "ktx")
            destination = root / ".x86qw/components/ktx/receipt"
            destination.parent.mkdir(parents=True)
            destination.write_text("player file\n", encoding="utf-8")

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "destination-occupied" for item in plan.conflicts))
            self.assertEqual("player file\n", destination.read_text(encoding="utf-8"))

    def test_crash_in_each_phase_rolls_back_byte_for_byte(self) -> None:
        for phase in migrations.MigrationPhase:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _write_state(root)
                _write_component(root, "ktx")
                before = sorted(
                    (path.relative_to(root).as_posix(), path.read_bytes())
                    for path in root.rglob("*")
                    if path.is_file()
                )
                plan = migrations.plan_migration(root, source_version="0.7.3")

                with self.assertRaises(migrations.MigrationExecutionError):
                    migrations.execute_migration(plan, fail_phase=phase)

                after = sorted(
                    (path.relative_to(root).as_posix(), path.read_bytes())
                    for path in root.rglob("*")
                    if path.is_file()
                )
                self.assertEqual(before, after)

    def test_cleanup_failure_after_commit_remains_visible_and_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx")
            plan = migrations.plan_migration(root, source_version="0.7.3")
            attempts = 0

            real_remove = migrations._remove_cleanup_tree

            def fail_cleanup(plan: object) -> None:
                nonlocal attempts
                attempts += 1
                if attempts <= 2:
                    raise OSError("cleanup unavailable")
                real_remove(plan)

            with mock.patch.object(
                migrations, "_remove_cleanup_tree", side_effect=fail_cleanup,
            ):
                with self.assertRaises(migrations.MigrationExecutionError) as raised:
                    migrations.execute_migration(plan)

            self.assertTrue(raised.exception.committed)
            self.assertFalse(raised.exception.rolled_back)
            self.assertTrue((root / ".x86qw/components/ktx/receipt").is_file())
            self.assertFalse((root / ".x86qw/ktx.receipt").exists())
            pending = migrations.inspect_pending_migration(root)
            self.assertIsNotNone(pending)
            assert pending is not None
            self.assertEqual("cleanup-pending", pending.document["status"])

            self.assertTrue(migrations.recover_migration(root))
            self.assertIsNone(migrations.inspect_pending_migration(root))
            self.assertFalse(migrations.recover_migration(root))

    def test_cleanup_pending_transaction_replacement_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx")
            plan = migrations.plan_migration(root, source_version="0.7.3")

            with mock.patch.object(
                migrations, "_remove_cleanup_tree", side_effect=OSError("cleanup unavailable"),
            ):
                with self.assertRaises(migrations.MigrationExecutionError):
                    migrations.execute_migration(plan)

            pending = migrations.inspect_pending_migration(root)
            self.assertIsNotNone(pending)
            assert pending is not None
            outside = root / "outside"
            outside.mkdir()
            marker = outside / "marker"
            marker.write_text("player-owned\n", encoding="utf-8")
            saved = pending.directory.with_name("saved-transaction")
            pending.directory.rename(saved)
            pending.directory.symlink_to(outside, target_is_directory=True)

            with self.assertRaises(migrations.MigrationError):
                migrations.recover_migration(root)

            self.assertTrue(pending.directory.is_symlink())
            self.assertTrue(saved.is_dir())
            self.assertEqual("player-owned\n", marker.read_text(encoding="utf-8"))

    def test_cleanup_pending_regular_replacement_with_extra_bytes_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx")
            plan = migrations.plan_migration(root, source_version="0.7.3")
            with mock.patch.object(
                migrations, "_remove_cleanup_tree", side_effect=OSError("cleanup unavailable"),
            ):
                with self.assertRaises(migrations.MigrationExecutionError):
                    migrations.execute_migration(plan)

            pending = migrations.inspect_pending_migration(root)
            self.assertIsNotNone(pending)
            assert pending is not None
            original = pending.directory
            copied = root / "copied-transaction"
            shutil.copytree(original, copied)
            shutil.rmtree(original)
            original.mkdir(mode=0o700)
            shutil.copytree(copied, original, dirs_exist_ok=True)
            victim = original / "victim"
            victim.write_text("player-owned\n", encoding="utf-8")
            victim.chmod(0o600)

            with self.assertRaises(migrations.MigrationError):
                migrations.recover_migration(root)

            self.assertTrue(victim.is_file())
            self.assertIsNotNone(migrations.inspect_pending_migration(root))

    def test_cleanup_pending_extra_backup_or_stage_bytes_block_cleanup(self) -> None:
        for location in ("backup", "stage"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _write_state(root)
                _write_component(root, "ktx")
                plan = migrations.plan_migration(root, source_version="0.7.3")
                with mock.patch.object(
                    migrations, "_remove_cleanup_tree", side_effect=OSError("cleanup unavailable"),
                ):
                    with self.assertRaises(migrations.MigrationExecutionError):
                        migrations.execute_migration(plan)
                pending = migrations.inspect_pending_migration(root)
                self.assertIsNotNone(pending)
                assert pending is not None
                extra = pending.directory / location / "unowned"
                extra.write_bytes(b"unowned")
                extra.chmod(0o600)

                with self.assertRaises(migrations.MigrationError):
                    migrations.recover_migration(root)
                self.assertTrue(extra.is_file())

    def test_cleanup_pending_changed_stage_bytes_block_identity_bound_removal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx")
            plan = migrations.plan_migration(root, source_version="0.7.3")
            with mock.patch.object(
                migrations, "_remove_cleanup_tree", side_effect=OSError("cleanup unavailable"),
            ):
                with self.assertRaises(migrations.MigrationExecutionError):
                    migrations.execute_migration(plan)
            pending = migrations.inspect_pending_migration(root)
            self.assertIsNotNone(pending)
            assert pending is not None
            stage_files = sorted((pending.directory / "stage").iterdir())
            self.assertTrue(stage_files)
            changed = stage_files[0]
            changed.write_bytes(b"changed")
            changed.chmod(0o600)

            with self.assertRaises(migrations.MigrationError):
                migrations.recover_migration(root)
            self.assertTrue(changed.is_file())

    def test_duplicate_pair_cleanup_recovers_after_finalize_crash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx", payload=b"same")
            _write_component(root, "nquake-ktx", payload=b"same")
            before = sorted(
                (path.relative_to(root).as_posix(), path.read_bytes())
                for path in root.rglob("*")
                if path.is_file()
            )
            plan = migrations.plan_migration(root, source_version="0.7.3")

            with self.assertRaises(migrations.MigrationExecutionError) as raised:
                migrations.execute_migration(
                    plan,
                    fail_phase=migrations.MigrationPhase.FINALIZE,
                )

            self.assertEqual(migrations.MigrationPhase.FINALIZE, raised.exception.phase)
            self.assertTrue(raised.exception.rolled_back)
            after = sorted(
                (path.relative_to(root).as_posix(), path.read_bytes())
                for path in root.rglob("*")
                if path.is_file()
            )
            self.assertEqual(before, after)
            self.assertIsNone(migrations.inspect_pending_migration(root))

    def test_unsupported_0_8_and_0_9_contracts_are_explicitly_prospective(self) -> None:
        for version in ("0.8.0", "0.9.0"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                plan = migrations.plan_migration(
                    Path(directory), source_version=version,
                )
                self.assertFalse(plan.executable)
                self.assertTrue(any(item.code == "prospective-source" for item in plan.conflicts))

    def test_hard_crash_leaves_a_recoverable_journal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx")
            before = sorted(
                (path.relative_to(root).as_posix(), path.read_bytes())
                for path in root.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
            script = """
import os
from pathlib import Path
from x86qw_runtime import migrations

root = Path(os.environ["MIGRATION_ROOT"])
plan = migrations.plan_migration(root, source_version="0.7.3")
persist = migrations._persist_migration_journal

def crash(path, document):
    persist(path, document)
    if document.get("phase") == "commit" and any(
        operation.get("status") == "committed"
        for operation in document.get("operations", [])
    ):
        os._exit(73)

migrations._persist_migration_journal = crash
migrations.execute_migration(plan)
"""
            environment = os.environ.copy()
            environment["MIGRATION_ROOT"] = str(root)
            crashed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=environment,
                check=False,
            )

            self.assertEqual(73, crashed.returncode)
            self.assertIsNotNone(migrations.inspect_pending_migration(root))
            blocked = migrations.plan_migration(root, source_version="0.7.3")
            self.assertTrue(any(item.code == "recovery-required" for item in blocked.conflicts))
            migrations.recover_migration(root)
            after = sorted(
                (path.relative_to(root).as_posix(), path.read_bytes())
                for path in root.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
            self.assertEqual(before, after)
            self.assertIsNone(migrations.inspect_pending_migration(root))

    def test_corrupt_pending_journal_fails_closed_and_blocks_planning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = root / ".x86qw/migrations/1.0/broken/journal.json"
            journal.parent.mkdir(parents=True)
            journal.write_bytes(b"{not-json")

            plan = migrations.plan_migration(root, source_version="0.7.3")

            self.assertFalse(plan.executable)
            self.assertTrue(any(item.code == "recovery-required" for item in plan.conflicts))
            with self.assertRaises(migrations.MigrationError):
                migrations.recover_migration(root)
            self.assertEqual(b"{not-json", journal.read_bytes())

    def test_enospc_during_each_persistent_checkpoint_rolls_back(self) -> None:
        for phase in migrations.MigrationPhase:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _write_state(root)
                _write_component(root, "ktx")
                before = sorted(
                    (path.relative_to(root).as_posix(), path.read_bytes())
                    for path in root.rglob("*")
                    if path.is_file() and not path.is_symlink()
                )
                persist = migrations._persist_migration_journal

                def fail_checkpoint(path: Path, document: dict[str, object]) -> None:
                    if document.get("phase") == phase.value:
                        raise OSError("ENOSPC")
                    persist(path, document)

                with mock.patch.object(
                    migrations, "_persist_migration_journal", side_effect=fail_checkpoint,
                ):
                    with self.assertRaises(migrations.MigrationExecutionError) as raised:
                        migrations.execute_migration(
                            migrations.plan_migration(root, source_version="0.7.3")
                        )
                self.assertEqual(phase, raised.exception.phase)
                self.assertTrue(raised.exception.rolled_back)
                after = sorted(
                    (path.relative_to(root).as_posix(), path.read_bytes())
                    for path in root.rglob("*")
                    if path.is_file() and not path.is_symlink()
                )
                self.assertEqual(before, after)

    def test_recovery_rejects_personal_replacement_and_tampered_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_state(root)
            _write_component(root, "ktx")
            script = """
import os
from pathlib import Path
from x86qw_runtime import migrations
root = Path(os.environ["MIGRATION_ROOT"])
plan = migrations.plan_migration(root, source_version="0.7.3")
persist = migrations._persist_migration_journal
def crash(path, document):
    persist(path, document)
    if document.get("phase") == "commit" and any(
        operation.get("status") == "committed"
        for operation in document.get("operations", [])
    ):
        os._exit(73)
migrations._persist_migration_journal = crash
migrations.execute_migration(plan)
"""
            environment = os.environ.copy()
            environment["MIGRATION_ROOT"] = str(root)
            crashed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=environment,
                check=False,
            )
            self.assertEqual(73, crashed.returncode)
            pending = migrations.inspect_pending_migration(root)
            self.assertIsNotNone(pending)
            assert pending is not None
            original_journal = pending.journal.read_bytes()
            tampered_document = json.loads(original_journal.decode("utf-8"))
            tampered_document["operations"][0]["source_backup"] = ".x86qw/state.json"
            pending.journal.write_text(
                json.dumps(tampered_document),
                encoding="utf-8",
            )
            with self.assertRaises(migrations.MigrationError):
                migrations.recover_migration(root)
            pending.journal.write_bytes(original_journal)

            destination = root / ".x86qw/state.json"
            same_bytes = destination.read_bytes()
            replacement = root / ".x86qw/state.replacement"
            replacement.write_bytes(same_bytes)
            replacement.replace(destination)
            with self.assertRaises(migrations.MigrationError):
                migrations.recover_migration(root)

            destination.write_text("player-owned\n", encoding="utf-8")
            with self.assertRaises(migrations.MigrationError):
                migrations.recover_migration(root)
            self.assertEqual("player-owned\n", destination.read_text(encoding="utf-8"))

            destination.unlink()
            backup = next((pending.directory / "backup").glob("source-*.bin"))
            backup.write_bytes(b"tampered")
            with self.assertRaises(migrations.MigrationError):
                migrations.recover_migration(root)
            self.assertTrue(pending.journal.exists())


if __name__ == "__main__":
    unittest.main()
