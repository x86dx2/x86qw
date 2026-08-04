from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
INSTALLER_BIN = ROOT / "dist/installer/bin"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(INSTALLER_BIN) not in sys.path:
    sys.path.insert(0, str(INSTALLER_BIN))

install_qw = sys.modules.get("manager")
if install_qw is None:
    manager_spec = importlib.util.spec_from_file_location(
        "manager", INSTALLER_BIN / "manager.py",
    )
    install_qw = importlib.util.module_from_spec(manager_spec)
    assert manager_spec.loader is not None
    sys.modules[manager_spec.name] = install_qw
    manager_spec.loader.exec_module(install_qw)

play_qw = sys.modules.get("gameplay")
if play_qw is None:
    gameplay_spec = importlib.util.spec_from_file_location(
        "gameplay", INSTALLER_BIN / "gameplay.py",
    )
    play_qw = importlib.util.module_from_spec(gameplay_spec)
    assert gameplay_spec.loader is not None
    sys.modules[gameplay_spec.name] = play_qw
    gameplay_spec.loader.exec_module(play_qw)

play_qw.configure_context(install_qw.gameplay_composition_context(play_qw))

from x86qw_runtime.transaction import (  # noqa: E402
    MutationApplyError,
    MutationRollbackError,
    finalize_mutation,
    rollback_mutation,
)
from x86qw_runtime.io import personal_files  # noqa: E402
from x86qw_runtime.io import managed_files  # noqa: E402
from x86qw_runtime.io.atomic import AtomicWriteError  # noqa: E402


