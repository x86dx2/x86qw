import contextlib
import importlib.util
import io
import os
import stat
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from x86qw_runtime.io import atomic as atomic_io


install_qw = sys.modules.get("manager")
if install_qw is None:
    MANAGER_SPEC = importlib.util.spec_from_file_location(
        "manager", ROOT / "dist/installer/bin/manager.py",
    )
    install_qw = importlib.util.module_from_spec(MANAGER_SPEC)
    assert MANAGER_SPEC.loader is not None
    sys.modules[MANAGER_SPEC.name] = install_qw
    MANAGER_SPEC.loader.exec_module(install_qw)

play_qw = sys.modules.get("gameplay")
if play_qw is None:
    GAMEPLAY_SPEC = importlib.util.spec_from_file_location(
        "gameplay", ROOT / "dist/installer/bin/gameplay.py",
    )
    play_qw = importlib.util.module_from_spec(GAMEPLAY_SPEC)
    assert GAMEPLAY_SPEC.loader is not None
    sys.modules[GAMEPLAY_SPEC.name] = play_qw
    GAMEPLAY_SPEC.loader.exec_module(play_qw)

play_qw.configure_context(install_qw.gameplay_composition_context(play_qw))


class PersonalConfigTransactionTests(unittest.TestCase):
    def make_player(self, root: Path):
        target = root / "quake-world"
        cache = root / "cache/x86qw"
        target.mkdir(parents=True)
        cache.parent.mkdir(parents=True)
        player = play_qw.Player(ROOT, target, cache)
        base = next(game for game in play_qw.LOCAL_GAMES if game.key == "td2")
        game = replace(
            base,
            play_support_gamecode=None,
            personal_config="profiles/custom/user.cfg",
        )
        return player, target, game

    def test_later_parent_failure_removes_created_profile_and_parent_topology(self):
        """Dropping the profile result would leave personal state after rollback."""

        with tempfile.TemporaryDirectory() as temporary:
            player, target, game = self.make_player(Path(temporary))
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "falha tardia"):
                    with player.component_state_transaction() as results:
                        created = player.ensure_local_play_support(
                            [game], mutation_results=results,
                        )
                        self.assertEqual(1, len(created))
                        self.assertIs(created[0], results[-1])
                        self.assertTrue((target / game.personal_config).is_file())
                        if os.name != "nt":
                            self.assertEqual(
                                0o644,
                                stat.S_IMODE((target / game.personal_config).stat().st_mode),
                            )
                        raise RuntimeError("falha tardia")

            self.assertFalse((target / game.personal_config).exists())
            self.assertFalse((target / "profiles").exists())

    def test_preexisting_profile_is_preserved_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, game = self.make_player(Path(temporary))
            destination = target / game.personal_config
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"\xffpersonal\x00configuration\n")

            result = player.ensure_game_user_profile(game)

            self.assertIsNone(result)
            self.assertEqual(b"\xffpersonal\x00configuration\n", destination.read_bytes())

    def test_parent_failure_preserves_a_profile_modified_after_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, game = self.make_player(Path(temporary))
            destination = target / game.personal_config

            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "falha tardia"):
                    with player.component_state_transaction() as results:
                        player.ensure_local_play_support(
                            [game], mutation_results=results,
                        )
                        destination.write_bytes("personalizado durante a operação\n".encode())
                        raise RuntimeError("falha tardia")

            self.assertEqual(
                "personalizado durante a operação\n".encode(), destination.read_bytes(),
            )
            self.assertTrue(destination.parent.is_dir())

    def test_parent_failure_preserves_an_identical_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, game = self.make_player(Path(temporary))
            destination = target / game.personal_config

            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "falha tardia"):
                    with player.component_state_transaction() as results:
                        player.ensure_local_play_support(
                            [game], mutation_results=results,
                        )
                        payload = destination.read_bytes()
                        replacement = destination.with_name("replacement.cfg")
                        replacement.write_bytes(payload)
                        os.replace(replacement, destination)
                        raise RuntimeError("falha tardia")

            self.assertEqual(payload, destination.read_bytes())

    def test_rollback_removes_only_the_parent_directories_it_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, game = self.make_player(Path(temporary))
            preexisting = target / "profiles"
            preexisting.mkdir()

            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, "falha tardia"):
                    with player.component_state_transaction() as results:
                        player.ensure_local_play_support(
                            [game], mutation_results=results,
                        )
                        raise RuntimeError("falha tardia")

            self.assertTrue(preexisting.is_dir())
            self.assertFalse((preexisting / "custom").exists())

    def test_profile_and_parent_symlinks_are_rejected_without_touching_targets(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks indisponíveis")
        for conflict in ("profile", "parent"):
            with self.subTest(conflict=conflict), tempfile.TemporaryDirectory() as temporary:
                player, target, game = self.make_player(Path(temporary))
                personal = target / "personal"
                personal.mkdir()
                sentinel = personal / "sentinel"
                sentinel.write_bytes(b"keep")
                destination = target / game.personal_config
                try:
                    if conflict == "profile":
                        destination.parent.mkdir(parents=True)
                        destination.symlink_to(sentinel)
                    else:
                        (target / "profiles").symlink_to(personal, target_is_directory=True)
                except OSError as error:
                    self.skipTest(f"symlink indisponível: {error}")

                with self.assertRaises(install_qw.InstallerError):
                    player.ensure_game_user_profile(game)

                self.assertEqual(b"keep", sentinel.read_bytes())

    def test_profile_path_cannot_escape_the_installation(self):
        for preexisting in (False, True):
            with self.subTest(preexisting=preexisting), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                player, _, game = self.make_player(root)
                game = replace(game, personal_config="../outside.cfg")
                outside = root / "outside.cfg"
                if preexisting:
                    outside.write_bytes(b"personal")

                with self.assertRaises(install_qw.InstallerError):
                    player.ensure_game_user_profile(game)

                if preexisting:
                    self.assertEqual(b"personal", outside.read_bytes())
                else:
                    self.assertFalse(outside.exists())

    def test_profile_and_parent_type_conflicts_are_rejected(self):
        for conflict in ("profile-directory", "parent-file"):
            with self.subTest(conflict=conflict), tempfile.TemporaryDirectory() as temporary:
                player, target, game = self.make_player(Path(temporary))
                destination = target / game.personal_config
                if conflict == "profile-directory":
                    destination.mkdir(parents=True)
                else:
                    (target / "profiles").write_bytes(b"personal")

                with self.assertRaises(install_qw.InstallerError):
                    player.ensure_game_user_profile(game)

                if conflict == "profile-directory":
                    self.assertTrue(destination.is_dir())
                else:
                    self.assertEqual(b"personal", (target / "profiles").read_bytes())

    def test_atomic_create_does_not_overwrite_a_concurrent_personal_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "user.cfg"
            real_link = os.link

            def collide(source, target, *args, **kwargs):
                destination.write_bytes("personal concorrente\n".encode())
                return real_link(source, target, *args, **kwargs)

            with mock.patch.object(atomic_io.os, "link", side_effect=collide):
                with self.assertRaises(atomic_io.AtomicWriteError) as captured:
                    atomic_io.atomic_create_bytes(destination, b"x86qw\n", mode=0o644)

            self.assertFalse(captured.exception.committed)
            self.assertEqual("personal concorrente\n".encode(), destination.read_bytes())

    def test_profile_creation_uses_create_only_atomic_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, game = self.make_player(Path(temporary))
            destination = target / game.personal_config
            real_link = os.link

            def collide(source, target_path, *args, **kwargs):
                destination.write_bytes("personal concorrente\n".encode())
                return real_link(source, target_path, *args, **kwargs)

            with mock.patch.object(atomic_io.os, "link", side_effect=collide):
                with self.assertRaises(install_qw.InstallerError):
                    player.ensure_game_user_profile(game)

            self.assertEqual("personal concorrente\n".encode(), destination.read_bytes())

    def test_committed_atomic_failure_removes_profile_and_created_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            player, target, game = self.make_player(Path(temporary))
            destination = target / game.personal_config

            with mock.patch.object(
                atomic_io,
                "_fsync_directory",
                side_effect=OSError("simulated directory sync failure"),
            ):
                with self.assertRaises(install_qw.InstallerError) as captured:
                    player.ensure_game_user_profile(game)

            self.assertTrue(captured.exception.operation_error.committed)
            self.assertFalse(destination.exists())
            self.assertFalse((target / "profiles").exists())


if __name__ == "__main__":
    unittest.main()
