import ast
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

from maintenance.tools import component_sources


# Um teste que por engano iniciar o runtime real nunca deve capturar a tela.
# Casos que verificam o comando normal removem a variável em escopo controlado.
os.environ.setdefault("X86QW_TEST_WINDOWED", "1")


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("install_qw_modern", ROOT / "dist/installer/bin/manager.py")
install_qw = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = install_qw
SPEC.loader.exec_module(install_qw)
install_qw.configure_development_source_provider(install_qw.ComponentSourceProvider(
    load_context=component_sources.load_source_context,
    resolve_payloads=component_sources.resolve_component_payloads,
))
sys.modules["cli"] = install_qw

PLAY_SPEC = importlib.util.spec_from_file_location("play_qw_modern", ROOT / "dist/installer/bin/gameplay.py")
play_qw = importlib.util.module_from_spec(PLAY_SPEC)
assert PLAY_SPEC.loader is not None
sys.modules[PLAY_SPEC.name] = play_qw
PLAY_SPEC.loader.exec_module(play_qw)
sys.modules["gameplay"] = play_qw
play_qw.configure_context(install_qw.gameplay_composition_context(play_qw))

SERVICES_SPEC = importlib.util.spec_from_file_location(
    "services_qw_modern", ROOT / "dist/installer/bin/services.py",
)
services_qw = importlib.util.module_from_spec(SERVICES_SPEC)
assert SERVICES_SPEC.loader is not None
sys.modules[SERVICES_SPEC.name] = services_qw
SERVICES_SPEC.loader.exec_module(services_qw)
sys.modules["services"] = services_qw
services_qw.configure_context(
    install_qw.service_composition_context(services_qw, play_qw),
)


def local_server_baseline(game: str) -> list[str]:
    arguments = [
        "+sb_listcache", "0", "+spectator", "0",
        "+bind", "F12", "quit",
    ]
    settings = (
        play_qw.KTX_LOCAL_SERVER_SETTINGS
        if game == "ktx" else play_qw.NQUAKE_LOCAL_SERVER_SETTINGS
    )
    for name, value in settings:
        arguments.extend([f"+{name}", value])
    return arguments


def ktx_launch_setup_alias(*commands: str, mode_key: str = "duel") -> list[str]:
    mode = next(mode for mode in play_qw.load_ktx_modes(ROOT) if mode.key == mode_key)
    body = ";".join((
        "unalias x86qw_ktx_launch_setup",
        f"tempalias ktx_mode echo x86QW KTX preset: {mode.label} [{mode.key}]",
        *play_qw.ktx_key_alias_commands(mode, play_qw.KtxLaunchOptions()),
        *commands,
        "x86qw_ktx_mode_help",
    ))
    return [
        "+tempalias", "x86qw_ktx_launch_setup",
        play_qw.quote_console_command(body),
    ]


def ktx_entry_aliases(usermode: str = "1on1") -> list[str]:
    body = "exec x86qw-ktx.cfg;x86qw_ktx_launch_setup"
    event = {
        "ffa": "on_enter_ffa",
        "tot": "on_enter_ffa",
        "ctf": "on_enter_ctf",
    }.get(usermode, "on_enter")
    return [
        "+tempalias", event, play_qw.quote_console_command(body),
    ]


