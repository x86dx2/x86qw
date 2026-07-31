import contextlib
import hashlib
import importlib.util
import io
import json
import os
import re
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


# Um teste que por engano iniciar o runtime real nunca deve capturar a tela.
# Casos que verificam o comando normal removem a variável em escopo controlado.
os.environ.setdefault("X86QW_TEST_WINDOWED", "1")


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

SERVICES_SPEC = importlib.util.spec_from_file_location(
    "services_qw_modern", ROOT / "dist/installer/bin/services.py",
)
services_qw = importlib.util.module_from_spec(SERVICES_SPEC)
assert SERVICES_SPEC.loader is not None
sys.modules[SERVICES_SPEC.name] = services_qw
SERVICES_SPEC.loader.exec_module(services_qw)
sys.modules["services"] = services_qw


def local_server_baseline(game: str) -> list[str]:
    arguments = ["+sb_listcache", "0", "+spectator", "0"]
    settings = (
        play_qw.KTX_LOCAL_SERVER_SETTINGS
        if game == "ktx" else play_qw.NQUAKE_LOCAL_SERVER_SETTINGS
    )
    for name, value in settings:
        arguments.extend([f"+{name}", value])
    return arguments


def ktx_mode_runtime_aliases(mode_key: str) -> list[str]:
    mode = next(mode for mode in play_qw.load_ktx_modes(ROOT) if mode.key == mode_key)
    return [
        "+tempalias", "ktx_mode",
        f"echo x86QW KTX preset: {mode.label} [{mode.key}]",
        "+tempalias", "x86qw_ktx_mode_help", play_qw.ktx_mode_help_alias(mode),
    ]


def ktx_launch_setup_alias(*commands: str) -> list[str]:
    body = ";".join(("unalias x86qw_ktx_launch_setup", *commands))
    return ["+tempalias", "x86qw_ktx_launch_setup", body]


