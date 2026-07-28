import contextlib
import hashlib
import importlib.util
import io
import json
import struct
import sys
import tempfile
import unittest
import zipfile
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
        target = root / "quake-world"
        cache = root / "cache" / "x86-qw"
        target.mkdir(parents=True)
        cache.parent.mkdir()
        return install_qw.Installer(ROOT, target, cache), target, cache

    def test_new_actions_are_accepted(self):
        for action in ("components", "presets", "play", "hub"):
            with self.subTest(action=action):
                parsed = install_qw.parse_arguments([action], ROOT)
                self.assertEqual(action, parsed.action)

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
                count = installer.install_component_overlay("nquake-maps", managed, "test", "https://example.invalid")
            self.assertEqual(1, count)
            self.assertEqual("mine", personal.read_text(encoding="utf-8"))
            self.assertEqual("new", (target / "qw/maps/new.loc").read_text(encoding="utf-8"))
            installer.verify_component("nquake-maps")
            self.assertEqual(1, installer.remove_component("nquake-maps"))
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

    def test_component_profiles_are_ezquake_only_and_dependency_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            self.assertEqual("ezquake", installer.component_catalog["client"]["id"])
            self.assertEqual(["stable", "nightly"], installer.component_catalog["client"]["channels"])
            self.assertEqual({"nquake", "ktx", "td2"}, installer.content_component_namespaces)
            self.assertEqual(set(installer.components), set(installer.component_catalog["profiles"]["complete"]))
            self.assertNotIn("qrp-hires", installer.component_catalog["profiles"]["recommended"])
            self.assertIn("qrp-hires", installer.component_catalog["profiles"]["complete"])
            self.assertNotIn("total-destruction-2", installer.component_catalog["profiles"]["recommended"])
            self.assertIn("total-destruction-2", installer.component_catalog["profiles"]["complete"])

    def test_nquake_component_is_prepared_and_receipted_from_a_fixed_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, target, _ = self.make_installer(root)
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, "w") as package:
                package.writestr("progs.dat", b"ktx")
            payload = inner.getvalue()
            commit = "a" * 40
            artifact = root / "nquake-ktx-aaaaaaaaaaaa.zip"
            metadata = {
                "format": 1, "project": "x86qw", "package": "nquake-ktx",
                "source_commit": commit,
                "members": [{
                    "path": "payload/qw/ktx.pk3",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "source": "gpl/qw/ktx.pk3",
                }],
            }
            with zipfile.ZipFile(artifact, "w") as package:
                package.writestr("payload/qw/ktx.pk3", payload)
                package.writestr("_x86qw/component.json", json.dumps(metadata))
            catalog_package = {
                "package": "nquake-ktx", "version": commit[:12],
                "source_commit": commit,
                "origin_url": f"https://example.invalid/{artifact.name}",
            }
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed, defaults = installer.prepare_component_package(catalog_package, artifact)
            self.assertEqual([], defaults)
            self.assertTrue((managed / "qw/ktx.pk3").is_file())
            count = installer.install_component_overlay(
                "nquake-ktx", managed, commit[:12], str(catalog_package["origin_url"]),
            )
            self.assertEqual(1, count)
            self.assertEqual(1, installer.verify_component("nquake-ktx"))

    def test_nquake_component_accepts_an_independent_upstream_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, _, _ = self.make_installer(root)
            commit = "a" * 40
            version = "1.47+nquake.aaaaaaaaaaaa"
            filename = f"nquake-ktx-{version}.zip"
            package = {
                "component": "ktx", "package": "nquake-ktx", "version": version,
                "channel": "content", "platform": "any", "architecture": "any",
                "filename": filename, "size": 123, "sha256": "b" * 64,
                "source_commit": commit, "redistribution_reviewed": True,
                "urls": [f"https://example.invalid/{filename}"],
                "release_url": "https://github.com/QW-Group/ktx/releases/tag/1.47",
                "release_notes": "KTX atualizado.",
            }
            installer._public_catalog = {"format": 1, "project": "x86qw", "packages": [package]}
            self.assertEqual(version, installer.component_package_record("nquake-ktx")["version"])

            artifact = root / filename
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as inner:
                inner.writestr("qwprogs.qvm", b"new qvm")
            data = payload.getvalue()
            metadata = {
                "format": 1, "project": "x86qw", "package": "nquake-ktx",
                "version": version, "source_commit": commit,
                "members": [{
                    "path": "payload/qw/ktx.pk3",
                    "sha256": hashlib.sha256(data).hexdigest(),
                }],
            }
            with zipfile.ZipFile(artifact, "w") as outer:
                outer.writestr("payload/qw/ktx.pk3", data)
                outer.writestr("_x86qw/component.json", json.dumps(metadata))
            installer.stage = root / "stage"
            installer.stage.mkdir()
            managed, defaults = installer.prepare_component_package(package, artifact)
            self.assertEqual([], defaults)
            self.assertTrue((managed / "qw/ktx.pk3").is_file())

    def test_nquake_component_accepts_a_standalone_source_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, _, _ = self.make_installer(root)
            revision = "b" * 64
            version = "2.22"
            filename = f"total-destruction-2-{version}.zip"
            package = {
                "component": "td2", "package": "total-destruction-2", "version": version,
                "channel": "content", "platform": "any", "architecture": "any",
                "filename": filename, "size": 123, "sha256": "c" * 64,
                "source_revision": revision, "redistribution_reviewed": True,
                "urls": [f"https://example.invalid/{filename}"],
            }
            installer._public_catalog = {"format": 1, "project": "x86qw", "packages": [package]}
            self.assertEqual(version, installer.component_package_record("total-destruction-2")["version"])

            artifact = root / filename
            metadata = {
                "format": 1, "project": "x86qw", "package": "total-destruction-2",
                "version": version, "source_revision": revision,
                "members": [{"path": "payload/td2/qwprogs.dat", "sha256": hashlib.sha256(b"td2").hexdigest()}],
            }
            with zipfile.ZipFile(artifact, "w") as outer:
                outer.writestr("payload/td2/qwprogs.dat", b"td2")
                outer.writestr("_x86qw/component.json", json.dumps(metadata))
            installer.stage = root / "stage"
            installer.stage.mkdir()
            managed, defaults = installer.prepare_component_package(package, artifact)
            self.assertEqual([], defaults)
            self.assertEqual(b"td2", (managed / "td2/qwprogs.dat").read_bytes())

    def test_clan_arena_runtime_config_is_normalized_to_a_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, target, _ = self.make_installer(root)
            commit = "a" * 40
            artifact = root / "clan-arena-aaaaaaaaaaaa.zip"
            payload = b"upstream config"
            metadata = {
                "format": 1, "project": "x86qw", "package": "clan-arena",
                "source_commit": commit,
                "members": [{
                    "path": "payload/prox/configs/config.cfg",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }],
            }
            with zipfile.ZipFile(artifact, "w") as package:
                package.writestr("payload/prox/configs/config.cfg", payload)
                package.writestr("_x86qw/component.json", json.dumps(metadata))
            catalog_package = {
                "package": "clan-arena", "version": commit[:12],
                "source_commit": commit, "origin_url": f"https://example.invalid/{artifact.name}",
            }
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed, defaults = installer.prepare_component_package(catalog_package, artifact)
            self.assertFalse((managed / "prox/configs/config.cfg").exists())
            self.assertEqual([(target / "prox/configs/config.cfg", payload)], [
                (destination, source.read_bytes()) for source, destination in defaults
            ])

    def test_modified_clan_arena_config_is_migrated_and_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed = installer.stage / "managed"
            config = managed / "prox/configs/config.cfg"
            config.parent.mkdir(parents=True)
            config.write_text("upstream\n", encoding="utf-8")
            for relative in ("arena/arena.pk3", "prox/prox.pk3"):
                package = managed / relative
                package.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(package, "w") as archive:
                    archive.writestr("qwprogs.dat", b"gamecode")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.install_component_overlay(
                    "clan-arena", managed, "test", "https://example.invalid/clan-arena.zip",
                )
            installed_config = target / "prox/configs/config.cfg"
            installed_config.write_text("personal\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.migrate_mutable_component_defaults("clan-arena")
            self.assertEqual("personal\n", installed_config.read_text(encoding="utf-8"))
            self.assertEqual(2, installer.verify_component("clan-arena"))
            inventory = (target / ".install/clan-arena.inventory").read_text(encoding="utf-8")
            self.assertNotIn("prox/configs/config.cfg", inventory)

    def test_component_download_falls_back_from_github_to_gitlab(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, target, _ = self.make_installer(root)
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            installer.prepare_cache()
            payload = b"verified package"
            filename = "nquake-ktx-1.47.zip"
            package = {
                "package": "nquake-ktx", "filename": filename,
                "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
                "urls": [
                    f"https://github.com/example/{filename}",
                    f"https://gitlab.com/example/{filename}",
                ],
            }

            def download(url, destination=None, headers=None):
                if "github.com" in url:
                    raise install_qw.InstallerError("GitHub unavailable")
                destination.write_bytes(payload)
                return b""

            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "http_get", side_effect=download) as request:
                    artifact = installer.download_component_package(package)
            self.assertEqual(payload, artifact.read_bytes())
            self.assertEqual(2, request.call_count)

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

    def test_play_uses_client_and_server_gamedirs_before_map(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            game = next(game for game in install_qw.LOCAL_GAMES if game.key == "td2")
            runtime = target / "ezQuake Nightly.app"
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "check_paks"):
                    with mock.patch.object(installer, "available_local_games", return_value=[game]):
                        with mock.patch.object(installer, "verify_component") as verify:
                            with mock.patch.object(installer, "local_map_names", return_value=["dm6", "dm2"]):
                                with mock.patch.object(installer, "choose_host_runtime", return_value=("nightly", runtime)):
                                    with mock.patch.object(installer, "launch_runtime") as launch:
                                        with mock.patch.object(installer, "ensure_local_play_support") as support:
                                            with mock.patch("builtins.input", side_effect=["", ""]):
                                                installer.play_local()
            verify.assert_called_once_with("total-destruction-2")
            support.assert_called_once_with([game])
            launch.assert_called_once_with(runtime, [
                "-game", "td2", "+gamedir", "td2", "+sv_gamedir", "td2",
                "+sv_progtype", "0", "+map", "dm6",
            ])

    def test_local_play_support_is_managed_and_reversible(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            game = next(game for game in install_qw.LOCAL_GAMES if game.key == "td2")
            gamecode = target / "td2/qwprogs.dat"
            gamecode.parent.mkdir(parents=True)
            gamecode.write_bytes(b"quakec")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.ensure_local_play_support([game])
            server_config = target / "td2/server.cfg"
            self.assertIn('sv_progtype "0"', server_config.read_text(encoding="utf-8"))
            self.assertIn('sv_gamedir "td2"', server_config.read_text(encoding="utf-8"))
            self.assertIn('sv_progsname "x86qw_td2"', server_config.read_text(encoding="utf-8"))
            self.assertEqual(b"quakec", (target / "td2/x86qw_td2.dat").read_bytes())
            self.assertEqual(2, installer.verify_component("play-support"))
            self.assertEqual(2, installer.remove_component("play-support"))
            self.assertFalse(server_config.exists())

    def test_local_map_discovery_reads_direct_bsp_pk3_and_pak(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            maps = target / "td2/maps"
            maps.mkdir(parents=True)
            (maps / "custom.bsp").write_bytes(b"bsp")
            with zipfile.ZipFile(target / "td2/addon.pk3", "w") as archive:
                archive.writestr("maps/zipmap.bsp", b"bsp")
                archive.writestr("../maps/escape.bsp", b"bad")
            id1 = target / "id1"
            id1.mkdir()
            payload = b"bsp"
            member = b"maps/dm6.bsp".ljust(56, b"\0") + struct.pack("<II", 12, len(payload))
            (id1 / "pak0.pak").write_bytes(
                b"PACK" + struct.pack("<II", 12 + len(payload), len(member)) + payload + member
            )
            self.assertEqual(["custom", "dm6", "zipmap"], installer.local_map_names("td2"))

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

    def test_uninstall_removes_component_receipt_when_managed_file_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed = installer.stage / "managed"
            (managed / "qw").mkdir(parents=True)
            (managed / "qw/ktx.pk3").write_bytes(b"pk3")
            installer.install_component_overlay("nquake-ktx", managed, "a" * 40, "https://example.invalid")
            (target / "qw/ktx.pk3").unlink()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                installer.uninstall()
            self.assertFalse((target / ".install").exists())
            self.assertNotIn("Os componentes x86QW não estão instalados", output.getvalue())


if __name__ == "__main__":
    unittest.main()
