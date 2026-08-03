from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from maintenance.tools import build_component_packages as component_builder
from maintenance.tools import build_core_package as core_builder
from maintenance.tools import build_installer_bundle as installer_builder
from x86qw_runtime.io import archive as archive_runtime


ROOT = Path(__file__).resolve().parents[2]
FORGED_DIGEST = "f" * 64
REPLACEMENT = b"replacement-after-canonical-validation"


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

    def test_core_package_records_the_validated_plan_digest_and_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            distribution = self.core_distribution(root)
            with mock.patch.object(
                core_builder, "file_sha256", return_value=FORGED_DIGEST,
            ):
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
            with mock.patch.object(
                component_builder,
                "file_sha256",
                return_value=FORGED_DIGEST,
            ):
                manifest = self.build_component_packages(root)
            record = manifest["packages"][0]
            artifact = next((root / "output").rglob(str(record["filename"])))
            payload = artifact.read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), record["sha256"])
            self.assertEqual(len(payload), record["size"])

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
            real_replace = os.replace

            def replace_then_mutate(
                source: str | os.PathLike[str],
                destination: str | os.PathLike[str],
            ) -> None:
                real_replace(source, destination)
                if Path(destination) == target:
                    target.write_bytes(REPLACEMENT)

            with mock.patch.object(
                installer_builder.os, "replace", side_effect=replace_then_mutate,
            ):
                with self.assertRaisesRegex(ValueError, "archive validation"):
                    installer_builder.build(output, version)
            self.assertEqual(REPLACEMENT, target.read_bytes())

    def test_installer_records_the_validated_target_plan_digest_and_size(self) -> None:
        version = installer_builder.VERSION
        with tempfile.TemporaryDirectory(
            prefix=".builder-binding-", dir=ROOT / "dist",
        ) as temporary:
            output = Path(temporary) / "packages"
            with mock.patch.object(
                installer_builder, "sha256", return_value=FORGED_DIGEST,
            ):
                result = installer_builder.build(output, version)
            target = output / version / f"x86qw-installer-{version}.zip"
            payload = target.read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), result["sha256"])
            self.assertEqual(len(payload), result["size"])


if __name__ == "__main__":
    unittest.main()
