from __future__ import annotations

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from x86qw_runtime.host_presets import (
    HOST_PRESETS_PATH,
    apply_host_preset,
    load_host_presets,
    save_host_preset,
)
from x86qw_runtime.profiles import is_user_profile_path


def _options(**overrides: object) -> Namespace:
    values: dict[str, object] = {
        "game": "ktx",
        "mode": "duel",
        "map": "dm6",
        "bind": "127.0.0.1",
        "port": 28501,
        "hostname": "",
        "maxclients": 16,
        "no_mvd": False,
        "with_qtv": False,
        "qtv_bind": "127.0.0.1",
        "qtv_port": 28000,
        "with_proxy": False,
        "proxy_bind": "127.0.0.1",
        "proxy_port": 30000,
        "password": "",
        "spectator_password": "",
        "rcon_password": "",
        "qtv_password": "",
        "prompt_password": False,
        "prompt_spectator_password": False,
        "prompt_rcon_password": False,
        "prompt_qtv_password": False,
        "password_file": None,
        "spectator_password_file": None,
        "rcon_password_file": None,
        "qtv_password_file": None,
    }
    values.update(overrides)
    return Namespace(**values)


class HostPresetTests(unittest.TestCase):
    def test_preset_file_is_user_owned_profile_data(self) -> None:
        self.assertTrue(is_user_profile_path(HOST_PRESETS_PATH))

    def test_save_and_load_round_trip_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            saved = save_host_preset(
                target, "local-duel", _options(hostname="Casa", with_qtv=True),
            )
            self.assertEqual("local-duel", saved["name"])
            self.assertEqual("ktx", saved["game"])
            self.assertTrue(saved["with_qtv"])
            self.assertNotIn("password", saved)
            loaded = load_host_presets(target)
            self.assertEqual(saved, loaded["local-duel"])

    def test_save_rejects_password_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            with self.assertRaises(ValueError):
                save_host_preset(target, "unsafe", _options(password="secret"))
            self.assertEqual({}, load_host_presets(target))

    def test_apply_preset_fills_host_options(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            save_host_preset(
                target,
                "lan",
                _options(bind="0.0.0.0", port=27500, hostname="LAN", maxclients=8, no_mvd=True),
            )
            applied = Namespace(selection=None, game=None, mode=None, map=None)
            apply_host_preset(applied, load_host_presets(target)["lan"])
        self.assertEqual("ktx", applied.game)
        self.assertEqual("ktx", applied.selection)
        self.assertEqual("duel", applied.mode)
        self.assertEqual("dm6", applied.map)
        self.assertEqual("0.0.0.0", applied.bind)
        self.assertEqual(27500, applied.port)
        self.assertTrue(applied.no_mvd)
