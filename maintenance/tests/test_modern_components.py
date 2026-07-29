import contextlib
import hashlib
import importlib.util
import io
import json
import re
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("install_qw_modern", ROOT / "dist/installer/bin/manager.py")
install_qw = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = install_qw
SPEC.loader.exec_module(install_qw)
sys.modules["cli"] = install_qw

PLAY_SPEC = importlib.util.spec_from_file_location("play_qw_modern", ROOT / "dist/installer/bin/gameplay.py")
play_qw = importlib.util.module_from_spec(PLAY_SPEC)
assert PLAY_SPEC.loader is not None
sys.modules[PLAY_SPEC.name] = play_qw
PLAY_SPEC.loader.exec_module(play_qw)
sys.modules["gameplay"] = play_qw


class ModernComponentTests(unittest.TestCase):
    def setUp(self):
        install_qw.console.configure(verbose=False, no_color=True)

    def make_installer(self, root):
        target = root / "quake-world"
        cache = root / "cache" / "x86qw"
        target.mkdir(parents=True)
        cache.parent.mkdir()
        return install_qw.Installer(ROOT, target, cache), target, cache

    def make_player(self, root):
        target = root / "quake-world"
        cache = root / "cache" / "x86qw"
        target.mkdir(parents=True)
        cache.parent.mkdir()
        return play_qw.Player(ROOT, target, cache), target, cache

    def test_new_actions_are_accepted(self):
        for action in ("components", "presets", "hub", "update", "upgrade"):
            with self.subTest(action=action):
                parsed = install_qw.parse_arguments([action], ROOT)
                self.assertEqual(action, parsed.action)
        uninstall = install_qw.parse_arguments(["uninstall", "--purge"], ROOT)
        self.assertTrue(uninstall.purge)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                install_qw.parse_arguments(["purge"], ROOT)
            with self.assertRaises(SystemExit):
                install_qw.parse_arguments(["install", "--purge"], ROOT)
            with self.assertRaises(SystemExit):
                install_qw.parse_arguments(["verify", "--dry-run"], ROOT)

    def test_play_has_its_own_module_and_is_exposed_by_the_main_cli(self):
        target = ROOT / "custom-quake"
        parsed = play_qw.parse_arguments(["--no-color", str(target)], ROOT)
        self.assertEqual(target, parsed.target)
        self.assertTrue(parsed.no_color)
        main = install_qw.parse_arguments(["play", str(target)], ROOT)
        self.assertEqual("play", main.action)
        with mock.patch.object(play_qw, "main", return_value=0) as delegated:
            self.assertEqual(0, install_qw.main(["play", str(target), "--no-color"]))
        delegated.assert_called_once_with([str(target), "--no-color"])

    def test_play_menu_shows_installed_versions_and_aligns_descriptions(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, _, _ = self.make_player(Path(temporary))
            output = io.StringIO()
            with mock.patch.object(
                player, "installed_game_version", side_effect=lambda game: game.version,
            ):
                with mock.patch("builtins.input", return_value=""):
                    with contextlib.redirect_stdout(output):
                        selected = player.choose_local_game(list(play_qw.LOCAL_GAMES))
            self.assertEqual("ktx", selected.key)
            lines = [line for line in output.getvalue().splitlines() if re.match(r"^  \d+\)", line)]
            self.assertEqual(len(play_qw.LOCAL_GAMES), len(lines))
            description_columns = []
            for line, game in zip(lines, play_qw.LOCAL_GAMES):
                self.assertIn(f"v{game.version}", line)
                description_columns.append(line.index(game.description))
            self.assertEqual(1, len(set(description_columns)))
            self.assertIn("KTX (padrão)", lines[0])

    def test_play_menu_uses_receipt_version_with_canonical_fallback(self):
        cases = {
            "ktx": ("1.48+nquake.abcdef+x86qw.1", "1.48"),
            "final-arena": ("e4cb23d40aa2+x86qw.6", "1.20"),
            "pro-x": ("1.1+x86qw.1", "1.1"),
            "team-fortress": ("2.9+nquake.e4cb23d40aa2+x86qw.1", "2.9"),
            "td2": ("2.22+x86qw.5", "2.22"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            player, _, _ = self.make_player(Path(temporary))
            for game in play_qw.LOCAL_GAMES:
                selection, expected = cases[game.key]
                with self.subTest(game=game.key):
                    with mock.patch.object(
                        player, "installed_component_for_game", return_value=game.component,
                    ):
                        with mock.patch.object(
                            player, "validate_component_pair",
                            return_value=(True, [], {"selection": selection}),
                        ):
                            self.assertEqual(expected, player.installed_game_version(game))

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
            compatibility = installer.component_catalog["compatibility"]
            self.assertEqual("common-baseline", compatibility["policy"])
            self.assertEqual(set(installer.components), set(compatibility["covered_components"]))
            self.assertEqual(
                {"nquake", "ktx", "pro-x", "team-fortress", "td2"},
                installer.content_component_namespaces,
            )
            self.assertEqual(set(installer.components), set(installer.component_catalog["profiles"]["complete"]))
            self.assertNotIn("qrp-hires", installer.component_catalog["profiles"]["recommended"])
            self.assertNotIn("nquake-matchinfo", installer.component_catalog["profiles"]["recommended"])
            self.assertIn("qrp-hires", installer.component_catalog["profiles"]["complete"])
            self.assertNotIn("total-destruction-2", installer.component_catalog["profiles"]["recommended"])
            self.assertIn("total-destruction-2", installer.component_catalog["profiles"]["complete"])
            td2 = installer.components["total-destruction-2"]
            self.assertEqual(
                {
                    "dist/mods/td2/2.22/x86qw/client.cfg",
                    "dist/mods/td2/2.22/x86qw/server.cfg",
                    "dist/mods/td2/2.22/x86qw/user.cfg.example",
                },
                {source["path"] for source in td2["project_sources"]},
            )
            final_arena = installer.components["final-arena"]
            self.assertEqual(
                {
                    "dist/mods/final-arena/1.20+x86qw.1/x86qw/client.cfg",
                    "dist/mods/final-arena/1.20+x86qw.1/x86qw/server.cfg",
                    "dist/mods/final-arena/1.20+x86qw.1/x86qw/user.cfg.example",
                },
                {source["path"] for source in final_arena["project_sources"]},
            )
            pro_x = installer.components["pro-x"]
            self.assertEqual(
                {
                    "dist/mods/pro-x/1.1/x86qw/client.cfg",
                    "dist/mods/pro-x/1.1/x86qw/qw-server.cfg",
                    "dist/mods/pro-x/1.1/x86qw/server.cfg",
                    "dist/mods/pro-x/1.1/x86qw/user.cfg.example",
                },
                {source["path"] for source in pro_x["project_sources"]},
            )

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

    def test_combined_clan_arena_receipt_is_removed_before_the_split_components(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed = installer.stage / "combined"
            for relative, payload in (
                ("arena/arena.pk3", b"arena"),
                ("prox/prox.pk3", b"prox"),
            ):
                destination = managed / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload)
            with contextlib.redirect_stdout(io.StringIO()):
                installer.install_component_overlay(
                    "clan-arena", managed, "legacy", "https://example.invalid/clan-arena.zip",
                )
                installer.migrate_legacy_clan_arena(["final-arena", "pro-x"])
            self.assertFalse((target / ".install/clan-arena.receipt").exists())
            self.assertFalse((target / ".install/clan-arena.inventory").exists())
            self.assertFalse((target / "arena/arena.pk3").exists())
            self.assertFalse((target / "prox/prox.pk3").exists())

    def test_play_support_releases_profiles_to_their_component(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed = installer.stage / "play"
            for relative in (
                "arena/x86qw-arena.cfg",
                "arena/server.cfg",
                "arena/x86qw_arena.dat",
            ):
                destination = managed / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(relative, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.install_component_overlay(
                    "play-support", managed, "3", "x86QW legacy local-play layer",
                )
                installer.release_play_support_profiles(["final-arena"])
            self.assertFalse((target / "arena/x86qw-arena.cfg").exists())
            self.assertFalse((target / "arena/server.cfg").exists())
            self.assertTrue((target / "arena/x86qw_arena.dat").is_file())
            _, entries, _ = installer.validate_component_pair("play-support")
            self.assertEqual(["arena/x86qw_arena.dat"], [name for name, _ in entries])

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

    def test_component_is_materialized_from_canonical_sources_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            package = installer.component_package_record("nquake-bootstrap")
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "http_get", side_effect=AssertionError("network used")):
                    prepared = installer.prepare_component_sources(package)
            self.assertIsNotNone(prepared)
            assert prepared is not None
            managed, defaults, source = prepared
            self.assertTrue((managed / "qw/autoexec.cfg").is_file())
            self.assertTrue(any(destination == target / "ezquake/configs/config.cfg" for _, destination in defaults))
            self.assertTrue(source.startswith("x86qw:dist/nquake-bootstrap@"))

    def test_component_install_prefers_canonical_sources_over_remote_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, cache = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(
                    installer, "download_component_package", side_effect=AssertionError("remote package used"),
                ):
                    installer.install_components(["nquake-bootstrap"])
            self.assertTrue((target / "qw/autoexec.cfg").is_file())
            self.assertTrue((target / "ezquake/configs/config.cfg").is_file())
            self.assertGreater(installer.verify_component("nquake-bootstrap"), 0)
            receipt = (target / ".install/nquake-bootstrap.receipt").read_text(encoding="utf-8")
            self.assertIn("source\tx86qw:dist/nquake-bootstrap@", receipt)
            self.assertFalse(cache.exists())

    def test_bootstrap_migrates_only_the_obsolete_nquake_texture_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            config = target / "ezquake/configs/config.cfg"
            config.parent.mkdir(parents=True)
            config.write_text(
                'name "personal"\ngl_max_size                           "32768"\nvolume "0.5"\n',
                encoding="utf-8",
            )
            td2_config = target / "td2/configs/config.cfg"
            td2_config.parent.mkdir(parents=True)
            td2_config.write_bytes(b'gl_max_size "32768"\nname "td2"\n')
            prox_config = target / "prox/configs/config.cfg"
            prox_config.parent.mkdir(parents=True)
            prox_config.write_text('gl_max_size "2048"\n', encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.migrate_nquake_texture_limit()
            self.assertEqual(
                'name "personal"\ngl_max_size                           "16384"\nvolume "0.5"\n',
                config.read_text(encoding="utf-8"),
            )
            self.assertEqual(b'gl_max_size "16384"\nname "td2"\n', td2_config.read_bytes())
            self.assertEqual('gl_max_size "2048"\n', prox_config.read_text(encoding="utf-8"))

    def test_package_order_is_deterministic_and_tracks_custom_pk3_last(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            qw = target / "qw"
            qw.mkdir()
            for name in ("textures.pk3", "ktx.pk3", "z-custom.pk3", "nquake.pk3", "a-custom.pk3"):
                (qw / name).write_bytes(name.encode())
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                installer.refresh_qw_package_order()
            self.assertEqual(
                "ktx.pk3\nnquake.pk3\ntextures.pk3\na-custom.pk3\nz-custom.pk3\n",
                (qw / "pak.lst").read_text(encoding="utf-8"),
            )
            installer.verify_qw_package_order()
            (qw / "middle-custom.pk3").write_bytes(b"custom")
            with self.assertRaisesRegex(install_qw.InstallerError, "pak.lst"):
                installer.verify_qw_package_order()
            with contextlib.redirect_stdout(io.StringIO()):
                installer.refresh_qw_package_order()
            installer.verify_qw_package_order()

    def test_saved_configs_drop_managed_aliases_and_migrate_legacy_pro_x(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            autoexec = target / "qw/autoexec.cfg"
            autoexec.parent.mkdir()
            autoexec.write_text('tempalias +zoom "fov 50"\n', encoding="utf-8")
            base = target / "ezquake/configs/config.cfg"
            base.parent.mkdir(parents=True)
            base.write_text(
                'alias +zoom "fov 50"\nalias personal "say oi"\ncfg_save_unchanged "1"\nname "x86"\n',
                encoding="utf-8",
            )
            prox = target / "prox/configs/config.cfg"
            prox.parent.mkdir(parents=True)
            legacy = b"// Niclas's config\nalias +zoom \"fov 10\"\n"
            prox.write_bytes(legacy)
            with contextlib.redirect_stdout(io.StringIO()):
                installer.migrate_saved_configs()
            self.assertEqual(legacy, (prox.parent / "config.pre-x86qw.cfg").read_bytes())
            self.assertNotIn('alias +zoom', base.read_text(encoding="utf-8"))
            self.assertIn('alias personal "say oi"', base.read_text(encoding="utf-8"))
            self.assertIn('cfg_save_unchanged "1"', base.read_text(encoding="utf-8"))
            self.assertIn('alias +zoom', (base.parent / "config.aliases-pre-x86qw.cfg").read_text(encoding="utf-8"))
            migrated = prox.read_text(encoding="utf-8")
            self.assertIn("base Pro-X migrada", migrated)
            self.assertNotIn('alias +zoom', migrated)
            self.assertIn('alias personal "say oi"', migrated)
            self.assertIn('alias +zoom', (prox.parent / "config.aliases-pre-x86qw.cfg").read_text(encoding="utf-8"))

    def test_cleanup_separates_regenerable_downloaded_and_personal_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed = installer.stage / "managed"
            sound = managed / "fortress/sound/managed.wav"
            sound.parent.mkdir(parents=True)
            sound.write_bytes(b"managed")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.install_component_overlay(
                    "team-fortress", managed, "test", "x86QW test package",
                )
            downloaded = target / "fortress/sound/server.wav"
            downloaded.write_bytes(b"download")
            temporary_file = target / "fortress/progs/partial.tmp"
            temporary_file.parent.mkdir(parents=True)
            temporary_file.write_bytes(b"partial")
            cache = target / "ezquake/sb/cache/index"
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b"cache")
            zero_demo = target / "td2/demos/zero.qwd"
            zero_demo.parent.mkdir(parents=True)
            zero_demo.write_bytes(b"")
            valid_demo = target / "td2/demos/match.qwd"
            valid_demo.write_bytes(b"demo")
            log = target / "qw/qconsole.log"
            log.parent.mkdir(parents=True)
            log.write_text("log", encoding="utf-8")

            removed, personal = installer.cleanup_runtime_data(downloads=False, personal_data=False)
            self.assertGreaterEqual(removed, 3)
            self.assertEqual(0, personal)
            self.assertTrue(downloaded.exists())
            self.assertTrue(sound.exists())
            self.assertTrue(valid_demo.exists())
            self.assertTrue(log.exists())

            installer.cleanup_runtime_data(downloads=True, personal_data=False)
            self.assertFalse(downloaded.exists())
            self.assertTrue(sound.exists())
            installer.cleanup_runtime_data(downloads=False, personal_data=True)
            self.assertFalse(valid_demo.exists())
            self.assertFalse(log.exists())

    def test_hub_filters_bad_addresses_and_launches_macos_binary_with_arguments(self):
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
            executable = runtime / "Contents/MacOS/ezQuake"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"mach-o")
            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(install_qw.subprocess, "Popen") as popen:
                    installer.launch_runtime(runtime, ["+connect", "server.example:27500"])
            command = popen.call_args.args[0]
            self.assertEqual([
                str(executable), "-nohome", "-basedir", str(target),
                "+connect", "server.example:27500",
            ], command)
            self.assertIs(popen.call_args.kwargs["stdin"], install_qw.subprocess.DEVNULL)
            self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_play_uses_client_and_server_gamedirs_before_map(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "td2")
            runtime = target / "ezQuake Nightly.app"
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "check_paks"):
                    with mock.patch.object(installer, "available_local_games", return_value=[game]):
                        with mock.patch.object(installer, "installed_component_for_game", return_value=game.component):
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
                "+sb_listcache", "0",
                "-game", "td2", "+gamedir", "td2", "+sv_gamedir", "td2",
                "+sv_progtype", "0",
                "+cl_remote_capabilities", "$cl_remote_capabilities,bind,scr_centertime",
                "+cl_pext_lagteleport", "0",
                "+map", "dm6", "+wait",
                "+exec", "x86qw-td2.cfg",
            ])

    def test_play_loads_the_specific_arena_and_prox_profiles(self):
        expectations = {
            "final-arena": (
                "arena", "23ar-a", "x86qw-arena.cfg",
                [
                    "+cl_remote_capabilities", "$cl_remote_capabilities,noaim",
                    "+cl_pext_lagteleport", "0",
                ],
                [],
            ),
            "pro-x": (
                "prox", "proxmap1", "x86qw-prox.cfg",
                [
                    "+cl_remote_capabilities", "$cl_remote_capabilities,setinfo,bind",
                    "+cl_pext_lagteleport", "0",
                ],
                [],
            ),
        }
        for key, (gamedir, map_name, profile, before_map, after_wait) in expectations.items():
            with self.subTest(game=key), tempfile.TemporaryDirectory() as temporary:
                installer, target, _ = self.make_player(Path(temporary))
                game = next(game for game in play_qw.LOCAL_GAMES if game.key == key)
                runtime = target / "ezQuake Stable.app"
                with contextlib.redirect_stdout(io.StringIO()):
                    with mock.patch.object(installer, "check_paks"):
                        with mock.patch.object(installer, "available_local_games", return_value=[game]):
                            with mock.patch.object(installer, "installed_component_for_game", return_value=game.component):
                                with mock.patch.object(installer, "verify_component"):
                                    with mock.patch.object(installer, "local_map_names", return_value=[map_name]):
                                        with mock.patch.object(installer, "choose_host_runtime", return_value=("stable", runtime)):
                                            with mock.patch.object(installer, "launch_runtime") as launch:
                                                with mock.patch.object(installer, "ensure_local_play_support"):
                                                    with mock.patch("builtins.input", side_effect=["", ""]):
                                                        installer.play_local()
                launch.assert_called_once_with(runtime, [
                    "+sb_listcache", "0",
                    "-game", gamedir, "+gamedir", gamedir, "+sv_gamedir", gamedir,
                    "+sv_progtype", "0", *before_map, "+map", map_name, "+wait",
                    *after_wait, "+exec", profile,
                ])

    def test_team_fortress_loads_legacy_capabilities_before_the_map(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "team-fortress")
            runtime = target / "ezQuake Nightly.app"
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "check_paks"):
                    with mock.patch.object(installer, "available_local_games", return_value=[game]):
                        with mock.patch.object(installer, "installed_component_for_game", return_value=game.component):
                            with mock.patch.object(installer, "verify_component"):
                                with mock.patch.object(installer, "local_map_names", return_value=["2fort5r"]):
                                    with mock.patch.object(installer, "choose_host_runtime", return_value=("nightly", runtime)):
                                        with mock.patch.object(installer, "launch_runtime") as launch:
                                            with mock.patch.object(installer, "ensure_local_play_support"):
                                                with mock.patch("builtins.input", side_effect=["", ""]):
                                                    installer.play_local()
            launch.assert_called_once_with(runtime, [
                "+sb_listcache", "0",
                "-game", "fortress", "+gamedir", "fortress", "+sv_gamedir", "fortress",
                "+sv_progtype", "0", "+exec", "x86qw-fortress-pre.cfg",
                "+cl_remote_capabilities", "$cl_remote_capabilities,bind",
                "+cl_pext_lagteleport", "0",
                "+map", "2fort5r", "+wait",
                "+exec", "x86qw-fortress.cfg",
            ])

    def test_legacy_combined_receipt_keeps_arena_and_pro_x_visible_until_migration(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_player(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed = installer.stage / "combined"
            for relative in ("arena/arena.pk3", "prox/prox.pk3"):
                destination = managed / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(destination, "w") as package:
                    package.writestr("qwprogs.dat", relative.encode())
            with contextlib.redirect_stdout(io.StringIO()):
                installer.install_component_overlay(
                    "clan-arena", managed, "legacy", "https://example.invalid/clan-arena.zip",
                )
            games = installer.available_local_games()
            self.assertEqual(["final-arena", "pro-x"], [game.key for game in games])
            self.assertEqual("clan-arena", installer.installed_component_for_game(games[0]))
            self.assertEqual("clan-arena", installer.installed_component_for_game(games[1]))

    def test_local_play_support_is_managed_and_reversible(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "td2")
            gamecode = target / "td2/qwprogs.dat"
            gamecode.parent.mkdir(parents=True)
            gamecode.write_bytes(b"quakec")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.ensure_local_play_support([game])
            server_config = target / "td2/server.cfg"
            client_config = target / "td2/x86qw-td2.cfg"
            user_config = target / "td2/x86qw-td2-user.cfg"
            self.assertIn('sv_progtype "0"', server_config.read_text(encoding="utf-8"))
            self.assertIn('sv_gamedir "td2"', server_config.read_text(encoding="utf-8"))
            self.assertIn('sv_progsname "x86qw_td2"', server_config.read_text(encoding="utf-8"))
            self.assertIn('localinfo temp1 "65560"', server_config.read_text(encoding="utf-8"))
            self.assertIn('bind 1 "impulse 1"', client_config.read_text(encoding="utf-8"))
            self.assertIn('bind 9 "impulse 20"', client_config.read_text(encoding="utf-8"))
            self.assertIn('exec x86qw-td2-user.cfg', client_config.read_text(encoding="utf-8"))
            self.assertEqual((ROOT / "dist/mods/td2/2.22/x86qw/client.cfg").read_bytes(), client_config.read_bytes())
            self.assertEqual((ROOT / "dist/mods/td2/2.22/x86qw/server.cfg").read_bytes(), server_config.read_bytes())
            self.assertEqual((ROOT / "dist/mods/td2/2.22/x86qw/user.cfg.example").read_bytes(), user_config.read_bytes())
            self.assertEqual(b"quakec", (target / "td2/x86qw_td2.dat").read_bytes())
            self.assertTrue(user_config.is_file())
            self.assertEqual(3, installer.verify_component("play-support"))
            self.assertEqual(3, installer.remove_component("play-support"))
            self.assertFalse(server_config.exists())
            self.assertFalse(client_config.exists())
            self.assertTrue(user_config.exists())

    def test_team_fortress_uses_29_gamecode_instead_of_misc_pak_28(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "team-fortress")
            fortress = target / "fortress"
            fortress.mkdir(parents=True)
            (fortress / "qwprogs.dat").write_bytes(b"team-fortress-2.9")
            (fortress / "misc.pak").write_bytes(b"legacy-team-fortress-2.8")

            with contextlib.redirect_stdout(io.StringIO()):
                installer.ensure_local_play_support([game])

            self.assertEqual(
                b"team-fortress-2.9",
                (fortress / "x86qw_fortress.dat").read_bytes(),
            )
            self.assertNotEqual(
                (fortress / "misc.pak").read_bytes(),
                (fortress / "x86qw_fortress.dat").read_bytes(),
            )

    def test_td2_upstream_update_rebuilds_gamecode_and_preserves_x86qw_user_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "td2")
            upstream = target / "td2/qwprogs.dat"
            upstream.parent.mkdir(parents=True)
            upstream.write_bytes(b"td2-v1")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.ensure_local_play_support([game])

            user_config = target / "td2/x86qw-td2-user.cfg"
            user_config.write_text('bind MOUSE4 "impulse 23"\n', encoding="utf-8")
            upstream.write_bytes(b"td2-v2")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.ensure_local_play_support([game])

            self.assertEqual(b"td2-v2", (target / "td2/x86qw_td2.dat").read_bytes())
            self.assertEqual('bind MOUSE4 "impulse 23"\n', user_config.read_text(encoding="utf-8"))
            self.assertEqual(3, installer.verify_component("play-support"))
            _, entries, receipt = installer.validate_component_pair("play-support")
            self.assertNotIn("td2/x86qw-td2-user.cfg", dict(entries))
            self.assertEqual(play_qw.PLAY_SUPPORT_VERSION, receipt["selection"])

    def test_arena_and_prox_profiles_update_gamecode_and_preserve_user_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_player(Path(temporary))
            games = [
                next(game for game in play_qw.LOCAL_GAMES if game.key == key)
                for key in ("final-arena", "pro-x")
            ]
            for game in games:
                package = target / game.marker
                package.parent.mkdir(parents=True, exist_ok=True)
                if package.suffix == ".dat":
                    package.write_bytes(f"{game.key}-v1".encode())
                else:
                    with zipfile.ZipFile(package, "w") as archive:
                        archive.writestr("qwprogs.dat", f"{game.key}-v1".encode())
            with contextlib.redirect_stdout(io.StringIO()):
                installer.ensure_local_play_support(games)

            for game in games:
                gamedir = target / game.gamedir
                client = gamedir / f"x86qw-{game.profile}.cfg"
                server = gamedir / "server.cfg"
                compatibility = gamedir / "qw_server.cfg"
                user = gamedir / f"x86qw-{game.profile}-user.cfg"
                self.assertEqual(
                    (ROOT / (
                        "dist/mods/final-arena/1.20+x86qw.1/x86qw/client.cfg"
                        if game.key == "final-arena"
                        else "dist/mods/pro-x/1.1/x86qw/client.cfg"
                    )).read_bytes(),
                    client.read_bytes(),
                )
                self.assertEqual(
                    (ROOT / (
                        "dist/mods/final-arena/1.20+x86qw.1/x86qw/server.cfg"
                        if game.key == "final-arena"
                        else "dist/mods/pro-x/1.1/x86qw/server.cfg"
                    )).read_bytes(),
                    server.read_bytes(),
                )
                self.assertIn(f'sv_progsname "x86qw_{game.gamedir}"', server.read_text())
                if game.key == "pro-x":
                    self.assertIn('set sv_aim "0"', server.read_text())
                    self.assertEqual("exec x86qw-prox.cfg", compatibility.read_text().strip().splitlines()[-1])
                user.write_text(f"// personal {game.key}\n", encoding="utf-8")
                package = target / game.marker
                if package.suffix == ".dat":
                    package.write_bytes(f"{game.key}-v2".encode())
                else:
                    with zipfile.ZipFile(package, "w") as archive:
                        archive.writestr("qwprogs.dat", f"{game.key}-v2".encode())

            with contextlib.redirect_stdout(io.StringIO()):
                installer.ensure_local_play_support(games)

            for game in games:
                gamedir = target / game.gamedir
                self.assertEqual(
                    f"{game.key}-v2".encode(),
                    (gamedir / f"x86qw_{game.gamedir}.dat").read_bytes(),
                )
                self.assertEqual(
                    f"// personal {game.key}\n",
                    (gamedir / f"x86qw-{game.profile}-user.cfg").read_text(),
                )
            self.assertEqual(7, installer.verify_component("play-support"))
            self.assertEqual(7, installer.remove_component("play-support"))
            for game in games:
                self.assertTrue((
                    target / game.gamedir / f"x86qw-{game.profile}-user.cfg"
                ).is_file())

    def test_every_playable_mod_profile_prints_its_keys_and_binds_help(self):
        expected_gameplay = {
            "ktx": {
                'tempalias sv_enableprofile ""',
                'bind 1 "x86qw_ktx_axe"',
                'bind q "x86qw_ktx_gl"',
                'bind e "x86qw_ktx_rl"',
                'bind z "tp_msgquaddead"',
                'bind x "tp_msgenemypwr"',
                'bind F5 "toggleready"',
                'bind F7 "join"',
            },
            "final-arena": {
                'tempalias arena_stats "impulse 68"',
                'tempalias arena_position "impulse 69"',
                'tempalias arena_break "impulse 70"',
                'tempalias arena_commands "impulse 82"',
                'tempalias arena_next "impulse 83"',
                'tempalias arena_backpacks "impulse 85"',
                'tempalias arena_status "impulse 86"',
                'tempalias arena_airgib "impulse 88"',
                'bind 1 "impulse 1"',
                'bind F1 "join"',
                'bind F2 "arena_position"',
                'bind F7 "arena_backpacks"',
                'bind F8 "arena_airgib"',
            },
            "pro-x": {
                'tempalias prox_menu "menu"',
                'tempalias prox_id "id"',
                'tempalias prox_map1 "impulse 201"',
                'tempalias prox_map5 "impulse 205"',
                'bind 1 "impulse 1;weapon 1"',
                'bind 9 "impulse 9"',
                'bind 0 "impulse 10"',
                'bind F2 "prox_admin_yes"',
                'bind F9 "prox_menu"',
            },
            "team-fortress": {
                'bind 1 "impulse 1"',
                'bind c "+det50"',
                'bind f "saveme"',
                'bind r "reload"',
                'bind x "+det20"',
                'bind z "+det5"',
                'bind MOUSE2 "+gren1"',
                'bind MOUSE3 "+gren2"',
                'bind ALT "flaginfo"',
                'bind CTRL "discard"',
                'bind SHIFT "special"',
                'bind F1 "inv"',
                'bind F2 "showclasses"',
                'bind F3 "changeclass"',
            },
            "td2": {
                'tempalias td2_magic "impulse 1"',
                'tempalias td2_special "impulse 20"',
                'tempalias td2_drop_rune "impulse 22"',
                'tempalias td2_drop_special "impulse 23"',
                'tempalias td2_vote_next "impulse 100"',
                'bind 1 "impulse 1"',
                'bind 9 "impulse 20"',
                'bind 0 "impulse 21"',
                'bind MOUSE4 "td2_magic"',
                'bind F1 "td2_vote_next"',
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_player(Path(temporary))
            for game in play_qw.LOCAL_GAMES:
                with self.subTest(game=game.key):
                    sources = installer.game_project_sources(game)
                    profile = sources[f"{game.gamedir}/x86qw-{game.profile}.cfg"].decode()
                    help_alias = f"x86qw_{game.profile}_help"
                    self.assertIn(f"tempalias {help_alias}", profile)
                    self.assertIn(f'bind F10 "{help_alias}', profile)
                    for expected in expected_gameplay[game.key]:
                        self.assertIn(expected, profile)
                    user_exec = f"exec x86qw-{game.profile}-user.cfg"
                    self.assertIn(user_exec, profile)
                    self.assertLess(profile.index(user_exec), profile.rindex(help_alias))
                    self.assertEqual(help_alias, profile.strip().splitlines()[-1])

    def test_local_map_discovery_reads_direct_bsp_pk3_and_pak(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_player(Path(temporary))
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