class MacOSPersonalTransactionTests(unittest.TestCase):
    def make_player(self, root: Path):
        target = root / "quake-world"
        cache = root / "cache/x86qw"
        target.mkdir(parents=True)
        cache.parent.mkdir(parents=True)
        return play_qw.Player(ROOT, target, cache), target

    def legacy_layout(self, root: Path):
        player, target = self.make_player(root)
        config = target / "ezquake/configs/config.cfg"
        config.parent.mkdir(parents=True)
        settings = {
            "vid_fullscreen": "0",
            "vid_usedesktopres": "1",
            "vid_win_borderless": "1",
            "vid_win_displaynumber": "0",
            "vid_win_width": "1800",
            "vid_win_height": "1130",
            "vid_xpos": "0",
            "vid_ypos": "39",
        }
        config_payload = b"".join(
            f'{name} "{value}"\n'.encode() for name, value in settings.items()
        )
        backup_payload = b'vid_fullscreen "1"\nvid_usedesktopres "1"\n'
        marker_payload = json.dumps({
            "managed": True,
            "settings": settings,
        }).encode()
        config.write_bytes(config_payload)
        backup = config.with_name("config.video-pre-x86qw.cfg")
        backup.write_bytes(backup_payload)
        marker = target / play_qw.LEGACY_MACOS_VIDEO_LAYOUT
        marker.parent.mkdir(parents=True)
        marker.write_bytes(marker_payload)
        return player, config, backup, marker, config_payload, backup_payload, marker_payload

    def test_legacy_cleanup_is_reversible_as_one_config_marker_backup_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            (
                player, config, backup, marker,
                config_payload, backup_payload, marker_payload,
            ) = self.legacy_layout(Path(temporary))

            with mock.patch.object(play_qw, "is_macos_host", return_value=True), \
                    contextlib.redirect_stdout(io.StringIO()):
                retained = []
                result = player.remove_legacy_macos_video_layout(retained)

            self.assertIsNotNone(result, "a limpeza legado ainda não retorna sua transação")
            self.assertEqual([result], retained)
            rollback_mutation(result)
            self.assertEqual(config_payload, config.read_bytes())
            self.assertEqual(backup_payload, backup.read_bytes())
            self.assertEqual(marker_payload, marker.read_bytes())

    def test_notched_fullscreen_config_and_marker_are_one_reversible_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            player, target = self.make_player(Path(temporary))
            config = target / "ezquake/configs/config.cfg"
            config.parent.mkdir(parents=True)
            original = b'vid_fullscreen "1"\nvid_usedesktopres "1"\n'
            config.write_bytes(original)
            marker = target / play_qw.MACOS_FULLSCREEN_LAYOUT
            desired = {
                "vid_fullscreen": "1",
                "vid_usedesktopres": "0",
                "vid_width": "3024",
                "vid_height": "1890",
                "vid_displayfrequency": "0",
            }

            with mock.patch.object(play_qw, "is_macos_host", return_value=True), \
                    mock.patch.object(
                        player, "macos_notched_fullscreen_settings", return_value=desired,
                    ), contextlib.redirect_stdout(io.StringIO()):
                retained = []
                result = player.configure_macos_fullscreen(retained)

            self.assertIsNotNone(result, "fullscreen ainda não retorna sua transação")
            self.assertEqual([result], retained)
            rollback_mutation(result)
            self.assertEqual(original, config.read_bytes())
            self.assertFalse(marker.exists())
            self.assertFalse((target / ".x86qw").exists())

    def test_concurrent_occupant_after_original_is_withdrawn_keeps_the_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            player, target = self.make_player(Path(temporary))
            config = target / "ezquake/configs/config.cfg"
            config.parent.mkdir(parents=True)
            original = b'vid_fullscreen "1"\nvid_usedesktopres "1"\n'
            concurrent = b'vid_fullscreen "0"\n// personal concorrente\n'
            config.write_bytes(original)
            desired = {
                "vid_fullscreen": "1",
                "vid_usedesktopres": "0",
                "vid_width": "3024",
                "vid_height": "1890",
                "vid_displayfrequency": "0",
            }
            atomic_create = personal_files.atomic_create_bytes

            def collide(path: Path, payload: bytes, *, mode: int):
                if Path(path) == config:
                    config.write_bytes(concurrent)
                    raise AtomicWriteError("ocupação concorrente", committed=False)
                return atomic_create(path, payload, mode=mode)

            with mock.patch.object(play_qw, "is_macos_host", return_value=True), \
                    mock.patch.object(
                        player, "macos_notched_fullscreen_settings", return_value=desired,
                    ), mock.patch.object(
                        personal_files, "atomic_create_bytes", side_effect=collide,
                    ), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(install_qw.InstallerError):
                    player.configure_macos_fullscreen()

            self.assertEqual(concurrent, config.read_bytes())
            quarantines = tuple(config.parent.glob(".x86qw-config.cfg-quarantine.*"))
            self.assertEqual(1, len(quarantines))
            self.assertEqual(original, (quarantines[0] / "node").read_bytes())

    def test_late_marker_failure_rolls_back_legacy_config_and_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            (
                player, config, backup, marker,
                config_payload, backup_payload, marker_payload,
            ) = self.legacy_layout(Path(temporary))
            apply = personal_files.apply_personal_file

            def fail_marker(snapshot, payload):
                if snapshot.path == marker:
                    raise OSError("falha tardia no marcador")
                return apply(snapshot, payload)

            with mock.patch.object(play_qw, "is_macos_host", return_value=True), \
                    mock.patch.object(
                        personal_files, "apply_personal_file", side_effect=fail_marker,
                    ), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(MutationApplyError):
                    player.remove_legacy_macos_video_layout()

            self.assertEqual(config_payload, config.read_bytes())
            self.assertEqual(backup_payload, backup.read_bytes())
            self.assertEqual(marker_payload, marker.read_bytes())
            self.assertFalse(tuple(config.parent.glob(".x86qw-*-quarantine.*")))

    def test_rollback_preserves_concurrent_config_bytes_and_original_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            player, target = self.make_player(Path(temporary))
            config = target / "ezquake/configs/config.cfg"
            config.parent.mkdir(parents=True)
            original = b'vid_fullscreen "1"\nvid_usedesktopres "1"\n'
            concurrent = b'vid_fullscreen "0"\n// alterado depois\n'
            config.write_bytes(original)
            desired = {
                "vid_fullscreen": "1",
                "vid_usedesktopres": "0",
                "vid_width": "3024",
                "vid_height": "1890",
                "vid_displayfrequency": "0",
            }
            with mock.patch.object(play_qw, "is_macos_host", return_value=True), \
                    mock.patch.object(
                        player, "macos_notched_fullscreen_settings", return_value=desired,
                    ), contextlib.redirect_stdout(io.StringIO()):
                result = player.configure_macos_fullscreen([])
            assert result is not None
            config.write_bytes(concurrent)

            with self.assertRaises(MutationRollbackError):
                rollback_mutation(result)

            self.assertEqual(concurrent, config.read_bytes())
            quarantines = tuple(config.parent.glob(".x86qw-config.cfg-quarantine.*"))
            self.assertEqual(1, len(quarantines))
            self.assertEqual(original, (quarantines[0] / "node").read_bytes())

    def test_rollback_preserves_a_concurrent_backup_and_restores_the_other_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            (
                player, config, backup, marker,
                config_payload, backup_payload, marker_payload,
            ) = self.legacy_layout(Path(temporary))
            concurrent = "backup pessoal criado durante a operação\n".encode()
            with mock.patch.object(play_qw, "is_macos_host", return_value=True), \
                    contextlib.redirect_stdout(io.StringIO()):
                result = player.remove_legacy_macos_video_layout([])
            assert result is not None
            backup.write_bytes(concurrent)

            with self.assertRaises(MutationRollbackError):
                rollback_mutation(result)

            self.assertEqual(config_payload, config.read_bytes())
            self.assertEqual(concurrent, backup.read_bytes())
            self.assertEqual(marker_payload, marker.read_bytes())
            quarantines = tuple(backup.parent.glob(".x86qw-config.video-pre-x86qw.cfg-quarantine.*"))
            self.assertEqual(1, len(quarantines))
            self.assertEqual(backup_payload, (quarantines[0] / "node").read_bytes())

    def test_finalize_discards_original_quarantines_only_after_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            player, config, backup, marker, *_payloads = self.legacy_layout(Path(temporary))
            with mock.patch.object(play_qw, "is_macos_host", return_value=True), \
                    contextlib.redirect_stdout(io.StringIO()):
                result = player.remove_legacy_macos_video_layout([])
            assert result is not None
            quarantines = tuple(config.parent.glob(".x86qw-*-quarantine.*")) + tuple(
                marker.parent.glob(".x86qw-*-quarantine.*")
            )
            self.assertTrue(quarantines)

            finalize_mutation(result)

            self.assertFalse(tuple(config.parent.glob(".x86qw-*-quarantine.*")))
            self.assertFalse(tuple(marker.parent.glob(".x86qw-*-quarantine.*")))
            self.assertFalse(backup.exists())
            self.assertFalse(marker.exists())

    def test_finalize_preserves_a_quarantine_node_replaced_after_validation(self) -> None:
        """Finalization must never unlink a different inode through a stale path."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.cfg"
            config.write_bytes(b"original\n")
            snapshot = personal_files.observe_personal_file(root, config)
            token = personal_files.apply_personal_file(snapshot, b"x86qw\n")
            assert token.quarantine is not None
            previous = token.quarantine.previous
            concurrent = b"personal concurrent quarantine node\n"
            rename_api = managed_files._get_posix_rename_api()
            if rename_api is None:
                self.skipTest("atomic POSIX rename is unavailable")
            replaced = False

            class RacingRename:
                def move_no_replace(self, source_dir, source, target_dir, target):
                    nonlocal replaced
                    rename_api.move_no_replace(source_dir, source, target_dir, target)
                    if source == previous.name and not replaced:
                        replaced = True
                        previous.write_bytes(concurrent)

            with mock.patch.object(
                managed_files, "_get_posix_rename_api", return_value=RacingRename(),
            ), self.assertRaises(install_qw.InstallerError):
                personal_files.finalize_personal_file(token)

            self.assertTrue(replaced)
            self.assertEqual(concurrent, previous.read_bytes())

    def test_success_without_parent_transaction_leaves_no_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            player, config, backup, marker, *_payloads = self.legacy_layout(
                Path(temporary)
            )

            with mock.patch.object(play_qw, "is_macos_host", return_value=True), \
                    contextlib.redirect_stdout(io.StringIO()):
                result = player.remove_legacy_macos_video_layout()

            self.assertIsNone(result)
            self.assertFalse(tuple(config.parent.glob(".x86qw-*-quarantine.*")))
            self.assertFalse(tuple(marker.parent.glob(".x86qw-*-quarantine.*")))
            self.assertFalse(backup.exists())
            self.assertFalse(marker.exists())

    def test_fullscreen_success_without_parent_transaction_leaves_no_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            player, target = self.make_player(Path(temporary))
            config = target / "ezquake/configs/config.cfg"
            config.parent.mkdir(parents=True)
            config.write_bytes(b'vid_fullscreen "1"\nvid_usedesktopres "1"\n')
            desired = {
                "vid_fullscreen": "1",
                "vid_usedesktopres": "0",
                "vid_width": "3024",
                "vid_height": "1890",
                "vid_displayfrequency": "0",
            }

            with mock.patch.object(play_qw, "is_macos_host", return_value=True), \
                    mock.patch.object(
                        player, "macos_notched_fullscreen_settings", return_value=desired,
                    ), contextlib.redirect_stdout(io.StringIO()):
                result = player.configure_macos_fullscreen()

            self.assertIsNone(result)
            self.assertFalse(tuple(config.parent.glob(".x86qw-*-quarantine.*")))
            marker = target / play_qw.MACOS_FULLSCREEN_LAYOUT
            self.assertTrue(marker.is_file())
            self.assertFalse(tuple(marker.parent.glob(".x86qw-*-quarantine.*")))

    def test_finalize_preserves_unrelated_quarantine_like_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            player, config, *_rest = self.legacy_layout(Path(temporary))
            unrelated = config.parent / ".x86qw-unrelated-quarantine.keep"
            unrelated.mkdir()
            personal = unrelated / "personal.txt"
            personal.write_bytes(b"preservar\n")
            with mock.patch.object(play_qw, "is_macos_host", return_value=True), \
                    contextlib.redirect_stdout(io.StringIO()):
                result = player.remove_legacy_macos_video_layout([])
            assert result is not None

            finalize_mutation(result)

            self.assertEqual(b"preservar\n", personal.read_bytes())

    def test_rollback_publication_never_replaces_a_last_moment_occupant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.cfg"
            original = b"original\n"
            concurrent = b"concorrente\n"
            config.write_bytes(original)
            snapshot = personal_files.observe_personal_file(root, config)
            token = personal_files.apply_personal_file(snapshot, b"x86qw\n")
            link = personal_files.os.link

            def occupy(source, destination, *args, **kwargs):
                Path(destination).write_bytes(concurrent)
                return link(source, destination, *args, **kwargs)

            with mock.patch.object(personal_files.os, "link", side_effect=occupy):
                with self.assertRaises(install_qw.InstallerError):
                    personal_files.rollback_personal_file(token)

            self.assertEqual(concurrent, config.read_bytes())
            assert token.quarantine is not None
            self.assertEqual(original, token.quarantine.previous.read_bytes())

    def test_rollback_preserves_backup_replaced_after_restored_link_validation(self) -> None:
        """Rollback must never unlink a replacement occupying the backup name."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.cfg"
            original = b"original\n"
            concurrent = b"backup concorrente\n"
            config.write_bytes(original)
            snapshot = personal_files.observe_personal_file(root, config)
            token = personal_files.apply_personal_file(snapshot, b"x86qw\n")
            assert token.quarantine is not None
            previous = token.quarantine.previous
            path_identity = personal_files._quarantine_identity
            replaced = False

            def replace_after_destination_validation(path: Path):
                nonlocal replaced
                identity = path_identity(path)
                if Path(path) == config and not replaced:
                    replaced = True
                    previous.unlink()
                    previous.write_bytes(concurrent)
                return identity

            with mock.patch.object(
                personal_files,
                "_quarantine_identity",
                side_effect=replace_after_destination_validation,
            ), self.assertRaises(install_qw.InstallerError):
                personal_files.rollback_personal_file(token)

            self.assertTrue(replaced)
            self.assertEqual(original, config.read_bytes())
            self.assertEqual(concurrent, previous.read_bytes())

    def test_in_place_change_during_withdrawal_is_restored_and_never_published_over(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.cfg"
            original = b"original\n"
            concurrent = "alteração pessoal na janela de retirada\n".encode()
            config.write_bytes(original)
            snapshot = personal_files.observe_personal_file(root, config)
            quarantine = personal_files.apply_quarantine_removal

            def change_then_withdraw(path: Path):
                path.write_bytes(concurrent)
                return quarantine(path)

            with mock.patch.object(
                personal_files,
                "apply_quarantine_removal",
                side_effect=change_then_withdraw,
            ):
                with self.assertRaises(install_qw.InstallerError):
                    personal_files.apply_personal_file(snapshot, b"x86qw\n")

            self.assertEqual(concurrent, config.read_bytes())
            self.assertFalse(tuple(root.glob(".x86qw-*-quarantine.*")))

    def test_personal_file_root_cannot_be_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            real_root = base / "real"
            real_root.mkdir()
            config = real_root / "config.cfg"
            config.write_bytes(b"personal\n")
            linked_root = base / "linked"
            linked_root.symlink_to(real_root, target_is_directory=True)

            with self.assertRaises(install_qw.InstallerError):
                personal_files.observe_personal_file(
                    linked_root, linked_root / "config.cfg",
                )

            self.assertEqual(b"personal\n", config.read_bytes())

    def test_personal_file_path_must_be_inside_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            root.mkdir()
            outside = base / "outside.cfg"
            outside.write_bytes(b"personal\n")

            with self.assertRaises(install_qw.InstallerError):
                personal_files.observe_personal_file(root, outside)

            self.assertEqual(b"personal\n", outside.read_bytes())

    def test_replaced_personal_file_root_is_rejected_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            root.mkdir()
            config = root / "config.cfg"
            snapshot = personal_files.observe_personal_file(root, config)
            original_root = base / "original-root"
            root.rename(original_root)
            root.mkdir()

            with self.assertRaises(install_qw.InstallerError):
                personal_files.apply_personal_file(snapshot, b"x86qw\n")

            self.assertFalse(config.exists())
            self.assertFalse((original_root / "config.cfg").exists())


if __name__ == "__main__":
    unittest.main()