def ktx_entry_aliases() -> list[str]:
    body = "exec x86qw-ktx.cfg;x86qw_ktx_launch_setup"
    return [
        "+tempalias", "on_enter", body,
        "+tempalias", "on_enter_ffa", body,
        "+tempalias", "on_enter_ctf", body,
    ]


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

    @staticmethod
    def seed_game_profile(player, target, game):
        for entry in player.components[game.component].get("project_sources", []):
            destination = str(entry["destination"])
            if not destination.endswith((".cfg", ".example")):
                continue
            source = ROOT / str(entry["path"])
            output = target / destination
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(source.read_bytes())

    def test_new_actions_are_accepted(self):
        for action in ("host", "proxy", "qtv", "version", "components", "presets", "hub", "update", "upgrade"):
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

        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            install_qw.parse_arguments(["--help"], ROOT)
        self.assertIn(f"x86QW {install_qw.application_version()}", output.getvalue())
        self.assertIn("play, host, proxy, qtv, version, update", output.getvalue())
        self.assertIn("--version", output.getvalue())

        version_output = io.StringIO()
        with contextlib.redirect_stdout(version_output):
            self.assertEqual(0, install_qw.main(["version"]))
        self.assertEqual(
            f"x86QW {install_qw.application_version()}\n", version_output.getvalue(),
        )
        flag_output = io.StringIO()
        with contextlib.redirect_stdout(flag_output), self.assertRaises(SystemExit) as raised:
            install_qw.parse_arguments(["--version"], ROOT)
        self.assertEqual(0, raised.exception.code)
        self.assertEqual(version_output.getvalue(), flag_output.getvalue())

    def test_play_has_its_own_module_and_is_exposed_by_the_main_cli(self):
        target = ROOT / "custom-quake"
        parsed = play_qw.parse_arguments(["--no-color", str(target)], ROOT)
        self.assertEqual(target, parsed.target)
        self.assertIsNone(parsed.game)
        self.assertTrue(parsed.no_color)
        direct = play_qw.parse_arguments(["ktx", "--mode", "duel", "--target", str(target)], ROOT)
        self.assertEqual("ktx", direct.game)
        self.assertEqual("duel", direct.mode)
        self.assertEqual(target, direct.target)
        main = install_qw.parse_arguments(["play", str(target)], ROOT)
        self.assertEqual("play", main.action)
        with mock.patch.object(play_qw, "main", return_value=0) as delegated:
            self.assertEqual(0, install_qw.main(["play", str(target), "--no-color"]))
        delegated.assert_called_once_with([str(target), "--no-color"])

    def test_service_cli_exposes_host_proxy_and_qtv(self):
        target = ROOT / "custom-quake"
        host = services_qw.parse_arguments([
            "host", "ktx", "--target", str(target), "--mode", "4on4", "--map", "dm3",
            "--bots", "2", "--bot-skill", "12", "--bind", "0.0.0.0",
            "--with-qtv", "--with-proxy",
        ], ROOT)
        self.assertEqual("host", host.action)
        self.assertEqual("ktx", host.game)
        self.assertEqual("4on4", host.mode)
        self.assertEqual("dm3", host.map)
        self.assertEqual(2, host.ktx_options.bots)
        self.assertEqual(12, host.ktx_options.bot_skill)
        self.assertTrue(host.with_qtv)
        self.assertTrue(host.with_proxy)
        td2 = services_qw.parse_arguments(["host", "td2", "--map", "dm6"], ROOT)
        self.assertEqual("td2", td2.game)
        self.assertIsNone(td2.mode)
        proxy = services_qw.parse_arguments(["proxy", "--port", "30001"], ROOT)
        self.assertEqual(30001, proxy.proxy_port)
        qtv = services_qw.parse_arguments(["qtv", "--upstream", "127.0.0.1:28501"], ROOT)
        self.assertEqual("127.0.0.1:28501", qtv.upstream)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                services_qw.parse_arguments(["host", "--bind", "localhost"], ROOT)
            with self.assertRaises(SystemExit):
                services_qw.parse_arguments(["proxy", "--port", "80"], ROOT)
            with self.assertRaises(SystemExit):
                services_qw.parse_arguments(["host", "td2", "--bots", "1"], ROOT)

    def test_dedicated_ktx_options_are_translated_to_server_cvars(self):
        modes = {mode.key: mode for mode in play_qw.load_ktx_modes(ROOT)}
        bot_options = play_qw.KtxLaunchOptions(
            bots=2, bot_skill=12, bot_weapon="random", bot_health=200,
            bot_break_on_death=True,
        )
        settings = dict(services_qw.dedicated_ktx_settings(
            modes["duel"], "dm6", frozenset({"bots/maps/dm6.bot"}), bot_options, 16,
        ))
        self.assertEqual("1", settings["k_fb_enabled"])
        self.assertEqual("12", settings["k_fb_skill"])
        self.assertEqual("3", settings["k_fb_autoadd_limit"])
        self.assertEqual("0", settings["k_fb_weapon"])
        self.assertEqual("200", settings["k_fb_health"])
        ctf = dict(services_qw.dedicated_ktx_settings(
            modes["ctf"], "e2m2", frozenset(), play_qw.KtxLaunchOptions(
                ctf_hook="smooth", ctf_runes="off", ctf_based_spawn=True,
            ), 16,
        ))
        self.assertEqual("1", ctf["k_ctf_hook"])
        self.assertEqual("1", ctf["k_ctf_hookstyle"])
        self.assertEqual("0", ctf["k_ctf_runes"])
        self.assertEqual("1", ctf["k_ctf_based_spawn"])

    def test_non_ktx_host_spec_uses_only_mvdsv_and_the_selected_gamedir(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "td2")
            (target / game.gamedir).mkdir()
            options = services_qw.parse_arguments([
                "host", "td2", "--map", "dm6", "--target", str(target),
            ], ROOT)
            selection = services_qw.HostedGame(
                game, None, "dm6", frozenset(), options.ktx_options,
            )
            sessions = []
            materialized = []
            binary = target / "mvdsv"
            with mock.patch.object(services_qw, "runtime_binary", return_value=binary):
                spec = services_qw.host_spec(
                    player, options, selection, sessions, materialized,
                )
            self.assertEqual("MVDSV", spec.label)
            self.assertEqual(str(binary), spec.arguments[0])
            self.assertIn(("-game", "td2"), list(zip(spec.arguments, spec.arguments[1:])))
            self.assertNotIn("ezquake", " ".join(spec.arguments).casefold())
            config = sessions[0].read_text(encoding="utf-8")
            self.assertIn("exec x86qw-td2-user.cfg", config)
            self.assertIn("sv_progtype 0", config)
            self.assertIn("sv_progsname x86qw_td2", config)
            self.assertIn('map "dm6"', config)
            self.assertEqual([], materialized)

    def test_ktx_host_reapplies_explicit_settings_after_map_load(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
            mode = next(mode for mode in play_qw.load_ktx_modes(ROOT) if mode.key == "ctf")
            (target / game.gamedir).mkdir()
            options = services_qw.parse_arguments([
                "host", "ktx", "--mode", "ctf", "--map", "e2m2",
                "--ctf-hook", "smooth", "--ctf-runes", "off",
                "--target", str(target),
            ], ROOT)
            selection = services_qw.HostedGame(
                game, mode, "e2m2", frozenset(), options.ktx_options,
            )
            sessions = []
            with mock.patch.object(
                services_qw, "runtime_binary", return_value=target / "mvdsv",
            ), mock.patch.object(services_qw, "materialize_hosted_game", return_value=None):
                spec = services_qw.host_spec(player, options, selection, sessions, [])
            startup = sessions[0].read_text(encoding="utf-8")
            post_map = sessions[1].read_text(encoding="utf-8")
            self.assertIn('map "e2m2"', startup)
            self.assertNotIn("set k_ctf_hook 1", startup)
            self.assertIn("set k_ctf_hook 1", post_map)
            self.assertIn("set k_ctf_runes 0", post_map)
            self.assertTrue(post_map.rstrip().endswith('rcon_password ""'))
            self.assertIsNotNone(spec.startup_rcon)
            self.assertEqual(sessions[1].name, spec.startup_rcon.config_name)

    def test_service_runtime_platforms_and_ipv6_endpoints_are_explicit(self):
        self.assertEqual("macos-arm64", services_qw.runtime_variant("Darwin", "arm64"))
        self.assertEqual("linux-amd64", services_qw.runtime_variant("Linux", "x86_64"))
        self.assertEqual("windows-x64", services_qw.runtime_variant("Windows", "AMD64"))
        self.assertEqual("127.0.0.1:28000", services_qw.endpoint("127.0.0.1", 28000))
        self.assertEqual("[::1]:28000", services_qw.endpoint("::1", 28000))
        with self.assertRaises(services_qw.InstallerError):
            services_qw.runtime_variant("Darwin", "x86_64")

    def test_service_sessions_are_private_and_reject_command_injection(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            session = services_qw.temporary_config(directory, "session-", ["hostname local"])
            self.assertEqual("// x86QW: configuração efêmera removida ao encerrar.\nhostname local\n", session.read_text())
            if os.name != "nt":
                self.assertEqual(0o600, session.stat().st_mode & 0o777)
        with self.assertRaises(services_qw.InstallerError):
            services_qw.safe_text('bad"\nquit', "hostname")
        with self.assertRaises(services_qw.InstallerError):
            services_qw.safe_text("local;quit", "hostname")

    def test_service_directory_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real = root / "real"
            real.mkdir()
            linked = root / "linked"
            linked.symlink_to(real, target_is_directory=True)
            with self.assertRaises(services_qw.InstallerError):
                services_qw.ensure_private_directory(linked)
            created = root / "created"
            services_qw.ensure_private_directory(created)
            self.assertTrue(created.is_dir())

    def test_dedicated_ktx_materialization_is_verified_and_reversible(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            qw = target / "qw"
            qw.mkdir()
            preexisting = qw / "same.cfg"
            preexisting.write_bytes(b"same")
            with zipfile.ZipFile(qw / "ktx.pk3", "w") as package:
                package.writestr("qwprogs.qvm", b"qvm")
                package.writestr("configs/default.cfg", b"cfg")
                package.writestr("same.cfg", b"same")
            materialized = services_qw.materialize_dedicated_ktx(target)
            self.assertEqual(b"qvm", (qw / "qwprogs.qvm").read_bytes())
            self.assertEqual(b"cfg", (qw / "configs/default.cfg").read_bytes())
            services_qw.cleanup_dedicated_ktx(materialized)
            self.assertFalse((qw / "qwprogs.qvm").exists())
            self.assertFalse((qw / "configs").exists())
            self.assertEqual(b"same", preexisting.read_bytes())

    def test_dedicated_ktx_materialization_preserves_conflicting_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            qw = target / "qw"
            qw.mkdir()
            conflict = qw / "qwprogs.qvm"
            conflict.write_bytes(b"personal")
            with zipfile.ZipFile(qw / "ktx.pk3", "w") as package:
                package.writestr("qwprogs.qvm", b"upstream")
            with self.assertRaises(services_qw.InstallerError):
                services_qw.materialize_dedicated_ktx(target)
            self.assertEqual(b"personal", conflict.read_bytes())

    def test_main_cli_delegates_service_actions_without_entering_the_installer(self):
        for action in ("host", "proxy", "qtv"):
            with self.subTest(action=action):
                with mock.patch.object(services_qw, "main", return_value=0) as delegated:
                    self.assertEqual(0, install_qw.main([action, "--target", "/tmp/x86qw-test"]))
                delegated.assert_called_once_with([action, "--target", "/tmp/x86qw-test"])

    def test_ktx_mode_catalog_is_declarative_and_uses_only_supported_commands(self):
        modes = play_qw.load_ktx_modes(ROOT)
        self.assertEqual(
            [
                "duel", "2on2", "4on4", "3on3", "10on10", "ffa",
                "clan-arena", "wipeout", "tot", "blitz-2v2", "blitz-4v4",
                "2on2on2", "3on3on3", "4on4on4", "xonx", "hoony", "ctf",
                "midair", "dmm4", "instagib", "lgc", "rocket-arena",
                "race", "practice",
            ],
            [mode.key for mode in modes],
        )
        self.assertEqual("1on1", modes[0].usermode)
        self.assertEqual("x86qw-ktx-mode-midair.cfg", next(
            mode.entry_config for mode in modes if mode.key == "midair"
        ))
        self.assertEqual("x86qw-ktx-mode-race.cfg", next(
            mode.entry_config for mode in modes if mode.key == "race"
        ))
        ctf = next(mode for mode in modes if mode.key == "ctf")
        self.assertEqual("ctf", ctf.usermode)
        self.assertFalse(ctf.bots)
        self.assertEqual((
            ("sv_loadentfiles", "1"), ("sv_loadentfiles_dir", "ctf"),
        ), ctf.launch_settings)
        race = next(mode for mode in modes if mode.key == "race")
        self.assertEqual("race/routes/{map}.route", race.required_map_asset)
        self.assertEqual(54, len(race.suggested_maps))
        self.assertFalse(race.bots)

    def test_ktx_mode_menu_aligns_players_and_accepts_aliases(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, _, _ = self.make_player(Path(temporary))
            modes = play_qw.load_ktx_modes(ROOT)
            output = io.StringIO()
            with mock.patch("builtins.input", return_value="ca"):
                with contextlib.redirect_stdout(output):
                    selected = player.choose_ktx_mode(modes)
            self.assertEqual("clan-arena", selected.key)
            lines = [line for line in output.getvalue().splitlines() if re.match(r"^\s+\d+\)", line)]
            self.assertEqual(len(modes), len(lines))
            self.assertIn("Duel (padrão)", lines[0])
            description_columns = [line.index(mode.description) for line, mode in zip(lines, modes)]
            self.assertEqual(1, len(set(description_columns)))

    def test_ktx_cli_exposes_map_bots_ctf_and_race_options(self):
        target = ROOT / "custom-quake"
        parsed = play_qw.parse_arguments([
            "ktx", "--mode", "duel", "--map", "dm6", "--bots", "2",
            "--bot-skill", "12", "--bot-team", "red", "--bot-weapon", "8",
            "--bot-health", "200", "--bot-break-on-death", "--target", str(target),
        ], ROOT)
        self.assertEqual("dm6", parsed.map)
        self.assertEqual(play_qw.KtxLaunchOptions(
            bots=2, bot_skill=12, bot_team="red", bot_weapon="8",
            bot_health=200, bot_break_on_death=True,
        ), parsed.ktx_options)
        race = play_qw.parse_arguments([
            "--mode", "race", "--race-style", "match",
            "--race-scoring", "formula1", "--race-pacemaker", "3",
            "--race-hide-players", "--target", str(target),
        ], ROOT)
        self.assertEqual("ktx", race.game)
        self.assertEqual("formula1", race.ktx_options.race_scoring)
        ctf = play_qw.parse_arguments([
            "--mode", "ctf", "--ctf-hook", "smooth", "--ctf-runes", "off",
            "--ctf-based-spawn", "--target", str(target),
        ], ROOT)
        self.assertEqual("smooth", ctf.ktx_options.ctf_hook)
        self.assertTrue(ctf.ktx_options.ctf_based_spawn)

    def test_ktx_launch_commands_validate_routes_and_mode_specific_options(self):
        modes = {mode.key: mode for mode in play_qw.load_ktx_modes(ROOT)}
        assets = frozenset({"bots/maps/dm6.bot", "race/routes/dm6.route"})
        options = play_qw.KtxLaunchOptions(
            bots=2, bot_skill=12, bot_team="red", bot_weapon="8", bot_health=200,
            bot_break_on_death=True,
        )
        self.assertEqual((
            "cmd botcmd skill 12",
            "cmd botcmd health 200",
            "cmd botcmd weapon 8",
            "cmd botcmd addbot 12 red",
            "cmd botcmd addbot 12 red",
        ), play_qw.ktx_launch_commands(modes["duel"], "dm6", assets, options))
        self.assertEqual((
            "cmd race_match", "cmd race_scoring", "cmd race_scoring",
            "cmd race_pacemaker 3", "cmd race_hide_players",
        ), play_qw.ktx_launch_commands(
            modes["race"], "dm6", assets,
            play_qw.KtxLaunchOptions(
                race_style="match", race_scoring="formula1",
                race_pacemaker=3, race_hide_players=True,
            ),
        ))
        self.assertEqual((
            "cmd hook_smooth", "cmd norunes", "cmd ctfbasedspawn",
        ), play_qw.ktx_launch_commands(
            modes["ctf"], "e2m2", frozenset(),
            play_qw.KtxLaunchOptions(
                ctf_hook="smooth", ctf_runes="off", ctf_based_spawn=True,
            ),
        ))
        with self.assertRaisesRegex(play_qw.InstallerError, "não possui rota Frogbot"):
            play_qw.ktx_launch_commands(
                modes["duel"], "dm2", assets, play_qw.KtxLaunchOptions(bots=1),
            )
        with self.assertRaisesRegex(play_qw.InstallerError, "não são compatíveis"):
            play_qw.ktx_launch_commands(
                modes["race"], "dm6", assets, play_qw.KtxLaunchOptions(bots=1),
            )
        with self.assertRaisesRegex(play_qw.InstallerError, "só podem ser usadas"):
            play_qw.ktx_launch_commands(
                modes["duel"], "dm6", assets,
                play_qw.KtxLaunchOptions(ctf_hook="off"),
            )

    def test_ktx_race_map_selection_requires_an_packaged_route(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, _, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
            with mock.patch.object(player, "local_map_names", return_value=["dm6", "dm2"]):
                selected = player.choose_local_map(
                    game,
                    requested_map="dm6",
                    required_asset="race/routes/{map}.route",
                    available_assets=frozenset({"race/routes/dm6.route"}),
                )
                self.assertEqual("dm6", selected)
                with self.assertRaisesRegex(play_qw.InstallerError, "não é compatível"):
                    player.choose_local_map(
                        game,
                        requested_map="dm2",
                        required_asset="race/routes/{map}.route",
                        available_assets=frozenset({"race/routes/dm6.route"}),
                    )

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
            "ktx": ("1.48+x86qw.1", "1.48"),
            "final-arena": ("1.20+nquake.e4cb23d40aa2+x86qw.2", "1.20"),
            "pro-x": ("1.1+x86qw.3", "1.1"),
            "team-fortress": ("2.9+nquake.e4cb23d40aa2+x86qw.4", "2.9"),
            "td2": ("2.22+x86qw.3", "2.22"),
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

    def test_legacy_borderless_layout_restores_the_previous_fullscreen_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, _ = self.make_player(Path(temporary))
            config = target / "ezquake/configs/config.cfg"
            config.parent.mkdir(parents=True)
            settings = {
                "vid_fullscreen": "0", "vid_usedesktopres": "1", "vid_win_borderless": "1",
                "vid_win_displaynumber": "0", "vid_win_width": "1800", "vid_win_height": "1130",
                "vid_xpos": "0", "vid_ypos": "39",
            }
            config.write_bytes(b"".join(f'{name} \"{value}\"\n'.encode() for name, value in settings.items()))
            backup = config.with_name("config.video-pre-x86qw.cfg")
            backup.write_bytes(b'vid_fullscreen "1"\nvid_usedesktopres "1"\n')
            marker = target / play_qw.LEGACY_MACOS_VIDEO_LAYOUT
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({"managed": True, "settings": settings}), encoding="utf-8")
            with mock.patch.object(play_qw.sys, "platform", "darwin"):
                with contextlib.redirect_stdout(io.StringIO()):
                    player.remove_legacy_macos_video_layout()
            self.assertEqual(b'vid_fullscreen "1"\nvid_usedesktopres "1"\n', config.read_bytes())
            self.assertFalse(marker.exists())
            self.assertFalse(backup.exists())

    def test_legacy_borderless_layout_preserves_a_personal_video_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, _ = self.make_player(Path(temporary))
            config = target / "ezquake/configs/config.cfg"
            config.parent.mkdir(parents=True)
            config.write_bytes(b'vid_fullscreen "1"\n')
            backup = config.with_name("config.video-pre-x86qw.cfg")
            backup.write_bytes(b'vid_fullscreen "1"\nvid_usedesktopres "1"\n')
            marker = target / play_qw.LEGACY_MACOS_VIDEO_LAYOUT
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({
                "managed": True,
                "settings": {name: "0" for name in play_qw.LEGACY_MACOS_VIDEO_CVARS},
            }), encoding="utf-8")
            with mock.patch.object(play_qw.sys, "platform", "darwin"):
                with contextlib.redirect_stdout(io.StringIO()):
                    player.remove_legacy_macos_video_layout()
            self.assertEqual(b'vid_fullscreen "1"\n', config.read_bytes())
            self.assertFalse(marker.exists())
            self.assertTrue(backup.exists())

    def test_notched_macos_sets_the_safe_fullscreen_mode_before_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, _ = self.make_player(Path(temporary))
            config = target / "ezquake/configs/config.cfg"
            config.parent.mkdir(parents=True)
            config.write_bytes(b'vid_fullscreen "1"\nvid_usedesktopres "1"\n')
            response = mock.Mock(stdout=json.dumps({"SPDisplaysDataType": [{"spdisplays_ndrvs": [{
                    "spdisplays_main": "spdisplays_yes",
                    "spdisplays_connection_type": "spdisplays_internal",
                    "spdisplays_pixelresolution": "spdisplays_3024x1964Retina",
                    "_spdisplays_resolution": "1800 x 1169 @ 120.00Hz",
                }]}]}))
            with mock.patch.object(play_qw.sys, "platform", "darwin"):
                with mock.patch.object(play_qw.subprocess, "run", return_value=response):
                    with contextlib.redirect_stdout(io.StringIO()):
                        player.configure_macos_fullscreen()
            values = player.config_cvars(config.read_bytes(), play_qw.MACOS_FULLSCREEN_CVARS)
            self.assertEqual({
                "vid_fullscreen": "1",
                "vid_usedesktopres": "0",
                "vid_width": "3024",
                "vid_height": "1890",
                "vid_displayfrequency": "0",
            }, values)
            marker = json.loads((target / play_qw.MACOS_FULLSCREEN_LAYOUT).read_text(encoding="utf-8"))
            self.assertTrue(marker["managed"])
            self.assertEqual(values, marker["settings"])

    def test_notched_macos_preserves_an_existing_custom_video_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, _ = self.make_player(Path(temporary))
            config = target / "ezquake/configs/config.cfg"
            config.parent.mkdir(parents=True)
            original = b'vid_fullscreen "1"\nvid_usedesktopres "0"\nvid_width "1920"\nvid_height "1200"\n'
            config.write_bytes(original)
            response = mock.Mock(stdout=json.dumps({"SPDisplaysDataType": [{"spdisplays_ndrvs": [{
                    "spdisplays_main": "spdisplays_yes",
                    "spdisplays_connection_type": "spdisplays_internal",
                    "spdisplays_pixelresolution": "spdisplays_3024x1964Retina",
                    "_spdisplays_resolution": "1800 x 1169 @ 120.00Hz",
                }]}]}))
            with mock.patch.object(play_qw.sys, "platform", "darwin"):
                with mock.patch.object(play_qw.subprocess, "run", return_value=response):
                    with contextlib.redirect_stdout(io.StringIO()):
                        player.configure_macos_fullscreen()
            self.assertEqual(original, config.read_bytes())
            marker = json.loads((target / play_qw.MACOS_FULLSCREEN_LAYOUT).read_text(encoding="utf-8"))
            self.assertFalse(marker["managed"])

    def test_notched_macos_migrates_desktop_fullscreen_to_the_safe_explicit_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, _ = self.make_player(Path(temporary))
            config = target / "ezquake/configs/config.cfg"
            config.parent.mkdir(parents=True)
            previous = {
                "vid_fullscreen": "1",
                "vid_usedesktopres": "1",
                "vid_width": "3024",
                "vid_height": "1964",
                "vid_displayfrequency": "0",
            }
            config.write_bytes(b"".join(
                f'{name} "{value}"\n'.encode() for name, value in previous.items()
            ))
            marker = target / play_qw.MACOS_FULLSCREEN_LAYOUT
            marker.parent.mkdir(parents=True)
            marker.write_text(json.dumps({
                "format": 1,
                "project": "x86qw",
                "mode": "notched-fullscreen",
                "managed": True,
                "settings": previous,
            }), encoding="utf-8")
            response = mock.Mock(stdout=json.dumps({"SPDisplaysDataType": [{"spdisplays_ndrvs": [{
                    "spdisplays_main": "spdisplays_yes",
                    "spdisplays_connection_type": "spdisplays_internal",
                    "spdisplays_pixelresolution": "spdisplays_3024x1964Retina",
                }]}]}))
            with mock.patch.object(play_qw.sys, "platform", "darwin"):
                with mock.patch.object(play_qw.subprocess, "run", return_value=response):
                    player.configure_macos_fullscreen()
            values = player.config_cvars(config.read_bytes(), play_qw.MACOS_FULLSCREEN_CVARS)
            self.assertEqual("0", values["vid_usedesktopres"])
            self.assertEqual("1890", values["vid_height"])
            self.assertEqual("0", values["vid_displayfrequency"])
            state = json.loads(marker.read_text(encoding="utf-8"))
            self.assertTrue(state["managed"])
            self.assertEqual(values, state["settings"])

    def test_macos_without_a_notch_keeps_desktop_fullscreen(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, _ = self.make_player(Path(temporary))
            config = target / "ezquake/configs/config.cfg"
            config.parent.mkdir(parents=True)
            original = b'vid_fullscreen "1"\nvid_usedesktopres "1"\n'
            config.write_bytes(original)
            with mock.patch.object(play_qw.sys, "platform", "darwin"):
                with mock.patch.object(
                    play_qw.subprocess, "run",
                    return_value=mock.Mock(stdout=json.dumps({
                        "SPDisplaysDataType": [{"spdisplays_ndrvs": [{
                            "spdisplays_main": "spdisplays_yes",
                            "spdisplays_connection_type": "spdisplays_internal",
                            "spdisplays_pixelresolution": "spdisplays_1920x1200Retina",
                        }]}],
                    })),
                ):
                    player.configure_macos_fullscreen()
            self.assertEqual(original, config.read_bytes())
            self.assertFalse((target / play_qw.MACOS_FULLSCREEN_LAYOUT).exists())

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
                {
                    "nquake", "ktx", "final-arena", "pro-x", "team-fortress", "td2",
                    "mvdsv", "qwfwd", "qtv",
                },
                installer.content_component_namespaces,
            )
            self.assertEqual(set(installer.components), set(installer.component_catalog["profiles"]["complete"]))
            self.assertNotIn("qrp-hires", installer.component_catalog["profiles"]["recommended"])
            self.assertNotIn("nquake-matchinfo", installer.component_catalog["profiles"]["recommended"])
            self.assertIn("qrp-hires", installer.component_catalog["profiles"]["complete"])
            self.assertNotIn("total-destruction-2", installer.component_catalog["profiles"]["recommended"])
            self.assertIn("total-destruction-2", installer.component_catalog["profiles"]["complete"])
            ktx = installer.components["ktx"]
            ktx_overlay = ROOT / "dist/mods/ktx/1.47/x86qw"
            expected_ktx_sources = {
                "dist/mods/ktx/1.47/x86qw/client.cfg",
                "dist/mods/ktx/1.47/x86qw/user.cfg.example",
                *(str(path.relative_to(ROOT)) for path in ktx_overlay.glob("help*.cfg")),
                *(str(path.relative_to(ROOT)) for path in ktx_overlay.glob("mode-*.cfg")),
            }
            self.assertEqual(
                expected_ktx_sources,
                {source["path"] for source in ktx["project_sources"]},
            )
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
                    "dist/mods/final-arena/1.20/x86qw/client.cfg",
                    "dist/mods/final-arena/1.20/x86qw/server.cfg",
                    "dist/mods/final-arena/1.20/x86qw/user.cfg.example",
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
                    "dist/mods/pro-x/1.1/x86qw/runtime/maps/proxmap1.ent",
                },
                {source["path"] for source in pro_x["project_sources"]},
            )

    def test_ktx_component_is_prepared_and_receipted_from_its_upstream_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, target, _ = self.make_installer(root)
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, "w") as package:
                package.writestr("progs.dat", b"ktx")
            payload = inner.getvalue()
            revision = "a" * 40
            version = "1.47+x86qw.2"
            artifact = root / f"ktx-{version}.zip"
            metadata = {
                "format": 1, "project": "x86qw", "package": "ktx",
                "version": version, "source_revision": revision,
                "members": [{
                    "path": "payload/qw/ktx.pk3",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "source": "official upstream archive: qw/ktx.pk3",
                }],
            }
            with zipfile.ZipFile(artifact, "w") as package:
                package.writestr("payload/qw/ktx.pk3", payload)
                package.writestr("_x86qw/component.json", json.dumps(metadata))
            catalog_package = {
                "package": "ktx", "version": version,
                "source_revision": revision,
                "origin_url": f"https://example.invalid/{artifact.name}",
            }
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed, defaults = installer.prepare_component_package(catalog_package, artifact)
            self.assertEqual([], defaults)
            self.assertTrue((managed / "qw/ktx.pk3").is_file())
            count = installer.install_component_overlay(
                "ktx", managed, version, str(catalog_package["origin_url"]),
            )
            self.assertEqual(1, count)
            self.assertEqual(1, installer.verify_component("ktx"))

    def test_ktx_component_accepts_an_independent_upstream_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, _, _ = self.make_installer(root)
            revision = "a" * 40
            version = "1.47+x86qw.2"
            filename = f"ktx-{version}.zip"
            package = {
                "component": "ktx", "package": "ktx", "version": version,
                "channel": "content", "platform": "any", "architecture": "any",
                "filename": filename, "size": 123, "sha256": "b" * 64,
                "source_revision": revision, "redistribution_reviewed": True,
                "urls": [f"https://example.invalid/{filename}"],
                "release_url": "https://github.com/QW-Group/ktx/releases/tag/1.47",
                "release_notes": "KTX atualizado.",
            }
            installer._public_catalog = {"format": 1, "project": "x86qw", "packages": [package]}
            self.assertEqual(version, installer.component_package_record("ktx")["version"])

            artifact = root / filename
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as inner:
                inner.writestr("qwprogs.qvm", b"new qvm")
            data = payload.getvalue()
            metadata = {
                "format": 1, "project": "x86qw", "package": "ktx",
                "version": version, "source_revision": revision,
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

    def test_legacy_nquake_ktx_is_replaced_by_the_harmonized_ktx_component(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, target, _ = self.make_installer(root)
            installer.stage = target / ".legacy-stage"
            installer.stage.mkdir()
            managed = installer.stage / "legacy-managed"
            (managed / "qw").mkdir(parents=True)
            legacy_pk3 = io.BytesIO()
            with zipfile.ZipFile(legacy_pk3, "w") as package:
                package.writestr("legacy.txt", b"nquake")
            (managed / "qw/ktx.pk3").write_bytes(legacy_pk3.getvalue())
            (managed / "qw/x86qw-ktx.cfg").write_text("legacy\n", encoding="utf-8")
            installer.install_component_overlay(
                "nquake-ktx", managed, "1.47+nquake.old+x86qw.1", "legacy mirror",
            )
            installer.cleanup_stage()

            installer.stage = target / ".migration-stage"
            installer.stage.mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                installer.install_components(["ktx"])

            self.assertFalse(installer.validate_component_pair("nquake-ktx")[0])
            self.assertTrue(installer.validate_component_pair("ktx")[0])
            self.assertEqual(["ktx"], installer.installed_components())
            installer.verify_component("ktx")
            with zipfile.ZipFile(target / "qw/ktx.pk3") as package:
                names = set(package.namelist())
                self.assertIn("bots/maps/anarena.bot", names)
                self.assertEqual(77, sum(
                    name.startswith("bots/maps/") and name.endswith(".bot") for name in names
                ))
                self.assertEqual(54, sum(
                    name.startswith("race/routes/") and name.endswith(".route") for name in names
                ))
                self.assertIn("race/routes/dm6.route", names)
                self.assertIn("qwprogs.qvm", names)
                self.assertIn("locs/dm6.loc", names)
                self.assertIn("configs/usermodes/dmm4base.cfg", names)

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
            inventory = (target / ".install/components/clan-arena/inventory").read_text(encoding="utf-8")
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
            self.assertFalse((target / ".install/components/clan-arena/receipt").exists())
            self.assertFalse((target / ".install/components/clan-arena/inventory").exists())
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
            filename = "ktx-1.47.zip"
            package = {
                "package": "ktx", "filename": filename,
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
            receipt = (target / ".install/components/nquake-bootstrap/receipt").read_text(encoding="utf-8")
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
                'alias +zoom "fov 50"\nalias personal "say oi"\n'
                'cl_remote_capabilities "$cl_remote_capabilities,cl_movespeedkey"\n'
                'cfg_save_unchanged "1"\nname "x86"\n',
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
            self.assertNotIn('$cl_remote_capabilities', base.read_text(encoding="utf-8"))
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
            with mock.patch.dict(install_qw.os.environ, {"X86QW_TEST_WINDOWED": ""}):
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

    def test_runtime_smoke_uses_a_window_unless_fullscreen_is_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            runtime = target / "ezQuake Stable.app"
            executable = runtime / "Contents/MacOS/ezQuake"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"mach-o")
            with mock.patch.dict(install_qw.os.environ, {"X86QW_TEST_WINDOWED": "1"}):
                with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                    with mock.patch.object(install_qw.subprocess, "Popen") as popen:
                        installer.launch_runtime(runtime, ["+map", "dm6"])
            self.assertEqual([
                str(executable), "-nohome", "-basedir", str(target),
                "-window", "-width", "1280", "-height", "720",
                "+cfg_save_onquit", "0", "+map", "dm6",
            ], popen.call_args.args[0])

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
                *local_server_baseline("td2"),
                "-game", "td2", "+sv_gamedir", "td2",
                "+sv_progtype", "0",
                "+cl_pext_lagteleport", "0",
                "+map", "dm6", "+wait",
                "+exec", "x86qw-td2.cfg",
            ])

    def test_ktx_uses_the_native_qw_gamedir_only_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
            runtime = target / "ezQuake Stable.app"
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "check_paks"):
                    with mock.patch.object(installer, "available_local_games", return_value=[game]):
                        with mock.patch.object(installer, "installed_component_for_game", return_value=game.component):
                            with mock.patch.object(installer, "verify_component"):
                                with mock.patch.object(installer, "local_map_names", return_value=["dm6"]):
                                    with mock.patch.object(installer, "choose_host_runtime", return_value=("stable", runtime)):
                                        with mock.patch.object(installer, "launch_runtime") as launch:
                                            with mock.patch.object(installer, "ensure_local_play_support"):
                                                with mock.patch("builtins.input", side_effect=["", "", ""]):
                                                    installer.play_local()
            launch.assert_called_once_with(runtime, [
                *local_server_baseline("ktx"),
                *ktx_launch_setup_alias(),
                *ktx_entry_aliases(),
                "+set", "k_defmap", "dm6",
                "+set", "k_defmode", "1on1",
                "+set", "x86qw_ktx_preset", "duel",
                *ktx_mode_runtime_aliases("duel"),
                "+map", "dm6",
            ])

    def test_ktx_direct_midair_mode_installs_a_one_shot_entry_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
            runtime = target / "ezQuake Stable.app"
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "check_paks"):
                    with mock.patch.object(installer, "available_local_games", return_value=[game]):
                        with mock.patch.object(installer, "installed_component_for_game", return_value="ktx"):
                            with mock.patch.object(installer, "verify_component"):
                                with mock.patch.object(installer, "local_map_names", return_value=["povdmm4"]):
                                    with mock.patch.object(installer, "choose_host_runtime", return_value=("stable", runtime)):
                                        with mock.patch.object(installer, "launch_runtime") as launch:
                                            with mock.patch.object(installer, "ensure_local_play_support"):
                                                with mock.patch("builtins.input", return_value=""):
                                                    installer.play_local("ktx", "midair")
            launch.assert_called_once_with(runtime, [
                *local_server_baseline("ktx"),
                *ktx_launch_setup_alias(),
                *ktx_entry_aliases(),
                "+tempalias", "on_enter", "exec x86qw-ktx-mode-midair.cfg",
                "+set", "k_defmap", "povdmm4",
                "+set", "k_defmode", "1on1",
                "+set", "x86qw_ktx_preset", "midair",
                *ktx_mode_runtime_aliases("midair"),
                "+map", "povdmm4",
            ])

    def test_ktx_bot_options_enable_frogbot_before_map_and_add_after_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
            runtime = target / "ezQuake Stable.app"
            options = play_qw.KtxLaunchOptions(
                bots=2, bot_skill=10, bot_team="red", bot_weapon="8",
                bot_health=150, bot_break_on_death=True,
            )
            assets = frozenset({"bots/maps/dm6.bot"})
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "check_paks"):
                    with mock.patch.object(installer, "available_local_games", return_value=[game]):
                        with mock.patch.object(installer, "installed_component_for_game", return_value="ktx"):
                            with mock.patch.object(installer, "verify_component"):
                                with mock.patch.object(installer, "ktx_archive_members", return_value=assets):
                                    with mock.patch.object(installer, "local_map_names", return_value=["dm6"]):
                                        with mock.patch.object(installer, "choose_host_runtime", return_value=("stable", runtime)):
                                            with mock.patch.object(installer, "launch_runtime") as launch:
                                                with mock.patch.object(installer, "ensure_local_play_support"):
                                                    installer.play_local(
                                                        "ktx", "duel", "dm6", options,
                                                    )
            launch.assert_called_once_with(runtime, [
                *local_server_baseline("ktx"),
                "+set", "k_fb_enabled", "1",
                "+set", "k_fb_break_on_death", "1",
                *ktx_launch_setup_alias(
                    "cmd botcmd skill 10",
                    "cmd botcmd health 150",
                    "cmd botcmd weapon 8",
                    "cmd botcmd addbot 10 red",
                    "cmd botcmd addbot 10 red",
                ),
                *ktx_entry_aliases(),
                "+set", "k_defmap", "dm6",
                "+set", "k_defmode", "1on1",
                "+set", "x86qw_ktx_preset", "duel",
                *ktx_mode_runtime_aliases("duel"),
                "+map", "dm6",
            ])

    def test_ktx_ctf_loads_curated_entities_before_the_map(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
            runtime = target / "ezQuake Stable.app"
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "check_paks"):
                    with mock.patch.object(installer, "available_local_games", return_value=[game]):
                        with mock.patch.object(installer, "installed_component_for_game", return_value="ktx"):
                            with mock.patch.object(installer, "verify_component"):
                                with mock.patch.object(installer, "local_map_names", return_value=["e2m2"]):
                                    with mock.patch.object(installer, "choose_host_runtime", return_value=("stable", runtime)):
                                        with mock.patch.object(installer, "launch_runtime") as launch:
                                            with mock.patch.object(installer, "ensure_local_play_support"):
                                                with mock.patch("builtins.input", return_value=""):
                                                    installer.play_local("ktx", "ctf")
            launch.assert_called_once_with(runtime, [
                *local_server_baseline("ktx"),
                "+sv_loadentfiles", "1",
                "+sv_loadentfiles_dir", "ctf",
                *ktx_launch_setup_alias(),
                *ktx_entry_aliases(),
                "+set", "k_defmap", "e2m2",
                "+set", "k_defmode", "ctf",
                "+set", "x86qw_ktx_preset", "ctf",
                *ktx_mode_runtime_aliases("ctf"),
                "+map", "e2m2",
            ])

    def test_ktx_ctf_is_the_only_managed_content_allowed_under_id1_maps(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.validate_managed_path("id1/maps/ctf/e2m2.ent")
            for relative in (
                "id1/pak0.pak",
                "id1/maps/e2m2.ent",
                "id1/maps/ctf/e2m2.bsp",
                "id1/maps/ctf/nested/e2m2.ent",
            ):
                with self.subTest(path=relative):
                    with self.assertRaises(install_qw.InstallerError):
                        installer.validate_managed_path(relative)

    def test_ktx_race_and_practice_use_the_correct_one_shot_entry_event(self):
        cases = {
            "race": ("on_enter_ffa", "x86qw-ktx-mode-race.cfg", "ffa"),
            "practice": ("on_enter", "x86qw-ktx-mode-practice.cfg", "1on1"),
        }
        for mode, (event, entry_config, usermode) in cases.items():
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                installer, target, _ = self.make_player(Path(temporary))
                game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
                runtime = target / "ezQuake Stable.app"
                with contextlib.redirect_stdout(io.StringIO()):
                    with mock.patch.object(installer, "check_paks"):
                        with mock.patch.object(installer, "available_local_games", return_value=[game]):
                            with mock.patch.object(installer, "installed_component_for_game", return_value="ktx"):
                                with mock.patch.object(installer, "verify_component"):
                                    assets = frozenset({"race/routes/dm6.route"})
                                    with mock.patch.object(installer, "ktx_archive_members", return_value=assets):
                                        with mock.patch.object(installer, "local_map_names", return_value=["dm6"]):
                                            with mock.patch.object(installer, "choose_host_runtime", return_value=("stable", runtime)):
                                                with mock.patch.object(installer, "launch_runtime") as launch:
                                                    with mock.patch.object(installer, "ensure_local_play_support"):
                                                        with mock.patch("builtins.input", return_value=""):
                                                            installer.play_local("ktx", mode)
                launch.assert_called_once_with(runtime, [
                    *local_server_baseline("ktx"),
                    *ktx_launch_setup_alias(),
                    *ktx_entry_aliases(),
                    "+tempalias", event, f"exec {entry_config}",
                    "+set", "k_defmap", "dm6",
                    "+set", "k_defmode", usermode,
                    "+set", "x86qw_ktx_preset", mode,
                    *ktx_mode_runtime_aliases(mode),
                    "+map", "dm6",
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
                    "+sv_loadentfiles", "1",
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
                    *local_server_baseline(key),
                    "-game", gamedir, "+sv_gamedir", gamedir,
                    "+sv_progtype", "0", *before_map, "+map", map_name, "+wait",
                    *after_wait, "+exec", profile,
                ])

    def test_team_fortress_loads_only_required_legacy_settings_before_the_map(self):
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
                *local_server_baseline("team-fortress"),
                "-game", "fortress", "+sv_gamedir", "fortress",
                "+sv_progtype", "0", "+exec", "x86qw-fortress-pre.cfg",
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
            self.seed_game_profile(installer, target, game)
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
            self.assertEqual(1, installer.verify_component("play-support"))
            self.assertEqual(1, installer.remove_component("play-support"))
            self.assertTrue(server_config.exists())
            self.assertTrue(client_config.exists())
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
            self.seed_game_profile(installer, target, game)
            with contextlib.redirect_stdout(io.StringIO()):
                installer.ensure_local_play_support([game])

            user_config = target / "td2/x86qw-td2-user.cfg"
            user_config.write_text('bind MOUSE4 "impulse 23"\n', encoding="utf-8")
            upstream.write_bytes(b"td2-v2")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.ensure_local_play_support([game])

            self.assertEqual(b"td2-v2", (target / "td2/x86qw_td2.dat").read_bytes())
            self.assertEqual('bind MOUSE4 "impulse 23"\n', user_config.read_text(encoding="utf-8"))
            self.assertEqual(1, installer.verify_component("play-support"))
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
                self.seed_game_profile(installer, target, game)
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
                        "dist/mods/final-arena/1.20/x86qw/client.cfg"
                        if game.key == "final-arena"
                        else "dist/mods/pro-x/1.1/x86qw/client.cfg"
                    )).read_bytes(),
                    client.read_bytes(),
                )
                self.assertEqual(
                    (ROOT / (
                        "dist/mods/final-arena/1.20/x86qw/server.cfg"
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
            self.assertEqual(2, installer.verify_component("play-support"))
            self.assertEqual(2, installer.remove_component("play-support"))
            for game in games:
                self.assertTrue((
                    target / game.gamedir / f"x86qw-{game.profile}-user.cfg"
                ).is_file())

    def test_every_playable_mod_profile_keeps_help_on_demand(self):
        expected_gameplay = {
            "ktx": {
                'tempalias sv_enableprofile ""',
                'bind 1 "tp_msgquaddead"',
                'bind 5 "tp_msgenemypwr"',
                'bind q "weapon 6"',
                'bind e "weapon 7"',
                'bind MOUSE2 "weapon 8"',
                'bind MWHEELUP "time_inc"',
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
                    destination = f"{game.gamedir}/x86qw-{game.profile}.cfg"
                    entry = next(
                        item for item in installer.components[game.component].get("project_sources", [])
                        if item.get("destination") == destination
                    )
                    profile = (ROOT / str(entry["path"])).read_text(encoding="utf-8")
                    help_alias = f"x86qw_{game.profile}_help"
                    f10 = re.search(r'(?m)^bind F10 "([^"]+)"$', profile)
                    self.assertIsNotNone(f10)
                    assert f10 is not None
                    if game.key == "ktx":
                        self.assertIn("tempalias ktx_controls", profile)
                        self.assertEqual("ktx_controls", f10.group(1))
                    else:
                        self.assertIn(f"tempalias {help_alias}", profile)
                        self.assertIn(help_alias, f10.group(1).split(";"))
                    for expected in expected_gameplay[game.key]:
                        self.assertIn(expected, profile)
                    user_exec = f"exec x86qw-{game.profile}-user.cfg"
                    self.assertIn(user_exec, profile)
                    executable_lines = [
                        line.strip() for line in profile.splitlines()
                        if line.strip() and not line.lstrip().startswith("//")
                    ]
                    self.assertEqual(user_exec, executable_lines[-1])
                    self.assertNotIn(help_alias, executable_lines)

    def test_only_ktx_restores_the_original_nquake_startup_message(self):
        snapshot_root = ROOT / "dist/distributions/nquake"
        revisions = [path for path in snapshot_root.iterdir() if path.is_dir()]
        self.assertEqual(1, len(revisions))
        nquake = (revisions[0] / "non-gpl/qw/autoexec.cfg").read_text(
            encoding="latin-1"
        )
        ktx = (ROOT / "dist/mods/ktx/1.47/x86qw/client.cfg").read_text(
            encoding="utf-8"
        )

        def alias_body(payload: str, prefix: str, number: int) -> str:
            match = re.search(
                rf'(?m)^(?:temp)?alias\s+{re.escape(prefix)}{number}\s+"(.*)"$',
                payload,
            )
            self.assertIsNotNone(match)
            assert match is not None
            return match.group(1)

        for number in range(1, 13):
            self.assertEqual(
                alias_body(nquake, "_startup_message_", number),
                alias_body(ktx, "x86qw_ktx_startup_", number),
            )

        executable_lines = [
            line.strip() for line in ktx.splitlines()
            if line.strip() and not line.lstrip().startswith("//")
        ]
        self.assertIn("x86qw_ktx_startup", executable_lines)
        self.assertLess(
            executable_lines.index("x86qw_ktx_startup"),
            executable_lines.index("exec x86qw-ktx-user.cfg"),
        )
        for game in play_qw.LOCAL_GAMES:
            if game.key == "ktx":
                continue
            profile = (
                ROOT / f"dist/mods/{game.key}/{game.version}/x86qw/client.cfg"
            ).read_text(encoding="utf-8")
            self.assertNotIn("x86qw_ktx_startup", profile)

    def test_ktx_f10_prints_colored_multiline_controls_and_active_mode(self):
        profile = (ROOT / "dist/mods/ktx/1.47/x86qw/client.cfg").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'tempalias ktx_controls "scr_consize 0.8;toggleconsole;exec x86qw-ktx-help.cfg"',
            profile,
        )
        self.assertIn('bind F10 "ktx_controls"', profile)

        help_profile = (ROOT / "dist/mods/ktx/1.47/x86qw/help.cfg").read_text(
            encoding="utf-8"
        )
        self.assertIn("ktx_mode", help_profile)
        self.assertIn("x86qw_ktx_mode_help", help_profile)
        visible_help = re.sub(r"\^(.)", r"\1", help_profile).replace("$x20", " ")
        self.assertIn("BOTS FROGBOT", visible_help)
        self.assertIn("BOTCMD SKILL 5", visible_help)
        self.assertIn("BOTCMD ADDBOT", visible_help)
        self.assertIn("BOTCMD REMOVEBOT", visible_help)
        rows = [
            line.removeprefix("echo ") for line in help_profile.splitlines()
            if line.startswith("echo ") and " - " in line
        ]
        self.assertGreaterEqual(len(rows), 40)
        columns = []
        for row in rows:
            self.assertIn("^", row)
            visible = re.sub(r"\^(.)", r"\1", row).replace("$x20", " ")
            columns.append(visible.index(" - "))
        self.assertEqual([26], sorted(set(columns)))

    def test_each_ktx_mode_has_aligned_contextual_help_from_upstream_commands(self):
        all_separator_columns = []
        for mode in play_qw.load_ktx_modes(ROOT):
            with self.subTest(mode=mode.key):
                self.assertEqual(
                    f"exec x86qw-ktx-help-{mode.key}.cfg",
                    play_qw.ktx_mode_help_alias(mode),
                )
                help_profile = (
                    ROOT / f"dist/mods/ktx/1.47/x86qw/help-{mode.key}.cfg"
                ).read_text(encoding="utf-8")
                rows = [
                    line.removeprefix("echo ") for line in help_profile.splitlines()
                    if line.startswith("echo ") and "- " in line
                ]
                self.assertEqual(len(mode.help_commands), len(rows))
                separator_columns = []
                for row, (command, description) in zip(rows, mode.help_commands):
                    visible = re.sub(r"\^(.)", r"\1", row).replace("$x20", " ")
                    self.assertIn(command, visible)
                    self.assertTrue(visible.endswith(description))
                    separator_columns.append(visible.index("- "))
                self.assertEqual(1, len(set(separator_columns)))
                all_separator_columns.extend(separator_columns)
        self.assertEqual([27], sorted(set(all_separator_columns)))

    def test_ktx_help_files_stay_below_the_console_line_limit(self):
        profiles = [
            ROOT / "dist/mods/ktx/1.47/x86qw/client.cfg",
            *sorted((ROOT / "dist/mods/ktx/1.47/x86qw").glob("help*.cfg")),
        ]
        for profile in profiles:
            with self.subTest(profile=profile.name):
                lines = profile.read_text(encoding="utf-8").splitlines()
                self.assertLessEqual(max(map(len, lines)), 512)

    def test_each_mod_profile_is_isolated_from_every_other_mod(self):
        profiles = {}
        for game in play_qw.LOCAL_GAMES:
            path = ROOT / f"dist/mods/{game.key}/{game.version}/x86qw/client.cfg"
            profiles[game.key] = path.read_text(encoding="utf-8")

        for game in play_qw.LOCAL_GAMES:
            with self.subTest(game=game.key):
                profile = profiles[game.key]
                self.assertIn(f"exec x86qw-{game.profile}-user.cfg", profile)
                for other in play_qw.LOCAL_GAMES:
                    if other.key == game.key:
                        continue
                    self.assertNotIn(f"exec x86qw-{other.profile}.cfg", profile)
                    self.assertNotIn(f"exec x86qw-{other.profile}-user.cfg", profile)

        for key, profile in profiles.items():
            if key != "ktx":
                self.assertNotRegex(profile, r'(?m)^bind\s+\S+\s+"tp_msg')

    def test_ktx_profile_preserves_the_nquake_competitive_keymap(self):
        snapshot_root = ROOT / "dist/distributions/nquake"
        revisions = [path for path in snapshot_root.iterdir() if path.is_dir()]
        self.assertEqual(1, len(revisions))
        nquake = (revisions[0] / "non-gpl/qw/nquake_default.cfg").read_text(encoding="latin-1")
        profile = (ROOT / "dist/mods/ktx/1.47/x86qw/client.cfg").read_text(encoding="utf-8")

        def bindings(payload: str) -> dict[str, str]:
            return dict(re.findall(r'(?m)^bind\s+(\S+)\s+"([^"]*)"', payload))

        nquake_bindings = bindings(nquake)
        profile_bindings = bindings(profile)
        restored = {
            "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
            "c", "e", "f", "g", "h", "i", "m", "q", "r", "t", "v", "x", "z",
            "ALT", "CTRL", "SHIFT",
            "MOUSE1", "MOUSE2", "MOUSE3", "MOUSE4", "MOUSE5",
            "MWHEELUP", "MWHEELDOWN",
            "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9",
        }
        self.assertEqual(
            {key: nquake_bindings[key] for key in restored},
            {key: profile_bindings[key] for key in restored},
        )
        self.assertEqual("", nquake_bindings["F10"])
        self.assertEqual("ktx_controls", profile_bindings["F10"])
        user_example = (
            ROOT / "dist/mods/ktx/1.47/x86qw/user.cfg.example"
        ).read_text(encoding="utf-8")
        self.assertIn('// bind MOUSE2 "weapon 7"', user_example)
        self.assertNotIn("x86qw_ktx_rl", user_example)

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
            installer.install_component_overlay("ktx", managed, "a" * 40, "https://example.invalid")
            (target / "qw/ktx.pk3").unlink()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                installer.uninstall()
            self.assertFalse((target / ".install").exists())
            self.assertNotIn("Os componentes x86QW não estão instalados", output.getvalue())


if __name__ == "__main__":
    unittest.main()
