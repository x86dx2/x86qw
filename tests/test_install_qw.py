import contextlib
import importlib.util
import io
import json
import os
import socket
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

    def test_repository_bundles_the_registered_paks(self):
        expected = {
            "pak0.pak": install_qw.ID1_PAK0_SHA256,
            "pak1.pak": install_qw.ID1_PAK1_SHA256,
        }
        for name, digest in expected.items():
            with self.subTest(name=name):
                pak = ROOT / "dist/id1" / name
                self.assertTrue(pak.is_file())
                self.assertFalse(pak.is_symlink())
                with pak.open("rb") as source:
                    self.assertEqual(b"PACK", source.read(4))
                self.assertEqual(digest, install_qw.file_hash(pak))

    def test_cancel_before_selection_leaves_no_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            target.rmdir()
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        installer.install()
            installer.cleanup_stage()
            self.assertFalse(target.exists())

    def test_new_install_target_receives_bundled_registered_paks(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            target.rmdir()
            bundled = installer.project_root / "dist/id1"
            bundled.mkdir(parents=True)
            pak0 = b"PACK" + b"pak0"
            pak1 = b"PACK" + b"pak1"
            (bundled / "pak0.pak").write_bytes(pak0)
            (bundled / "pak1.pak").write_bytes(pak1)
            with mock.patch.object(install_qw, "ID1_PAK0_SHA256", install_qw.hashlib.sha256(pak0).hexdigest()):
                with mock.patch.object(install_qw, "ID1_PAK1_SHA256", install_qw.hashlib.sha256(pak1).hexdigest()):
                    installer.validate_target("install")
                    with contextlib.redirect_stdout(io.StringIO()):
                        installer.provision_install_target()
                    installer.check_paks()
            self.assertEqual(pak0, (target / "id1/pak0.pak").read_bytes())
            self.assertEqual(pak1, (target / "id1/pak1.pak").read_bytes())

    def test_missing_target_is_still_rejected_for_non_install_actions(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            target.rmdir()
            with self.assertRaisesRegex(install_qw.InstallerError, "não existe"):
                installer.validate_target("verify")

    def test_existing_pak_is_never_overwritten_by_the_bundled_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            bundled = installer.project_root / "dist/id1"
            bundled.mkdir(parents=True)
            valid = b"PACK" + b"registered"
            for name in ("pak0.pak", "pak1.pak"):
                (bundled / name).write_bytes(valid)
            (target / "id1").mkdir()
            existing = target / "id1/pak0.pak"
            existing.write_bytes(b"PACKpersonal")
            digest = install_qw.hashlib.sha256(valid).hexdigest()
            with mock.patch.object(install_qw, "ID1_PAK0_SHA256", digest):
                with mock.patch.object(install_qw, "ID1_PAK1_SHA256", digest):
                    with self.assertRaisesRegex(install_qw.InstallerError, "versão registrada"):
                        installer.provision_install_target()
            self.assertEqual(b"PACKpersonal", existing.read_bytes())

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

    def test_native_macos_install_rejects_an_open_ezquake(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["macos"]
            process = mock.Mock(returncode=0, stdout="1234\n", stderr="")
            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(install_qw.subprocess, "run", return_value=process):
                    with self.assertRaisesRegex(install_qw.InstallerError, "Feche o ezQuake"):
                        installer.ensure_macos_ezquake_closed()

    def test_native_macos_install_clears_stale_game_directory_preferences(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["macos"]
            closed = mock.Mock(returncode=1, stdout="", stderr="")
            deleted = mock.Mock(returncode=0, stdout="", stderr="")
            missing = mock.Mock(returncode=1, stdout="", stderr="Domain not found.")
            responses = [closed, deleted, missing, deleted]
            output = io.StringIO()
            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(install_qw.subprocess, "run", side_effect=responses) as run:
                    with contextlib.redirect_stdout(output):
                        installer.reset_macos_game_directory()
            self.assertEqual(4, run.call_count)
            for key, call in zip(install_qw.MACOS_DIRECTORY_KEYS, run.call_args_list[1:]):
                self.assertEqual(
                    ["defaults", "delete", install_qw.MACOS_PREFERENCES_DOMAIN, key],
                    call.args[0],
                )
            self.assertIn("Seleção antiga", output.getvalue())

    def test_macos_preferences_are_untouched_for_cross_platform_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["windows"]
            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(install_qw.subprocess, "run") as run:
                    installer.reset_macos_game_directory()
            run.assert_not_called()

    def test_nquake_startup_state_reports_pending_and_loaded(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            config = target / "ezquake/configs/config.cfg"
            config.parent.mkdir(parents=True)
            for marker, expected in (("1", "aguardando a primeira execução"), ("0", "carregadas pelo ezQuake")):
                with self.subTest(marker=marker):
                    config.write_text(f'set _nquake_first_startup "{marker}"\n', encoding="utf-8")
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        installer.report_nquake_startup_state(["nquake-bootstrap"])
                    self.assertIn(expected, output.getvalue())

    def test_nightly_catalog_can_expand_without_overwhelming_initial_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["macos"]
            installer.channel = "nightly"
            catalog = []
            for day in range(20, 5, -1):
                version = f"202607{day:02d}-120000_abcdef0"
                name = version + installer.spec.nightly_suffix
                url = f"https://downloads.x86.com.br/x86qw/{name}"
                catalog.append((version, (url,), "a" * 64))

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                with mock.patch.object(installer, "nightly_catalog", return_value=catalog):
                    with mock.patch("builtins.input", side_effect=["t", "13"]):
                        installer.choose_release()
            rendered = output.getvalue()
            self.assertIn("... mais 3 versões. Digite t para mostrar todas.", rendered)
            self.assertGreaterEqual(rendered.count(catalog[12][0]), 1)
            self.assertIn(f"Versão selecionada: {catalog[12][0]}", rendered)

    def test_x86qw_catalog_is_filtered_and_requires_redistribution_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["macos"]
            filename = installer.spec.stable_archive
            package = {
                "component": "ezquake", "version": "3.6.9", "channel": "stable",
                "platform": "macos", "architecture": "universal", "filename": filename,
                "size": 42, "sha256": "a" * 64,
                "origin_url": f"https://example.invalid/original/{filename}",
                "license": "GPL-2.0", "license_url": "https://example.invalid/LICENSE",
                "source_urls": ["https://example.invalid/source.tar.gz"],
                "redistribution_reviewed": True,
                "urls": [f"https://downloads.x86.com.br/x86qw/{filename}"],
            }
            catalog = {"format": 1, "project": "x86qw", "packages": [package]}
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "http_get", return_value=json.dumps(catalog).encode()):
                    self.assertEqual(
                        [("3.6.9", tuple(package["urls"]), "a" * 64)],
                        installer.stable_catalog(),
                    )
            package["redistribution_reviewed"] = False
            installer._public_catalog = None
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "http_get", return_value=json.dumps(catalog).encode()):
                    with self.assertRaises(install_qw.InstallerError):
                        installer.stable_catalog()

    def test_public_catalog_is_reused_between_client_and_components(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["macos"]
            catalog = json.loads((ROOT / "site/public/api/v1/catalog.json").read_text())
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "http_get", return_value=json.dumps(catalog).encode()) as get:
                    installer.stable_catalog()
                    installer.component_package_record("nquake-bootstrap")
            get.assert_called_once_with(install_qw.CATALOG_URL)

    def test_resilient_connection_uses_reachable_dns_address_without_waiting(self):
        class FakeSocket:
            def __init__(self, reachable):
                self.reachable = reachable
                self.closed = False
                self.timeout = None

            def setblocking(self, value):
                del value

            def connect_ex(self, address):
                del address
                return install_qw.errno.EINPROGRESS

            def getsockopt(self, level, option):
                del level, option
                return 0 if self.reachable else install_qw.errno.EHOSTUNREACH

            def settimeout(self, value):
                self.timeout = value

            def close(self):
                self.closed = True

        class FakeSelector:
            def __init__(self):
                self.registered = []

            def register(self, connection, event):
                del event
                self.registered.append(connection)

            def unregister(self, connection):
                self.registered.remove(connection)

            def get_map(self):
                return {id(connection): connection for connection in self.registered}

            def select(self, timeout):
                del timeout
                reachable = next(connection for connection in self.registered if connection.reachable)
                return [(mock.Mock(fileobj=reachable), None)]

            def close(self):
                pass

        sockets = [FakeSocket(False), FakeSocket(True)]
        candidates = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.0.2.1", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443)),
        ]
        with mock.patch.object(install_qw.socket, "getaddrinfo", return_value=candidates):
            with mock.patch.object(install_qw.socket, "socket", side_effect=sockets):
                with mock.patch.object(install_qw.selectors, "DefaultSelector", FakeSelector):
                    connection = install_qw.create_resilient_connection(("example.invalid", 443), timeout=2)
        self.assertIs(sockets[1], connection)
        self.assertTrue(sockets[0].closed)
        self.assertFalse(sockets[1].closed)
        self.assertEqual(2, sockets[1].timeout)

    def test_download_falls_back_to_the_next_catalog_mirror(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["macos"]
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            installer.prepare_cache()
            payload = b"verified archive"
            filename = installer.spec.stable_archive
            installer.selected_version = "3.6.9"
            installer.channel = "stable"
            installer.app_archive_name = filename
            installer.app_checksum_kind = "sha256"
            installer.app_expected_checksum = install_qw.hashlib.sha256(payload).hexdigest()
            installer.app_urls = (
                f"https://first.invalid/{filename}",
                f"https://second.invalid/{filename}",
            )
            installer.app_url = installer.app_urls[0]

            def fake_http_get(url, destination=None, headers=None):
                del headers
                if url == installer.app_urls[0]:
                    raise install_qw.InstallerError("first mirror unavailable")
                destination.write_bytes(payload)
                return b""

            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "http_get", side_effect=fake_http_get):
                    archive = installer.ensure_archive()
            self.assertEqual(payload, archive.read_bytes())
            self.assertEqual(installer.app_urls[1], installer.app_url)

    def test_nquake_confirmation_defaults_to_no_and_reprompts_invalid_answer(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            with mock.patch("builtins.input", return_value=""):
                self.assertFalse(installer.confirm_components())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                with mock.patch("builtins.input", side_effect=["talvez", "sim"]):
                    self.assertTrue(installer.confirm_components())
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