class ModernComponentTests(unittest.TestCase):
    def test_installed_runtime_modules_parse_with_python_310_grammar(self):
        for filename in ("gameplay.py", "services.py", "manager.py", "menu.py"):
            with self.subTest(filename=filename):
                source = (ROOT / "dist/installer/bin" / filename).read_text(encoding="utf-8")
                ast.parse(source, filename=filename, feature_version=(3, 10))

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
        for action in (
            "host", "proxy", "qtv", "status", "version", "components", "presets", "hub",
            "update", "upgrade", "repair",
        ):
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
        self.assertIn("play, host, proxy, qtv, status, version,", output.getvalue())
        self.assertIn("update, upgrade, repair", output.getvalue())
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
        named = play_qw.parse_arguments([
            "ktx", "--mode", "2on2", "--bots", "2", "--bot-names", "x86qw",
            "--target", str(target),
        ], ROOT)
        self.assertEqual("x86qw", named.ktx_options.bot_names_profile)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            play_qw.parse_arguments([
                "ktx", "--mode", "duel", "--bot-names", "personal",
                "--target", str(target),
            ], ROOT)
        main = install_qw.parse_arguments(["play", str(target)], ROOT)
        self.assertEqual("play", main.action)
        with mock.patch.object(play_qw, "main", return_value=0) as delegated:
            self.assertEqual(0, install_qw.main(["play", str(target), "--no-color"]))
        delegated.assert_called_once_with([str(target), "--no-color"])

    def test_service_cli_exposes_host_proxy_qtv_and_status(self):
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
        background_host = services_qw.parse_arguments([
            "host", "td2", "--map", "dm6", "--background",
        ], ROOT)
        self.assertTrue(background_host.background)
        td2 = services_qw.parse_arguments(["host", "td2", "--map", "dm6"], ROOT)
        self.assertEqual("td2", td2.game)
        self.assertIsNone(td2.mode)
        proxy = services_qw.parse_arguments(["proxy", "--port", "30001"], ROOT)
        self.assertEqual(30001, proxy.proxy_port)
        qtv = services_qw.parse_arguments(["qtv", "--upstream", "127.0.0.1:28501"], ROOT)
        self.assertEqual("127.0.0.1:28501", qtv.upstream)
        status = services_qw.parse_arguments(["status", "--target", str(target)], ROOT)
        self.assertEqual("status", status.action)
        self.assertEqual(target, status.target)
        stop = services_qw.parse_arguments(["status", "--stop", "--yes"], ROOT)
        self.assertTrue(stop.stop)
        self.assertTrue(stop.yes)
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
            modes["tot"], "dm4", frozenset({"bots/maps/dm4.bot"}), bot_options, 16,
        ))
        self.assertEqual("1", settings["k_fb_enabled"])
        self.assertEqual("12", settings["k_fb_skill"])
        self.assertEqual("3", settings["k_fb_autoadd_limit"])
        self.assertEqual("0", settings["k_fb_weapon"])
        self.assertEqual("200", settings["k_fb_health"])
        named_settings = dict(services_qw.dedicated_ktx_settings(
            modes["2on2"], "dm6", frozenset({"bots/maps/dm6.bot"}),
            play_qw.KtxLaunchOptions(
                bots=2, bot_name_pool=("Luffy", "Zoro", "Nami", "Usopp", "Sanji", "Robin"),
            ), 16,
        ))
        self.assertEqual(b"/\xa0\xcc\xf5\xe6\xe6\xf9", named_settings["k_fb_name_0"])
        self.assertEqual(b"/\xa0\xcc\xf5\xe6\xe6\xf9", named_settings["k_fb_name_team_0"])
        self.assertEqual(b"/\xa0\xcc\xf5\xe6\xe6\xf9", named_settings["k_fb_name_enemy_0"])
        ctf = dict(services_qw.dedicated_ktx_settings(
            modes["ctf"], "e2m2", frozenset({"id1/maps/ctf/e2m2.ent"}), play_qw.KtxLaunchOptions(
                ctf_hook="smooth", ctf_runes="off", ctf_based_spawn=True,
            ), 16,
        ))
        self.assertEqual("1", ctf["k_ctf_hook"])
        self.assertEqual("1", ctf["k_ctf_hookstyle"])
        self.assertEqual("0", ctf["k_ctf_runes"])
        self.assertEqual("1", ctf["k_ctf_based_spawn"])

    def test_dedicated_fixed_roster_is_not_truncated_by_maxclients(self):
        mode = next(
            mode for mode in play_qw.load_ktx_modes(ROOT)
            if mode.key == "3on3"
        )
        options = play_qw.KtxLaunchOptions(fill_bots=True, bot_skill=6)
        settings = dict(services_qw.dedicated_ktx_settings(
            mode, "dm3", frozenset({"bots/maps/dm3.bot"}), options, 6,
        ))
        self.assertEqual("6", settings["k_fb_autoadd_limit"])
        with self.assertRaisesRegex(
            play_qw.InstallerError, "exige --maxclients de pelo menos 6",
        ):
            services_qw.dedicated_ktx_settings(
                mode, "dm3", frozenset({"bots/maps/dm3.bot"}), options, 5,
            )

    def test_external_ktx_bot_host_restricts_roster_management(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
            mode = next(mode for mode in play_qw.load_ktx_modes(ROOT) if mode.key == "duel")
            (target / game.gamedir).mkdir()
            selection = services_qw.HostedGame(
                game, mode, "dm6", frozenset({"bots/maps/dm6.bot"}),
                play_qw.KtxLaunchOptions(bots=1),
            )
            for bind, expected in (("0.0.0.0", True), ("127.0.0.1", False)):
                with self.subTest(bind=bind):
                    options = services_qw.parse_arguments([
                        "host", "ktx", "--mode", "duel", "--map", "dm6",
                        "--bots", "1", "--bind", bind,
                        "--target", str(target),
                    ], ROOT)
                    sessions: list[Path] = []
                    with mock.patch.object(
                        services_qw, "runtime_binary", return_value=target / "mvdsv",
                    ), mock.patch.object(
                        services_qw, "materialize_hosted_game", return_value=None,
                    ):
                        services_qw.host_spec(
                            player, options, selection, sessions, [],
                        )
                    startup = sessions[0].read_text(encoding="utf-8")
                    post_map = sessions[1].read_text(encoding="utf-8")
                    self.assertEqual(
                        expected, "k_fb_admin_only 1" in startup,
                    )
                    self.assertEqual(
                        expected, "set k_fb_admin_only 1" in post_map,
                    )
                    for path in sessions:
                        path.unlink()

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
                game, mode, "e2m2", frozenset({"id1/maps/ctf/e2m2.ent"}), options.ktx_options,
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
            parameters = dict(spec.parameters)
            self.assertEqual("KTX", parameters["game"])
            self.assertEqual("Capture The Flag", parameters["mode"])
            self.assertEqual("e2m2", parameters["map"])
            self.assertEqual("nenhum", parameters["secrets"])
            self.assertNotIn("password", parameters)

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
            self.assertEqual(
                "// x86QW: configuração efêmera removida ao encerrar.\nhostname local\n",
                session.read_text(encoding="utf-8"),
            )
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
        for action in ("host", "proxy", "qtv", "status"):
            with self.subTest(action=action):
                with mock.patch.object(services_qw, "main", return_value=0) as delegated:
                    self.assertEqual(0, install_qw.main([action, "--target", "/tmp/x86qw-test"]))
                delegated.assert_called_once_with([action, "--target", "/tmp/x86qw-test"])

    def test_main_menu_routes_service_status_and_pauses_before_redrawing(self):
        target = ROOT / "custom-quake"
        with mock.patch.object(
            install_qw.navigation, "select_one",
            side_effect=("services", "status", None, "exit"),
        ), mock.patch.object(
            services_qw, "main", return_value=0,
        ) as status, mock.patch(
            "builtins.input", return_value="",
        ) as pause:
            self.assertEqual(0, install_qw.run_main_menu(target))
        status.assert_called_once_with(
            ["status", "--target", str(target), "--menu"],
            propagate_menu_exit=True,
        )
        pause.assert_called_once_with("\nPressione Enter para retornar ao menu de serviços...")

    def test_main_menu_preserves_every_completed_action_result_before_redrawing(self):
        target = ROOT / "custom-quake"
        direct_cases = (
            ("play", ("play", "exit"), play_qw, ["--target", str(target), "--menu"]),
            ("host", ("host", "exit"), services_qw, ["host", "--target", str(target), "--menu"]),
        )
        for label, selections, module, expected in direct_cases:
            with self.subTest(route=label), mock.patch.object(
                install_qw.navigation, "select_one", side_effect=selections,
            ), mock.patch.object(module, "main", return_value=0) as execute, mock.patch(
                "builtins.input", return_value="",
            ) as pause:
                self.assertEqual(0, install_qw.run_main_menu(target))
            execute.assert_called_once_with(expected, propagate_menu_exit=True)
            pause.assert_called_once_with("\nPressione Enter para retornar ao menu principal...")

        with mock.patch.object(
            install_qw.navigation, "select_one", side_effect=("hub", "exit"),
        ), mock.patch.object(
            install_qw, "main", return_value=0,
        ) as execute, mock.patch("builtins.input", return_value="") as pause:
            self.assertEqual(0, install_qw.run_main_menu(target))
        execute.assert_called_once_with([
            "--online-only", "--installed-cli", "hub", str(target),
        ])
        pause.assert_called_once_with("\nPressione Enter para retornar ao menu principal...")

        for service in ("status", "stop", "qtv", "proxy"):
            with self.subTest(service=service), mock.patch.object(
                install_qw.navigation, "select_one",
                side_effect=("services", service, None, "exit"),
            ), mock.patch.object(
                services_qw, "main", return_value=0,
            ) as execute, mock.patch("builtins.input", return_value="") as pause:
                self.assertEqual(0, install_qw.run_main_menu(target))
            execute.assert_called_once_with(
                [
                    "status" if service == "stop" else service,
                    "--target", str(target), "--menu",
                    *(("--stop",) if service == "stop" else ()),
                ],
                propagate_menu_exit=True,
            )
            pause.assert_called_once_with("\nPressione Enter para retornar ao menu de serviços...")

        for action in ("update", "upgrade", "verify", "repair", "cleanup"):
            selections = (
                ("manage", "cleanup", "cache", "exit")
                if action == "cleanup" else ("manage", action, "exit")
            )
            with self.subTest(action=action), mock.patch.object(
                install_qw.navigation, "select_one", side_effect=selections,
            ), mock.patch.object(
                install_qw, "main", return_value=0,
            ) as execute, mock.patch("builtins.input", return_value="") as pause:
                self.assertEqual(0, install_qw.run_main_menu(target))
            execute.assert_called_once_with([
                "--online-only", "--installed-cli", action, str(target),
            ])
            pause.assert_called_once_with("\nPressione Enter para retornar ao menu principal...")

    def test_primary_menus_are_searchable_and_content_action_describes_bootstrap(self):
        target = ROOT / "custom-quake"
        calls = []
        selections = iter(("manage", None, "services", None, "exit"))

        def choose(title, options, **kwargs):
            entries = tuple(options)
            calls.append((title, entries, kwargs))
            return next(selections)

        with mock.patch.object(install_qw.navigation, "select_one", side_effect=choose):
            self.assertEqual(0, install_qw.run_main_menu(target))
        self.assertEqual(
            ["QuakeWorld moderno", "Gerenciar instalação", "QuakeWorld moderno", "Serviços x86QW", "QuakeWorld moderno"],
            [title for title, _entries, _kwargs in calls],
        )
        self.assertTrue(all(kwargs.get("searchable") for _title, _entries, kwargs in calls))
        management = next(entries for title, entries, _kwargs in calls if title == "Gerenciar instalação")
        content = next(option for option in management if option.key == "content")
        self.assertEqual("Alterar conteúdo pelo bootstrap", content.label)
        self.assertIn("adicionar ou remover", content.description)

    def test_information_content_and_exit_render_the_promised_result(self):
        target = ROOT / "custom-quake"
        bootstrap = "install.ps1" if os.name == "nt" else "install.sh"
        cases = (
            (
                ("info", "exit"),
                ("x86QW ", f"Instalação: {target}", "Comandos: play, host, hub", "No menu:"),
                "\nPressione Enter para voltar ao menu...",
            ),
            (
                ("manage", "content", "exit"),
                ("Reexecute o bootstrap", bootstrap, f"Destino atual: {target}"),
                "\nPressione Enter para voltar ao menu...",
            ),
            (
                ("exit",),
                ("Até a próxima partida.",),
                None,
            ),
        )
        for selections, expected, prompt in cases:
            with self.subTest(route=selections), mock.patch.object(
                install_qw.navigation, "select_one", side_effect=selections,
            ), mock.patch("builtins.input", return_value="") as pause:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(0, install_qw.run_main_menu(target))
            for value in expected:
                self.assertIn(value, output.getvalue())
            if prompt is None:
                pause.assert_not_called()
            else:
                pause.assert_called_once_with(prompt)

    def test_main_menu_routes_play_without_changing_the_public_flags(self):
        target = ROOT / "custom-quake"
        with mock.patch.object(
            install_qw.navigation, "select_one", side_effect=("play", "exit"),
        ), mock.patch.object(play_qw, "main", return_value=0) as play_main:
            self.assertEqual(0, install_qw.run_main_menu(target, no_color=True))
        play_main.assert_called_once_with([
            "--target", str(target), "--menu", "--no-color",
        ], propagate_menu_exit=True)

    def test_escape_from_child_navigators_exits_the_root_menu(self):
        target = ROOT / "custom-quake"
        cases = (
            ("play", play_qw, ["--target", str(target), "--menu"]),
            ("host", services_qw, ["host", "--target", str(target), "--menu"]),
        )
        for selected, module, expected in cases:
            with self.subTest(selected=selected), mock.patch.object(
                install_qw.navigation, "select_one", return_value=selected,
            ) as menu, mock.patch.object(module, "main", return_value=130) as child:
                self.assertEqual(130, install_qw.run_main_menu(target))
            menu.assert_called_once()
            child.assert_called_once_with(expected, propagate_menu_exit=True)

        with mock.patch.object(
            install_qw.navigation, "select_one", side_effect=("services", "qtv"),
        ) as menu, mock.patch.object(services_qw, "main", return_value=130) as child:
            self.assertEqual(130, install_qw.run_main_menu(target))
        self.assertEqual(2, menu.call_count)
        child.assert_called_once_with(
            ["qtv", "--target", str(target), "--menu"],
            propagate_menu_exit=True,
        )

    def test_escape_from_child_exits_root_cleanly(self):
        target = ROOT / "custom-quake"
        with mock.patch.object(
            install_qw.navigation, "select_one", return_value="play",
        ), mock.patch.object(
            play_qw, "main", side_effect=play_qw.navigation.MenuExit("Jogar"),
        ):
            self.assertEqual(0, install_qw.main(["menu", str(target)]))

        with mock.patch.object(
            install_qw.navigation, "select_one", return_value="hub",
        ) as menu, mock.patch.object(install_qw, "main", return_value=130) as child:
            self.assertEqual(130, install_qw.run_main_menu(target))
        menu.assert_called_once()
        child.assert_called_once_with([
            "--online-only", "--installed-cli", "hub", str(target),
        ])

    def test_child_error_is_paused_before_the_root_menu_is_redrawn(self):
        target = ROOT / "custom-quake"
        with mock.patch.object(
            install_qw.navigation, "select_one", side_effect=("play", "exit"),
        ), mock.patch.object(
            play_qw, "main", return_value=1,
        ), mock.patch("builtins.input", return_value="") as pause:
            self.assertEqual(0, install_qw.run_main_menu(target))
        pause.assert_called_once_with("\nPressione Enter para retornar ao menu principal...")

    def test_personal_cleanup_requires_confirmation_before_running(self):
        target = ROOT / "custom-quake"
        with mock.patch.object(
            install_qw.navigation, "select_one",
            side_effect=("manage", "cleanup", "personal", "exit"),
        ), mock.patch.object(
            install_qw.navigation, "confirm", return_value=False,
        ) as confirm, mock.patch.object(install_qw, "main") as execute:
            self.assertEqual(0, install_qw.run_main_menu(target))
        confirm.assert_called_once()
        execute.assert_not_called()

    def test_main_menu_routes_every_cleanup_and_uninstall_scope_to_exact_flags(self):
        target = ROOT / "custom-quake"
        cleanup_cases = {
            "cache": [],
            "downloads": ["--downloads"],
            "personal": ["--personal-data"],
            "all": ["--downloads", "--personal-data"],
        }
        for scope, flags in cleanup_cases.items():
            with self.subTest(cleanup=scope), mock.patch.object(
                install_qw.navigation, "select_one",
                side_effect=("manage", "cleanup", scope, "exit"),
            ), mock.patch.object(
                install_qw.navigation, "confirm", return_value=True,
            ), mock.patch.object(
                install_qw, "main", return_value=0,
            ) as execute, mock.patch("builtins.input", return_value=""):
                self.assertEqual(0, install_qw.run_main_menu(target))
            self.assertEqual([
                "--online-only", "--installed-cli", "cleanup", str(target), *flags,
            ], execute.call_args.args[0])

        for scope, flags in (("preserve", []), ("purge", ["--purge"])):
            with self.subTest(uninstall=scope), mock.patch.object(
                install_qw.navigation, "select_one",
                side_effect=("manage", "uninstall", scope),
            ), mock.patch.object(
                install_qw.navigation, "confirm", return_value=True,
            ), mock.patch.object(
                install_qw, "main", return_value=0,
            ) as execute:
                self.assertEqual(0, install_qw.run_main_menu(target))
            self.assertEqual([
                "--online-only", "--installed-cli", "uninstall", str(target), *flags,
            ], execute.call_args.args[0])

    def test_play_menu_cancel_is_reported_without_an_unexpected_failure(self):
        player = mock.Mock()
        player.play_local.side_effect = play_qw.navigation.MenuCancelled("Jogar")
        output = io.StringIO()
        with mock.patch.object(play_qw, "Player", return_value=player):
            with contextlib.redirect_stdout(output):
                self.assertEqual(130, play_qw.main(["--target", "/tmp/x86qw-test"]))
        self.assertIn("Operação cancelada", output.getvalue())

    def test_left_from_ktx_mode_returns_to_the_game_menu_not_the_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, _, _ = self.make_player(Path(temporary))
            (player.target / "qw").mkdir()
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(player, "check_paks"))
                stack.enter_context(mock.patch.object(
                    player, "available_local_games", return_value=[game],
                ))
                choose_game = stack.enter_context(mock.patch.object(
                    player, "choose_local_game", side_effect=(game, None),
                ))
                stack.enter_context(mock.patch.object(
                    player, "installed_component_for_game", return_value="ktx",
                ))
                stack.enter_context(mock.patch.object(player, "verify_component"))
                stack.enter_context(mock.patch.object(
                    player, "choose_ktx_mode", return_value=None,
                ))
                player.play_local(configure_interactively=True)
            self.assertEqual(2, choose_game.call_count)

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
        with tempfile.TemporaryDirectory() as temporary:
            player, _, _ = self.make_player(Path(temporary))
            self.assertEqual("2on2on2", player.choose_ktx_mode(modes, "2on2on2").key)
            self.assertEqual("2on2on2", player.choose_ktx_mode(modes, "2v2v2").key)
            self.assertEqual("3on3", player.choose_ktx_mode(modes, "3v3").key)
        ctf = next(mode for mode in modes if mode.key == "ctf")
        self.assertEqual("ctf", ctf.usermode)
        self.assertFalse(ctf.bots)
        self.assertEqual((
            ("sv_loadentfiles", "1"), ("sv_loadentfiles_dir", "ctf"),
        ), ctf.launch_settings)
        race = next(mode for mode in modes if mode.key == "race")
        self.assertEqual(
            ("race/routes/{map}.route",),
            tuple(requirement.asset for requirement in race.map_requirements),
        )
        self.assertEqual(
            ("id1/maps/ctf/{map}.ent",),
            tuple(requirement.asset for requirement in ctf.map_requirements),
        )
        self.assertEqual(54, len(race.suggested_maps))
        self.assertFalse(race.bots)
        groups = play_qw.load_ktx_menu_groups(ROOT)
        self.assertEqual(
            ("recommended", "individual", "teams", "arena", "training"),
            tuple(group.key for group in groups),
        )
        self.assertEqual(
            {mode.key for mode in modes},
            {mode_id for group in groups for mode_id in group.modes},
        )

    def test_ezquake_command_line_aliases_preserve_their_complete_body(self):
        self.assertEqual(
            '"exec x86qw-ktx.cfg;cmd botcmd addbot 5"',
            play_qw.quote_console_command(
                "exec x86qw-ktx.cfg;cmd botcmd addbot 5"
            ),
        )
        for invalid in ('echo "broken"', "echo broken\nquit"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(play_qw.InstallerError, "caracteres inválidos"):
                    play_qw.quote_console_command(invalid)

    def test_every_ktx_mode_builds_a_complete_quoted_launch_command(self):
        modes = play_qw.load_ktx_modes(ROOT)
        maps = sorted({mode.default_map for mode in modes})
        assets = frozenset(
            requirement.asset.replace("{map}", mode.default_map.casefold())
            for mode in modes
            for requirement in play_qw.active_ktx_map_requirements(
                mode, play_qw.KtxLaunchOptions(),
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            player, _, _ = self.make_player(Path(temporary))
            (player.target / "qw").mkdir()
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
            runtime = player.target / "ezQuake Stable.app"
            with mock.patch.object(player, "check_paks"):
                with mock.patch.object(player, "available_local_games", return_value=[game]):
                    with mock.patch.object(player, "installed_component_for_game", return_value="ktx"):
                        with mock.patch.object(player, "verify_component"):
                            with mock.patch.object(player, "verify_local_play_support"):
                                with mock.patch.object(player, "ktx_archive_members", return_value=assets):
                                    with mock.patch.object(player, "local_map_names", return_value=maps):
                                        with mock.patch.object(player, "choose_host_runtime", return_value=("stable", runtime)):
                                            with mock.patch.object(player, "launch_runtime") as launch:
                                                for mode in modes:
                                                    with self.subTest(mode=mode.key):
                                                        player.play_local(
                                                            "ktx", mode.key, mode.default_map,
                                                        )
                                                        arguments = launch.call_args.args[1]
                                                        self.assertIn(
                                                            ["+map", mode.default_map],
                                                            [arguments[index:index + 2] for index in range(len(arguments) - 1)],
                                                        )
                                                        self.assertIn(
                                                            ["+set", "k_defmode", mode.usermode],
                                                            [arguments[index:index + 3] for index in range(len(arguments) - 2)],
                                                        )
                                                        for index, argument in enumerate(arguments):
                                                            if argument == "+tempalias":
                                                                body = arguments[index + 2]
                                                                if body.startswith('"'):
                                                                    self.assertTrue(body.startswith('"'), body)
                                                                    self.assertTrue(body.endswith('"'), body)
                                                                else:
                                                                    self.assertFalse(body.endswith('"'), body)
                                                        self.assertLessEqual(
                                                            max(map(len, arguments)),
                                                            play_qw.KTX_INLINE_SETUP_LIMIT,
                                                        )
                                                        launch.reset_mock()

    def test_every_bot_compatible_ktx_mode_schedules_the_selected_frogbot(self):
        modes = play_qw.load_ktx_modes(ROOT)
        for mode in modes:
            if not mode.bots:
                continue
            with self.subTest(mode=mode.key):
                route = f"bots/maps/{mode.default_map.casefold()}.bot"
                commands = play_qw.ktx_launch_commands(
                    mode,
                    mode.default_map,
                    frozenset({route}),
                    play_qw.KtxLaunchOptions(bots=1, bot_skill=7),
                )
                self.assertIn("cmd botcmd skill 7", commands)
                self.assertEqual("cmd botcmd addbot 7", commands[-1])

    def test_tot_leaves_frogbot_enable_registration_to_the_server(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, _ = self.make_player(Path(temporary))
            (target / "qw").mkdir()
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
            runtime = player.target / "ezQuake Stable.app"
            options = play_qw.KtxLaunchOptions(bots=1, bot_skill=5)
            captured: dict[str, object] = {}

            def capture_launch(_runtime, arguments):
                config_name = next(
                    arguments[index + 1]
                    for index, argument in enumerate(arguments[:-1])
                    if argument == "+exec"
                    and arguments[index + 1].startswith("x86qw-ktx-session-")
                )
                config_path = target / "qw" / config_name
                captured["config"] = config_path.read_text(encoding="ascii")

            with contextlib.ExitStack() as stack:
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                stack.enter_context(mock.patch.object(player, "check_paks"))
                stack.enter_context(mock.patch.object(
                    player, "available_local_games", return_value=[game],
                ))
                stack.enter_context(mock.patch.object(
                    player, "installed_component_for_game", return_value="ktx",
                ))
                stack.enter_context(mock.patch.object(player, "verify_component"))
                stack.enter_context(mock.patch.object(
                    player, "verify_local_play_support",
                ))
                stack.enter_context(mock.patch.object(
                    player, "ktx_archive_members",
                    return_value=frozenset({"bots/maps/dm6.bot"}),
                ))
                stack.enter_context(mock.patch.object(
                    player, "local_map_names", return_value=["dm6"],
                ))
                stack.enter_context(mock.patch.object(
                    player, "choose_host_runtime", return_value=("stable", runtime),
                ))
                launch = stack.enter_context(mock.patch.object(
                    player, "launch_runtime", side_effect=capture_launch,
                ))
                player.play_local("ktx", "tot", "dm6", options)
            arguments = launch.call_args.args[1]
            triples = [
                arguments[index:index + 3]
                for index in range(len(arguments) - 2)
            ]
            self.assertNotIn(["+set", "k_fb_enabled", "1"], triples)
            self.assertIn(
                ["+unset", "k_fb_enabled", "k_fb_break_on_death"], triples,
            )
            self.assertNotIn("+tempalias", arguments)
            config = str(captured["config"])
            self.assertIn(
                'tempalias on_enter_ffa "exec x86qw-ktx.cfg;'
                'x86qw_ktx_launch_setup"',
                config,
            )
            self.assertIn("cmd botcmd addbot 5", config)

    def test_ktx_mode_menu_aligns_players_and_accepts_aliases(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, _, _ = self.make_player(Path(temporary))
            modes = play_qw.load_ktx_modes(ROOT)
            output = io.StringIO()
            with mock.patch("builtins.input", side_effect=("all", "ca")):
                with contextlib.redirect_stdout(output):
                    selected = player.choose_ktx_mode(modes)
            self.assertEqual("clan-arena", selected.key)
            mode_output = output.getvalue().split("Qual modo KTX deseja jogar?", 1)[1]
            lines = [line for line in mode_output.splitlines() if re.match(r"^\s+\d+\)", line)]
            self.assertEqual(len(modes), len(lines))
            self.assertIn("Duel (padrão)", lines[0])
            divider_columns = [line.index("|") for line in lines]
            self.assertEqual(1, len(set(divider_columns)))
            for line, mode in zip(lines, modes):
                self.assertIn(mode.description, line)

    def test_ktx_cli_exposes_map_bots_ctf_and_race_options(self):
        target = ROOT / "custom-quake"
        parsed = play_qw.parse_arguments([
            "ktx", "--mode", "2on2", "--map", "dm6", "--bots", "2",
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
        randomized = play_qw.parse_arguments([
            "ktx", "--mode", "ffa", "--bots", "4", "--bot-skill", "random",
            "--target", str(target),
        ], ROOT)
        self.assertEqual("random", randomized.ktx_options.bot_skill)

    def test_ktx_cli_rejects_bot_modifiers_without_a_roster(self):
        target = ROOT / "custom-quake"
        for modifier in (
            ("--bot-skill", "8"),
            ("--bot-names", "x86qw"),
            ("--bot-health", "200"),
            ("--no-bot-break-on-death",),
        ):
            with self.subTest(modifier=modifier):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        play_qw.parse_arguments([
                            "ktx", "--mode", "duel", *modifier,
                            "--target", str(target),
                        ], ROOT)

    def test_mode_specific_bot_team_and_tot_controls_are_enforced(self):
        modes = {mode.key: mode for mode in play_qw.load_ktx_modes(ROOT)}
        route = frozenset({"bots/maps/dm6.bot", "bots/maps/dm4.bot"})
        with self.assertRaisesRegex(play_qw.InstallerError, "não pertence ao modo"):
            play_qw.ktx_launch_commands(
                modes["2on2"], "dm6", route,
                play_qw.KtxLaunchOptions(bots=1, bot_team="green"),
            )
        with self.assertRaisesRegex(play_qw.InstallerError, "só podem ser usados"):
            play_qw.ktx_launch_commands(
                modes["duel"], "dm6", route,
                play_qw.KtxLaunchOptions(bots=1, bot_health=200),
            )
        commands = play_qw.ktx_launch_commands(
            modes["tot"], "dm4", route,
            play_qw.KtxLaunchOptions(
                bots=1, bot_weapon="8", bot_health=200,
                bot_break_on_death=False,
            ),
        )
        self.assertIn("cmd botcmd weapon 8", commands)
        self.assertIn("cmd botcmd health 200", commands)

    def test_random_frogbot_skill_is_applied_independently_by_the_qvm(self):
        ffa = next(mode for mode in play_qw.load_ktx_modes(ROOT) if mode.key == "ffa")
        options = play_qw.KtxLaunchOptions(bots=4, bot_skill="random")
        commands = play_qw.ktx_launch_commands(
            ffa, "dm6", frozenset({"bots/maps/dm6.bot"}), options,
        )
        self.assertIn("cmd botcmd skill random", commands)
        self.assertEqual(4, commands.count("cmd botcmd addbot random"))
        patch = (
            ROOT / "dist/mods/ktx/1.47/x86qw/source/0002-frogbot-identities.patch"
        ).read_text(encoding="utf-8")
        self.assertIn('#define FB_CVAR_SKILL_RANDOM      "k_fb_skill_random"', patch)
        self.assertIn("skill_level = i_rnd(MIN_FROGBOT_SKILL, MAX_FROGBOT_SKILL);", patch)

    def test_frogbot_map_menu_only_offers_maps_with_routes(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, _, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
            seen: list[str] = []

            def select(_question, options, **_kwargs):
                choices = list(options)
                seen.extend(option.key for option in choices)
                return choices[0].key

            with mock.patch.object(
                player, "local_map_names", return_value=["dm2", "dm6"],
            ), mock.patch.object(play_qw.navigation, "select_one", side_effect=select):
                selected = player.choose_local_map(
                    game,
                    default_map="dm2",
                    suggested_maps=("dm2", "dm6"),
                    required_assets=("bots/maps/{map}.bot",),
                    available_assets=frozenset({"bots/maps/dm6.bot"}),
                )
            self.assertEqual("dm6", selected)
            self.assertEqual(["dm6"], seen)

    def test_map_without_frogbot_route_remains_available_without_bots(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, _, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
            with mock.patch.object(
                player, "local_map_names", return_value=["dm2", "dm6"],
            ):
                self.assertEqual(
                    "dm2",
                    player.choose_local_map(game, requested_map="dm2"),
                )

    def test_personal_ktx_routes_extend_assets_without_following_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            bots = target / "qw/bots/maps"
            race = target / "qw/race/routes"
            ctf = target / "id1/maps/ctf"
            bots.mkdir(parents=True)
            race.mkdir(parents=True)
            ctf.mkdir(parents=True)
            (bots / "personal.bot").write_text(
                "CreateMarker 0 0 0\nSetZone 1 1\n", encoding="ascii",
            )
            (race / "personal.route").write_text(
                "race_route_add_start\nrace_add_route_node 0 0 0 0 0\n"
                "race_route_add_end\n",
                encoding="ascii",
            )
            (ctf / "personal.ent").write_text(
                '{\n"classname" "item_flag_team1"\n}\n'
                '{\n"classname" "item_flag_team2"\n}\n',
                encoding="ascii",
            )
            (bots / "command.bot").write_text("quit\n", encoding="ascii")
            (bots / "separator.bot").write_text(
                "CreateMarker 0 0 0;quit\n", encoding="ascii",
            )
            outside = target / "outside.bot"
            outside.write_text("private\n", encoding="ascii")
            try:
                (bots / "linked.bot").symlink_to(outside)
            except OSError:
                pass
            assets = play_qw.ktx_external_assets(target)
            self.assertIn("bots/maps/personal.bot", assets)
            self.assertIn("race/routes/personal.route", assets)
            self.assertIn("id1/maps/ctf/personal.ent", assets)
            self.assertNotIn("bots/maps/linked.bot", assets)
            self.assertNotIn("bots/maps/command.bot", assets)
            self.assertNotIn("bots/maps/separator.bot", assets)

    def test_f12_quits_every_managed_game_after_personal_overrides(self):
        profiles = (
            ("ktx/1.47", "x86qw-ktx-user.cfg"),
            ("pro-x/1.1", "x86qw-prox-user.cfg"),
            ("team-fortress/2.9", "x86qw-fortress-user.cfg"),
            ("final-arena/1.20", "x86qw-arena-user.cfg"),
            ("td2/2.22", "x86qw-td2-user.cfg"),
        )
        for module, personal in profiles:
            with self.subTest(module=module):
                relative = (
                    "x86qw/config/client.cfg" if module == "ktx/1.47"
                    else "x86qw/client.cfg"
                )
                profile = (ROOT / "dist/mods" / module / relative).read_text(
                    encoding="utf-8"
                )
                self.assertIn('bind F12 "quit"', profile)
                self.assertGreater(profile.index('bind F12 "quit"'), profile.index(f"exec {personal}"))

    def test_race_menu_collects_rules_before_launching_the_selected_map(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, _, _ = self.make_player(Path(temporary))
            race = next(mode for mode in play_qw.load_ktx_modes(ROOT) if mode.key == "race")
            with mock.patch.object(
                play_qw.navigation, "select_one",
                side_effect=("match", "formula1", "3"),
            ), mock.patch.object(play_qw.navigation, "confirm", return_value=True):
                options = player.choose_ktx_launch_options(race)
            self.assertEqual("match", options.race_style)
            self.assertEqual("formula1", options.race_scoring)
            self.assertEqual(3, options.race_pacemaker)
            self.assertTrue(options.race_hide_players)

    def test_frogbot_menu_exposes_default_random_and_personal_name_profiles(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, _, _ = self.make_player(Path(temporary))
            duel = next(mode for mode in play_qw.load_ktx_modes(ROOT) if mode.key == "duel")
            with mock.patch.object(
                play_qw.navigation, "select_one", side_effect=("1", "8", "x86qw"),
            ) as select:
                options = player.choose_ktx_launch_options(duel)
            self.assertEqual(1, options.bots)
            self.assertEqual(8, options.bot_skill)
            self.assertEqual("x86qw", options.bot_names_profile)
            bot_options = tuple(select.call_args_list[0].args[1])
            self.assertEqual(
                ("none", "1"),
                tuple(option.key for option in bot_options),
            )
            name_options = tuple(select.call_args_list[2].args[1])
            self.assertEqual(
                ("default", "x86qw", "personal"),
                tuple(option.key for option in name_options),
            )
            self.assertEqual(
                ("KTX Default", "x86QW aleatório", "Lista pessoal"),
                tuple(option.label for option in name_options),
            )
            self.assertEqual(1, select.call_args_list[2].kwargs["default"])

    def test_selecting_no_bots_clears_previous_frogbot_state(self):
        duel = next(mode for mode in play_qw.load_ktx_modes(ROOT) if mode.key == "duel")
        previous = play_qw.KtxLaunchOptions(
            bots=1,
            bot_skill=8,
            bot_names_profile="x86qw",
            bot_name_pool=(play_qw.FrogbotIdentity("Luffy"),),
        )
        with mock.patch.object(
            play_qw.navigation, "select_one", return_value="none",
        ):
            selected = play_qw.Player.choose_ktx_launch_options(None, duel, previous)
        self.assertEqual(play_qw.KtxLaunchOptions(), selected)

    def test_changing_ktx_mode_discards_transient_options_from_the_previous_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
            modes = {mode.key: mode for mode in play_qw.load_ktx_modes(ROOT)}
            runtime = target / "ezQuake Stable.app"
            chosen_inputs = []

            def choose_options(_mode, options, **_kwargs):
                chosen_inputs.append(options)
                if len(chosen_inputs) == 1:
                    return play_qw.replace(
                        options, bots=1, bot_skill=8, bot_names_profile="x86qw",
                    )
                if len(chosen_inputs) == 2:
                    return None
                return options

            with mock.patch.object(player, "check_paks"), mock.patch.object(
                player, "available_local_games", return_value=[game],
            ), mock.patch.object(
                player, "choose_local_game", return_value=game,
            ), mock.patch.object(
                player, "installed_component_for_game", return_value=game.component,
            ), mock.patch.object(player, "verify_component"), mock.patch.object(
                player, "choose_ktx_mode", side_effect=(modes["duel"], modes["race"]),
            ), mock.patch.object(
                player, "choose_ktx_launch_options", side_effect=choose_options,
            ), mock.patch.object(
                player, "ktx_archive_members", return_value=frozenset(),
            ), mock.patch.object(
                player, "choose_local_map", side_effect=(None, "dm6"),
            ), mock.patch.object(
                player, "choose_host_runtime",
                return_value=("ezQuake stable 1.0", runtime),
            ), mock.patch.object(
                player, "launch_runtime",
            ) as launch, mock.patch.object(
                play_qw, "resolve_frogbot_name_profile",
                side_effect=lambda _root, _target, _game, options, _mode: options,
            ), mock.patch.object(
                play_qw.navigation, "confirm", return_value=False,
            ), contextlib.redirect_stdout(io.StringIO()):
                player.play_local(configure_interactively=True)

            self.assertEqual(3, len(chosen_inputs))
            self.assertEqual(1, chosen_inputs[1].bots)
            self.assertEqual(play_qw.KtxLaunchOptions(), chosen_inputs[2])
            launch.assert_not_called()

    def test_frogbot_skill_menu_can_return_to_count_and_select_random(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, _, _ = self.make_player(Path(temporary))
            ffa = next(mode for mode in play_qw.load_ktx_modes(ROOT) if mode.key == "ffa")
            with mock.patch.object(
                play_qw.navigation, "select_one",
                side_effect=("2", None, "2", "random", "x86qw"),
            ) as select:
                options = player.choose_ktx_launch_options(ffa)
            self.assertEqual(2, options.bots)
            self.assertEqual("random", options.bot_skill)
            self.assertEqual("x86qw", options.bot_names_profile)
            self.assertEqual("Adicionar Frogbots?", select.call_args_list[2].args[0])

    def test_tot_menu_preserves_map_defaults_until_explicitly_overridden(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, _, _ = self.make_player(Path(temporary))
            tot = next(mode for mode in play_qw.load_ktx_modes(ROOT) if mode.key == "tot")
            with mock.patch.object(
                play_qw.navigation, "select_one",
                side_effect=("1", "5", "x86qw", "default", "default", "default"),
            ) as select:
                options = player.choose_ktx_launch_options(tot)
            self.assertEqual(1, options.bots)
            self.assertIsNone(options.bot_weapon)
            self.assertIsNone(options.bot_health)
            self.assertIsNone(options.bot_break_on_death)
            health_call = next(
                call for call in select.call_args_list
                if call.args[0] == "Vida inicial dos Frogbots no ToT"
            )
            self.assertEqual(0, health_call.kwargs["default"])

    def test_tot_summary_does_not_duplicate_weapon_or_health(self):
        lines = play_qw.ktx_summary_lines(play_qw.KtxLaunchOptions(
            bots=1, bot_weapon="8", bot_health=200, bot_break_on_death=False,
        ))
        self.assertEqual(1, sum(line.startswith("  Arma") for line in lines))
        self.assertEqual(1, sum(line.startswith("  Vida") for line in lines))
        self.assertEqual(1, sum(line.startswith("  Morte") for line in lines))

    def test_frogbot_menu_respects_fixed_modes_and_keeps_open_modes_flexible(self):
        modes = {mode.key: mode for mode in play_qw.load_ktx_modes(ROOT)}
        expectations = {
            "duel": ("none", "1"),
            "2on2": ("none", "3"),
            "3on3": ("none", "5"),
            "2on2on2": ("none", "5"),
            "4on4": ("none", "7"),
            "ffa": ("none", "1", "2", "4", "fill", "custom"),
            "practice": ("none", "1", "2", "4", "fill", "custom"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            player, _, _ = self.make_player(Path(temporary))
            for mode_key, expected in expectations.items():
                with self.subTest(mode=mode_key), mock.patch.object(
                    play_qw.navigation, "select_one", return_value="none",
                ) as select:
                    player.choose_ktx_launch_options(modes[mode_key])
                options = tuple(select.call_args.args[1])
                self.assertEqual(expected, tuple(option.key for option in options))

    def test_fixed_team_modes_balance_bots_across_the_declared_ktx_teams(self):
        modes = {mode.key: mode for mode in play_qw.load_ktx_modes(ROOT)}
        cases = {
            "2on2": (
                ("red", "blue"),
                ("blue", "red", "blue"),
                "2 equipes de 2 jogadores",
            ),
            "3on3": (
                ("red", "blue"),
                ("blue", "red", "blue", "red", "blue"),
                "2 equipes de 3 jogadores",
            ),
            "2on2on2": (
                ("red", "blue", "yellow"),
                ("blue", "yellow", "red", "blue", "yellow"),
                "3 equipes de 2 jogadores",
            ),
            "3on3on3": (
                ("red", "blue", "yellow"),
                ("blue", "yellow", "red", "blue", "yellow", "red", "blue", "yellow"),
                "3 equipes de 3 jogadores",
            ),
        }
        for key, (teams, sequence, description) in cases.items():
            mode = modes[key]
            options = play_qw.KtxLaunchOptions(
                bots=play_qw.ktx_mode_bot_limit(mode) or 0,
            )
            with self.subTest(mode=key):
                self.assertEqual(teams, mode.bot_teams)
                self.assertEqual(sequence, play_qw.ktx_bot_team_sequence(mode, options))
                self.assertEqual(description, play_qw.ktx_mode_roster_description(mode))

        for mode in modes.values():
            limit = play_qw.ktx_mode_bot_limit(mode)
            if not mode.bot_teams:
                continue
            if limit is None:
                for bot_count in (1, 2, 4, 8):
                    with self.subTest(open_roster=mode.key, bots=bot_count):
                        options = play_qw.KtxLaunchOptions(bots=bot_count)
                        sequence = play_qw.ktx_bot_team_sequence(mode, options)
                        populations = {team: 0 for team in mode.bot_teams}
                        populations[mode.bot_teams[0]] = 1
                        for team in sequence:
                            populations[str(team)] += 1
                        self.assertLessEqual(
                            max(populations.values()) - min(populations.values()), 1,
                        )
                continue
            with self.subTest(complete_roster=mode.key):
                options = play_qw.KtxLaunchOptions(bots=limit)
                sequence = play_qw.ktx_bot_team_sequence(mode, options)
                populations = {team: 0 for team in mode.bot_teams}
                populations[mode.bot_teams[0]] = 1
                for team in sequence:
                    populations[str(team)] += 1
                expected = int(mode.recommended_players) // len(mode.bot_teams)
                self.assertEqual(
                    {team: expected for team in mode.bot_teams}, populations,
                )
                commands = play_qw.ktx_launch_commands(
                    mode, mode.default_map,
                    frozenset({f"bots/maps/{mode.default_map.casefold()}.bot"}),
                    options,
                )
                self.assertEqual(
                    ("cmd botcmd addbot 5",) * len(sequence),
                    tuple(
                        command for command in commands
                        if command.startswith("cmd botcmd addbot")
                    ),
                )
                self.assertEqual(
                    max(0, len(sequence) - 1) * play_qw.FROGBOT_ADD_WAIT_FRAMES,
                    commands.count("wait"),
                )
                if mode.key == "4on4":
                    self.assertEqual(7, len(sequence))
                    self.assertIn(
                        "if ($maxclients < 8) then maxclients 8", commands,
                    )

    def test_fill_bots_respects_the_fixed_mode_roster(self):
        modes = {mode.key: mode for mode in play_qw.load_ktx_modes(ROOT)}
        assets = frozenset({"bots/maps/dm3.bot"})
        commands = play_qw.ktx_launch_commands(
            modes["3on3"], "dm3", assets,
            play_qw.KtxLaunchOptions(fill_bots=True, bot_skill=5),
        )
        addbots = tuple(command for command in commands if command.startswith("cmd botcmd addbot"))
        self.assertEqual(("cmd botcmd addbot 5",) * 5, addbots)
        self.assertNotIn("cmd botcmd fill 5", commands)
        self.assertIn("if ($maxclients < 6) then maxclients 6", commands)

    def test_ktx_launch_commands_validate_routes_and_mode_specific_options(self):
        modes = {mode.key: mode for mode in play_qw.load_ktx_modes(ROOT)}
        assets = frozenset({"bots/maps/dm6.bot", "race/routes/dm6.route"})
        options = play_qw.KtxLaunchOptions(
            bots=2, bot_skill=12, bot_team="red",
        )
        self.assertEqual((
            "if ($k_maxclients < 3) then k_maxclients 3",
            "if ($maxclients < 3) then maxclients 3",
            "cmd botcmd skill 12",
            "cmd botcmd addbot 12 red",
            *("wait",) * play_qw.FROGBOT_ADD_WAIT_FRAMES,
            "cmd botcmd addbot 12 red",
        ), play_qw.ktx_launch_commands(modes["2on2"], "dm6", assets, options))
        tot_options = play_qw.KtxLaunchOptions(
            bots=1, bot_skill=12, bot_weapon="8", bot_health=200,
            bot_break_on_death=False,
        )
        tot_commands = play_qw.ktx_launch_commands(
            modes["tot"], "dm4", frozenset({"bots/maps/dm4.bot"}), tot_options,
        )
        self.assertIn("cmd botcmd health 200", tot_commands)
        self.assertIn("cmd botcmd weapon 8", tot_commands)
        with self.assertRaisesRegex(play_qw.InstallerError, "só podem ser usados"):
            play_qw.ktx_launch_commands(
                modes["2on2"], "dm6", assets,
                play_qw.KtxLaunchOptions(bots=1, bot_health=200),
            )
        with self.assertRaisesRegex(play_qw.InstallerError, "no máximo 1 Frogbot"):
            play_qw.ktx_launch_commands(
                modes["duel"], "dm6", assets,
                play_qw.KtxLaunchOptions(bots=2),
            )
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
            modes["ctf"], "e2m2", frozenset({"id1/maps/ctf/e2m2.ent"}),
            play_qw.KtxLaunchOptions(
                ctf_hook="smooth", ctf_runes="off", ctf_based_spawn=True,
            ),
        ))
        with self.assertRaisesRegex(play_qw.InstallerError, "não possui o recurso rota Frogbot"):
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
            divider_columns = []
            for line, game in zip(lines, play_qw.LOCAL_GAMES):
                self.assertIn(f"v{game.version}", line)
                self.assertIn(game.description, line)
                divider_columns.append(line.index("|"))
            self.assertEqual(1, len(set(divider_columns)))
            self.assertIn("KTX (padrão)", lines[0])

    def test_play_menu_uses_receipt_version_with_canonical_fallback(self):
        cases = {
            "ktx": ("1.48+x86qw.1", "1.48"),
            "final-arena": ("1.20+nquake.e4cb23d40aa2+x86qw.3", "1.20"),
            "pro-x": ("1.1+x86qw.4", "1.1"),
            "team-fortress": ("2.9+nquake.e4cb23d40aa2+x86qw.5", "2.9"),
            "td2": ("2.22+x86qw.4", "2.22"),
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

    def test_component_removal_transaction_rolls_back_after_parent_state_failure(self):
        """A state failure must restore owned payload and metadata, not personal edits."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed = installer.stage / "managed"
            maps = managed / "qw/maps"
            maps.mkdir(parents=True)
            (maps / "owned.loc").write_text("owned", encoding="utf-8")
            (maps / "personal.loc").write_text("original", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.install_component_overlay(
                    "nquake-maps", managed, "test", "https://example.invalid/maps.zip",
                )
            personal = target / "qw/maps/personal.loc"
            personal.write_text("personal edit", encoding="utf-8")

            with self.assertRaises(install_qw.InstallerError):
                with installer.component_state_transaction() as results:
                    removed, result = installer.remove_component_transaction("nquake-maps")
                    results.append(result)
                    self.assertEqual(1, removed)
                    self.assertFalse((target / "qw/maps/owned.loc").exists())
                    self.assertEqual("personal edit", personal.read_text(encoding="utf-8"))
                    raise install_qw.PersistenceError(
                        "injected state failure", committed=False,
                    )

            self.assertEqual(
                "owned", (target / "qw/maps/owned.loc").read_text(encoding="utf-8"),
            )
            self.assertEqual("personal edit", personal.read_text(encoding="utf-8"))
            present, entries, receipt = installer.validate_component_pair("nquake-maps")
            self.assertTrue(present)
            self.assertEqual(
                ["qw/maps/owned.loc", "qw/maps/personal.loc"],
                [name for name, _digest in entries],
            )
            self.assertEqual("test", receipt["selection"])

    def test_component_menu_removal_rolls_back_when_state_commit_fails(self):
        """Interactive removal and state.json are one mutation boundary."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".seed-stage"
            installer.stage.mkdir()
            managed = installer.stage / "managed"
            owned = managed / "qw/maps/owned.loc"
            owned.parent.mkdir(parents=True)
            owned.write_text("owned", encoding="utf-8")
            installer.install_component_overlay(
                "nquake-maps", managed, "test", "fixture",
            )
            installer.cleanup_stage()
            installer.stage = None

            with (
                mock.patch.object(play_qw.navigation, "select_one", return_value="remove"),
                mock.patch.object(installer, "check_paks"),
                mock.patch.object(
                    installer, "choose_components_to_remove", return_value=["nquake-maps"],
                ),
                mock.patch.object(installer, "refresh_qw_package_order"),
                mock.patch.object(installer, "reconcile_play_support_transaction"),
                mock.patch.object(
                    installer, "write_install_state",
                    side_effect=install_qw.PersistenceError(
                        "falha de state.json", committed=False,
                    ),
                ),
            ):
                with self.assertRaises(install_qw.InstallerError):
                    installer.manage_components()

            self.assertEqual("owned", (target / "qw/maps/owned.loc").read_text())
            self.assertTrue(installer.validate_component_pair("nquake-maps")[0])

    def test_component_removal_tracks_a_node_before_post_move_identity_validation(self):
        """A post-rename identity failure must still restore the moved payload."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed = installer.stage / "managed"
            payload = managed / "qw/maps/owned.loc"
            payload.parent.mkdir(parents=True)
            payload.write_text("owned", encoding="utf-8")
            installer.install_component_overlay(
                "nquake-maps", managed, "test", "https://example.invalid/maps.zip",
            )
            original_identity = installer._component_removal_node_identity
            rejected = False

            def reject_moved_backup(path, kind):
                nonlocal rejected
                identity = original_identity(path, kind)
                if "remove-payload" in str(path) and not rejected:
                    rejected = True
                    return identity[0], identity[1] + 1
                return identity

            with mock.patch.object(
                installer,
                "_component_removal_node_identity",
                side_effect=reject_moved_backup,
            ):
                with self.assertRaises(install_qw.InstallerError):
                    installer.remove_component_transaction("nquake-maps")

            self.assertEqual(
                "owned", (target / "qw/maps/owned.loc").read_text(encoding="utf-8"),
            )
            self.assertTrue(installer.validate_component_pair("nquake-maps")[0])

    def test_component_removal_rejects_metadata_identity_swap_after_revalidation(self):
        """A same-content inode swap must not be removed as the observed metadata."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed = installer.stage / "managed"
            payload = managed / "qw/maps/owned.loc"
            payload.parent.mkdir(parents=True)
            payload.write_text("owned", encoding="utf-8")
            installer.install_component_overlay(
                "nquake-maps", managed, "test", "https://example.invalid/maps.zip",
            )
            canonical = target / install_qw.COMPONENT_METADATA_DIR / "nquake-maps"
            displaced = installer.stage / "displaced-metadata"
            real_observation = installer._component_metadata_observation
            observations = 0

            def swap_after_revalidation(component):
                nonlocal observations
                result = real_observation(component)
                observations += 1
                if observations == 2:
                    canonical.replace(displaced)
                    canonical.mkdir()
                    for source in displaced.iterdir():
                        (canonical / source.name).write_bytes(source.read_bytes())
                return result

            with mock.patch.object(
                installer,
                "_component_metadata_observation",
                side_effect=swap_after_revalidation,
            ):
                with self.assertRaises(install_qw.InstallerError):
                    installer.remove_component_transaction("nquake-maps")

            self.assertEqual(
                "owned", (target / "qw/maps/owned.loc").read_text(encoding="utf-8"),
            )
            self.assertTrue((canonical / "receipt").is_file())
            self.assertTrue((canonical / "inventory").is_file())

    def test_component_removal_failure_on_second_payload_restores_first_file(self):
        """An nth-file failure must leave the whole component installed."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed = installer.stage / "managed"
            maps = managed / "qw/maps"
            maps.mkdir(parents=True)
            (maps / "a.loc").write_text("a", encoding="utf-8")
            (maps / "b.loc").write_text("b", encoding="utf-8")
            installer.install_component_overlay(
                "nquake-maps", managed, "test", "https://example.invalid/maps.zip",
            )
            real_move = installer._move_component_removal_node
            calls = 0

            def fail_second(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected second-file failure")
                return real_move(*args, **kwargs)

            with mock.patch.object(
                installer, "_move_component_removal_node", side_effect=fail_second,
            ):
                with self.assertRaises(install_qw.MutationApplyError) as raised:
                    installer.remove_component_transaction("nquake-maps")
            self.assertIsInstance(raised.exception.operation_error, OSError)

            self.assertEqual("a", (target / "qw/maps/a.loc").read_text(encoding="utf-8"))
            self.assertEqual("b", (target / "qw/maps/b.loc").read_text(encoding="utf-8"))
            self.assertTrue(installer.validate_component_pair("nquake-maps")[0])

    def test_component_removal_metadata_failure_restores_payload(self):
        """A metadata-step failure must reverse the completed payload step."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed = installer.stage / "managed"
            payload = managed / "qw/maps/owned.loc"
            payload.parent.mkdir(parents=True)
            payload.write_text("owned", encoding="utf-8")
            installer.install_component_overlay(
                "nquake-maps", managed, "test", "https://example.invalid/maps.zip",
            )

            with mock.patch.object(
                installer,
                "_apply_component_removal_metadata",
                side_effect=install_qw.InstallerError("injected metadata failure"),
            ):
                with self.assertRaisesRegex(
                    install_qw.InstallerError, "injected metadata failure",
                ):
                    installer.remove_component_transaction("nquake-maps")

            self.assertEqual(
                "owned", (target / "qw/maps/owned.loc").read_text(encoding="utf-8"),
            )
            self.assertTrue(installer.validate_component_pair("nquake-maps")[0])

    def test_component_removal_rollback_preserves_identity_swapped_backup(self):
        """Rollback must not restore a stage pathname whose inode was replaced."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed = installer.stage / "managed"
            payload = managed / "qw/maps/owned.loc"
            payload.parent.mkdir(parents=True)
            payload.write_text("owned", encoding="utf-8")
            installer.install_component_overlay(
                "nquake-maps", managed, "test", "https://example.invalid/maps.zip",
            )
            _removed, result = installer.remove_component_transaction("nquake-maps")
            backup = next(installer.stage.glob(
                ".nquake-maps-remove-payload.*/qw/maps/owned.loc",
            ))
            backup.unlink()
            backup.write_text("replacement", encoding="utf-8")

            with self.assertRaises(install_qw.MutationRollbackError):
                install_qw.rollback_mutation(result)

            self.assertFalse((target / "qw/maps/owned.loc").exists())
            self.assertEqual("replacement", backup.read_text(encoding="utf-8"))
            self.assertTrue(installer.validate_component_pair("nquake-maps")[0])

    def test_component_metadata_failure_rolls_back_payload_and_stale_files(self):
        """A receipt failure must not leave a new payload under old metadata."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            first = installer.stage / "first"
            (first / "qw/maps").mkdir(parents=True)
            (first / "qw/maps/current.loc").write_text("old", encoding="utf-8")
            (first / "qw/maps/stale.loc").write_text("stale", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.install_component_overlay(
                    "nquake-maps", first, "v1", "https://example.invalid/v1.zip",
                )
            receipt, inventory = (
                target / relative
                for relative in installer.component_metadata("nquake-maps")
            )
            previous_metadata = (receipt.read_bytes(), inventory.read_bytes())
            personal_empty = target / "qw/personal-empty"
            personal_empty.mkdir()

            second = installer.stage / "second"
            (second / "qw/maps").mkdir(parents=True)
            (second / "qw/maps/current.loc").write_text("new", encoding="utf-8")
            (second / "qw/maps/new.loc").write_text("new", encoding="utf-8")

            with mock.patch.object(
                installer,
                "commit_component_metadata",
                side_effect=install_qw.InstallerError("injected metadata failure"),
            ):
                with self.assertRaisesRegex(
                    install_qw.InstallerError, "injected metadata failure",
                ):
                    installer.install_component_overlay(
                        "nquake-maps", second, "v2", "https://example.invalid/v2.zip",
                    )

            self.assertEqual(
                "old", (target / "qw/maps/current.loc").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "stale", (target / "qw/maps/stale.loc").read_text(encoding="utf-8"),
            )
            self.assertFalse((target / "qw/maps/new.loc").exists())
            self.assertEqual(previous_metadata, (receipt.read_bytes(), inventory.read_bytes()))
            self.assertTrue(personal_empty.is_dir())

    def test_component_copy_failure_rolls_back_files_written_by_the_failed_step(self):
        """A copy error inside the payload step must restore its earlier writes."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            first = installer.stage / "first"
            (first / "qw/maps").mkdir(parents=True)
            (first / "qw/maps/a.loc").write_text("old-a", encoding="utf-8")
            (first / "qw/maps/b.loc").write_text("old-b", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.install_component_overlay(
                    "nquake-maps", first, "v1", "https://example.invalid/v1.zip",
                )

            second = installer.stage / "second"
            (second / "qw/maps").mkdir(parents=True)
            (second / "qw/maps/a.loc").write_text("new-a", encoding="utf-8")
            (second / "qw/maps/b.loc").write_text("new-b", encoding="utf-8")
            receipt, inventory = (
                target / relative
                for relative in installer.component_metadata("nquake-maps")
            )
            previous_metadata = (receipt.read_bytes(), inventory.read_bytes())
            real_copy = install_qw.atomic_copy_file

            def fail_second_payload_copy(source: Path, destination: Path, *args, **kwargs):
                if source == second / "qw/maps/b.loc":
                    raise install_qw.AtomicWriteError(
                        "injected payload copy failure", committed=False,
                    )
                return real_copy(source, destination, *args, **kwargs)

            with mock.patch.object(
                install_qw,
                "atomic_copy_file",
                side_effect=fail_second_payload_copy,
            ):
                with self.assertRaises(install_qw.InstallerError):
                    installer.install_component_overlay(
                        "nquake-maps", second, "v2", "https://example.invalid/v2.zip",
                    )

            self.assertEqual(
                ("old-a", "old-b"),
                tuple(
                    (target / f"qw/maps/{name}.loc").read_text(encoding="utf-8")
                    for name in ("a", "b")
                ),
            )
            self.assertEqual(previous_metadata, (receipt.read_bytes(), inventory.read_bytes()))

    def test_component_payload_uses_verified_atomic_copy_boundary(self):
        """Payload publication must not write directly into the managed destination."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            first = installer.stage / "first"
            (first / "qw/maps").mkdir(parents=True)
            (first / "qw/maps/dm6.loc").write_text(
                "old-managed-content", encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                installer.install_component_overlay(
                    "nquake-maps", first, "v1", "https://example.invalid/v1.zip",
                )

            second = installer.stage / "second"
            (second / "qw/maps").mkdir(parents=True)
            source = second / "qw/maps/dm6.loc"
            source.write_text("new-managed-content", encoding="utf-8")
            destination = target / "qw/maps/dm6.loc"
            receipt, inventory = (
                target / relative
                for relative in installer.component_metadata("nquake-maps")
            )
            previous_metadata = (receipt.read_bytes(), inventory.read_bytes())
            with mock.patch.object(
                install_qw,
                "atomic_copy_file",
                wraps=install_qw.atomic_copy_file,
            ) as atomic_copy:
                installer.install_component_overlay(
                    "nquake-maps", second, "v2", "https://example.invalid/v2.zip",
                )

            self.assertEqual(
                "new-managed-content", destination.read_text(encoding="utf-8"),
            )
            self.assertNotEqual(previous_metadata, (receipt.read_bytes(), inventory.read_bytes()))
            matching = [
                call for call in atomic_copy.call_args_list
                if call.args[:2] == (source, destination)
            ]
            self.assertEqual(1, len(matching))
            self.assertEqual(
                hashlib.sha256(source.read_bytes()).hexdigest(),
                matching[0].kwargs["expected_sha256"],
            )

    def test_component_phase_state_failure_rolls_back_payload_and_metadata(self):
        """state.json failure must reverse every component committed by the phase."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed = installer.stage / "managed"
            (managed / "qw/maps").mkdir(parents=True)
            (managed / "qw/maps/new.loc").write_text("new", encoding="utf-8")
            installer.selected_component_profile = "custom"
            installer.requested_components = ["nquake-maps"]

            def install_selected(selected: list[str]):
                self.assertEqual(["nquake-maps"], selected)
                _, result = installer.install_component_overlay_transaction(
                    "nquake-maps",
                    managed,
                    "v1",
                    "https://example.invalid/v1.zip",
                )
                return (result,)

            with mock.patch.object(
                installer, "choose_components", return_value=["nquake-maps"],
            ), mock.patch.object(
                installer, "install_components", side_effect=install_selected,
            ), mock.patch.object(
                installer,
                "write_install_state",
                side_effect=install_qw.InstallerError("injected state failure"),
            ):
                with self.assertRaisesRegex(
                    install_qw.InstallerError, "injected state failure",
                ):
                    installer.install_component_phase()

            self.assertFalse((target / "qw/maps/new.loc").exists())
            self.assertFalse((target / ".x86qw/components/nquake-maps").exists())
            self.assertFalse((target / install_qw.INSTALL_STATE).exists())

    def test_component_default_created_before_state_is_rolled_back(self):
        """A newly created personal default cannot survive a failed parent state."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed = installer.stage / "managed"
            managed.mkdir()
            staged_default = installer.stage / "default.cfg"
            staged_default.write_text("managed default\n", encoding="utf-8")
            destination = target / "fortress/x86qw-user.cfg"
            component_result = install_qw.execute_mutation(install_qw.prepare_mutation(
                install_qw.MutationPlan(
                    identifier="fixture-component",
                    summary="fixture",
                    steps=(install_qw.MutationStep(
                        key="noop", description="noop", observe=lambda: None,
                        apply=lambda: None, rollback=lambda _token: None,
                    ),),
                ),
            ))

            with (
                mock.patch.object(installer, "migrate_legacy_nquake"),
                mock.patch.object(installer, "migrate_legacy_clan_arena"),
                mock.patch.object(installer, "migrate_legacy_component_replacements"),
                mock.patch.object(installer, "release_play_support_profiles"),
                mock.patch.object(installer, "component_package_record", return_value={
                    "package": "team-fortress", "version": "fixture",
                    "origin_url": "https://example.invalid/team-fortress.zip",
                }),
                mock.patch.object(
                    installer, "prepare_component_sources",
                    return_value=(managed, [(staged_default, destination)], "fixture"),
                ),
                mock.patch.object(installer, "normalize_component_platform_payload"),
                mock.patch.object(
                    installer, "install_component_overlay_transaction",
                    return_value=(1, component_result),
                ),
                mock.patch.object(installer, "migrate_saved_configs"),
                mock.patch.object(installer, "refresh_qw_package_order"),
                mock.patch.object(installer, "reconcile_play_support_transaction"),
            ):
                results = installer.install_components(["team-fortress"])

            self.assertTrue(destination.is_file())
            with self.assertRaises(install_qw.PersistenceError):
                try:
                    raise install_qw.PersistenceError("falha de state.json", committed=False)
                except BaseException as error:
                    installer.rollback_component_transactions(list(results), error)
                    raise
            self.assertFalse(destination.exists())

    def test_component_default_modified_before_rollback_is_preserved(self):
        """Rollback never infers ownership after the user changes a default."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            source = target / ".default-source.cfg"
            source.write_text("default\n", encoding="utf-8")
            destination = target / "fortress/x86qw-user.cfg"
            result = installer.install_component_default_transaction(source, destination)
            self.assertIsNotNone(result)
            destination.write_text("personal\n", encoding="utf-8")

            assert result is not None
            install_qw.rollback_mutation(result)

            self.assertEqual("personal\n", destination.read_text(encoding="utf-8"))

    def test_committed_state_durability_error_keeps_matching_component_payload(self):
        """A post-rename fsync failure must not roll payload back behind state.json."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            managed = installer.stage / "managed"
            (managed / "qw/maps").mkdir(parents=True)
            (managed / "qw/maps/new.loc").write_text("new", encoding="utf-8")
            installer.selected_component_profile = "custom"
            installer.requested_components = ["nquake-maps"]

            def install_selected(selected: list[str]):
                self.assertEqual(["nquake-maps"], selected)
                _, result = installer.install_component_overlay_transaction(
                    "nquake-maps",
                    managed,
                    "v1",
                    "https://example.invalid/v1.zip",
                )
                return (result,)

            write_state = installer.write_install_state
            atomic_write = install_qw.atomic_write_bytes

            def write_then_report_lost_directory_fsync(path: Path, payload: bytes, **kwargs):
                atomic_write(path, payload, **kwargs)
                raise install_qw.AtomicWriteError(
                    "injected directory fsync failure", committed=True,
                )

            def committed_state_error(*args, **kwargs):
                with mock.patch.object(
                    install_qw,
                    "atomic_write_bytes",
                    side_effect=write_then_report_lost_directory_fsync,
                ):
                    return write_state(*args, **kwargs)

            with mock.patch.object(
                installer, "choose_components", return_value=["nquake-maps"],
            ), mock.patch.object(
                installer, "install_components", side_effect=install_selected,
            ), mock.patch.object(
                installer, "write_install_state", side_effect=committed_state_error,
            ):
                with self.assertRaisesRegex(
                    install_qw.InstallerError,
                    "Estado da instalação não pôde ser gravado",
                ):
                    installer.install_component_phase()

            self.assertEqual(
                "new", (target / "qw/maps/new.loc").read_text(encoding="utf-8"),
            )
            self.assertTrue((target / ".x86qw/components/nquake-maps").is_dir())
            state = json.loads((target / install_qw.INSTALL_STATE).read_text(encoding="utf-8"))
            self.assertEqual(["nquake-maps"], state["recorded_components"])

    def test_component_metadata_uses_unique_legacy_backups_within_one_stage(self):
        """Two commits in one parent transaction must retain both inverses."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            inventory = installer.stage / "nquake-maps.inventory"
            installer.write_inventory_record(
                inventory,
                (("qw/maps/dm6.loc", "a" * 64),),
            )
            receipt = installer.stage / "nquake-maps.receipt"
            installer.write_component_receipt(
                "nquake-maps",
                "v1",
                "https://example.invalid/v1.zip",
                inventory,
                receipt,
            )
            legacy_receipt, legacy_inventory = (
                target / relative
                for relative in installer.legacy_component_metadata("nquake-maps")
            )
            legacy_receipt.parent.mkdir(parents=True)
            install_qw.shutil.copy2(receipt, legacy_receipt)
            install_qw.shutil.copy2(inventory, legacy_inventory)

            first = installer.commit_component_metadata(
                "nquake-maps", inventory, receipt,
            )
            install_qw.shutil.copy2(receipt, legacy_receipt)
            install_qw.shutil.copy2(inventory, legacy_inventory)
            second = installer.commit_component_metadata(
                "nquake-maps", inventory, receipt,
            )

            first_backups = tuple(item[1] for item in first.legacy_backups)
            second_backups = tuple(item[1] for item in second.legacy_backups)
            self.assertEqual(2, len(first_backups))
            self.assertEqual(2, len(second_backups))
            self.assertTrue(all(path.exists() for path in first_backups))
            self.assertTrue(all(path.exists() for path in second_backups))
            self.assertTrue(set(first_backups).isdisjoint(second_backups))

    def test_component_metadata_restores_legacy_file_if_post_move_check_fails(self):
        """A moved legacy receipt must be tracked before any later check can fail."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            inventory = installer.stage / "nquake-maps.inventory"
            installer.write_inventory_record(
                inventory,
                (("qw/maps/dm6.loc", "a" * 64),),
            )
            receipt = installer.stage / "nquake-maps.receipt"
            installer.write_component_receipt(
                "nquake-maps",
                "v1",
                "https://example.invalid/v1.zip",
                inventory,
                receipt,
            )
            legacy_receipt, legacy_inventory = (
                target / relative
                for relative in installer.legacy_component_metadata("nquake-maps")
            )
            legacy_receipt.parent.mkdir(parents=True)
            install_qw.shutil.copy2(receipt, legacy_receipt)
            install_qw.shutil.copy2(inventory, legacy_inventory)
            expected = (legacy_receipt.read_bytes(), legacy_inventory.read_bytes())
            regular_identity = installer._regular_identity
            failed = False

            def fail_first_backup_check(path: Path):
                nonlocal failed
                if installer.stage in path.parents and path.name == "metadata" and not failed:
                    failed = True
                    raise OSError("injected post-move identity failure")
                return regular_identity(path)

            with mock.patch.object(
                installer,
                "_regular_identity",
                side_effect=fail_first_backup_check,
            ):
                with self.assertRaises(install_qw.InstallerError):
                    installer.commit_component_metadata(
                        "nquake-maps", inventory, receipt,
                    )

            self.assertTrue(legacy_receipt.is_file())
            self.assertTrue(legacy_inventory.is_file())
            self.assertEqual(
                expected,
                (legacy_receipt.read_bytes(), legacy_inventory.read_bytes()),
            )

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
            self.assertEqual("ezquake-client-content", compatibility["scope"])
            self.assertEqual(
                {
                    identifier for identifier, component in installer.components.items()
                    if component["kind"] not in {"runtime", "service"}
                },
                set(compatibility["covered_components"]),
            )
            self.assertTrue({"mvdsv", "qwfwd", "qtv"}.isdisjoint(compatibility["covered_components"]))
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
            self.assertEqual([], installer.components["mvdsv"]["requires"])
            self.assertEqual([], installer.components["qtv"]["requires"])
            self.assertEqual([], installer.components["qwfwd"]["requires"])
            ktx = installer.components["ktx"]
            ktx_overlay = ROOT / "dist/mods/ktx/1.47/x86qw"
            expected_ktx_sources = {
                "dist/mods/ktx/1.47/x86qw/config/client.cfg",
                "dist/mods/ktx/1.47/x86qw/config/user.cfg.example",
                "dist/mods/ktx/1.47/x86qw/catalog/frogbots/names.user.json.example",
                *(path.relative_to(ROOT).as_posix() for path in (ktx_overlay / "config").glob("help*.cfg")),
                *(path.relative_to(ROOT).as_posix() for path in (ktx_overlay / "config/modes").glob("mode-*.cfg")),
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
            managed_pk3 = target / "qw/ktx.pk3"
            replacement = io.BytesIO()
            with zipfile.ZipFile(replacement, "w") as package:
                package.writestr("progs.dat", b"different but valid")
            managed_pk3.write_bytes(replacement.getvalue())
            real_file_hash = install_qw.file_hash

            def stale_hash(path: Path) -> str:
                if path == managed_pk3:
                    return hashlib.sha256(payload).hexdigest()
                return real_file_hash(path)

            with mock.patch.object(install_qw, "file_hash", side_effect=stale_hash):
                with self.assertRaisesRegex(
                    install_qw.InstallerError,
                    "Arquivo gerenciado foi alterado",
                ):
                    installer.verify_component("ktx")

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
            package = dict(installer.component_package_record("ktx"))
            releases = json.loads((
                ROOT / "maintenance/inventory/component-releases.json"
            ).read_text(encoding="utf-8"))
            package["version"] = releases["components"]["ktx"]["version"]
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(
                    installer, "component_package_record", return_value=package,
                ):
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
            inventory = (target / ".x86qw/components/clan-arena/inventory").read_text(encoding="utf-8")
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
            self.assertFalse((target / ".x86qw/components/clan-arena/receipt").exists())
            self.assertFalse((target / ".x86qw/components/clan-arena/inventory").exists())
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

            def download(contracts, **options):
                self.assertEqual(tuple(package["urls"]), tuple(item.url for item in contracts))
                self.assertTrue(all(item.expected_size == len(payload) for item in contracts))
                self.assertTrue(all(
                    item.expected_sha256 == package["sha256"] for item in contracts
                ))
                self.assertTrue(all(
                    item.maximum_size == install_qw.MAX_ARTIFACT_BYTES
                    for item in contracts
                ))
                options["on_mirror_failure"](
                    1, contracts[0], install_qw.DownloadError("GitHub unavailable"),
                )
                contracts[1].destination.write_bytes(payload)
                return mock.Mock(data=None)

            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(
                    install_qw, "bounded_download_mirrors", side_effect=download,
                ) as request:
                    artifact = installer.download_component_package(package)
            self.assertEqual(payload, artifact.read_bytes())
            request.assert_called_once()

    def test_component_is_materialized_from_canonical_sources_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            package = installer.component_package_record("nquake-bootstrap")
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(
                    installer, "http_get_mirrors", side_effect=AssertionError("network used"),
                ):
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
            receipt = (target / ".x86qw/components/nquake-bootstrap/receipt").read_text(encoding="utf-8")
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
                "-clientport", "0",
                "+cfg_save_onquit", "0",
                "+sb_findroutes", "0", "+sb_autoupdate", "0",
                "+map", "dm6",
            ], popen.call_args.args[0])

    def test_native_runtime_smoke_can_capture_a_disposable_console_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            runtime = target / "ezQuake Stable.app"
            executable = runtime / "Contents/MacOS/ezQuake"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"mach-o")
            with mock.patch.dict(install_qw.os.environ, {
                "X86QW_TEST_WINDOWED": "1",
                "X86QW_TEST_CONSOLE_LOG": "1",
            }):
                with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                    with mock.patch.object(install_qw.subprocess, "Popen") as popen:
                        installer.launch_runtime(runtime, ["+map", "dm6"])
            self.assertEqual([
                str(executable), "-nohome", "-basedir", str(target),
                "-window", "-width", "1280", "-height", "720",
                "-clientport", "0", "-condebug",
                "+cfg_save_onquit", "0",
                "+sb_findroutes", "0", "+sb_autoupdate", "0",
                "+map", "dm6",
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
                                            with mock.patch.object(installer, "verify_local_play_support") as support:
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
                "+bind", "F12", "quit",
            ])

    def test_play_validates_support_without_mutating_managed_or_personal_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "td2")
            runtime = target / "ezquake-stable.exe"
            components = target / ".x86qw/components"
            personal = target / "td2/x86qw-td2-user.cfg"
            components.mkdir(parents=True)
            personal.parent.mkdir(parents=True)
            (components / "sentinel.json").write_text("managed\n", encoding="utf-8")
            personal.write_text("personal\n", encoding="utf-8")
            before = {
                path.relative_to(target).as_posix(): path.read_bytes()
                for path in target.rglob("*") if path.is_file()
            }
            mutators = (
                "ensure_local_play_support", "migrate_saved_configs",
                "migrate_mutable_component_defaults", "remove_legacy_macos_video_layout",
                "configure_macos_fullscreen", "refresh_qw_package_order",
            )
            patches = [
                mock.patch.object(
                    player, name,
                    side_effect=AssertionError(f"play tentou executar {name}"),
                )
                for name in mutators
            ]
            for patcher in patches:
                patcher.start()
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    with mock.patch.object(player, "check_paks"):
                        with mock.patch.object(player, "available_local_games", return_value=[game]):
                            with mock.patch.object(player, "installed_component_for_game", return_value=game.component):
                                with mock.patch.object(player, "verify_component"):
                                    with mock.patch.object(player, "verify_local_play_support"):
                                        with mock.patch.object(player, "choose_local_map", return_value="dm6"):
                                            with mock.patch.object(player, "choose_host_runtime", return_value=("stable", runtime)):
                                                with mock.patch.object(player, "launch_runtime"):
                                                    player.play_local(game_key="td2", map_key="dm6")
            finally:
                for patcher in reversed(patches):
                    patcher.stop()
            after = {
                path.relative_to(target).as_posix(): path.read_bytes()
                for path in target.rglob("*") if path.is_file()
            }
            self.assertEqual(before, after)

    def test_host_selection_validates_support_without_materializing_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, _, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "td2")
            options = services_qw.parse_arguments(["host", "td2", "--map", "dm6"], ROOT)
            with mock.patch.object(player, "check_paks"):
                with mock.patch.object(player, "available_local_games", return_value=[game]):
                    with mock.patch.object(player, "choose_local_game", return_value=game):
                        with mock.patch.object(player, "installed_component_for_game", return_value=game.component):
                            with mock.patch.object(player, "verify_component"):
                                with mock.patch.object(player, "verify_local_play_support") as verify_support:
                                    with mock.patch.object(player, "choose_local_map", return_value="dm6"):
                                        with mock.patch.object(
                                            player, "ensure_local_play_support",
                                            side_effect=AssertionError("host tentou instalar play-support"),
                                        ):
                                            selected = services_qw.select_hosted_game(player, options)
            self.assertEqual("td2", selected.game.key)
            verify_support.assert_called_once_with([game])

    def test_host_resolves_frogbot_profile_with_the_selected_mode(self):
        game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
        mode = next(
            mode for mode in play_qw.load_ktx_modes(ROOT)
            if mode.key == "2on2on2"
        )
        options = services_qw.parse_arguments([
            "host", "ktx", "--mode", "2on2on2", "--map", "dm3",
            "--fill-bots", "--bot-names", "x86qw",
        ], ROOT)
        player = mock.Mock(project_root=ROOT, target=ROOT / "quake-world")
        player.available_local_games.return_value = [game]
        player.choose_local_game.return_value = game
        player.installed_component_for_game.return_value = game.component
        player.choose_ktx_mode.return_value = mode
        player.choose_local_map.return_value = "dm3"
        resolved = play_qw.replace(options.ktx_options, bot_name_pool=("Luffy",) * 5)
        with mock.patch.object(
            play_qw, "resolve_frogbot_name_profile", return_value=resolved,
        ) as resolve, mock.patch.object(
            services_qw, "ktx_assets", return_value=frozenset({"bots/maps/dm3.bot"}),
        ):
            selection = services_qw.select_hosted_game(player, options)
        self.assertEqual(resolved, selection.ktx_options)
        self.assertIs(mode, resolve.call_args.args[4])

    def test_play_menu_summarizes_and_confirms_before_opening_the_client(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, _ = self.make_player(Path(temporary))
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "td2")
            runtime = target / "ezQuake Stable.app"
            output = io.StringIO()
            with mock.patch.object(player, "check_paks"), mock.patch.object(
                player, "available_local_games", return_value=[game],
            ), mock.patch.object(
                player, "installed_component_for_game", return_value=game.component,
            ), mock.patch.object(player, "verify_component"), mock.patch.object(
                player, "choose_local_map", return_value="dm6",
            ), mock.patch.object(
                player, "choose_host_runtime",
                return_value=("ezQuake stable 1.0", runtime),
            ), mock.patch.object(
                player, "verify_local_play_support",
            ) as support, mock.patch.object(
                player, "launch_runtime",
            ) as launch, mock.patch.object(
                play_qw.navigation, "confirm", return_value=False,
            ) as confirm, contextlib.redirect_stdout(output):
                player.play_local(
                    "td2", map_key="dm6", configure_interactively=True,
                )
            confirm.assert_called_once()
            support.assert_not_called()
            launch.assert_not_called()
            rendered = confirm.call_args.kwargs["subtitle"]
            self.assertIn("Resumo da partida", rendered)
            self.assertIn("Cliente | ezQuake stable 1.0", rendered)
            launcher = "x86qw.cmd" if os.name == "nt" else "./x86qw.sh"
            self.assertIn(f"{launcher} play td2 --map dm6", rendered)

    def test_host_menu_selects_the_game_before_quick_profile_and_confirmation(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            target.mkdir()
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "td2")
            selection = services_qw.HostedGame(
                game, None, "dm6", frozenset(), play_qw.KtxLaunchOptions(),
            )
            player = mock.Mock(target=target, project_root=ROOT)
            events = []
            captured = {}

            def select_game(*_args, **_kwargs):
                events.append("game")
                return selection

            def quick(options, _current=None):
                events.append("profile")
                services_qw.apply_quick_host_defaults(options)
                return "quick"

            def execution(options, _breadcrumb):
                events.append("execution")
                options.background = False
                return True

            def refuse(*_args, **kwargs):
                events.append("confirm")
                captured["summary"] = kwargs["subtitle"]
                return False

            output = io.StringIO()
            with mock.patch.object(
                services_qw.gameplay, "Player", return_value=player,
            ), mock.patch.object(
                services_qw, "select_hosted_game", side_effect=select_game,
            ), mock.patch.object(
                services_qw, "choose_host_configuration", side_effect=quick,
            ), mock.patch.object(
                services_qw, "menu_execution_mode", side_effect=execution,
            ), mock.patch.object(
                services_qw.navigation, "confirm", side_effect=refuse,
            ), mock.patch.object(
                services_qw.SessionLock, "acquire",
            ) as acquire, contextlib.redirect_stdout(output):
                self.assertEqual(0, services_qw.main([
                    "host", "--target", str(target), "--menu",
                ]))
            self.assertEqual(["game", "profile", "execution", "confirm"], events)
            acquire.assert_not_called()
            rendered = captured["summary"]
            self.assertIn("Resumo da hospedagem", rendered)
            self.assertIn("Perfil     | Rápido local", rendered)
            self.assertIn("MVDSV      | 127.0.0.1:28501", rendered)
            self.assertIn("QTV        | desativado", rendered)
            self.assertIn("QWFWD      | desativado", rendered)

    def test_advanced_host_menu_runs_before_confirmation_and_redacts_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            target.mkdir()
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
            mode = next(mode for mode in play_qw.load_ktx_modes(ROOT) if mode.key == "duel")
            selection = services_qw.HostedGame(
                game, mode, "dm6", frozenset(), play_qw.KtxLaunchOptions(),
            )
            player = mock.Mock(target=target, project_root=ROOT)
            secret = "never-print-this-secret"

            def configure(options):
                options.password = secret
                return True

            output = io.StringIO()
            with mock.patch.object(
                services_qw.gameplay, "Player", return_value=player,
            ), mock.patch.object(
                services_qw, "select_hosted_game", return_value=selection,
            ), mock.patch.object(
                services_qw, "choose_host_configuration", return_value="advanced",
            ), mock.patch.object(
                services_qw, "configure_advanced_host_menu", side_effect=configure,
            ) as advanced, mock.patch.object(
                services_qw.navigation, "confirm", return_value=False,
            ) as confirm, mock.patch.object(
                services_qw.SessionLock, "acquire",
            ) as acquire, contextlib.redirect_stdout(output):
                self.assertEqual(0, services_qw.main([
                    "host", "--target", str(target), "--menu",
                ]))
            advanced.assert_called_once()
            acquire.assert_not_called()
            rendered = confirm.call_args.kwargs["subtitle"]
            self.assertIn("Perfil     | Avançado", rendered)
            self.assertIn("valores redigidos", rendered)
            self.assertNotIn(secret, rendered)
            self.assertNotIn(secret, output.getvalue())

    def test_standalone_services_show_safe_summary_and_confirm_before_locking(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            target.mkdir()
            player = mock.Mock(target=target, project_root=ROOT)
            cases = (
                (
                    ["qtv", "--target", str(target), "--menu", "--bind", "::", "--upstream", "[::1]:28501"],
                    ("Resumo do serviço", "Serviço   | QTV", "HTTP      | http://[::]:28000/", "Upstream  | [::1]:28501"),
                ),
                (
                    ["proxy", "--target", str(target), "--menu", "--bind", "0.0.0.0", "--port", "30001"],
                    ("Resumo do serviço", "Serviço   | QWFWD", "Endpoint  | 0.0.0.0:30001/UDP"),
                ),
            )
            for arguments, expected in cases:
                with self.subTest(service=arguments[0]), mock.patch.object(
                    services_qw.gameplay, "Player", return_value=player,
                ), mock.patch.object(
                    services_qw, "configure_service_menu", return_value=True,
                ), mock.patch.object(
                    services_qw.navigation, "confirm", return_value=False,
                ) as confirm, mock.patch.object(
                    services_qw.SessionLock, "acquire",
                ) as acquire, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(0, services_qw.main(arguments))
                acquire.assert_not_called()
                rendered = confirm.call_args.kwargs["subtitle"]
                for value in expected:
                    self.assertIn(value, rendered)
                self.assertIn("Comando equivalente seguro", rendered)

    def test_service_menu_can_select_background_execution(self):
        proxy = services_qw.parse_arguments([
            "proxy", "--target", "/tmp/x86qw-test", "--menu",
        ], ROOT)
        with mock.patch.object(
            services_qw, "menu_bind", return_value="127.0.0.1",
        ), mock.patch.object(
            services_qw, "menu_port", return_value=30000,
        ), mock.patch.object(
            services_qw.navigation, "select_one", return_value="background",
        ):
            self.assertTrue(services_qw.configure_service_menu(proxy))
        self.assertTrue(proxy.background)

        qtv = services_qw.parse_arguments([
            "qtv", "--target", "/tmp/x86qw-test", "--menu",
        ], ROOT)
        with mock.patch.object(
            services_qw, "menu_bind", return_value="127.0.0.1",
        ), mock.patch.object(
            services_qw, "menu_port", return_value=28000,
        ), mock.patch.object(
            services_qw.navigation, "select_one",
            side_effect=("none", "background"),
        ):
            self.assertTrue(services_qw.configure_service_menu(qtv))
        self.assertTrue(qtv.background)

    def test_service_summary_redacts_upstream_secret_and_uses_prompt_in_safe_command(self):
        options = services_qw.parse_arguments([
            "qtv", "--target", "/tmp/x86qw-test", "--upstream", "127.0.0.1:28501",
            "--qtv-password", "never-print-this-secret",
        ], ROOT)
        rendered = services_qw.service_summary_text(options)
        self.assertIn("segredo do upstream configurado; valor redigido", rendered)
        self.assertIn("--prompt-qtv-password", rendered)
        self.assertNotIn("never-print-this-secret", rendered)

        options.background = True
        rendered = services_qw.service_summary_text(options)
        self.assertIn("Execução  | segundo plano", rendered)
        self.assertIn("--background", rendered)

    def test_equivalent_commands_use_the_windows_launcher_on_windows(self):
        with mock.patch.object(play_qw.os, "name", "nt"):
            rendered = play_qw.public_command([
                "play", "ktx", "--mode", "duel", "--map", "dm6",
            ])
        self.assertTrue(rendered.startswith("x86qw.cmd play ktx "), rendered)
        self.assertNotIn("./x86qw.sh", rendered)

    @unittest.skipIf(os.name == "nt", "permissão executável não existe no Windows")
    def test_service_runtime_does_not_repair_permissions_during_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            binary = target / "qwfwd/qwfwd"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"runtime")
            binary.chmod(0o600)
            with mock.patch.object(installer, "verify_component", return_value=1):
                with mock.patch.object(services_qw, "runtime_variant", return_value="linux-amd64"):
                    with self.assertRaisesRegex(services_qw.InstallerError, "Execute repair"):
                        services_qw.runtime_binary(installer, "qwfwd")
            self.assertEqual(0o600, binary.stat().st_mode & 0o777)

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
                                            with mock.patch.object(installer, "verify_local_play_support"):
                                                with mock.patch("builtins.input", return_value=""):
                                                    installer.play_local("ktx", "duel", "dm6")
            launch.assert_called_once_with(runtime, [
                *local_server_baseline("ktx"),
                *ktx_launch_setup_alias(),
                *ktx_entry_aliases(),
                "+set", "k_defmap", "dm6",
                "+set", "k_defmode", "1on1",
                "+map", "dm6",
                "+bind", "F12", "quit",
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
                                            with mock.patch.object(installer, "verify_local_play_support"):
                                                with mock.patch("builtins.input", return_value=""):
                                                    installer.play_local("ktx", "midair")
            launch.assert_called_once_with(runtime, [
                *local_server_baseline("ktx"),
                *ktx_launch_setup_alias(mode_key="midair"),
                "+tempalias", "on_enter",
                "exec x86qw-ktx-mode-midair.cfg",
                "+set", "k_defmap", "povdmm4",
                "+set", "k_defmode", "1on1",
                "+map", "povdmm4",
                "+bind", "F12", "quit",
            ])

    def test_ktx_bot_options_enable_frogbot_before_map_and_add_after_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_player(Path(temporary))
            (target / "qw").mkdir()
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
            runtime = target / "ezQuake Stable.app"
            options = play_qw.KtxLaunchOptions(
                bots=2, bot_skill=10, bot_team="red",
            )
            assets = frozenset({"bots/maps/dm6.bot"})
            captured: dict[str, object] = {}

            def capture_launch(selected_runtime, arguments):
                captured["runtime"] = selected_runtime
                captured["arguments"] = arguments
                config_name = next(
                    arguments[index + 1]
                    for index, argument in enumerate(arguments[:-1])
                    if argument == "+exec"
                    and arguments[index + 1].startswith("x86qw-ktx-session-")
                )
                config_path = target / "qw" / config_name
                captured["config_path"] = config_path
                captured["config"] = config_path.read_text(encoding="ascii")

            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "check_paks"):
                    with mock.patch.object(installer, "available_local_games", return_value=[game]):
                        with mock.patch.object(installer, "installed_component_for_game", return_value="ktx"):
                            with mock.patch.object(installer, "verify_component"):
                                with mock.patch.object(installer, "ktx_archive_members", return_value=assets):
                                    with mock.patch.object(installer, "local_map_names", return_value=["dm6"]):
                                        with mock.patch.object(installer, "choose_host_runtime", return_value=("stable", runtime)):
                                            with mock.patch.object(
                                                installer, "launch_runtime",
                                                side_effect=capture_launch,
                                            ) as launch:
                                                with mock.patch.object(installer, "verify_local_play_support"):
                                                    installer.play_local(
                                                        "ktx", "2on2", "dm6", options,
                                                    )
            launch.assert_called_once()
            arguments = launch.call_args.args[1]
            self.assertEqual(runtime, captured["runtime"])
            self.assertEqual(local_server_baseline("ktx"), arguments[:len(local_server_baseline("ktx"))])
            self.assertIn(["+set", "k_fb_enabled", "1"], [arguments[index:index + 3] for index in range(len(arguments) - 2)])
            self.assertNotIn("+tempalias", arguments)
            self.assertLess(arguments.index("+exec"), arguments.index("+map"))
            config = str(captured["config"])
            self.assertIn("unset_re ^k_fb_name_", config)
            self.assertIn("if ($k_maxclients < 3) then k_maxclients 3", config)
            self.assertIn("if ($maxclients < 3) then maxclients 3", config)
            # The runtime state machine also carries this command in the
            # managed INS aliases; the launch sequence itself must contain at
            # least the two requested additions.
            self.assertGreaterEqual(
                config.count("cmd botcmd addbot 10 red"), 2,
            )
            self.assertGreaterEqual(config.count(";wait"), play_qw.FROGBOT_ADD_WAIT_FRAMES)
            self.assertFalse(Path(captured["config_path"]).exists())

    def test_frogbot_name_catalog_prioritizes_straw_hats_and_is_unique(self):
        document = json.loads(
            (ROOT / "dist/mods/ktx/1.47/x86qw/catalog/frogbots/names.json").read_text(
                encoding="utf-8",
            )
        )
        identities = play_qw.validate_frogbot_name_document(
            document, profile="x86qw", label="fixture",
        )
        self.assertEqual(
            ("Luffy", "Zoro", "Nami", "Usopp", "Sanji", "Chopper", "Robin", "Franky", "Brook", "Jinbe"),
            tuple(identity.name for identity in identities[:10]),
        )
        self.assertEqual(32, len(identities))
        self.assertEqual(32, len({identity.name.casefold() for identity in identities}))
        self.assertTrue(all(
            set(character) == {"name"}
            for group in document["groups"]
            for character in group["characters"]
        ))

    def test_frogbot_names_use_slash_and_quake_high_bit_color(self):
        self.assertEqual("/$xa0$xcc$xf5$xe6$xe6$xf9", play_qw.quake_colored_frogbot_name("Luffy"))
        self.assertEqual(
            "/$xa0$xc2$xe9$xe7$xa0$xcd$xef$xed",
            play_qw.quake_colored_frogbot_name("Big Mom"),
        )
        self.assertNotIn("&c", play_qw.quake_colored_frogbot_name("Zoro"))
        self.assertEqual((), play_qw.ktx_bot_name_settings(
            play_qw.KtxLaunchOptions(bots=1),
        ))

    def test_one_piece_frogbot_profiles_never_override_ktx_colors(self):
        identities = play_qw.load_x86qw_frogbot_names(ROOT)
        settings = dict(play_qw.ktx_bot_name_settings(
            play_qw.KtxLaunchOptions(
                bots=1, bot_name_pool=identities[:3],
            ),
        ))
        self.assertEqual(
            {"k_fb_name_0", "k_fb_name_team_0", "k_fb_name_enemy_0"},
            set(settings),
        )
        self.assertFalse(any("color" in name for name in settings))
        qvm = (
            ROOT / "dist/mods/ktx/1.47/x86qw/runtime/qwprogs.qvm"
        ).read_bytes()
        self.assertNotIn(b"k_fb_topcolor", qvm)
        self.assertNotIn(b"k_fb_bottomcolor", qvm)

    def test_x86qw_ktx_qvm_is_reproducible_and_extends_frogbots_declaratively(self):
        expected = {
            "qwprogs.qvm": "6b28a08def85cf13f4d5d909ca0902390fd6c2cbf77cb10a89647384f4c40655",
            "qwprogs.map": "9e1a45c173deff48baf211bf079e7acc41af5cd353eaa878f792431fe51124ea",
        }
        root = ROOT / "dist/mods/ktx/1.47/x86qw/runtime"
        for filename, digest in expected.items():
            with self.subTest(filename=filename):
                self.assertEqual(digest, hashlib.sha256((root / filename).read_bytes()).hexdigest())
        source = ROOT / "dist/mods/ktx/1.47/x86qw/source"
        patch = (source / "0002-frogbot-identities.patch").read_text(encoding="utf-8")
        self.assertIn("FrogbotCacheNames", patch)
        self.assertNotIn("k_fb_topcolor", patch)
        self.assertNotIn("k_fb_bottomcolor", patch)
        self.assertNotIn("BotColors", patch)
        team_patch = (source / "0003-frogbot-team-balance.patch").read_text(encoding="utf-8")
        self.assertIn("FrogbotAutomaticTeamCount", team_patch)
        self.assertIn("FrogbotLeastPopulatedTeam", team_patch)
        self.assertIn("um2on2on2", team_patch)
        self.assertIn("um4on4on4", team_patch)
        role_patch = (source / "0004-frogbot-role-names.patch").read_text(
            encoding="utf-8",
        )
        self.assertIn('": Spice",', role_patch)
        self.assertIn("BotNameEnemy(i)", role_patch)
        build_patch = (source / "0001-reproducible-build-date.patch").read_text(
            encoding="utf-8",
        )
        self.assertIn('MOD_BUILD_DATE\t\t\t("May 16 2026, 20:38:52")', build_patch)

    def test_x86qw_frogbot_profile_randomizes_once_per_launch(self):
        game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
        source_names = play_qw.load_x86qw_frogbot_names(ROOT)
        generator = mock.Mock()
        generator.sample.return_value = list(reversed(source_names))
        options = play_qw.resolve_frogbot_name_profile(
            ROOT, ROOT / "unused", game,
            play_qw.KtxLaunchOptions(bots=2, bot_names_profile="x86qw"),
            generator=generator,
        )
        self.assertEqual(tuple(reversed(source_names)), options.bot_name_pool)
        generator.sample.assert_called_once_with(source_names, len(source_names))

    def test_personal_frogbot_profile_preserves_declaration_order(self):
        game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            personal = target / str(game.bot_names_personal_config)
            personal.parent.mkdir(parents=True)
            personal.write_text(json.dumps({
                "format": 1,
                "game": "ktx",
                "profile": "personal",
                "prefix": "/",
                "color": "quake-high-bit",
                "names": ["Nami", "Robin", "Luffy"],
            }), encoding="utf-8")
            options = play_qw.resolve_frogbot_name_profile(
                ROOT, target, game,
                play_qw.KtxLaunchOptions(bots=2, bot_names_profile="personal"),
            )
            self.assertEqual(
                ("Nami", "Robin", "Luffy"),
                tuple(identity.name for identity in options.bot_name_pool),
            )

    def test_personal_frogbot_profile_rejects_duplicates_symlinks_and_short_lists(self):
        game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            personal = target / str(game.bot_names_personal_config)
            personal.parent.mkdir(parents=True)
            document = {
                "format": 1,
                "game": "ktx",
                "profile": "personal",
                "prefix": "/",
                "color": "quake-high-bit",
                "names": ["Luffy", "luffy"],
            }
            personal.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(play_qw.InstallerError, "duplicatas"):
                play_qw.load_personal_frogbot_names(target, str(game.bot_names_personal_config))
            document["names"] = ["Luffy"]
            personal.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(play_qw.InstallerError, "possui 1 nome"):
                play_qw.resolve_frogbot_name_profile(
                    ROOT, target, game,
                    play_qw.KtxLaunchOptions(bots=2, bot_names_profile="personal"),
                )
            original = target / "names.json"
            original.write_text(json.dumps(document), encoding="utf-8")
            personal.unlink()
            personal.symlink_to(original)
            with self.assertRaisesRegex(play_qw.InstallerError, "ausente ou insegura"):
                play_qw.load_personal_frogbot_names(target, str(game.bot_names_personal_config))

    def test_frogbot_name_schema_rejects_silently_ignored_appearance_fields(self):
        document = {
            "format": 1,
            "project": "x86qw",
            "game": "ktx",
            "profile": "personal",
            "prefix": "/",
            "color": "quake-high-bit",
            "names": [{"name": "Luffy", "topcolor": 4}],
        }
        with self.assertRaisesRegex(play_qw.InstallerError, "Identidade Frogbot inválida"):
            play_qw.validate_frogbot_name_document(
                document, profile="personal", label="lista pessoal",
            )

    def test_named_frogbot_settings_cover_all_name_roles(self):
        options = play_qw.KtxLaunchOptions(
            bots=1, bot_name_pool=("Luffy", "Zoro", "Nami"),
        )
        self.assertEqual(3, len(play_qw.ktx_bot_name_settings(options)))

    def test_duel_accepts_only_one_bot_and_keeps_named_bot_cleanup(self):
        duel = next(mode for mode in play_qw.load_ktx_modes(ROOT) if mode.key == "duel")
        assets = frozenset({"bots/maps/dm6.bot"})
        named = play_qw.KtxLaunchOptions(
            bots=1,
            bot_skill=5,
            bot_name_pool=("Luffy", "Zoro", "Nami"),
        )
        commands = play_qw.ktx_launch_commands(duel, "dm6", assets, named)
        self.assertEqual("if ($k_maxclients < 2) then k_maxclients 2", commands[0])
        self.assertEqual("if ($maxclients < 2) then maxclients 2", commands[1])
        self.assertEqual(1, commands.count("cmd botcmd addbot 5"))
        self.assertEqual(
            play_qw.FROGBOT_ADD_WAIT_FRAMES,
            commands.count("wait"),
        )
        self.assertTrue(commands[-1].startswith("unset k_fb_name_0 "))
        self.assertIn("k_fb_name_enemy_0", commands[-1])

        with self.assertRaisesRegex(play_qw.InstallerError, "no máximo 1 Frogbot"):
            play_qw.ktx_launch_commands(
                duel, "dm6", assets,
                play_qw.KtxLaunchOptions(bots=4, bot_skill=5),
            )

    def test_maximum_frogbot_name_list_builds_all_three_name_sets(self):
        names = play_qw.load_x86qw_frogbot_names(ROOT)
        settings = play_qw.ktx_bot_name_settings(
            play_qw.KtxLaunchOptions(bots=31, bot_name_pool=names),
        )
        self.assertEqual(93, len(settings))
        values = dict(settings)
        generic = [values[f"k_fb_name_{index}"] for index in range(31)]
        self.assertEqual(31, len(set(generic)))
        for index, identity in enumerate(generic):
            self.assertEqual(identity, values[f"k_fb_name_team_{index}"])
            self.assertEqual(identity, values[f"k_fb_name_enemy_{index}"])

    def test_frogbot_runtime_config_is_private_and_identity_safe(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / "qw").mkdir()
            config = play_qw.write_frogbot_runtime_config(
                target,
                (("k_fb_name_0", play_qw.quake_colored_frogbot_name("Luffy")),),
            )
            self.assertTrue(config.path.is_file())
            self.assertFalse(config.path.is_symlink())
            if os.name != "nt":
                self.assertEqual(0o600, config.path.stat().st_mode & 0o777)
            self.assertIn(
                "set_ex k_fb_name_0 $qt/$xa0$xcc$xf5$xe6$xe6$xf9$qt",
                config.path.read_text(encoding="ascii"),
            )
            self.assertIn(
                "unset_re ^k_fb_name_",
                config.path.read_text(encoding="ascii"),
            )
            config.lease.close()
            config.path.unlink()
            config.path.mkdir()
            personal = config.path / "personal.txt"
            personal.write_text("preserve", encoding="utf-8")
            self.assertFalse(play_qw.remove_frogbot_runtime_config(config))
            self.assertEqual("preserve", personal.read_text(encoding="utf-8"))

    def test_play_sets_colored_frogbot_names_before_the_map(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_player(Path(temporary))
            (target / "qw").mkdir(parents=True)
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
            runtime = target / "ezQuake Stable.app"
            requested = play_qw.KtxLaunchOptions(
                bots=1, bot_skill=8, bot_names_profile="x86qw",
            )
            resolved = play_qw.replace(
                requested, bot_name_pool=("Luffy", "Zoro", "Nami"),
            )
            with contextlib.ExitStack() as stack:
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                stack.enter_context(mock.patch.object(installer, "check_paks"))
                stack.enter_context(mock.patch.object(
                    installer, "available_local_games", return_value=[game],
                ))
                stack.enter_context(mock.patch.object(
                    installer, "installed_component_for_game", return_value="ktx",
                ))
                stack.enter_context(mock.patch.object(installer, "verify_component"))
                stack.enter_context(mock.patch.object(installer, "verify_local_play_support"))
                stack.enter_context(mock.patch.object(
                    installer, "ktx_archive_members",
                    return_value=frozenset({"bots/maps/dm6.bot"}),
                ))
                stack.enter_context(mock.patch.object(
                    installer, "local_map_names", return_value=["dm6"],
                ))
                stack.enter_context(mock.patch.object(
                    installer, "choose_host_runtime", return_value=("stable", runtime),
                ))
                stack.enter_context(mock.patch.object(
                    play_qw, "resolve_frogbot_name_profile", return_value=resolved,
                ))
                captured: dict[str, object] = {}

                def capture_launch(selected_runtime, arguments):
                    captured["runtime"] = selected_runtime
                    captured["arguments"] = arguments
                    config_name = next(
                        arguments[index + 1]
                        for index, argument in enumerate(arguments[:-1])
                        if argument == "+exec"
                        and arguments[index + 1].startswith("x86qw-ktx-session-")
                    )
                    config_path = target / "qw" / config_name
                    captured["config_path"] = config_path
                    captured["config"] = config_path.read_text(encoding="ascii")

                launch = stack.enter_context(mock.patch.object(
                    installer, "launch_runtime", side_effect=capture_launch,
                ))
                installer.play_local("ktx", "duel", "dm6", requested)
            arguments = launch.call_args.args[1]
            config = str(captured["config"])
            self.assertIn("set_ex k_fb_name_0 $qt/$xa0$xcc$xf5$xe6$xe6$xf9$qt", config)
            self.assertIn("set_ex k_fb_name_team_0 $qt/$xa0$xcc$xf5$xe6$xe6$xf9$qt", config)
            self.assertIn("set_ex k_fb_name_enemy_0 $qt/$xa0$xcc$xf5$xe6$xe6$xf9$qt", config)
            self.assertFalse(Path(captured["config_path"]).exists())
            config_index = next(
                index for index, argument in enumerate(arguments)
                if argument == "+exec"
                and arguments[index + 1].startswith("x86qw-ktx-session-")
            )
            map_index = arguments.index("+map")
            self.assertLess(config_index, map_index)
            self.assertNotIn("+tempalias", arguments)
            self.assertIn("tempalias x86qw_ktx_launch_setup", config)
            self.assertIn("cmd botcmd addbot 8", config)
            self.assertIn("x86qw_ktx_mode_help", config)
            self.assertIn(
                ";" + ";".join(("wait",) * play_qw.FROGBOT_ADD_WAIT_FRAMES)
                + ";unset k_fb_name_0 k_fb_name_team_0 k_fb_name_enemy_0",
                config,
            )

    def test_ktx_ctf_loads_curated_entities_before_the_map(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_player(Path(temporary))
            (target / "qw").mkdir()
            game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
            runtime = target / "ezQuake Stable.app"
            captured = {}

            def capture_launch(_runtime, arguments):
                config_name = next(
                    arguments[index + 1]
                    for index, argument in enumerate(arguments[:-1])
                    if argument == "+exec"
                    and arguments[index + 1].startswith("x86qw-ktx-session-")
                )
                captured["config_name"] = config_name
                captured["config"] = (target / "qw" / config_name).read_text(
                    encoding="ascii",
                )

            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "check_paks"):
                    with mock.patch.object(installer, "available_local_games", return_value=[game]):
                        with mock.patch.object(installer, "installed_component_for_game", return_value="ktx"):
                            with mock.patch.object(installer, "verify_component"):
                                with mock.patch.object(
                                    installer, "ktx_archive_members",
                                    return_value=frozenset({"id1/maps/ctf/e2m2.ent"}),
                                ), mock.patch.object(installer, "local_map_names", return_value=["e2m2"]):
                                    with mock.patch.object(installer, "choose_host_runtime", return_value=("stable", runtime)):
                                        with mock.patch.object(
                                            installer, "launch_runtime", side_effect=capture_launch,
                                        ) as launch:
                                            with mock.patch.object(installer, "verify_local_play_support"):
                                                with mock.patch("builtins.input", return_value=""):
                                                    installer.play_local("ktx", "ctf")
            launch.assert_called_once_with(runtime, [
                *local_server_baseline("ktx"),
                "+sv_loadentfiles", "1",
                "+sv_loadentfiles_dir", "ctf",
                "+exec", captured["config_name"],
                "+set", "k_defmap", "e2m2",
                "+set", "k_defmode", "ctf",
                "+map", "e2m2",
                "+bind", "F12", "quit",
            ])
            self.assertIn("tempalias x86qw_ktx_launch_setup_1", captured["config"])
            self.assertIn(
                'tempalias on_enter_ctf "exec x86qw-ktx.cfg;x86qw_ktx_launch_setup"',
                captured["config"],
            )
            self.assertFalse((target / "qw" / captured["config_name"]).exists())

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
                (target / "qw").mkdir()
                game = next(game for game in play_qw.LOCAL_GAMES if game.key == "ktx")
                runtime = target / "ezQuake Stable.app"
                captured: dict[str, object] = {}

                def capture_launch(_runtime, arguments):
                    captured["arguments"] = arguments
                    for index, argument in enumerate(arguments[:-1]):
                        if (
                            argument == "+exec"
                            and arguments[index + 1].startswith("x86qw-ktx-session-")
                        ):
                            captured["config"] = (
                                target / "qw" / arguments[index + 1]
                            ).read_text(encoding="ascii")

                with contextlib.redirect_stdout(io.StringIO()):
                    with mock.patch.object(installer, "check_paks"):
                        with mock.patch.object(installer, "available_local_games", return_value=[game]):
                            with mock.patch.object(installer, "installed_component_for_game", return_value="ktx"):
                                with mock.patch.object(installer, "verify_component"):
                                    assets = frozenset({"race/routes/dm6.route"})
                                    with mock.patch.object(installer, "ktx_archive_members", return_value=assets):
                                        with mock.patch.object(installer, "local_map_names", return_value=["dm6"]):
                                            with mock.patch.object(installer, "choose_host_runtime", return_value=("stable", runtime)):
                                                with mock.patch.object(
                                                    installer,
                                                    "launch_runtime",
                                                    side_effect=capture_launch,
                                                ) as launch:
                                                    with mock.patch.object(installer, "verify_local_play_support"):
                                                        with mock.patch("builtins.input", return_value=""):
                                                            installer.play_local("ktx", mode)
                launch.assert_called_once()
                arguments = captured["arguments"]
                self.assertIn(
                    ["+set", "k_defmode", usermode],
                    [arguments[index:index + 3] for index in range(len(arguments) - 2)],
                )
                if "config" in captured:
                    self.assertIn(
                        f'tempalias {event} "exec {entry_config}"', captured["config"],
                    )
                else:
                    self.assertIn(
                        ["+tempalias", event, f"exec {entry_config}"],
                        [arguments[index:index + 3] for index in range(len(arguments) - 2)],
                    )

    def test_ktx_practice_adds_bots_without_waiting_for_a_second_entry(self):
        profile = (
            ROOT / "dist/mods/ktx/1.47/x86qw/config/modes/mode-practice.cfg"
        ).read_text(encoding="utf-8").splitlines()
        commands = [
            line.strip() for line in profile
            if line.strip() and not line.lstrip().startswith("//")
        ]
        self.assertEqual([
            "unalias on_enter",
            "cmd practice",
            "exec x86qw-ktx.cfg",
            "x86qw_ktx_launch_setup",
        ], commands)

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
                                                with mock.patch.object(installer, "verify_local_play_support"):
                                                    with mock.patch("builtins.input", side_effect=["", ""]):
                                                        installer.play_local()
                launch.assert_called_once_with(runtime, [
                    *local_server_baseline(key),
                    "-game", gamedir, "+sv_gamedir", gamedir,
                    "+sv_progtype", "0", *before_map, "+map", map_name, "+wait",
                    *after_wait, "+exec", profile,
                    "+bind", "F12", "quit",
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
                                            with mock.patch.object(installer, "verify_local_play_support"):
                                                with mock.patch("builtins.input", side_effect=["", ""]):
                                                    installer.play_local()
            launch.assert_called_once_with(runtime, [
                *local_server_baseline("team-fortress"),
                "-game", "fortress", "+sv_gamedir", "fortress",
                "+sv_progtype", "0", "+exec", "x86qw-fortress-pre.cfg",
                "+cl_pext_lagteleport", "0",
                "+map", "2fort5r", "+wait",
                "+exec", "x86qw-fortress.cfg",
                "+bind", "F12", "quit",
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

    def test_every_playable_mod_profile_prints_its_bound_key_plan(self):
        expected_gameplay = {
            "ktx": {
                'tempalias sv_enableprofile ""',
                'bind 1 "tp_msgquaddead"',
                'bind 5 "tp_msgenemypwr"',
                'bind q "weapon 6"',
                'bind e "weapon 7"',
                'bind MOUSE2 "weapon 8"',
                'bind MWHEELUP "time_inc"',
                'bind F5 "x86qw_ktx_key_f5"',
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
                    self.assertEqual('bind F12 "quit"', executable_lines[-1])
                    self.assertEqual(user_exec, executable_lines[-2])
                    if game.key != "ktx":
                        self.assertEqual(help_alias, executable_lines[-3])

    def test_module_help_never_prints_raw_console_commands(self):
        for game in play_qw.LOCAL_GAMES:
            if game.key == "ktx":
                profile_path = ROOT / "dist/mods/ktx/1.47/x86qw/config/client.cfg"
            else:
                profile_path = (
                    ROOT / f"dist/mods/{game.key}/{game.version}/x86qw/client.cfg"
                )
            profile = profile_path.read_text(encoding="utf-8")
            help_alias = re.search(
                rf'(?m)^tempalias x86qw_{re.escape(game.profile)}_help "([^"]*)"$',
                profile,
            )
            if game.key == "ktx":
                self.assertNotIn("BOTCMD", (
                    ROOT / "dist/mods/ktx/1.47/x86qw/config/help.cfg"
                ).read_text(encoding="utf-8"))
                continue
            self.assertIsNotNone(help_alias)
            assert help_alias is not None
            self.assertNotIn("Console:", help_alias.group(1))
            self.assertNotIn("impulse ", help_alias.group(1))
            self.assertNotIn("cmd ", help_alias.group(1))

    def test_ktx_replaces_the_raw_nquake_startup_with_contextual_key_help(self):
        ktx = (ROOT / "dist/mods/ktx/1.47/x86qw/config/client.cfg").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("x86qw_ktx_startup", ktx)
        self.assertIn(
            'tempalias x86qw_ktx_mode_help '
            '"x86qw_ktx_key_help;x86qw_ktx_bot_help"',
            ktx,
        )
        for game in play_qw.LOCAL_GAMES:
            if game.key == "ktx":
                continue
            profile = (
                ROOT / f"dist/mods/{game.key}/{game.version}/x86qw/client.cfg"
            ).read_text(encoding="utf-8")
            self.assertNotIn("x86qw_ktx_startup", profile)

    def test_ktx_f10_prints_colored_multiline_controls_and_active_mode(self):
        profile = (ROOT / "dist/mods/ktx/1.47/x86qw/config/client.cfg").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'tempalias ktx_controls "scr_consize 0.8;toggleconsole;exec x86qw-ktx-help.cfg"',
            profile,
        )
        self.assertIn('bind F10 "ktx_controls"', profile)

        help_profile = (ROOT / "dist/mods/ktx/1.47/x86qw/config/help.cfg").read_text(
            encoding="utf-8"
        )
        self.assertIn("ktx_mode", help_profile)
        self.assertIn("x86qw_ktx_mode_help", help_profile)
        visible_help = re.sub(r"\^(.)", r"\1", help_profile).replace("$x20", " ")
        self.assertNotIn("BOTCMD", visible_help)
        self.assertIn("F12", visible_help)
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

    def test_each_ktx_mode_help_prints_bindings_instead_of_raw_commands(self):
        for mode in play_qw.load_ktx_modes(ROOT):
            with self.subTest(mode=mode.key):
                self.assertEqual(
                    "x86qw_ktx_mode_help",
                    play_qw.ktx_mode_help_alias(mode),
                )
                for command, _description in mode.help_commands:
                    self.assertNotIn(command, play_qw.ktx_mode_help_alias(mode))

    def test_each_ktx_mode_declares_and_prints_a_contextual_key_plan(self):
        for mode in play_qw.load_ktx_modes(ROOT):
            with self.subTest(mode=mode.key):
                declared = {key for key, _command, _description in mode.key_bindings}
                self.assertTrue({"F5", "F6", "F11"}.issubset(declared))
                self.assertTrue(declared.issubset(set(play_qw.KTX_CONTEXT_KEYS)))
                aliases = play_qw.ktx_key_alias_commands(
                    mode, play_qw.KtxLaunchOptions(),
                )
                for key, command, description in mode.key_bindings:
                    alias_key = key.casefold()
                    self.assertIn(
                        f"tempalias x86qw_ktx_key_{alias_key} {command}", aliases,
                    )
                    self.assertTrue(any(
                        item.startswith(f"tempalias x86qw_ktx_key_help_{alias_key} echo ")
                        and description in item
                        for item in aliases
                    ))
                self.assertEqual(
                    "x86qw_ktx_mode_help",
                    play_qw.ktx_mode_help_alias(mode),
                )

    def test_bot_sessions_bind_management_keys_and_print_them_on_entry(self):
        duel = next(mode for mode in play_qw.load_ktx_modes(ROOT) if mode.key == "duel")
        aliases = play_qw.ktx_key_alias_commands(
            duel, play_qw.KtxLaunchOptions(bots=1, bot_skill=5),
        )
        self.assertIn(
            'tempalias x86qw_ktx_bot_add_1 "echo Limite de Frogbots deste modo atingido"',
            aliases,
        )
        self.assertIn(
            'tempalias x86qw_ktx_bot_remove_1 "cmd botcmd removebot;x86qw_ktx_bot_roster_0"',
            aliases,
        )
        self.assertIn(
            'tempalias x86qw_ktx_bot_skill_down_5 '
            '"echo Habilidade dos proximos Frogbots: 4;cmd botcmd skill 4;x86qw_ktx_bot_skill_4"',
            aliases,
        )
        self.assertIn(
            'tempalias x86qw_ktx_bot_skill_up_5 '
            '"echo Habilidade dos proximos Frogbots: 6;cmd botcmd skill 6;x86qw_ktx_bot_skill_6"',
            aliases,
        )
        self.assertIn("x86qw_ktx_bot_roster_1", aliases)
        self.assertIn(
            "tempalias x86qw_ktx_bot_help exec x86qw-ktx-help-bots.cfg", aliases,
        )
        skin_overrides = (
            "teamskin $qt$qt", "enemyskin $qt$qt", "teamcolor off",
            "enemycolor off", "r_teamskincolor $qt$qt", "r_enemyskincolor $qt$qt",
        )
        for command in skin_overrides:
            self.assertNotIn(command, aliases)
        real_server_aliases = play_qw.ktx_key_alias_commands(
            duel, play_qw.KtxLaunchOptions(),
        )
        for command in skin_overrides:
            self.assertNotIn(command, real_server_aliases)
        profile = (ROOT / "dist/mods/ktx/1.47/x86qw/config/client.cfg").read_text(
            encoding="utf-8",
        )
        for key, alias in {
            "INS": "x86qw_ktx_bot_add",
            "DEL": "x86qw_ktx_bot_remove",
            "HOME": "x86qw_ktx_bot_skill_down",
            "END": "x86qw_ktx_bot_skill_up",
        }.items():
            self.assertIn(f'bind {key} "{alias}"', profile)

        ffa = next(mode for mode in play_qw.load_ktx_modes(ROOT) if mode.key == "ffa")
        maximum = play_qw.KtxLaunchOptions(
            bots=31,
            bot_name_pool=play_qw.load_x86qw_frogbot_names(ROOT),
        )
        commands = (
            *play_qw.ktx_launch_commands(
                ffa, "dm6", frozenset({"bots/maps/dm6.bot"}), maximum,
            ),
            "x86qw_ktx_mode_help",
        )
        setup_aliases = play_qw.ktx_chunked_setup_alias_commands(commands)
        self.assertGreater(len(setup_aliases), 2)
        self.assertTrue(all(len(command) < 800 for command in setup_aliases))
        self.assertTrue(
            "x86qw_ktx_launch_setup_1" in setup_aliases[-1]
            or "x86qw_ktx_launch_group_" in setup_aliases[-1]
        )

        two_on_two = next(
            mode for mode in play_qw.load_ktx_modes(ROOT) if mode.key == "2on2"
        )
        team_aliases = play_qw.ktx_bot_management_alias_commands(
            two_on_two,
            play_qw.KtxLaunchOptions(bots=1, bot_skill=5, bot_team="red"),
        )
        self.assertIn(
            'tempalias x86qw_ktx_bot_add_1 '
            '"x86qw_ktx_bot_add_command;x86qw_ktx_bot_roster_2"',
            team_aliases,
        )
        self.assertIn(
            'tempalias x86qw_ktx_bot_skill_5 '
            '"tempalias x86qw_ktx_bot_skill_down x86qw_ktx_bot_skill_down_5;'
            'tempalias x86qw_ktx_bot_skill_up x86qw_ktx_bot_skill_up_5;'
            'tempalias x86qw_ktx_bot_add_command cmd botcmd addbot 5 red"',
            team_aliases,
        )
        self.assertIn(
            'tempalias x86qw_ktx_bot_skill_6 '
            '"tempalias x86qw_ktx_bot_skill_down x86qw_ktx_bot_skill_down_6;'
            'tempalias x86qw_ktx_bot_skill_up x86qw_ktx_bot_skill_up_6;'
            'tempalias x86qw_ktx_bot_add_command cmd botcmd addbot 6 red"',
            team_aliases,
        )
        self.assertIn(
            'tempalias x86qw_ktx_bot_add_3 '
            '"echo Limite de Frogbots deste modo atingido"',
            team_aliases,
        )

        random_aliases = play_qw.ktx_bot_management_alias_commands(
            duel, play_qw.KtxLaunchOptions(bots=1, bot_skill="random"),
        )
        self.assertIn(
            'tempalias x86qw_ktx_bot_add_command "cmd botcmd addbot random"',
            random_aliases,
        )
        self.assertIn(
            'tempalias x86qw_ktx_bot_skill_up '
            '"echo Habilidade aleatoria ativa para cada novo Frogbot"',
            random_aliases,
        )

    def test_ktx_help_files_stay_below_the_console_line_limit(self):
        profiles = [
            ROOT / "dist/mods/ktx/1.47/x86qw/config/client.cfg",
            *sorted((ROOT / "dist/mods/ktx/1.47/x86qw/config").glob("help*.cfg")),
        ]
        for profile in profiles:
            with self.subTest(profile=profile.name):
                lines = profile.read_text(encoding="utf-8").splitlines()
                self.assertLessEqual(max(map(len, lines)), 512)

    def test_each_mod_profile_is_isolated_from_every_other_mod(self):
        profiles = {}
        for game in play_qw.LOCAL_GAMES:
            relative = (
                "x86qw/config/client.cfg" if game.key == "ktx"
                else "x86qw/client.cfg"
            )
            path = ROOT / f"dist/mods/{game.key}/{game.version}" / relative
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
        profile = (ROOT / "dist/mods/ktx/1.47/x86qw/config/client.cfg").read_text(encoding="utf-8")

        def bindings(payload: str) -> dict[str, str]:
            return dict(re.findall(r'(?m)^bind\s+(\S+)\s+"([^"]*)"', payload))

        nquake_bindings = bindings(nquake)
        profile_bindings = bindings(profile)
        restored = {
            "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
            "c", "e", "f", "g", "q", "r", "t", "v",
            "ALT", "CTRL", "SHIFT",
            "MOUSE1", "MOUSE2", "MOUSE3", "MOUSE4", "MOUSE5",
            "MWHEELUP", "MWHEELDOWN",
            "F1", "F2", "F3", "F4", "F7", "F8", "F9",
        }
        self.assertEqual(
            {key: nquake_bindings[key] for key in restored},
            {key: profile_bindings[key] for key in restored},
        )
        self.assertEqual("", nquake_bindings["F10"])
        self.assertEqual("ktx_controls", profile_bindings["F10"])
        self.assertEqual("x86qw_ktx_key_f5", profile_bindings["F5"])
        self.assertEqual("x86qw_ktx_key_f6", profile_bindings["F6"])
        self.assertEqual("x86qw_ktx_key_f11", profile_bindings["F11"])
        for key in ("h", "i", "m", "x", "z"):
            self.assertEqual(f"x86qw_ktx_key_{key}", profile_bindings[key])
        user_example = (
            ROOT / "dist/mods/ktx/1.47/x86qw/config/user.cfg.example"
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
            id1 = target / "id1"
            id1.mkdir()
            payload = b"bsp"
            member = b"maps/dm6.bsp".ljust(56, b"\0") + struct.pack("<II", 12, len(payload))
            (id1 / "pak0.pak").write_bytes(
                b"PACK" + struct.pack("<II", 12 + len(payload), len(member)) + payload + member
            )
            self.assertEqual(["custom", "dm6", "zipmap"], installer.local_map_names("td2"))
            with zipfile.ZipFile(target / "td2/addon.pk3", "w") as archive:
                archive.writestr("maps/zipmap.bsp", b"bsp")
                archive.writestr("../maps/escape.bsp", b"bad")
            with self.assertRaisesRegex(play_qw.InstallerError, "Pacote de mapas inválido"):
                installer.local_map_names("td2")

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

    def test_hub_menu_reviews_server_action_and_client_before_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            server = {
                "address": "server.example:27500", "mode": "duel", "players": [],
                "settings": {"map": "dm6", "hostname": "Servidor Teste"},
                "qtv_stream": {"url": "2@qtv.example:28000"},
            }
            runtime = target / "client"
            with mock.patch.object(
                installer, "hub_servers", return_value=[server],
            ), mock.patch.object(
                install_qw.navigation, "supports_navigation", return_value=True,
            ), mock.patch.object(
                install_qw.navigation, "select_one", side_effect=("0", "qtv"),
            ), mock.patch.object(
                installer, "choose_host_runtime", return_value=("ezQuake stable", runtime),
            ), mock.patch.object(
                install_qw.navigation, "confirm", return_value=False,
            ) as confirm, mock.patch.object(installer, "launch_runtime") as launch:
                installer.browse_hub()
            launch.assert_not_called()
            rendered = confirm.call_args.kwargs["subtitle"]
            for value in (
                "Resumo da conexão", "Servidor Teste", "server.example:27500",
                "Assistir pelo QTV", "ezQuake stable", "2@qtv.example:28000",
            ):
                self.assertIn(value, rendered)

    def test_hub_confirmation_left_returns_to_client_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            server = {
                "address": "server.example:27500", "mode": "duel", "players": [],
                "settings": {"map": "dm6", "hostname": "Servidor Teste"},
            }
            runtime = target / "client"
            with mock.patch.object(
                installer, "hub_servers", return_value=[server],
            ), mock.patch.object(
                install_qw.navigation, "supports_navigation", return_value=True,
            ), mock.patch.object(
                install_qw.navigation, "select_one", side_effect=("0", "join"),
            ), mock.patch.object(
                installer, "choose_host_runtime", return_value=("ezQuake stable", runtime),
            ) as choose_runtime, mock.patch.object(
                install_qw.navigation, "confirm", side_effect=(None, True),
            ), mock.patch.object(installer, "launch_runtime") as launch:
                installer.browse_hub()
            self.assertEqual(2, choose_runtime.call_count)
            launch.assert_called_once_with(runtime, ["+join", "server.example:27500"])

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
            self.assertFalse((target / ".x86qw").exists())
            self.assertNotIn("Os componentes x86QW não estão instalados", output.getvalue())


if __name__ == "__main__":
    unittest.main()
