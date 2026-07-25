import contextlib
import importlib.util
import io
import json
import os
import plistlib
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("install_qw_modern", ROOT / "install-qw.py")
install_qw = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = install_qw
SPEC.loader.exec_module(install_qw)


class ModernComponentTests(unittest.TestCase):
    def setUp(self):
        install_qw.console.configure(verbose=False, no_color=True)

    def make_installer(self, root):
        project = root / "project"
        target = project / "quake-world"
        cache = root / "cache" / "x86-qw"
        target.mkdir(parents=True)
        cache.parent.mkdir()
        return install_qw.Installer(project, target, cache), target, cache

    def write_client_receipt(self, target, client_key, version):
        client = install_qw.CLIENTS[client_key]
        platform = client.platforms["macos"]
        tag = version if client_key == "unezquake" else f"v{version}"
        artifact = platform.asset_name.format(version=version)
        metadata = target / ".install"
        metadata.mkdir(exist_ok=True)
        install_qw.write_table(metadata / Path(client.receipt("macos")).name, [
            ("format", "1"), ("client", client.key), ("platform", "macos"),
            ("architecture", platform.architecture), ("selection", tag),
            ("install_name", platform.runtime), ("bundle_version", version),
            ("artifact_name", artifact),
            ("artifact_url", f"https://github.com/{client.repository}/releases/download/{tag}/{artifact}"),
            ("artifact_sha256", "a" * 64), ("binary_sha256", "b" * 64),
        ])

    def test_new_actions_are_accepted(self):
        for action in ("clients", "maps", "presets", "hub"):
            with self.subTest(action=action):
                parsed = install_qw.parse_arguments([action], ROOT)
                self.assertEqual(action, parsed.action)

    def test_map_catalog_accepts_only_safe_expected_files(self):
        html = b'''<a href="dm6.bsp">ok</a><a href="named%20bad.bsp">bad</a>
            <a href="../escape.bsp">escape name only</a><a href="tool.exe">bad</a>
            <a href="dm6.loc">wrong collection</a><a href="readme.txt">text</a>'''
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            with mock.patch.object(installer, "http_get", return_value=html):
                self.assertEqual(["dm6.bsp", "escape.bsp", "readme.txt"], installer.map_catalog("base"))

    def test_component_overlay_preserves_unowned_files_and_is_reversible(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            personal = target / "qw/maps/personal.loc"
            personal.parent.mkdir(parents=True)
            personal.write_text("mine", encoding="utf-8")
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed = installer.stage / "managed"
            maps = managed / "qw/maps"
            maps.mkdir(parents=True)
            (maps / "personal.loc").write_text("upstream", encoding="utf-8")
            (maps / "new.loc").write_text("new", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                count = installer.install_component_overlay("maps", managed, "test", "https://example.invalid")
            self.assertEqual(1, count)
            self.assertEqual("mine", personal.read_text(encoding="utf-8"))
            self.assertEqual("new", (target / "qw/maps/new.loc").read_text(encoding="utf-8"))
            installer.verify_component("maps")
            self.assertEqual(1, installer.remove_component("maps"))
            self.assertEqual("mine", personal.read_text(encoding="utf-8"))
            self.assertFalse((target / "qw/maps/new.loc").exists())

    def test_presets_do_not_modify_personal_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            config = target / "ezquake/configs/config.cfg"
            config.parent.mkdir(parents=True)
            config.write_text("personal\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "check_paks"):
                    with mock.patch("builtins.input", return_value="1"):
                        installer.manage_presets()
            self.assertEqual("personal\n", config.read_text(encoding="utf-8"))
            self.assertEqual(len(install_qw.PRESETS), installer.verify_component("presets"))
            self.assertTrue((target / "ezquake/configs/x86-qw-modern.cfg").is_file())

    def test_individual_map_automatically_includes_matching_loc(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            catalogs = {"all": ["dm6.bsp"], "locs": ["DM6.loc"]}
            with mock.patch.object(installer, "map_catalog", side_effect=lambda name: catalogs[name]):
                with mock.patch("builtins.input", side_effect=["3", "dm6"]):
                    selection, files = installer.choose_map_files()
            self.assertEqual("individual:dm6", selection)
            self.assertEqual([("all", "dm6.bsp"), ("locs", "DM6.loc")], files)

    def test_map_install_validates_bsp_and_records_loc(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, cache = self.make_installer(Path(temporary))

            def fake_http_get(url, destination=None, headers=None):
                del headers
                if destination is None:
                    raise AssertionError(url)
                if url.endswith(".bsp"):
                    destination.write_bytes(struct.pack("<I", 29) + b"bsp")
                else:
                    destination.write_text("0 0 0 room\n", encoding="utf-8")
                return b""

            catalogs = {"base": ["dm6.bsp"], "locs": ["dm6.loc"]}
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "check_paks"):
                    with mock.patch.object(installer, "map_catalog", side_effect=lambda name: catalogs[name]):
                        with mock.patch.object(installer, "http_get", side_effect=fake_http_get):
                            with mock.patch("builtins.input", side_effect=["1", "1"]):
                                installer.manage_maps()
            self.assertEqual(2, installer.verify_component("maps"))
            self.assertTrue((target / "qw/maps/dm6.bsp").is_file())
            self.assertTrue((target / "qw/maps/dm6.loc").is_file())
            self.assertTrue((cache / "maps/dm6.bsp").is_file())

    def test_client_catalog_uses_only_releases_with_published_sha256(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.client = install_qw.CLIENTS["classicq"]
            installer.spec = install_qw.PLATFORMS["macos"]
            name = "classicQ-3.5.0-macos-arm64.zip"
            url = f"https://github.com/classicq/classicq/releases/download/v3.5.0/{name}"
            releases = [
                {"tag_name": "v3.5.0", "draft": False, "prerelease": False, "assets": [
                    {"name": name, "state": "uploaded", "browser_download_url": url, "digest": "sha256:" + "a" * 64}
                ]},
                {"tag_name": "v3.4.0", "draft": False, "prerelease": False, "assets": [
                    {"name": "classicQ-3.4.0-macos-arm64.zip", "state": "uploaded",
                     "browser_download_url": "https://github.com/classicq/classicq/releases/download/v3.4.0/classicQ-3.4.0-macos-arm64.zip",
                     "digest": None}
                ]},
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "http_get", return_value=json.dumps(releases).encode()):
                    self.assertEqual([("v3.5.0", url, "a" * 64)], installer.client_catalog())

    def test_thin_arm64_client_bundle_is_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            app = Path(temporary) / "classicQ.app"
            binary = app / "Contents/MacOS/classicq-macos-arm64"
            binary.parent.mkdir(parents=True)
            header = bytearray(32)
            header[:4] = b"\xcf\xfa\xed\xfe"
            struct.pack_into("<I", header, 4, 0x0100000C)
            binary.write_bytes(header)
            with (app / "Contents/Info.plist").open("wb") as destination:
                plistlib.dump({"CFBundleShortVersionString": "3.5.0", "CFBundleExecutable": binary.name}, destination)
            version, digest = installer.inspect_macos_client(app, binary.name, "arm64")
            self.assertEqual("3.5.0", version)
            self.assertEqual(64, len(digest))

    def test_hub_filters_bad_addresses_and_can_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            payload = [
                {"address": "server.example:27500", "players": [{"is_bot": False}]},
                {"address": "+exec bad.cfg", "players": []},
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "http_get", return_value=json.dumps(payload).encode()):
                    servers = installer.hub_servers()
            self.assertEqual(["server.example:27500"], [item["address"] for item in servers])
            runtime = target / "ezQuake Stable.app"
            runtime.mkdir()
            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(install_qw.subprocess, "Popen") as popen:
                    installer.launch_runtime(runtime, ["+connect", "server.example:27500"])
            command = popen.call_args.args[0]
            self.assertEqual(["open", "-n", str(runtime), "--args", "-basedir", str(target), "+connect", "server.example:27500"], command)

    def test_hub_uses_native_join_observe_and_qtv_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            server = {
                "address": "server.example:27500", "mode": "duel", "players": [],
                "settings": {"map": "dm6", "hostname": "Test"},
                "qtv_stream": {"url": "2@qtv.example:28000"},
            }
            runtime = target / "client"
            for answer, expected in (
                ("1", ["+join", "server.example:27500"]),
                ("o1", ["+observe", "server.example:27500"]),
                ("q1", ["+qtvplay", "2@qtv.example:28000"]),
            ):
                with self.subTest(answer=answer):
                    with contextlib.redirect_stdout(io.StringIO()):
                        with mock.patch.object(installer, "hub_servers", return_value=[server]):
                            with mock.patch.object(installer, "choose_host_runtime", return_value=("client", runtime)):
                                with mock.patch.object(installer, "launch_runtime") as launch:
                                    with mock.patch("builtins.input", return_value=answer):
                                        installer.browse_hub()
                    launch.assert_called_once_with(runtime, expected)

    def test_uninstall_removes_receipts_when_client_runtimes_are_missing_or_invalid(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            self.write_client_receipt(target, "classicq", "3.5.0")
            self.write_client_receipt(target, "unezquake", "2.0.4")
            (target / "unezQuake.app").mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                installer.uninstall()
            self.assertFalse((target / ".install").exists())
            self.assertFalse((target / "unezQuake.app").exists())

    def test_optional_client_removal_tolerates_missing_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            self.write_client_receipt(target, "classicq", "3.5.0")
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch("builtins.input", return_value="1"):
                    installer.remove_optional_client()
            self.assertFalse((target / ".install").exists())


if __name__ == "__main__":
    unittest.main()
