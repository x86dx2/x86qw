from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from x86qw_runtime.library import (
    LIBRARY_PATH,
    add_favorite,
    discover_servers,
    load_library,
    record_recent,
    remove_favorite,
    render_library_report,
)
from x86qw_runtime.profiles import backup_user_profile, is_user_profile_path


CLOCK = datetime(2026, 8, 16, 16, 0, tzinfo=timezone.utc)


class LibraryBoundaryTests(unittest.TestCase):
    def test_library_file_is_user_owned_profile_data(self) -> None:
        self.assertTrue(is_user_profile_path(LIBRARY_PATH))
        self.assertEqual("qw/x86qw-library.json", LIBRARY_PATH)

    def test_missing_library_is_empty_and_creates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            report = load_library(target)
            self.assertEqual((), report["favorites"])
            self.assertEqual((), report["recents"])
            self.assertFalse(target.exists())

    def test_favorite_records_origin_and_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            entry = add_favorite(
                target,
                "quake.example:27500",
                title="Duel EU",
                now=CLOCK,
            )
            self.assertEqual("quake.example:27500", entry["address"])
            self.assertEqual("Duel EU", entry["title"])
            self.assertEqual("user", entry["origin"])
            self.assertEqual("2026-08-16T16:00:00Z", entry["freshness"])
            stored = load_library(target)
            self.assertEqual((entry,), stored["favorites"])
            self.assertEqual((), stored["recents"])

    def test_duplicate_favorite_updates_freshness_without_duplicating(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            add_favorite(target, "quake.example:27500", title="old", now=CLOCK)
            later = datetime(2026, 8, 16, 17, 0, tzinfo=timezone.utc)
            add_favorite(target, "QUAKE.example:27500", title="new", now=later)
            stored = load_library(target)
        self.assertEqual(1, len(stored["favorites"]))
        self.assertEqual("quake.example:27500", stored["favorites"][0]["address"])
        self.assertEqual("new", stored["favorites"][0]["title"])
        self.assertEqual("2026-08-16T17:00:00Z", stored["favorites"][0]["freshness"])

    def test_recents_keep_newest_first_and_drop_the_oldest_over_the_cap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            for index in range(21):
                record_recent(
                    target,
                    f"server{index}.example:27500",
                    title=f"S{index}",
                    origin="hub",
                    now=datetime(2026, 8, 16, 16, index, tzinfo=timezone.utc),
                )
            recents = load_library(target)["recents"]
        self.assertEqual(20, len(recents))
        self.assertEqual("server20.example:27500", recents[0]["address"])
        self.assertEqual("hub", recents[0]["origin"])
        self.assertEqual("server1.example:27500", recents[-1]["address"])
        self.assertNotIn("server0.example:27500", [item["address"] for item in recents])

    def test_closed_origin_and_address_rules_reject_untrusted_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            with self.assertRaises(ValueError):
                add_favorite(target, "not-an-address", now=CLOCK)
            with self.assertRaises(ValueError):
                record_recent(
                    target, "quake.example:27500", origin="qwleague", now=CLOCK,
                )
            with self.assertRaises(ValueError):
                add_favorite(target, "quake.example:99999", now=CLOCK)

    def test_malformed_library_fails_closed_without_rewriting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            path = target / LIBRARY_PATH
            path.parent.mkdir(parents=True)
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_library(target)
            self.assertEqual("{not json", path.read_text(encoding="utf-8"))

    def test_profile_backup_includes_the_library_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "install"
            add_favorite(target, "quake.example:27500", title="Duel EU", now=CLOCK)
            bundle = root / "profile.zip"
            backup_user_profile(target, bundle)
            with zipfile.ZipFile(bundle) as archive:
                payload = json.loads(archive.read(LIBRARY_PATH))
        self.assertEqual("library", payload["kind"])
        self.assertEqual("quake.example:27500", payload["favorites"][0]["address"])

    def test_human_report_names_origin_and_freshness(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            add_favorite(target, "quake.example:27500", title="Duel EU", now=CLOCK)
            record_recent(
                target, "local.example:27500", title="Casa", origin="local", now=CLOCK,
            )
            text = render_library_report(load_library(target))
        self.assertIn("owner-only", text)
        self.assertIn("favorites: 1", text)
        self.assertIn("recents: 1", text)
        self.assertIn("user", text)
        self.assertIn("local", text)
        self.assertIn("2026-08-16T16:00:00Z", text)

    def test_remove_favorite_leaves_recents_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            add_favorite(target, "quake.example:27500", now=CLOCK)
            record_recent(target, "quake.example:27500", origin="hub", now=CLOCK)
            removed = remove_favorite(target, "quake.example:27500")
            stored = load_library(target)
        self.assertEqual("quake.example:27500", removed)
        self.assertEqual((), stored["favorites"])
        self.assertEqual(1, len(stored["recents"]))

    def test_discover_servers_falls_back_to_library_when_remote_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            add_favorite(target, "quake.example:27500", title="Duel EU", now=CLOCK)
            record_recent(
                target, "local.example:27500", title="Casa", origin="local", now=CLOCK,
            )
            servers = discover_servers(None, load_library(target))
        self.assertEqual(
            ("quake.example:27500", "local.example:27500"),
            tuple(item["address"] for item in servers),
        )
        self.assertEqual("user", servers[0]["origin"])
        self.assertEqual("Duel EU", servers[0]["title"])
        self.assertEqual("-", servers[0]["mode"])

    def test_discover_servers_keeps_remote_results_when_the_hub_answers(self) -> None:
        remote = ({"address": "hub.example:27500", "players": []},)
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "install"
            add_favorite(target, "quake.example:27500", now=CLOCK)
            servers = discover_servers(remote, load_library(target))
        self.assertEqual(("hub.example:27500",), tuple(item["address"] for item in servers))


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "x86qw_library_manager_test", ROOT / "dist/installer/bin/manager.py",
)
manager = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = manager
SPEC.loader.exec_module(manager)


class LibraryCliTests(unittest.TestCase):
    def setUp(self) -> None:
        manager.console.configure(verbose=False, no_color=True)

    def test_library_add_and_remove_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "installation"
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                result = manager.main([
                    "library", "--add", "quake.example:27500", str(target),
                ])
            self.assertEqual(0, result)
            text = output.getvalue()
            self.assertIn("quake.example:27500", text)
            self.assertIn("favorites: 1", text)
            stored = json.loads((target / LIBRARY_PATH).read_text(encoding="utf-8"))
            self.assertEqual("user", stored["favorites"][0]["origin"])
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                result = manager.main([
                    "library", "--remove", "quake.example:27500", str(target),
                ])
            self.assertEqual(0, result)
            self.assertEqual((), load_library(target)["favorites"])

    def test_add_is_rejected_outside_library(self) -> None:
        with self.assertRaises(SystemExit):
            manager.parse_arguments(["verify", "--add", "quake.example:27500"], ROOT)

    def test_hub_falls_back_to_library_when_remote_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "install"
            add_favorite(target, "quake.example:27500", title="Duel EU", now=CLOCK)
            installer = manager.Installer(root / "project", target, root / "cache")
            installer.remote.get = lambda *args, **kwargs: (_ for _ in ()).throw(
                manager.InstallerError("offline")
            )
            with contextlib.redirect_stdout(io.StringIO()):
                servers = installer.hub_servers()
            self.assertEqual("quake.example:27500", servers[0]["address"])
            self.assertEqual("user", servers[0]["origin"])

    def test_hub_join_records_a_recent_with_hub_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "install"
            target.mkdir()
            installer = manager.Installer(root / "project", target, root / "cache")
            server = {
                "address": "server.example:27500",
                "mode": "duel",
                "players": [],
                "settings": {"map": "dm6", "hostname": "Test"},
            }
            runtime = target / "client"
            with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(
                installer, "hub_servers", return_value=[server],
            ), mock.patch.object(
                manager.navigation, "supports_navigation", return_value=False,
            ), mock.patch.object(
                installer, "choose_host_runtime", return_value=("client", runtime),
            ), mock.patch.object(
                installer, "launch_runtime",
            ), mock.patch("builtins.input", return_value="1"):
                installer.browse_hub()
            recents = load_library(target)["recents"]
            self.assertEqual("server.example:27500", recents[0]["address"])
            self.assertEqual("hub", recents[0]["origin"])
            self.assertEqual("Test", recents[0]["title"])
