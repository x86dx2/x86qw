from __future__ import annotations

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("x86qw_menu_test", ROOT / "dist/installer/bin/menu.py")
menu = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = menu
SPEC.loader.exec_module(menu)


class TtyStringIO(io.StringIO):
    def isatty(self):
        return True


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
        self.assertEqual(1, len({line.index("|") for line in lines}))
        for line, option in zip(lines, self.options):
            self.assertIn(option.detail, line)

    def test_selected_row_is_colored_and_keeps_its_explanation_inline(self):
        output = TtyStringIO()
        with mock.patch.object(menu, "_NO_COLOR", False):
            with mock.patch.object(menu.sys, "stdout", output):
                selected = menu.select_one(
                    "Modo", self.options, interactive=True, key_reader=lambda: "enter",
                )
        self.assertEqual("duel", selected)
        self.assertIn(
            "\033[1;36m› Duel             | 2 jogadores — competitivo\033[0m",
            output.getvalue(),
        )
        self.assertNotIn("\n    competitivo", output.getvalue())

    def test_multiple_menu_aligns_description_and_keeps_active_detail_inline(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            selected = menu.select_many(
                "Modos", self.options, selected=("duel",), interactive=True,
                key_reader=lambda: "enter",
            )
        self.assertEqual(("duel",), selected)
        lines = [line for line in output.getvalue().splitlines() if "[" in line and "|" in line]
        self.assertEqual(1, len({line.index("|") for line in lines}))
        self.assertIn("2 jogadores — competitivo", lines[0])

    def test_confirmation_describes_yes_and_no_in_aligned_columns(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            accepted = menu.confirm(
                "Executar?", description="aplicar as alterações", default=False,
                interactive=False, input_fn=lambda _: "",
            )
        self.assertFalse(accepted)
        lines = [line for line in output.getvalue().splitlines() if "|" in line]
        self.assertEqual(2, len(lines))
        self.assertEqual(1, len({line.index("|") for line in lines}))
        self.assertIn("Sim", lines[0])
        self.assertIn("| aplicar as alterações", lines[0])
        self.assertIn("Não (padrão)", lines[1])
        self.assertIn("| não executar esta ação", lines[1])

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

    def test_posix_confirmation_accepts_ss3_arrow_and_queued_enter(self):
        if os.name == "nt" or not hasattr(os, "openpty"):
            self.skipTest("PTY POSIX indisponível nesta plataforma")
        master, slave = os.openpty()
        writer_error = []

        def write_keys():
            try:
                time.sleep(0.02)
                os.write(master, b"\x1bOA\r")
            except OSError as error:
                writer_error.append(error)

        writer = threading.Thread(target=write_keys)
        try:
            with os.fdopen(slave, "r", encoding="utf-8") as terminal_input:
                writer.start()
                with mock.patch.object(menu.sys, "stdin", terminal_input):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertTrue(menu.confirm("Confirmar?", default=False, interactive=True))
                writer.join(timeout=1)
        finally:
            if writer.is_alive():
                writer.join(timeout=1)
            os.close(master)
        self.assertFalse(writer.is_alive())
        self.assertEqual([], writer_error)

    def test_posix_escape_decoder_accepts_csi_modifiers_and_rejects_unknown_sequences(self):
        self.assertEqual("up", menu._decode_posix_escape(b"[A"))
        self.assertEqual("down", menu._decode_posix_escape(b"[1;2B"))
        self.assertEqual("left", menu._decode_posix_escape(b"OD"))
        self.assertEqual("unknown", menu._decode_posix_escape(b"[200~"))

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
