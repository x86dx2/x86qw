import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from maintenance.tools.build_installer_bundle import zipapp_bytes
from maintenance.tools.components import load_catalog as load_component_catalog
from maintenance.tools.runtime_catalog import (
    games_by_id,
    load_inventory,
    runtimes_by_id,
    validate_ktx_mode_catalog,
    validate_inventory,
)


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "maintenance/inventory"


class RuntimeCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.components = load_component_catalog(INVENTORY / "components.json")
        cls.public = json.loads((ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8"))
        cls.inventory = load_inventory(
            INVENTORY,
            component_catalog=cls.components,
            project_root=ROOT,
            public_catalog=cls.public,
        )

    def validate(self, documents):
        validate_inventory(
            documents["capabilities"], documents["runtimes"], documents["games"],
            documents["compatibility"], component_catalog=self.components,
        )

    def test_inventory_models_only_the_current_five_runtimes_and_games(self):
        self.assertEqual(
            {"ezquake-stable", "ezquake-nightly", "mvdsv", "qtv", "qwfwd"},
            set(runtimes_by_id(self.inventory["runtimes"])),
        )
        self.assertEqual(
            {"ktx", "final-arena", "pro-x", "team-fortress", "td2"},
            set(games_by_id(self.inventory["games"])),
        )

    def test_service_platforms_are_explicit_and_exclude_macos_intel(self):
        runtimes = runtimes_by_id(self.inventory["runtimes"])
        expected = {
            ("macos", "arm64", "macos-arm64"),
            ("linux", "amd64", "linux-amd64"),
            ("windows", "x64", "windows-x64"),
        }
        for identifier in ("mvdsv", "qtv", "qwfwd"):
            actual = {
                (entry["system"], entry["architecture"], entry["variant"])
                for entry in runtimes[identifier]["platforms"]
            }
            self.assertEqual(expected, actual)
            self.assertNotIn(("macos", "x86_64", "macos-intel"), actual)

    def test_runtime_compatibility_is_not_duplicated_by_the_client_content_baseline(self):
        legacy = self.components["compatibility"]
        self.assertEqual("ezquake-client-content", legacy["scope"])
        self.assertTrue({"mvdsv", "qtv", "qwfwd"}.isdisjoint(legacy["covered_components"]))
        runtime_entries = {
            (entry["kind"], entry["runtime"])
            for entry in self.inventory["compatibility"]["compatibility"]
        }
        self.assertIn(("server", "mvdsv"), runtime_entries)
        self.assertIn(("service", "qtv"), runtime_entries)
        self.assertIn(("service", "qwfwd"), runtime_entries)

    def test_invalid_protocol_cycle_and_personal_runtime_payload_fail(self):
        for mutation, message in (
            (lambda docs: docs["games"]["games"][0].__setitem__("protocol", "unknown"), "protocol"),
            (lambda docs: docs["runtimes"]["runtimes"][0].__setitem__("dependencies", ["ezquake-stable"]), "dependency"),
            (lambda docs: docs["runtimes"]["runtimes"][2].__setitem__("personal_configuration", ["mvdsv"]), "personal configuration"),
        ):
            documents = copy.deepcopy(self.inventory)
            mutation(documents)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.validate(documents)

    def test_personal_frogbot_list_must_be_a_preserved_component_source(self):
        documents = copy.deepcopy(self.inventory)
        games_by_id(documents["games"])["ktx"]["bot_names_personal_config"] = (
            "qw/unmanaged-bot-names.json"
        )
        with self.assertRaisesRegex(ValueError, "personal bot names config"):
            self.validate(documents)

    def test_ktx_modes_are_cross_checked_against_real_map_assets(self):
        path = ROOT / "dist/mods/ktx/1.47/x86qw/catalog/modes.json"
        catalog = json.loads(path.read_text(encoding="utf-8"))
        validate_ktx_mode_catalog(ROOT, self.components, mode_catalog=catalog)

        cases = (
            ("duel", "default_map", "dm2", "default lacks Frogbot route"),
            ("ctf", "suggested_maps", ["e2m2", "dm6"], "CTF suggestions"),
            ("race", "suggested_maps", ["dm6"], "Race suggestions"),
            ("tot", "suggested_maps", ["dm4", "dm6"], "ToT suggestions"),
            ("duel", "usermode", "missing-mode", "missing usermode"),
            (
                "duel", "help_commands",
                [["cmd missing_command", "comando ausente"]],
                "missing server command",
            ),
            (
                "midair", "entry_config", "x86qw-ktx-mode-missing.cfg",
                "missing entry config",
            ),
        )
        for mode_id, field, value, message in cases:
            mutated = copy.deepcopy(catalog)
            mode = next(item for item in mutated["modes"] if item["id"] == mode_id)
            mode[field] = value
            if field == "default_map":
                mode["suggested_maps"] = [value]
            with self.subTest(mode=mode_id), self.assertRaisesRegex(ValueError, message):
                validate_ktx_mode_catalog(
                    ROOT, self.components, mode_catalog=mutated,
                )

    def test_fixture_game_can_be_added_without_editing_python(self):
        documents = copy.deepcopy(self.inventory)
        fixture = copy.deepcopy(documents["games"]["games"][-1])
        fixture.update({
            "id": "fixture-game",
            "label": "Fixture Game",
            "profile": "fixture-game",
            "smoke_test": "fixture-game-smoke",
        })
        documents["games"]["games"].append(fixture)
        for entry in documents["compatibility"]["compatibility"]:
            if entry["kind"] in {"client", "server"}:
                entry["games"].append("fixture-game")
        self.validate(documents)
        self.assertIn("fixture-game", games_by_id(documents["games"]))

    def test_fixture_runtime_platform_can_be_added_without_editing_python(self):
        documents = copy.deepcopy(self.inventory)
        capabilities = documents["capabilities"]
        capabilities["architectures"].append("riscv64")
        capabilities["architecture_aliases"]["riscv64"] = ["riscv64"]
        capabilities["platform_labels"]["linux-riscv64"] = "Linux riscv64"
        runtime = runtimes_by_id(documents["runtimes"])["qtv"]
        platform = copy.deepcopy(runtime["platforms"][1])
        platform.update({
            "architecture": "riscv64",
            "variant": "linux-riscv64",
            "runtime_path": "qtv/qtv-riscv64",
        })
        runtime["platforms"].append(platform)
        runtime["architectures"].append("riscv64")
        runtime["executable"]["linux-riscv64"] = "qtv"
        runtime["runtime_path"]["linux-riscv64"] = platform["runtime_path"]
        self.validate(documents)

    def test_runtime_support_contract_accepts_conditional_but_rejects_unknown_claims(self):
        documents = copy.deepcopy(self.inventory)
        stable = runtimes_by_id(documents["runtimes"])["ezquake-stable"]
        macos = next(
            platform for platform in stable["platforms"]
            if platform["system"] == "macos"
        )
        macos["support"] = "conditional"
        try:
            self.validate(documents)
        except ValueError as error:
            self.fail(str(error))

        macos["support"] = "complete"
        with self.assertRaisesRegex(ValueError, "support"):
            self.validate(documents)

    def test_installer_zipapp_contains_only_runtime_projections(self):
        with zipfile.ZipFile(io.BytesIO(zipapp_bytes("0.1.25"))) as archive:
            names = set(archive.namelist())
            for name in (
                "_x86qw/runtimes.json", "_x86qw/games.json",
                "_x86qw/capabilities.json", "_x86qw/compatibility.json",
                "_x86qw/ktx-modes.json", "_x86qw/ktx-frogbot-names.json",
            ):
                self.assertIn(name, names)
            for name in names:
                if name.startswith("catalog/") and name.endswith(".json"):
                    document = json.loads(archive.read(name))
                    serialized = json.dumps(document).casefold()
                    self.assertNotIn("https://", serialized)
                    self.assertNotIn("source-patch", serialized)

    def test_static_command_contract_matches_pre_refactor_golden(self):
        golden = json.loads(
            (ROOT / "maintenance/tests/fixtures/runtime-command-golden.json").read_text(encoding="utf-8")
        )
        games = games_by_id(self.inventory["games"])
        for identifier, expected in golden["games"].items():
            actual = {field: games[identifier][field] for field in expected}
            self.assertEqual(expected, actual, identifier)
        runtimes = runtimes_by_id(self.inventory["runtimes"])
        for identifier, expected in golden["services"].items():
            self.assertEqual(expected, runtimes[identifier]["arguments"]["base"])


if __name__ == "__main__":
    unittest.main()
