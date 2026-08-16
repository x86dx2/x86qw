from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from x86qw_runtime.profiles import (
    DEFAULT_PROFILE_BUNDLE,
    backup_user_profile,
    classify_install_data,
    is_user_profile_path,
    render_profile_report,
    restore_user_profile,
)


class ProfileBoundaryTests(unittest.TestCase):
    def test_closed_path_rules_separate_profile_from_cache_and_personal(self) -> None:
        self.assertTrue(is_user_profile_path("ezquake/configs/config.cfg"))
        self.assertTrue(is_user_profile_path("qw/x86qw-user.cfg"))
        self.assertFalse(is_user_profile_path("qw/demos/match.mvd"))
        self.assertFalse(is_user_profile_path("ezquake/temp/cache.dat"))
        self.assertFalse(is_user_profile_path("id1/pak0.pak"))

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            (target / "ezquake/configs").mkdir(parents=True)
            (target / "qw/demos").mkdir(parents=True)
            (target / "ezquake/temp").mkdir(parents=True)
            (target / "ezquake/configs/config.cfg").write_bytes(b"bind w +forward\n")
            (target / "qw/x86qw-user.cfg").write_bytes(b"alias mine jump\n")
            (target / "qw/demos/local.mvd").write_bytes(b"demo\n")
            (target / "ezquake/temp/scratch").write_bytes(b"tmp\n")

            report = classify_install_data(target)
        self.assertEqual(
            ("ezquake/configs/config.cfg", "qw/x86qw-user.cfg"),
            report["profile"],
        )
        self.assertEqual(("ezquake/temp",), report["cache"])
        self.assertEqual(("qw/demos",), report["personal"])
        self.assertNotIn("id1/pak0.pak", report["profile"])

    def test_backup_is_byte_identical_and_omits_cache_and_personal_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "install"
            (target / "ezquake/configs").mkdir(parents=True)
            (target / "qw/demos").mkdir(parents=True)
            (target / "ezquake/temp").mkdir(parents=True)
            config = b"name player\n"
            user = b"alias personal rocket\n"
            (target / "ezquake/configs/config.cfg").write_bytes(config)
            (target / "qw/x86qw-user.cfg").write_bytes(user)
            (target / "qw/demos/local.mvd").write_bytes(b"secret-demo\n")
            (target / "ezquake/temp/scratch").write_bytes(b"cache\n")
            bundle = root / DEFAULT_PROFILE_BUNDLE

            written = backup_user_profile(target, bundle)
            self.assertEqual(bundle, written)
            with zipfile.ZipFile(bundle) as archive:
                names = set(archive.namelist())
                self.assertIn("NOTICE.txt", names)
                self.assertIn("manifest.json", names)
                self.assertIn("ezquake/configs/config.cfg", names)
                self.assertIn("qw/x86qw-user.cfg", names)
                self.assertNotIn("qw/demos/local.mvd", names)
                self.assertNotIn("ezquake/temp/scratch", names)
                self.assertEqual(config, archive.read("ezquake/configs/config.cfg"))
                manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual("profile", manifest["kind"])
            self.assertFalse(target.joinpath("qw/demos/local.mvd").read_bytes() == b"")
            with self.assertRaises(OSError):
                backup_user_profile(target, bundle)

    def test_restore_preserves_bytes_and_rolls_back_managed_and_failed_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "install"
            (target / "ezquake/configs").mkdir(parents=True)
            (target / "qw").mkdir()
            (target / "id1").mkdir()
            original = b"old-config\n"
            (target / "ezquake/configs/config.cfg").write_bytes(original)
            (target / "qw/x86qw-user.cfg").write_bytes(b"old-user\n")
            (target / "id1/pak0.pak").write_bytes(b"PAK")
            bundle = root / "profile.zip"
            backup_user_profile(target, bundle)

            (target / "ezquake/configs/config.cfg").write_bytes(b"dirty\n")
            (target / "qw/x86qw-user.cfg").write_bytes(b"dirty-user\n")
            restored = restore_user_profile(bundle, target)
            self.assertEqual(("ezquake/configs/config.cfg", "qw/x86qw-user.cfg"), restored)
            self.assertEqual(original, (target / "ezquake/configs/config.cfg").read_bytes())
            self.assertEqual(b"old-user\n", (target / "qw/x86qw-user.cfg").read_bytes())
            self.assertEqual(b"PAK", (target / "id1/pak0.pak").read_bytes())

            with zipfile.ZipFile(bundle, "a") as archive:
                archive.writestr("id1/pak0.pak", b"evil")
            with self.assertRaises(ValueError):
                restore_user_profile(bundle, target)
            self.assertEqual(b"PAK", (target / "id1/pak0.pak").read_bytes())

    def test_human_report_names_the_data_classes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "missing"
            text = render_profile_report(classify_install_data(target))
        self.assertIn("profile", text)
        self.assertIn("cache", text)
        self.assertIn("personal", text)
        self.assertIn("owner-only", text)


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "x86qw_profile_manager_test", ROOT / "dist/installer/bin/manager.py",
)
manager = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = manager
SPEC.loader.exec_module(manager)


class ProfileCliTests(unittest.TestCase):
    def setUp(self) -> None:
        manager.console.configure(verbose=False, no_color=True)

    def test_profile_backup_and_restore_round_trip_outside_the_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "installation"
            (target / "ezquake/configs").mkdir(parents=True)
            payload = b"bind mouse1 +attack\n"
            (target / "ezquake/configs/config.cfg").write_bytes(payload)
            bundle = root / "x86qw-profile.zip"
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                result = manager.main([
                    "profile", "--backup", str(bundle), str(target),
                ])
            self.assertEqual(0, result)
            self.assertIn(str(bundle.resolve()), output.getvalue())
            self.assertTrue(bundle.is_file())
            (target / "ezquake/configs/config.cfg").write_bytes(b"changed\n")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                result = manager.main([
                    "profile", "--restore", str(bundle), str(target),
                ])
            self.assertEqual(0, result)
            self.assertEqual(payload, (target / "ezquake/configs/config.cfg").read_bytes())

    def test_backup_is_rejected_outside_profile(self) -> None:
        with self.assertRaises(SystemExit):
            manager.parse_arguments(["verify", "--backup"], ROOT)
