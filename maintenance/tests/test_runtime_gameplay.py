import importlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads(
    (ROOT / "maintenance/tests/fixtures/gameplay-command-golden.json").read_text(
        encoding="utf-8",
    )
)

MANAGER_SPEC = importlib.util.spec_from_file_location(
    "manager", ROOT / "dist/installer/bin/manager.py",
)
manager = importlib.util.module_from_spec(MANAGER_SPEC)
assert MANAGER_SPEC.loader is not None
sys.modules[MANAGER_SPEC.name] = manager
MANAGER_SPEC.loader.exec_module(manager)

GAMEPLAY_SPEC = importlib.util.spec_from_file_location(
    "gameplay_runtime_facade", ROOT / "dist/installer/bin/gameplay.py",
)
gameplay = importlib.util.module_from_spec(GAMEPLAY_SPEC)
assert GAMEPLAY_SPEC.loader is not None
sys.modules[GAMEPLAY_SPEC.name] = gameplay
GAMEPLAY_SPEC.loader.exec_module(gameplay)


def canonical_gameplay():
    spec = importlib.util.find_spec("x86qw_runtime.gameplay")
    if spec is None:
        raise AssertionError("x86qw_runtime.gameplay ainda não existe")
    return importlib.import_module("x86qw_runtime.gameplay")


class RuntimeGameplayExtractionTests(unittest.TestCase):
    def test_facade_reexports_canonical_type_and_planner_identities(self):
        canonical = canonical_gameplay()

        for name in (
            "LocalGameSpec",
            "KtxModeSpec",
            "KtxMapRequirement",
            "KtxMenuGroupSpec",
            "FrogbotIdentity",
            "KtxLaunchOptions",
            "ktx_launch_commands",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(canonical, name), getattr(gameplay, name))
        self.assertTrue(issubclass(gameplay.Player, manager.Installer))

    def test_pure_catalog_parsers_preserve_games_modes_and_groups(self):
        canonical = canonical_gameplay()
        games_document = json.loads(
            (ROOT / "maintenance/inventory/games.json").read_text(encoding="utf-8")
        )
        modes_document = json.loads(
            (ROOT / "dist/mods/ktx/1.47/x86qw/catalog/modes.json").read_text(
                encoding="utf-8",
            )
        )

        games = canonical.parse_local_games(games_document)
        modes = canonical.parse_ktx_modes(modes_document)
        groups = canonical.parse_ktx_menu_groups(modes_document, modes)

        self.assertEqual(tuple(gameplay.load_local_games(ROOT)), games)
        self.assertEqual(tuple(gameplay.load_ktx_modes(ROOT)), modes)
        self.assertEqual(tuple(gameplay.load_ktx_menu_groups(ROOT)), groups)
        self.assertEqual(FIXTURE["mode_ids"], [mode.key for mode in modes])

    def test_all_modes_preserve_default_command_goldens(self):
        canonical = canonical_gameplay()
        document = json.loads(
            (ROOT / "dist/mods/ktx/1.47/x86qw/catalog/modes.json").read_text(
                encoding="utf-8",
            )
        )
        modes = canonical.parse_ktx_modes(document)

        actual = {}
        for mode in modes:
            assets = frozenset(
                requirement.asset.replace("{map}", mode.default_map.casefold()).casefold()
                for requirement in mode.map_requirements
            )
            actual[mode.key] = list(canonical.ktx_launch_commands(
                mode, mode.default_map, assets, canonical.KtxLaunchOptions(),
            ))

        self.assertEqual(FIXTURE["default_commands"], actual)

    def test_representative_mode_plans_preserve_command_goldens(self):
        canonical = canonical_gameplay()
        document = json.loads(
            (ROOT / "dist/mods/ktx/1.47/x86qw/catalog/modes.json").read_text(
                encoding="utf-8",
            )
        )
        modes = {mode.key: mode for mode in canonical.parse_ktx_modes(document)}
        scenarios = {
            "duel_bot": (
                modes["duel"], "dm6", canonical.KtxLaunchOptions(bots=1, bot_skill=12),
            ),
            "two_on_two_team_bots": (
                modes["2on2"], "dm6",
                canonical.KtxLaunchOptions(bots=2, bot_skill=12, bot_team="red"),
            ),
            "tot_bot_controls": (
                modes["tot"], "dm4",
                canonical.KtxLaunchOptions(
                    bots=1, bot_skill=12, bot_weapon="8", bot_health=200,
                    bot_break_on_death=False,
                ),
            ),
            "ctf_rules": (
                modes["ctf"], "e2m2",
                canonical.KtxLaunchOptions(
                    ctf_hook="smooth", ctf_runes="off", ctf_based_spawn=True,
                ),
            ),
            "race_rules": (
                modes["race"], "dm6",
                canonical.KtxLaunchOptions(
                    race_style="match", race_scoring="formula1",
                    race_pacemaker=3, race_hide_players=True,
                ),
            ),
        }

        actual = {}
        for name, (mode, map_name, options) in scenarios.items():
            assets = frozenset(
                requirement.asset.replace("{map}", map_name.casefold()).casefold()
                for requirement in mode.map_requirements
            )
            actual[name] = list(canonical.ktx_launch_commands(
                mode, map_name, assets, options,
            ))

        self.assertEqual(FIXTURE["scenarios"], actual)

    def test_planner_preserves_validation_messages(self):
        canonical = canonical_gameplay()
        document = json.loads(
            (ROOT / "dist/mods/ktx/1.47/x86qw/catalog/modes.json").read_text(
                encoding="utf-8",
            )
        )
        modes = {mode.key: mode for mode in canonical.parse_ktx_modes(document)}
        cases = {
            "missing_frogbot_route": (
                modes["duel"], "dm2", frozenset(),
                canonical.KtxLaunchOptions(bots=1),
            ),
            "ctf_option_on_duel": (
                modes["duel"], "dm6", frozenset({"bots/maps/dm6.bot"}),
                canonical.KtxLaunchOptions(ctf_hook="off"),
            ),
            "bots_on_race": (
                modes["race"], "dm6",
                frozenset({"bots/maps/dm6.bot", "race/routes/dm6.route"}),
                canonical.KtxLaunchOptions(bots=1),
            ),
        }

        actual = {}
        for name, arguments in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(canonical.InstallerError) as raised:
                    canonical.ktx_launch_commands(*arguments)
                actual[name] = str(raised.exception)

        self.assertEqual(FIXTURE["errors"], actual)


if __name__ == "__main__":
    unittest.main()
