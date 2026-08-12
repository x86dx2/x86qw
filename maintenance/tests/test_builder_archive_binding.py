from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from maintenance.tools import build_artifacts as build_artifacts_runtime
from maintenance.tools import build_component_packages as component_builder
from maintenance.tools import build_core_package as core_builder
from maintenance.tools import build_installer_bundle as installer_builder
from maintenance.tools import release_ownership
from x86qw_runtime.io import archive as archive_runtime


ROOT = Path(__file__).resolve().parents[2]
REPLACEMENT = b"replacement-after-canonical-validation"
PROJECT_REF = "c" * 40


class BuilderArchiveBindingTests(unittest.TestCase):
    @staticmethod
    def core_distribution(root: Path) -> Path:
        distribution = root / "dist"
        id1 = distribution / "game-data/id1"
        id1.mkdir(parents=True)
        (id1 / "pak0.pak").write_bytes(b"PACKzero")
        (id1 / "pak1.pak").write_bytes(b"PACKone")
        return distribution

    @staticmethod
    def component_context() -> SimpleNamespace:
        return SimpleNamespace(
            components={"test-component": {"label": "Test Component"}},
            commit="a" * 40,
        )

    @staticmethod
    def component_payloads() -> tuple[
        dict[str, object],
        str,
        list[tuple[str, str, bytes, list[dict[str, str]]]],
    ]:
        release = {
            "version": "1.2.3",
            "strategy": "upstream-package",
            "distribution_tag": "test-component-1.2.3",
            "license": "test-license",
            "license_url": "https://example.invalid/license",
            "upstream": {
                "source_url": "https://example.invalid/source",
                "release_url": "https://example.invalid/release",
                "release": "1.2.3",
            },
        }
        payloads = [("source/payload.dat", "payload.dat", b"payload", [])]
        return release, "b" * 64, payloads

    def build_component_packages(self, root: Path) -> dict[str, object]:
        with mock.patch.object(
            component_builder,
            "load_source_context",
            return_value=self.component_context(),
        ), mock.patch.object(
            component_builder,
            "resolve_component_payloads",
            return_value=self.component_payloads(),
        ):
            return component_builder.build_packages(root / "dist", root / "output")

    def test_core_package_rejects_source_replaced_after_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            distribution = self.core_distribution(root)

            def scan_then_replace(
                source: Path | bytes, *args: object, **kwargs: object,
            ):
                plan = archive_runtime.scan_archive(source, *args, **kwargs)
                self.assertIsInstance(source, Path)
                source.write_bytes(REPLACEMENT)
                return plan

            with mock.patch.object(
                core_builder, "scan_archive", side_effect=scan_then_replace,
            ):
                with self.assertRaisesRegex(ValueError, "canonical archive validation"):
                    core_builder.build_core_package(distribution, root / "output")

    def test_regular_build_input_rejects_a_symlink_swap_during_open(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bin"
            replacement = root / "replacement.bin"
            source.write_bytes(b"expected")
            replacement.write_bytes(b"personal")
            real_open = os.open
            swapped = False

            def swap_then_open(
                path: str | os.PathLike[str], flags: int, *args: object, **kwargs: object,
            ) -> int:
                nonlocal swapped
                if not swapped and Path(path) == source:
                    swapped = True
                    source.unlink()
                    try:
                        source.symlink_to(replacement)
                    except OSError as error:
                        self.skipTest(f"symlink indisponível: {error}")
                return real_open(path, flags, *args, **kwargs)

            with mock.patch.object(
                build_artifacts_runtime.os, "open", side_effect=swap_then_open,
            ):
                with self.assertRaisesRegex(ValueError, "could not be opened safely|changed"):
                    build_artifacts_runtime.read_regular_file(source)
            self.assertEqual(b"personal", replacement.read_bytes())

    def test_regular_build_input_rejects_same_size_regular_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bin"
            replacement = root / "replacement.bin"
            source.write_bytes(b"expected")
            replacement.write_bytes(b"personal")
            source_metadata = source.stat()
            os.utime(
                replacement,
                ns=(source_metadata.st_atime_ns, source_metadata.st_mtime_ns),
            )
            real_open = os.open
            swapped = False

            def swap_then_open(
                path: str | os.PathLike[str], flags: int, *args: object, **kwargs: object,
            ) -> int:
                nonlocal swapped
                if not swapped and Path(path) == source:
                    swapped = True
                    source.unlink()
                    replacement.replace(source)
                return real_open(path, flags, *args, **kwargs)

            with mock.patch.object(
                build_artifacts_runtime.os, "open", side_effect=swap_then_open,
            ):
                with self.assertRaisesRegex(ValueError, "changed while opening"):
                    build_artifacts_runtime.read_regular_file(source)

    @unittest.skipUnless(os.name == "nt", "identidade path/fstat exercitada no Windows")
    def test_windows_regular_build_input_accepts_command_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "fixture.cmd"
            source.write_bytes(b"@echo off\r\n")
            self.assertEqual(
                b"@echo off\r\n",
                build_artifacts_runtime.read_regular_file(source),
            )

    @unittest.skipUnless(os.name == "nt", "reparse point exercitado no Windows")
    def test_windows_regular_build_input_rejects_file_reparse_point(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bin"
            source.write_bytes(b"safe")
            link = root / "link.bin"
            try:
                link.symlink_to(source)
            except OSError as error:
                self.skipTest(f"privilégio para symlink indisponível: {error}")
            with self.assertRaisesRegex(ValueError, "regular non-symlink"):
                build_artifacts_runtime.read_regular_file(link)

    def test_installer_reads_bundle_sources_through_stable_snapshots(self) -> None:
        version = installer_builder.VERSION
        with tempfile.TemporaryDirectory(
            prefix=".builder-binding-", dir=ROOT / "dist",
        ) as temporary:
            output = Path(temporary) / "packages"
            real_read = installer_builder.read_regular_file
            seen: set[Path] = set()

            def record_read(path: Path, **kwargs: object) -> bytes:
                seen.add(path)
                return real_read(path, **kwargs)

            with mock.patch.object(
                installer_builder, "read_regular_file", side_effect=record_read,
            ):
                installer_builder.build(output, version)
            expected = {
                *(
                    ROOT / source
                    for source, _member in installer_builder.runtime_member_files()
                ),
                *(ROOT / source for source, _member, _mode in installer_builder.BUNDLE_FILES),
                installer_builder.RUNTIME_MEMBER_MANIFEST,
                installer_builder.ARCHIVE_SOURCE,
                installer_builder.SHELL_BOOTSTRAP,
                installer_builder.POWERSHELL_BOOTSTRAP,
                installer_builder.PUBLIC_SHELL_BOOTSTRAP,
                installer_builder.PUBLIC_POWERSHELL_BOOTSTRAP,
            }
            self.assertTrue(expected.issubset(seen), sorted(expected - seen))

    def test_core_package_records_the_validated_plan_digest_and_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            distribution = self.core_distribution(root)
            record = core_builder.build_core_package(distribution, root / "output")
            artifact = next((root / "output").rglob(str(record["filename"])))
            payload = artifact.read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), record["sha256"])
            self.assertEqual(len(payload), record["size"])

    def test_component_package_rejects_source_replaced_after_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def scan_then_replace(
                source: Path | bytes, *args: object, **kwargs: object,
            ):
                plan = archive_runtime.scan_archive(source, *args, **kwargs)
                self.assertIsInstance(source, Path)
                source.write_bytes(REPLACEMENT)
                return plan

            with mock.patch.object(
                component_builder,
                "scan_archive",
                side_effect=scan_then_replace,
            ):
                with self.assertRaisesRegex(ValueError, "canonical archive validation"):
                    self.build_component_packages(root)

    def test_component_package_records_the_validated_plan_digest_and_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self.build_component_packages(root)
            record = manifest["packages"][0]
            artifact = next((root / "output").rglob(str(record["filename"])))
            payload = artifact.read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), record["sha256"])
            self.assertEqual(len(payload), record["size"])

    def test_component_builder_emits_explicit_ownership_fragment_bound_to_package_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ownership_path = root / "ownership-content.json"
            self.build_component_packages_with_ownership(root, ownership_path)
            document = release_ownership.load_fragment(ownership_path)
            self.assertTrue(document["artifacts"])
            for entry in document["artifacts"]:
                artifact = root / "output" / Path(str(entry["path"]).removeprefix("content/"))
                self.assertEqual(artifact.stat().st_size, entry["size"])
                self.assertEqual(hashlib.sha256(artifact.read_bytes()).hexdigest(), entry["sha256"])
            flattened = release_ownership.flatten_entries(document)
            self.assertIn(
                "_x86qw/component.json",
                {member["path"] for member in next(
                    entry for entry in document["artifacts"] if entry["path"].endswith("test-component-1.2.3.zip")
                )["members"]},
            )
            self.assertTrue(flattened)

    def build_component_packages_with_ownership(self, root: Path, ownership_path: Path) -> dict[str, object]:
        with mock.patch.object(
            component_builder,
            "load_source_context",
            return_value=self.component_context(),
        ), mock.patch.object(
            component_builder,
            "resolve_component_payloads",
            return_value=self.component_payloads(),
        ):
            return component_builder.build_packages(
                root / "dist",
                root / "output",
                ownership_output=ownership_path,
                project_ref=PROJECT_REF,
            )

    def test_component_ownership_uses_only_the_explicit_project_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ownership_path = root / "ownership-content.json"
            self.build_component_packages_with_ownership(root, ownership_path)
            document = release_ownership.load_fragment(ownership_path)
            project_urls = {
                str(entry["license_url"])
                for entry in release_ownership.flatten_entries(document).values()
                if entry["ownership"] == "project"
            }
            self.assertEqual(
                {f"https://github.com/x86dx2/x86qw/blob/{PROJECT_REF}/LICENSE"},
                project_urls,
            )

    def test_component_ownership_requires_a_complete_project_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(
                component_builder,
                "load_source_context",
                return_value=self.component_context(),
            ), mock.patch.object(
                component_builder,
                "resolve_component_payloads",
                return_value=self.component_payloads(),
            ):
                with self.assertRaisesRegex(ValueError, "project-ref"):
                    component_builder.build_packages(
                        root / "dist",
                        root / "output",
                        ownership_output=root / "ownership.json",
                    )

    def test_generic_component_package_bytes_do_not_depend_on_ownership_fragment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generic = self.build_component_packages(root / "generic")
            ownership = self.build_component_packages_with_ownership(
                root / "ownership", root / "ownership.json",
            )
            generic_path = next((root / "generic/output").rglob(str(generic["packages"][0]["filename"])))
            ownership_path = next((root / "ownership/output").rglob(str(ownership["packages"][0]["filename"])))
            self.assertEqual(generic_path.read_bytes(), ownership_path.read_bytes())

    def test_modern_installer_builder_emits_project_owned_nested_facts(self) -> None:
        with tempfile.TemporaryDirectory(prefix=".installer-ownership-") as temporary:
            root = Path(temporary)
            ownership_path = root / "ownership-installer.json"
            installer_builder.build(root / "packages", "1.0.0", ownership_output=ownership_path)
            document = release_ownership.load_fragment(ownership_path)
            entry = document["artifacts"][0]
            self.assertEqual("project", entry["ownership"])
            self.assertEqual(
                "MIT",
                next(member for member in entry["members"] if member["path"] == "x86qw-installer-1.0.0/x86qw.pyz")["license_concluded"],
            )
            self.assertIn(
                "x86qw_runtime/io/archive.py",
                {member["source"] for member in next(
                    member for member in entry["members"] if member["path"] == "x86qw-installer-1.0.0/x86qw.pyz"
                )["members"]},
            )

    def test_core_package_never_writes_through_an_existing_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            distribution = self.core_distribution(root)
            victim = root / "personal.txt"
            victim.write_bytes(b"personal-core-data")
            artifact = (
                root / "output" / f"core-{core_builder.VERSION}"
                / f"{core_builder.PACKAGE}-{core_builder.VERSION}.zip"
            )
            artifact.parent.mkdir(parents=True)
            try:
                artifact.symlink_to(victim)
            except OSError as error:
                self.skipTest(f"symlink indisponível: {error}")
            with self.assertRaises(ValueError):
                core_builder.build_core_package(distribution, root / "output")
            self.assertEqual(b"personal-core-data", victim.read_bytes())
            self.assertTrue(artifact.is_symlink())

    def test_component_package_never_writes_through_an_existing_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            victim = root / "personal.txt"
            victim.write_bytes(b"personal-component-data")
            artifact = (
                root / "output" / f"components-{'a' * 40}"
                / "test-component-1.2.3.zip"
            )
            artifact.parent.mkdir(parents=True)
            try:
                artifact.symlink_to(victim)
            except OSError as error:
                self.skipTest(f"symlink indisponível: {error}")
            with self.assertRaises(ValueError):
                self.build_component_packages(root)
            self.assertEqual(b"personal-component-data", victim.read_bytes())
            self.assertTrue(artifact.is_symlink())

    def test_core_package_metadata_uses_the_same_source_snapshot_as_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            distribution = self.core_distribution(root)
            original = (distribution / "game-data/id1/pak0.pak").read_bytes()
            real_read = core_builder.read_regular_file

            def read_then_replace(path: Path, **kwargs: object) -> bytes:
                payload = real_read(path, **kwargs)
                if path.name == "pak0.pak":
                    path.write_bytes(b"PACKchanged-after-snapshot")
                return payload

            with mock.patch.object(
                core_builder, "read_regular_file", side_effect=read_then_replace,
            ):
                record = core_builder.build_core_package(distribution, root / "output")
            artifact = next((root / "output").rglob(str(record["filename"])))
            plan = archive_runtime.scan_archive(artifact)
            payloads = archive_runtime.read_archive_members(plan, (
                "payload/id1/pak0.pak", "_x86qw/component.json",
            ))
            metadata = json.loads(payloads["_x86qw/component.json"])
            self.assertEqual(original, payloads["payload/id1/pak0.pak"])
            self.assertEqual(
                hashlib.sha256(original).hexdigest(),
                metadata["members"][0]["sha256"],
            )

    def test_installer_rejects_temporary_replaced_after_bundle_validation(self) -> None:
        version = installer_builder.VERSION
        with tempfile.TemporaryDirectory(
            prefix=".builder-binding-", dir=ROOT / "dist",
        ) as temporary:
            output = Path(temporary) / "packages"

            def validate_then_replace(source: Path | bytes, requested: str):
                plan = archive_runtime.validate_installer_bundle(source, requested)
                self.assertIsInstance(source, Path)
                if source.name.startswith(".installer-"):
                    source.write_bytes(REPLACEMENT)
                return plan

            with mock.patch.object(
                installer_builder,
                "validate_installer_bundle",
                side_effect=validate_then_replace,
            ):
                with self.assertRaisesRegex(ValueError, "archive validation"):
                    installer_builder.build(output, version)
            target = output / version / f"x86qw-installer-{version}.zip"
            self.assertFalse(target.exists())

    def test_installer_revalidates_the_promoted_target_before_accepting_it(self) -> None:
        version = installer_builder.VERSION
        with tempfile.TemporaryDirectory(
            prefix=".builder-binding-", dir=ROOT / "dist",
        ) as temporary:
            output = Path(temporary) / "packages"
            target = output / version / f"x86qw-installer-{version}.zip"
            real_link = os.link

            def link_then_mutate(
                source: str | os.PathLike[str],
                destination: str | os.PathLike[str],
                **kwargs: object,
            ) -> None:
                real_link(source, destination, **kwargs)
                if Path(destination) == target:
                    target.write_bytes(REPLACEMENT)

            with mock.patch.object(
                installer_builder.os, "link", side_effect=link_then_mutate,
            ):
                with self.assertRaisesRegex(ValueError, "preserved for inspection"):
                    installer_builder.build(output, version)
            self.assertEqual(REPLACEMENT, target.read_bytes())

    def test_installer_never_overwrites_a_concurrent_target(self) -> None:
        version = installer_builder.VERSION
        personal = b"personal-concurrent-data"
        with tempfile.TemporaryDirectory(
            prefix=".builder-binding-", dir=ROOT / "dist",
        ) as temporary:
            output = Path(temporary) / "packages"
            target = output / version / f"x86qw-installer-{version}.zip"
            real_link = os.link

            def plant_then_link(
                source: str | os.PathLike[str],
                destination: str | os.PathLike[str],
                **kwargs: object,
            ) -> None:
                Path(destination).write_bytes(personal)
                real_link(source, destination, **kwargs)

            with mock.patch.object(
                installer_builder.os, "link", side_effect=plant_then_link,
            ):
                with self.assertRaises(ValueError):
                    installer_builder.build(output, version)
            self.assertEqual(personal, target.read_bytes())

    def test_core_package_rejects_a_symlinked_output_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            distribution = self.core_distribution(root)
            output = root / "output"
            output.mkdir()
            personal = root / "personal"
            personal.mkdir()
            release_root = output / f"core-{core_builder.VERSION}"
            try:
                release_root.symlink_to(personal, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink indisponível: {error}")

            with self.assertRaisesRegex(ValueError, "link or reparse point"):
                core_builder.build_core_package(distribution, output)

            self.assertEqual([], list(personal.iterdir()))

    def test_latest_pointer_never_replaces_a_concurrent_regular_file(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".latest-binding-", dir=ROOT / "dist",
        ) as temporary:
            package_root = Path(temporary)
            version = installer_builder.VERSION
            installer_builder.build(package_root, version)
            latest = package_root / "latest"
            latest.unlink()
            personal = b"personal-latest-data"
            real_symlink_to = Path.symlink_to

            def plant_then_create(
                path: Path,
                target: str | os.PathLike[str],
                target_is_directory: bool = False,
            ) -> None:
                if path == latest:
                    path.write_bytes(personal)
                real_symlink_to(
                    path, target, target_is_directory=target_is_directory,
                )

            with mock.patch.object(Path, "symlink_to", new=plant_then_create):
                with self.assertRaisesRegex(ValueError, "changed concurrently"):
                    installer_builder.update_latest_link(package_root)

            self.assertEqual(personal, latest.read_bytes())

    def test_installer_history_rejects_a_named_non_archive(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix=".history-binding-", dir=ROOT / "dist",
        ) as temporary:
            package_root = Path(temporary)
            version = "9.9.9"
            version_root = package_root / version
            version_root.mkdir()
            (version_root / f"x86qw-installer-{version}.zip").write_bytes(b"not-a-zip")

            with self.assertRaisesRegex(ValueError, "canonical archive validation"):
                installer_builder.update_latest_link(package_root)

            self.assertFalse(os.path.lexists(package_root / "latest"))

    def test_installer_history_accepts_every_immutable_repository_bundle(self) -> None:
        packages = installer_builder.package_results(ROOT / "dist/installer/packages")
        self.assertGreaterEqual(len(packages), 1)
        self.assertEqual(installer_builder.VERSION, packages[-1]["version"])
        self.assertEqual(
            {path.name for path in (ROOT / "dist/installer/packages").iterdir()}
            - {"latest"},
            {str(package["version"]) for package in packages},
        )

    def test_installer_history_orders_rc_before_the_matching_stable_release(self) -> None:
        with tempfile.TemporaryDirectory(prefix=".semver-history-", dir=ROOT / "dist") as temporary:
            package_root = Path(temporary)
            for version in ("1.0.0-rc.1", "1.0.0"):
                version_root = package_root / version
                version_root.mkdir()
                (version_root / f"x86qw-installer-{version}.zip").write_bytes(version.encode())

            plan = SimpleNamespace(source_size=1, source_sha256="a" * 64)
            with mock.patch.object(
                installer_builder, "validate_installer_history_bundle", return_value=plan,
            ), mock.patch.object(installer_builder, "read_archive_members"):
                packages = installer_builder.package_results(package_root)

            self.assertEqual(
                ["1.0.0-rc.1", "1.0.0"],
                [str(package["version"]) for package in packages],
            )

    def test_installer_history_rejects_a_bundle_changed_after_its_scan(self) -> None:
        version = installer_builder.VERSION
        with tempfile.TemporaryDirectory(
            prefix=".history-binding-", dir=ROOT / "dist",
        ) as temporary:
            package_root = Path(temporary)
            installer_builder.build(package_root, version)
            (package_root / "latest").unlink()
            target = package_root / version / f"x86qw-installer-{version}.zip"

            def validate_then_replace(source: Path | bytes, requested: str):
                plan = archive_runtime.validate_installer_history_bundle(source, requested)
                if Path(source) == target:
                    target.write_bytes(REPLACEMENT)
                return plan

            with mock.patch.object(
                installer_builder,
                "validate_installer_history_bundle",
                side_effect=validate_then_replace,
            ):
                with self.assertRaisesRegex(ValueError, "canonical archive validation"):
                    installer_builder.update_latest_link(package_root)

            self.assertFalse(os.path.lexists(package_root / "latest"))

    def test_installer_records_the_validated_target_plan_digest_and_size(self) -> None:
        version = installer_builder.VERSION
        with tempfile.TemporaryDirectory(
            prefix=".builder-binding-", dir=ROOT / "dist",
        ) as temporary:
            output = Path(temporary) / "packages"
            result = installer_builder.build(output, version)
            target = output / version / f"x86qw-installer-{version}.zip"
            payload = target.read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), result["sha256"])
            self.assertEqual(len(payload), result["size"])

    def test_installer_record_uses_immutable_project_license_tag(self) -> None:
        record = installer_builder.installer_record(
            {
                "version": "1.0.0",
                "filename": "x86qw-installer-1.0.0.zip",
                "size": 1,
                "sha256": "a" * 64,
                "distribution_path": "installer/packages/1.0.0/x86qw-installer-1.0.0.zip",
            },
            current=False,
        )
        self.assertEqual(
            "https://github.com/x86dx2/x86qw/blob/x86qw-installer-1.0.0/LICENSE",
            record["license_url"],
        )

    def test_installer_record_uses_mit_only_for_new_owned_bundles(self) -> None:
        modern = installer_builder.installer_record(
            {
                "version": "1.0.0",
                "filename": "x86qw-installer-1.0.0.zip",
                "size": 1,
                "sha256": "a" * 64,
                "distribution_path": "installer/packages/1.0.0/x86qw-installer-1.0.0.zip",
            },
            current=False,
        )
        historical = installer_builder.installer_record(
            {
                "version": "0.7.3",
                "filename": "x86qw-installer-0.7.3.zip",
                "size": 1,
                "sha256": "b" * 64,
                "distribution_path": "installer/packages/0.7.3/x86qw-installer-0.7.3.zip",
            },
            current=False,
        )
        self.assertEqual("MIT", modern["license"])
        self.assertEqual("x86qw-project-terms", historical["license"])
        self.assertEqual(
            "https://github.com/x86dx2/x86qw",
            historical["license_url"],
        )

    def test_modern_installer_carries_exact_project_notices_in_both_layers(self) -> None:
        with tempfile.TemporaryDirectory(prefix=".modern-bundle-") as temporary:
            output = Path(temporary) / "packages"
            result = installer_builder.build(output, "1.0.0")
            archive_path = output / "1.0.0" / "x86qw-installer-1.0.0.zip"
            outer = archive_runtime.validate_installer_bundle(archive_path, "1.0.0")
            prefix = "x86qw-installer-1.0.0"
            payloads = archive_runtime.read_archive_members(
                outer,
                (
                    f"{prefix}/LICENSE",
                    f"{prefix}/NOTICE",
                    f"{prefix}/x86qw.pyz",
                ),
            )
            self.assertEqual((ROOT / "LICENSE").read_bytes(), payloads[f"{prefix}/LICENSE"])
            self.assertEqual((ROOT / "NOTICE").read_bytes(), payloads[f"{prefix}/NOTICE"])
            nested = archive_runtime.scan_archive(payloads[f"{prefix}/x86qw.pyz"])
            nested_payloads = archive_runtime.read_archive_members(
                nested, ("_x86qw/LICENSE", "_x86qw/NOTICE"),
            )
            self.assertEqual((ROOT / "LICENSE").read_bytes(), nested_payloads["_x86qw/LICENSE"])
            self.assertEqual((ROOT / "NOTICE").read_bytes(), nested_payloads["_x86qw/NOTICE"])
            self.assertEqual(9, len(outer.members))
            self.assertEqual(hashlib.sha256(archive_path.read_bytes()).hexdigest(), result["sha256"])

    def test_historical_installer_build_keeps_the_seven_member_outer_layout(self) -> None:
        with tempfile.TemporaryDirectory(prefix=".legacy-bundle-") as temporary:
            output = Path(temporary) / "packages"
            installer_builder.build(output, "0.7.3")
            archive_path = output / "0.7.3" / "x86qw-installer-0.7.3.zip"
            plan = archive_runtime.validate_installer_bundle(archive_path, "0.7.3")
            self.assertEqual(7, len(plan.members))

    def test_release_candidate_builder_accepts_private_output_outside_dist(self) -> None:
        """Candidate preparation must not mutate or require the checkout path."""

        version = installer_builder.VERSION
        with tempfile.TemporaryDirectory(prefix=".candidate-builder-") as temporary:
            output = Path(temporary) / "installer-packages"
            result = installer_builder.build(output, version)
            self.assertEqual(
                f"installer/packages/{version}/x86qw-installer-{version}.zip",
                result["distribution_path"],
            )
            self.assertTrue(
                (output / version / f"x86qw-installer-{version}.zip").is_file(),
            )


if __name__ == "__main__":
    unittest.main()
