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
SPEC = importlib.util.spec_from_file_location(
    "x86qw_menu_test", ROOT / "x86qw_runtime/ui/menu.py",
)
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

    def test_canonical_no_color_fallback_matches_the_golden_output(self):
        canonical_spec = importlib.util.find_spec("x86qw_runtime.ui")
        self.assertIsNotNone(
            canonical_spec,
            "the portable menu engine must be owned by x86qw_runtime.ui",
        )
        canonical = importlib.import_module("x86qw_runtime.ui.menu")
        canonical.configure(no_color=True)
        options = (
            canonical.MenuOption(
                "duel", "Duel", "2 jogadores", "competitivo", aliases=("1on1",),
            ),
            canonical.MenuOption("race", "Race", "1+ jogadores", "rotas cronometradas"),
            canonical.MenuOption(
                "ctf", "Capture The Flag", "4+ jogadores", "duas bandeiras",
            ),
        )
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            selected = canonical.select_one(
                "Modo", options, interactive=False, input_fn=lambda _prompt: "1on1",
                searchable=True,
            )

        self.assertEqual("duel", selected)
        self.assertEqual(
            "\nModo\n"
            "  1) Duel (padrão)    | 2 jogadores  < competitivo\n"
            "  2) Race             | 1+ jogadores < rotas cronometradas\n"
            "  3) Capture The Flag | 4+ jogadores < duas bandeiras\n",
            output.getvalue(),
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

    def test_fallback_can_clear_a_refined_search(self):
        options = (
            menu.MenuOption("one", "Primeiro", "arena competitiva"),
            menu.MenuOption("two", "Segundo", "arena alternativa"),
            menu.MenuOption("three", "Terceiro", "corrida"),
        )
        answers = iter(("arena", "/", "3"))
        prompts = []

        def answer(prompt):
            prompts.append(prompt)
            return next(answers)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            selected = menu.select_one(
                "Modo", options, searchable=True, interactive=False,
                input_fn=answer,
            )
        self.assertEqual("three", selected)
        self.assertTrue(any("/ para limpar a busca" in prompt for prompt in prompts))

    def test_selected_row_is_colored_and_keeps_its_explanation_inline(self):
        output = TtyStringIO()
        with mock.patch.object(menu, "_NO_COLOR", False):
            with mock.patch.object(menu.sys, "stdout", output):
                selected = menu.select_one(
                    "Modo", self.options, interactive=True, key_reader=lambda: "enter",
                )
        self.assertEqual("duel", selected)
        self.assertIn(
            "\033[1;36m› 1) Duel             | 2 jogadores \033[0m"
            "\033[2m < competitivo\033[0m",
            output.getvalue(),
        )
        self.assertIn(" | 1+ jogadores", output.getvalue())
        self.assertNotIn("\033[0m | 2 jogadores", output.getvalue())
        self.assertNotIn("\033[2m |", output.getvalue())
        self.assertNotIn("\n    competitivo", output.getvalue())

    def test_navigation_numbers_every_item_and_documents_both_horizontal_arrows(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            selected = menu.select_one(
                "Modo", self.options, interactive=True, key_reader=lambda: "right",
                allow_back=True,
            )
        self.assertEqual("duel", selected)
        rendered = output.getvalue()
        self.assertIn("› 1) Duel", rendered)
        self.assertIn("  2) Race", rendered)
        self.assertIn("  3) Capture The Flag", rendered)
        self.assertIn(
            "↑↓ navegar   →/Enter selecionar   ← voltar   Esc Sair.", rendered,
        )

    def test_left_arrow_returns_to_the_immediate_parent(self):
        with contextlib.redirect_stdout(io.StringIO()):
            selected = menu.select_one(
                "Filho", self.options, interactive=True, key_reader=lambda: "left",
                allow_back=True,
            )
        self.assertIsNone(selected)

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
        self.assertIn("1) [✓]", lines[0])
        self.assertIn("2 jogadores  < competitivo", lines[0])

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

    def test_confirmation_keeps_the_plan_visible_on_the_same_screen(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            accepted = menu.confirm(
                "Iniciar esta partida?",
                breadcrumb="x86QW › Jogar › KTX › Duel › Confirmação",
                subtitle="\nResumo da partida\n  Modo    | Duel\n  Mapa    | dm6\n  Bots    | 1",
                description="abrir o jogo com as escolhas acima",
                default=False,
                interactive=False,
                input_fn=lambda _: "",
            )
        self.assertFalse(accepted)
        rendered = output.getvalue()
        self.assertIn("Resumo da partida", rendered)
        self.assertIn("Modo    | Duel", rendered)
        self.assertIn("Mapa    | dm6", rendered)
        self.assertIn("Bots    | 1", rendered)
        self.assertLess(rendered.index("Resumo da partida"), rendered.index("1) Sim"))

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
                import termios
                import tty

                original_attributes = termios.tcgetattr(terminal_input.fileno())
                try:
                    # Make the PTY input mode deterministic before the writer can
                    # queue bytes; otherwise a slow macOS runner can leave the
                    # carriage return in canonical mode and block the read.
                    tty.setraw(terminal_input.fileno(), when=termios.TCSANOW)
                    writer.start()
                    with mock.patch.object(menu.sys, "stdin", terminal_input):
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertTrue(menu.confirm("Confirmar?", default=False, interactive=True))
                    writer.join(timeout=1)
                finally:
                    termios.tcsetattr(
                        terminal_input.fileno(), termios.TCSANOW, original_attributes,
                    )
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

    def test_navigation_escape_exits_instead_of_returning_to_parent(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(menu.MenuExit):
                menu.select_one(
                    "Modo", self.options, interactive=True, key_reader=lambda: "escape",
                    allow_back=True,
                )

    def test_navigation_selects_multi_digit_number_after_enter(self):
        options = tuple(
            menu.MenuOption(str(index), f"Opção {index}")
            for index in range(1, 13)
        )
        keys = iter(("1", "0", "enter"))
        with contextlib.redirect_stdout(io.StringIO()):
            selected = menu.select_one(
                "Opções", options, interactive=True,
                key_reader=lambda: next(keys),
            )
        self.assertEqual("10", selected)

    def test_navigation_shows_disabled_option_and_skips_it(self):
        options = (
            menu.MenuOption("join", "Jogar"),
            menu.MenuOption(
                "qtv", "Assistir pelo QTV", enabled=False,
                disabled_reason="servidor sem stream",
            ),
            menu.MenuOption("observe", "Observar"),
        )
        keys = iter(("down", "enter"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            selected = menu.select_one(
                "Entrar", options, interactive=True,
                key_reader=lambda: next(keys),
            )
        self.assertEqual("observe", selected)
        self.assertIn("indisponível: servidor sem stream", output.getvalue())

    def test_narrow_terminal_wraps_rows_and_footer_within_width(self):
        output = io.StringIO()
        with mock.patch.object(
            menu.shutil, "get_terminal_size", return_value=os.terminal_size((32, 24)),
        ), contextlib.redirect_stdout(output):
            menu.select_one(
                "Modo", self.options, interactive=True, key_reader=lambda: "enter",
                allow_back=True,
            )
        rendered = [
            line for line in output.getvalue().replace("\033[2J\033[H", "").splitlines()
            if line
        ]
        self.assertLessEqual(max(map(len, rendered)), 32)

    def test_narrow_terminal_limits_visible_rows_and_reports_the_window(self):
        options = tuple(
            menu.MenuOption(
                str(index), f"Opção longa {index}",
                "descrição longa que precisa quebrar",
                "explicação contextual completa",
            )
            for index in range(1, 21)
        )
        output = io.StringIO()
        with mock.patch.object(
            menu.shutil, "get_terminal_size", return_value=os.terminal_size((32, 18)),
        ), contextlib.redirect_stdout(output):
            menu.select_one(
                "Opções", options, interactive=True,
                key_reader=lambda: "enter", allow_back=True,
            )
        rendered = output.getvalue().replace("\033[2J\033[H", "")
        self.assertIn("Exibindo 1–", rendered)
        self.assertIn("de 20.", rendered)
        self.assertLessEqual(len(rendered.splitlines()), 18)

    def test_fallback_multiple_selection_retries_invalid_input(self):
        answers = iter(("99", "3, 1on1"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            selected = menu.select_many(
                "Modos", self.options, interactive=False,
                input_fn=lambda _: next(answers),
            )
        self.assertEqual(("duel", "ctf"), selected)
        self.assertIn("Seleção inválida", output.getvalue())

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
