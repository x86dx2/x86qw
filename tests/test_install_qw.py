import contextlib
import importlib.util
import io
import os
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("install_qw", ROOT / "install-qw.py")
install_qw = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = install_qw
SPEC.loader.exec_module(install_qw)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        install_qw.console.configure(verbose=False, no_color=True)

    def make_installer(self, root):
        project = root / "project"
        target = project / "quake-world"
        cache = root / "cache" / "x86-qw"
        target.mkdir(parents=True)
        cache.parent.mkdir()
        return install_qw.Installer(project, target, cache), target, cache

    def test_cancel_before_selection_leaves_no_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        installer.install()
            installer.cleanup_stage()
            self.assertFalse((target / ".install").exists())
            self.assertEqual([], list(target.glob(".quake-install.*")))

    def test_invalid_platform_and_channel_are_explained_and_reprompted(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                with mock.patch("builtins.input", side_effect=["outro", "2", "beta", "1"]):
                    self.assertEqual("linux", installer.choose_platform().key)
                    self.assertEqual("stable", installer.choose_channel())
            rendered = output.getvalue()
            self.assertIn("Opção inválida. Digite 1, 2 ou 3.", rendered)
            self.assertIn("Sistema selecionado: Linux x86_64", rendered)
            self.assertIn("Opção inválida. Digite 1 para stable ou 2 para nightly.", rendered)
            self.assertIn("Canal selecionado: stable", rendered)

    def test_nightly_catalog_can_expand_without_overwhelming_initial_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["macos"]
            installer.channel = "nightly"
            catalog = []
            for day in range(20, 5, -1):
                version = f"202607{day:02d}-120000_abcdef0"
                name = version + installer.spec.nightly_suffix
                catalog.append((version, f"{installer.spec.nightly_root}/{name}", "-"))

            def fake_http_get(url, destination=None, headers=None):
                del url, destination, headers
                return ("0" * 32 + f"  {installer.app_archive_name}\n").encode()

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                with mock.patch.object(installer, "nightly_catalog", return_value=catalog):
                    with mock.patch.object(installer, "http_get", side_effect=fake_http_get):
                        with mock.patch("builtins.input", side_effect=["t", "13"]):
                            installer.choose_release()
            rendered = output.getvalue()
            self.assertIn("... mais 3 versões. Digite t para mostrar todas.", rendered)
            self.assertGreaterEqual(rendered.count(catalog[12][0]), 1)
            self.assertIn(f"Versão selecionada: {catalog[12][0]}", rendered)

    def test_nquake_confirmation_defaults_to_no_and_reprompts_invalid_answer(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            with mock.patch("builtins.input", return_value=""):
                self.assertFalse(installer.confirm_nquake())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                with mock.patch("builtins.input", side_effect=["talvez", "sim"]):
                    self.assertTrue(installer.confirm_nquake())
            self.assertIn("Resposta inválida. Digite s para sim ou n para não.", output.getvalue())

    def test_human_readable_sizes(self):
        self.assertEqual("0 B", install_qw.format_bytes(0))
        self.assertEqual("1.0 KiB", install_qw.format_bytes(1024))
        self.assertEqual("1.5 MiB", install_qw.format_bytes(1572864))

    def test_technical_details_are_opt_in(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            install_qw.console.detail("oculto")
            install_qw.console.configure(verbose=True, no_color=True)
            install_qw.console.detail("visível")
        self.assertNotIn("oculto", output.getvalue())
        self.assertIn("visível", output.getvalue())

    def test_cache_is_owned_and_cleanup_removes_only_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, cache = self.make_installer(Path(temporary))
            installer.prepare_cache()
            payload = cache / "bin/artifact.zip"
            payload.parent.mkdir()
            payload.write_bytes(b"artifact")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.cleanup_cache()
            self.assertFalse(cache.exists())
            self.assertTrue(cache.parent.exists())

    def test_unmarked_cache_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, cache = self.make_installer(Path(temporary))
            cache.mkdir()
            (cache / "foreign").write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(install_qw.InstallerError, "não pertencem ao instalador"):
                installer.prepare_cache()
            self.assertEqual("keep", (cache / "foreign").read_text(encoding="utf-8"))

    def test_purge_preserves_id1_and_removes_owned_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, cache = self.make_installer(Path(temporary))
            (target / "id1").mkdir()
            (target / "id1/pak0.pak").write_bytes(b"keep")
            (target / "qw").mkdir()
            (target / "qw/remove.txt").write_text("remove", encoding="utf-8")
            (target / "personal.txt").write_text("remove", encoding="utf-8")
            installer.prepare_cache()
            (cache / "payload").write_text("remove", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.purge()
            self.assertEqual([target / "id1"], list(target.iterdir()))
            self.assertEqual(b"keep", (target / "id1/pak0.pak").read_bytes())
            self.assertFalse(cache.exists())

    def test_zip_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.zip"
            destination = root / "output"
            destination.mkdir()
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("../escape", b"bad")
            with self.assertRaisesRegex(install_qw.InstallerError, "unsafe archive path"):
                install_qw.safe_extract_zip(archive, destination)
            self.assertFalse((root / "escape").exists())

    def test_windows_drive_archive_path_is_rejected(self):
        with self.assertRaisesRegex(install_qw.InstallerError, "unsafe archive path"):
            install_qw.archive_relative_path("C:/escape")

    def test_portable_binary_formats(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            linux = Path(temporary) / "ezquake.AppImage"
            elf = bytearray(64)
            elf[:5] = b"\x7fELF\x02"
            struct.pack_into("<H", elf, 18, 62)
            linux.write_bytes(elf)
            if os.name != "nt":
                linux.chmod(0o755)
            self.assertEqual(64, len(installer.inspect_portable_binary(install_qw.PLATFORMS["linux"], linux)))

            windows = Path(temporary) / "ezquake.exe"
            pe = bytearray(512)
            pe[:2] = b"MZ"
            struct.pack_into("<I", pe, 0x3C, 0x80)
            pe[0x80:0x84] = b"PE\0\0"
            struct.pack_into("<H", pe, 0x84, 0x8664)
            struct.pack_into("<H", pe, 0x98, 0x20B)
            windows.write_bytes(pe)
            self.assertEqual(64, len(installer.inspect_portable_binary(install_qw.PLATFORMS["windows"], windows)))

    def test_recursive_delete_does_not_follow_symlinks(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            protected = outside / "keep"
            protected.write_text("keep", encoding="utf-8")
            owned = root / "owned"
            owned.mkdir()
            try:
                (owned / "link").symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation is not permitted")
            install_qw.remove_path(owned)
            self.assertEqual("keep", protected.read_text(encoding="utf-8"))

    @unittest.skipUnless(os.environ.get("X86_QW_NETWORK_TESTS") == "1", "network tests disabled")
    def test_latest_official_artifacts_for_every_platform_and_channel(self):
        for platform_name in install_qw.PLATFORMS:
            for channel in ("stable", "nightly"):
                with self.subTest(platform=platform_name, channel=channel):
                    with tempfile.TemporaryDirectory() as temporary:
                        installer, target, _ = self.make_installer(Path(temporary))
                        installer.spec = install_qw.PLATFORMS[platform_name]
                        installer.channel = channel
                        installer.stage = target / ".integration-stage"
                        installer.stage.mkdir()
                        with contextlib.redirect_stdout(io.StringIO()):
                            with mock.patch("builtins.input", return_value="1"):
                                installer.choose_release()
                            installer.prepare_cache()
                            archive = installer.ensure_archive()
                            installer.prepare_runtime(archive)
                            receipt = installer.stage / "receipt"
                            installer.write_ezquake_receipt(receipt)
                        self.assertTrue(receipt.is_file())


if __name__ == "__main__":
    unittest.main()
