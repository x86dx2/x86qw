from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("x86qw_menu_test", ROOT / "dist/installer/bin/menu.py")
menu = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = menu
SPEC.loader.exec_module(menu)


class MenuTests(unittest.TestCase):
    def setUp(self):
        menu.configure(no_color=True)
        self.options = (
            menu.MenuOption("duel", "Duel", "2 jogadores", "competitivo", aliases=("1on1",)),
            menu.MenuOption("race", "Race", "1+ jogadores", "rotas cronometradas"),
            menu.MenuOption("ctf", "Capture The Flag", "4+ jogadores", "duas bandeiras"),
        )

    def test_fallback_accepts_alias_and_aligns_details(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            selected = menu.select_one(
                "Modo", self.options, interactive=False, input_fn=lambda _: "1on1",
                searchable=True,
            )
        self.assertEqual("duel", selected)
        lines = [line for line in output.getvalue().splitlines() if ")" in line]
        self.assertEqual(1, len({line.index(option.detail) for line, option in zip(lines, self.options)}))

    def test_navigation_supports_arrows_and_search(self):
        keys = iter(("down", "enter"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                "race",
                menu.select_one(
                    "Modo", self.options, interactive=True, key_reader=lambda: next(keys),
                ),
            )
        keys = iter(("/", "c", "t", "f", "enter", "enter"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                "ctf",
                menu.select_one(
                    "Modo", self.options, interactive=True, key_reader=lambda: next(keys),
                    searchable=True,
                ),
            )

    def test_navigation_escape_returns_to_parent(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(menu.select_one(
                "Modo", self.options, interactive=True, key_reader=lambda: "escape",
                allow_back=True,
            ))

    def test_multiple_selection_uses_space_and_preserves_catalog_order(self):
        keys = iter((" ", "down", "down", " ", "enter"))
        with contextlib.redirect_stdout(io.StringIO()):
            selected = menu.select_many(
                "Modos", self.options, interactive=True, key_reader=lambda: next(keys),
            )
        self.assertEqual(("duel", "ctf"), selected)
        with contextlib.redirect_stdout(io.StringIO()):
            selected = menu.select_many(
                "Modos", self.options, interactive=False, input_fn=lambda _: "3, 1on1",
            )
        self.assertEqual(("duel", "ctf"), selected)


if __name__ == "__main__":
    unittest.main()
