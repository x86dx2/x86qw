from __future__ import annotations

import importlib
import io
import os
import unittest
from unittest import mock


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class PolishedUiTests(unittest.TestCase):
    def setUp(self) -> None:
        package = importlib.reload(importlib.import_module("x86qw_runtime.ui"))
        self.menu = package.menu
        self.polished = package.polished
        self.menu.configure(no_color=True)
        self.options = (
            self.menu.MenuOption(
                "play",
                "Jogar",
                "partida local",
                "escolha jogo, modo, mapa e bots",
                group="Partida",
            ),
            self.menu.MenuOption(
                "servers",
                "Servidores",
                "partidas públicas",
                "encontre um servidor compatível",
                group="Online",
            ),
            self.menu.MenuOption(
                "manage",
                "Gerenciar",
                "manutenção",
                "atualizações, reparo e diagnóstico",
                group="Sistema",
            ),
        )

    def test_package_preserves_the_canonical_module_and_installs_renderers(self) -> None:
        self.assertEqual("x86qw_runtime.ui.menu", self.menu.__name__)
        self.assertIs(self.menu._render_navigation, self.polished._render_navigation)
        self.assertTrue(self.menu._x86qw_polished_ui)

    def test_wide_terminal_uses_a_contextual_two_pane_command_center(self) -> None:
        output = TtyStringIO()
        keys = iter(("down", "enter"))
        with mock.patch.object(
            self.menu, "terminal_size", return_value=os.terminal_size((100, 30)),
        ), mock.patch.object(self.menu.sys, "stdout", output):
            selected = self.menu.select_one(
                "O que deseja fazer?",
                self.options,
                breadcrumb="x86QW › Início",
                subtitle="Tudo pronto para a próxima partida.",
                searchable=True,
                interactive=True,
                key_reader=lambda: next(keys),
            )

        self.assertEqual("servers", selected)
        rendered = output.getvalue()
        self.assertIn("CENTRAL X86QW", rendered)
        self.assertIn("OPÇÕES", rendered)
        self.assertIn("DETALHES", rendered)
        self.assertIn("Servidores", rendered)
        self.assertIn("partidas públicas", rendered)
        control_free = (
            rendered.replace("\033[2J\033[H", "")
            .replace("\033[?25l", "")
            .replace("\033[?25h", "")
        )
        self.assertNotIn("\033[", control_free)

    def test_installer_wizard_preserves_scrollback_and_collapses_the_choice(self) -> None:
        output = TtyStringIO()
        with mock.patch.object(
            self.menu, "terminal_size", return_value=os.terminal_size((82, 28)),
        ), mock.patch.object(self.menu.sys, "stdout", output):
            selected = self.menu.select_one(
                "Qual conteúdo deseja instalar?",
                (
                    self.menu.MenuOption(
                        "recommended", "Recomendado", "pronto para jogar",
                    ),
                    self.menu.MenuOption(
                        "advanced", "Avançado", "controle detalhado",
                    ),
                ),
                presentation="wizard",
                interactive=True,
                key_reader=lambda: "enter",
            )

        self.assertEqual("recommended", selected)
        rendered = output.getvalue()
        self.assertIn("INSTALAÇÃO GUIADA", rendered)
        self.assertIn("◆  Qual conteúdo deseja instalar?", rendered)
        self.assertIn("◇  Qual conteúdo deseja instalar?", rendered)
        self.assertIn("Recomendado · pronto para jogar", rendered)

    def test_multi_digit_shortcut_remains_available_in_polished_menus(self) -> None:
        output = TtyStringIO()
        keys = iter(("1", "2", "enter"))
        options = tuple(
            self.menu.MenuOption(str(index), f"Release {index}")
            for index in range(1, 13)
        )
        with mock.patch.object(
            self.menu, "terminal_size", return_value=os.terminal_size((82, 28)),
        ), mock.patch.object(self.menu.sys, "stdout", output):
            selected = self.menu.select_one(
                "Qual versão deseja usar?",
                options,
                presentation="wizard",
                interactive=True,
                key_reader=lambda: next(keys),
            )

        self.assertEqual("12", selected)
        self.assertIn("Ir para o item: 12█", output.getvalue())

    def test_multi_selection_keeps_order_and_guided_feedback(self) -> None:
        output = TtyStringIO()
        keys = iter((" ", "down", " ", "enter"))
        with mock.patch.object(
            self.menu, "terminal_size", return_value=os.terminal_size((82, 28)),
        ), mock.patch.object(self.menu.sys, "stdout", output):
            selected = self.menu.select_many(
                "Quais componentes deseja instalar?",
                self.options,
                presentation="wizard",
                interactive=True,
                key_reader=lambda: next(keys),
            )

        self.assertEqual(("play", "servers"), selected)
        rendered = output.getvalue()
        self.assertIn("[✓]", rendered)
        self.assertIn("2 componentes selecionados", rendered)

    def test_narrow_terminal_and_explicit_classic_mode_keep_the_canonical_ui(self) -> None:
        output = TtyStringIO()
        with mock.patch.object(
            self.menu, "terminal_size", return_value=os.terminal_size((48, 24)),
        ), mock.patch.object(self.menu.sys, "stdout", output):
            selected = self.menu.select_one(
                "Modo", self.options, interactive=True, key_reader=lambda: "enter",
            )
        self.assertEqual("play", selected)
        self.assertNotIn("CENTRAL X86QW", output.getvalue())

        output = TtyStringIO()
        with mock.patch.dict(os.environ, {"X86QW_CLASSIC_UI": "1"}), mock.patch.object(
            self.menu, "terminal_size", return_value=os.terminal_size((100, 30)),
        ), mock.patch.object(self.menu.sys, "stdout", output):
            selected = self.menu.select_one(
                "Modo", self.options, interactive=True, key_reader=lambda: "enter",
            )
        self.assertEqual("play", selected)
        self.assertNotIn("CENTRAL X86QW", output.getvalue())


if __name__ == "__main__":
    unittest.main()
