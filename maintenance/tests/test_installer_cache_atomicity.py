import contextlib
import errno
import hashlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from x86qw_runtime.io import atomic as atomic_io
from x86qw_runtime.io import private_fs


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "install_qw_cache_atomicity",
    ROOT / "dist/installer/bin/manager.py",
)
install_qw = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = install_qw
SPEC.loader.exec_module(install_qw)


class InstallerCacheAtomicityTests(unittest.TestCase):
    def setUp(self):
        install_qw.console.configure(verbose=False, no_color=True)

    def make_installer(self, root: Path):
        project = root / "project"
        target = project / "quake-world"
        cache = root / "cache" / "x86qw"
        target.mkdir(parents=True)
        cache.parent.mkdir()
        installer = install_qw.Installer(project, target, cache)
        installer.stage = target / ".stage"
        installer.stage.mkdir()
        return installer, cache

    @staticmethod
    def configure_client_archive(
        installer, payload: bytes, *, distribution_path: str = ""
    ) -> None:
        installer.spec = install_qw.PlatformSpec(
            "linux",
            "Linux amd64",
            "amd64",
            "ezquake.zip",
            ".zip",
            "ezquake-linux-x86_64",
            "ezquake-linux-x86_64",
            "ezquake/ezquake-linux-x86_64",
            "ezquake/ezquake-linux-x86_64",
            ".x86qw/clients/ezquake/stable.receipt",
            ".x86qw/clients/ezquake/nightly.receipt",
        )
        installer.channel = "stable"
        installer.selected_version = "3.6.9"
        installer.app_archive_name = "ezquake.zip"
        installer.app_expected_checksum = hashlib.sha256(payload).hexdigest()
        installer.app_checksum_kind = "sha256"
        installer.app_expected_size = len(payload)
        installer.app_distribution_path = distribution_path
        installer.app_urls = ("https://example.invalid/ezquake.zip",)
        installer.app_url = installer.app_urls[0]

    def test_cache_marker_is_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, cache = self.make_installer(Path(temporary))

            with contextlib.redirect_stdout(io.StringIO()):
                installer.prepare_cache()

            marker = cache / install_qw.CACHE_MARKER_NAME
            private_fs.validate_private_file(marker)
            self.assertEqual(
                install_qw.CACHE_MARKER_VALUE + "\n",
                marker.read_text(encoding="utf-8"),
            )

    def test_cache_discovery_does_not_reprotect_a_valid_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, cache = self.make_installer(Path(temporary))
            cache.mkdir()
            marker = cache / install_qw.CACHE_MARKER_NAME
            marker.write_text(
                install_qw.CACHE_MARKER_VALUE + "\n", encoding="utf-8",
            )
            private_fs.protect_private_file(marker)
            protected: list[Path] = []

            with mock.patch.object(
                private_fs,
                "protect_private_file",
                side_effect=lambda path: protected.append(path),
            ):
                installer.validate_cache_marker_at(
                    cache,
                    install_qw.CACHE_MARKER_NAME,
                    install_qw.CACHE_MARKER_VALUE,
                )
                roots = installer.owned_cache_roots(include_legacy=False)

            self.assertEqual([cache.resolve()], roots)
            self.assertEqual([], protected)

    @unittest.skipIf(os.name == "nt", "POSIX permission normalization")
    def test_prepare_cache_repairs_a_legitimate_insecure_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, cache = self.make_installer(Path(temporary))
            cache.mkdir()
            marker = cache / install_qw.CACHE_MARKER_NAME
            marker.write_text(
                install_qw.CACHE_MARKER_VALUE + "\n", encoding="utf-8",
            )
            marker.chmod(0o644)

            with contextlib.redirect_stdout(io.StringIO()):
                installer.prepare_cache()

            private_fs.validate_private_file(marker)
            self.assertEqual(
                install_qw.CACHE_MARKER_VALUE + "\n",
                marker.read_text(encoding="utf-8"),
            )

    @unittest.skipIf(os.name == "nt", "POSIX inode and mode race")
    def test_prepare_cache_does_not_protect_a_replacement_marker(self):
        """Permission repair must stay bound to the marker that was inspected."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, cache = self.make_installer(Path(temporary))
            cache.mkdir()
            marker = cache / install_qw.CACHE_MARKER_NAME
            marker.write_text(
                install_qw.CACHE_MARKER_VALUE + "\n", encoding="utf-8",
            )
            marker.chmod(0o644)
            marker = marker.resolve(strict=True)
            parked = marker.parent / "original-marker"
            concurrent = b"personal marker\n"
            real_open = os.open
            replaced = False

            def replace_then_open(path, flags, *args, **kwargs):
                nonlocal replaced
                if Path(path) == marker and not replaced:
                    replaced = True
                    marker.rename(parked)
                    marker.write_bytes(concurrent)
                    marker.chmod(0o644)
                return real_open(path, flags, *args, **kwargs)

            with mock.patch.object(
                private_fs.os, "open", side_effect=replace_then_open,
            ), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(install_qw.InstallerError):
                    installer.prepare_cache()

            self.assertTrue(replaced)
            self.assertEqual(concurrent, marker.read_bytes())
            self.assertEqual(0o644, marker.stat().st_mode & 0o777)
            self.assertEqual(
                install_qw.CACHE_MARKER_VALUE + "\n",
                parked.read_text(encoding="utf-8"),
            )

    def test_cache_marker_atomic_write_failure_leaves_no_partial_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, cache = self.make_installer(Path(temporary))

            with mock.patch.object(
                install_qw,
                "atomic_write_bytes",
                side_effect=install_qw.AtomicWriteError("simulated marker write failure"),
            ), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(
                install_qw.InstallerError,
            ):
                installer.prepare_cache()

            self.assertFalse((cache / install_qw.CACHE_MARKER_NAME).exists())
            self.assertEqual([], list(cache.iterdir()))

    def test_cache_claim_refuses_content_raced_into_new_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, cache = self.make_installer(Path(temporary))
            resolved_cache = cache.resolve(strict=False)
            real_create = private_fs.create_private_directory

            def inject_foreign(path):
                result = real_create(path)
                if Path(path) == resolved_cache:
                    (Path(path) / "foreign.txt").write_text(
                        "personal\n", encoding="utf-8",
                    )
                return result

            with mock.patch.object(
                private_fs,
                "create_private_directory",
                side_effect=inject_foreign,
            ):
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(
                    install_qw.InstallerError,
                    "não pertencem ao instalador",
                ):
                    installer.prepare_cache()

            self.assertEqual("personal\n", (cache / "foreign.txt").read_text(encoding="utf-8"))
            self.assertFalse((cache / install_qw.CACHE_MARKER_NAME).exists())

    def test_client_local_source_mutation_is_rejected_before_cache_publication(self):
        original = b"verified-client"
        mutated = b"tampered-client"
        self.assertEqual(len(original), len(mutated))
        with tempfile.TemporaryDirectory() as temporary:
            installer, _ = self.make_installer(Path(temporary))
            relative = "clients/ezquake.zip"
            source = installer.project_root / "dist" / relative
            source.parent.mkdir(parents=True)
            source.write_bytes(original)
            self.configure_client_archive(
                installer, original, distribution_path=relative,
            )
            installer.prepare_cache()
            real_distribution_artifact = installer.distribution_artifact

            def mutate_after_validation(*args, **kwargs):
                candidate = real_distribution_artifact(*args, **kwargs)
                assert candidate is not None
                candidate.write_bytes(mutated)
                return candidate

            with mock.patch.object(
                installer,
                "distribution_artifact",
                side_effect=mutate_after_validation,
            ), contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(
                install_qw.InstallerError,
                "SHA-256|cache",
            ):
                installer.ensure_archive()

            assert installer.cache_bin is not None
            self.assertFalse(
                (installer.cache_bin / "stable-3.6.9-ezquake.zip").exists()
            )

    def test_component_copy_failure_preserves_valid_artifact_that_won_the_race(self):
        payload = b"verified-component"
        with tempfile.TemporaryDirectory() as temporary:
            installer, cache = self.make_installer(Path(temporary))
            relative = "mods/component.zip"
            source = installer.project_root / "dist" / relative
            source.parent.mkdir(parents=True)
            source.write_bytes(payload)
            package = {
                "package": "component",
                "version": "1",
                "filename": "component.zip",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "distribution_path": relative,
                "urls": ["https://example.invalid/component.zip"],
            }
            artifact = cache / "components/component.zip"
            real_distribution_artifact = installer.distribution_artifact
            installer.prepare_cache()

            def competing_publication(*args, **kwargs):
                candidate = real_distribution_artifact(*args, **kwargs)
                artifact.parent.mkdir(parents=True, exist_ok=True)
                artifact.write_bytes(payload)
                return candidate

            with mock.patch.object(
                installer,
                "distribution_artifact",
                side_effect=competing_publication,
            ), mock.patch.object(
                atomic_io.private_fs,
                "replace_open_private_file",
                side_effect=OSError("simulated cache promotion failure"),
            ), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(
                install_qw.InstallerError,
            ):
                installer.download_component_package(package)

            self.assertEqual(payload, artifact.read_bytes())

    def test_client_download_publication_does_not_move_across_filesystems(self):
        payload = b"downloaded-client"
        with tempfile.TemporaryDirectory() as temporary:
            installer, _ = self.make_installer(Path(temporary))
            self.configure_client_archive(installer, payload)
            installer.prepare_cache()

            def download(_urls, destination, **_options):
                destination.write_bytes(payload)
                return b"", installer.app_url

            real_replace = Path.replace

            def reject_stage_move(path, target):
                if path.parent == installer.stage:
                    raise OSError(errno.EXDEV, "cross-device link")
                return real_replace(path, target)

            with mock.patch.object(
                installer.remote,
                "get_mirrors",
                side_effect=download,
            ), mock.patch.object(
                Path,
                "replace",
                autospec=True,
                side_effect=reject_stage_move,
            ), contextlib.redirect_stdout(io.StringIO()):
                archive = installer.ensure_archive()

            self.assertEqual(payload, archive.read_bytes())

    def test_component_download_publication_does_not_move_across_filesystems(self):
        payload = b"downloaded-component"
        with tempfile.TemporaryDirectory() as temporary:
            installer, _ = self.make_installer(Path(temporary))
            package = {
                "package": "component",
                "version": "1",
                "filename": "component.zip",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "urls": ["https://example.invalid/component.zip"],
            }

            def download(_urls, destination, **_options):
                destination.write_bytes(payload)
                return b"", str(package["urls"][0])

            real_replace = Path.replace

            def reject_stage_move(path, target):
                if path.parent == installer.stage:
                    raise OSError(errno.EXDEV, "cross-device link")
                return real_replace(path, target)

            with mock.patch.object(
                installer.remote,
                "get_mirrors",
                side_effect=download,
            ), mock.patch.object(
                Path,
                "replace",
                autospec=True,
                side_effect=reject_stage_move,
            ), contextlib.redirect_stdout(io.StringIO()):
                artifact = installer.download_component_package(package)

            self.assertEqual(payload, artifact.read_bytes())


if __name__ == "__main__":
    unittest.main()
