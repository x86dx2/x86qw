from __future__ import annotations

import importlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class QuarantineTests(unittest.TestCase):
    def runtime(self):
        spec = importlib.util.find_spec("x86qw_runtime.io.quarantine")
        self.assertIsNotNone(
            spec,
            "destructive mutations need a same-filesystem quarantine boundary",
        )
        return importlib.import_module("x86qw_runtime.io.quarantine")

    def test_quarantined_tree_can_be_restored_byte_for_byte(self) -> None:
        """A parent failure must recover a complete tree before finalization."""

        quarantine = self.runtime()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "installed"
            nested = target / "sub/arquivo.bin"
            nested.parent.mkdir(parents=True)
            nested.write_bytes(b"payload")

            token = quarantine.apply_quarantine_removal(target)
            self.assertFalse(target.exists())

            quarantine.rollback_quarantine(token)

            self.assertEqual(nested.read_bytes(), b"payload")
            self.assertFalse(token.quarantine.exists())

    def test_finalization_discards_the_quarantine_only_after_commit(self) -> None:
        """A committed purge must remove its private rollback material."""

        quarantine = self.runtime()
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "cache"
            target.mkdir()
            (target / "artifact").write_bytes(b"bytes")

            token = quarantine.apply_quarantine_removal(target)
            self.assertTrue(token.quarantine.is_dir())

            quarantine.finalize_quarantine(token)

            self.assertFalse(target.exists())
            self.assertFalse(token.quarantine.exists())

    def test_finalization_preserves_a_tree_replaced_after_validation(self) -> None:
        """Finalization must bind deletion to the quarantined root identity."""

        quarantine = self.runtime()
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "cache"
            target.mkdir()
            (target / "managed").write_bytes(b"managed")
            token = quarantine.apply_quarantine_removal(target)
            parked = token.quarantine / "original"
            real_remove_tree = quarantine._remove_tree
            concurrent = b"concurrent personal data"

            def replace_before_remove(
                path: Path,
                device: int,
                expected_identity: tuple[int, int, int] | None = None,
            ) -> None:
                if path == token.previous and not parked.exists():
                    path.rename(parked)
                    path.mkdir()
                    (path / "personal").write_bytes(concurrent)
                if expected_identity is None:
                    real_remove_tree(path, device)
                else:
                    real_remove_tree(
                        path, device, expected_identity=expected_identity,
                    )

            with mock.patch.object(
                quarantine, "_remove_tree", side_effect=replace_before_remove,
            ):
                with self.assertRaises(quarantine.QuarantineError):
                    quarantine.finalize_quarantine(token)

            self.assertTrue((token.previous / "personal").is_file())
            self.assertEqual(concurrent, (token.previous / "personal").read_bytes())
            self.assertEqual(b"managed", (parked / "managed").read_bytes())

    def test_finalization_preserves_a_descendant_replaced_after_scandir(self) -> None:
        """Each recursive deletion must use the identity seen by its parent."""

        quarantine = self.runtime()
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "cache"
            nested = target / "nested"
            nested.mkdir(parents=True)
            (nested / "managed").write_bytes(b"managed")
            token = quarantine.apply_quarantine_removal(target)
            quarantined_nested = token.previous / "nested"
            parked = token.previous / "original-nested"
            real_remove_tree = quarantine._remove_tree
            concurrent = b"concurrent descendant"

            def replace_descendant_before_remove(
                path: Path,
                device: int,
                expected_identity: tuple[int, int, int] | None = None,
            ) -> None:
                if path == quarantined_nested and not parked.exists():
                    path.rename(parked)
                    path.mkdir()
                    (path / "personal").write_bytes(concurrent)
                if expected_identity is None:
                    real_remove_tree(path, device)
                else:
                    real_remove_tree(
                        path, device, expected_identity=expected_identity,
                    )

            with mock.patch.object(
                quarantine, "_remove_tree",
                side_effect=replace_descendant_before_remove,
            ):
                with self.assertRaises(quarantine.QuarantineError):
                    quarantine.finalize_quarantine(token)

            self.assertTrue((quarantined_nested / "personal").is_file())
            self.assertEqual(
                concurrent, (quarantined_nested / "personal").read_bytes(),
            )
            self.assertEqual(b"managed", (parked / "managed").read_bytes())

    def test_finalization_preserves_a_leaf_replaced_after_identity_check(self) -> None:
        """The unlink itself must remain bound to the validated leaf inode."""

        quarantine = self.runtime()
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "managed.cfg"
            target.write_bytes(b"managed")
            token = quarantine.apply_quarantine_removal(target)
            parked = token.quarantine / "original"
            concurrent = b"concurrent personal file"
            real_identity = quarantine._identity
            replaced = False
            identity_reads = 0

            def replace_after_identity(path: Path):
                nonlocal identity_reads, replaced
                identity = real_identity(path)
                if path == token.previous:
                    identity_reads += 1
                if path == token.previous and identity_reads == 2 and not replaced:
                    replaced = True
                    path.rename(parked)
                    path.write_bytes(concurrent)
                return identity

            with mock.patch.object(
                quarantine, "_identity", side_effect=replace_after_identity,
            ), self.assertRaises(quarantine.QuarantineError):
                quarantine.finalize_quarantine(token)

            self.assertTrue(replaced)
            self.assertTrue(
                token.previous.exists(), "a substituição concorrente foi removida",
            )
            self.assertEqual(concurrent, token.previous.read_bytes())
            self.assertEqual(b"managed", parked.read_bytes())

    def test_rollback_refuses_to_overwrite_a_replacement_destination(self) -> None:
        """Concurrent personal data must be preserved rather than overwritten."""

        quarantine = self.runtime()
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "installed"
            target.mkdir()
            (target / "managed").write_bytes(b"managed")
            token = quarantine.apply_quarantine_removal(target)
            target.mkdir()
            (target / "personal").write_bytes(b"personal")

            with self.assertRaises(quarantine.QuarantineError):
                quarantine.rollback_quarantine(token)

            self.assertEqual((target / "personal").read_bytes(), b"personal")
            self.assertTrue(token.quarantine.is_dir())

    def test_rollback_preserves_a_destination_created_after_absence_check(self) -> None:
        """The inverse rename must be atomic no-replace, not check-then-replace."""

        quarantine = self.runtime()
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "installed"
            target.mkdir()
            (target / "managed").write_bytes(b"managed")
            token = quarantine.apply_quarantine_removal(target)
            real_lexists = quarantine.lexists
            concurrent_identity: tuple[int, int] | None = None

            def create_destination_after_absence_check(path: Path) -> bool:
                nonlocal concurrent_identity
                if path == target and concurrent_identity is None:
                    self.assertFalse(real_lexists(path))
                    target.mkdir()
                    metadata = target.lstat()
                    concurrent_identity = (
                        int(metadata.st_dev), int(metadata.st_ino),
                    )
                    return False
                return real_lexists(path)

            with mock.patch.object(
                quarantine, "lexists",
                side_effect=create_destination_after_absence_check,
            ):
                with self.assertRaises(quarantine.QuarantineError):
                    quarantine.rollback_quarantine(token)

            self.assertIsNotNone(concurrent_identity)
            metadata = target.lstat()
            self.assertEqual(
                concurrent_identity,
                (int(metadata.st_dev), int(metadata.st_ino)),
            )
            self.assertTrue(token.previous.is_dir())

    def test_symlink_is_preserved_in_quarantine_instead_of_path_unlink(self) -> None:
        """Finalization must not unlink a non-regular node by pathname."""

        quarantine = self.runtime()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            personal = root / "personal"
            personal.mkdir()
            secret = personal / "keep.txt"
            secret.write_bytes(b"keep")
            link = root / "managed-link"
            try:
                link.symlink_to(personal, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symlink indisponível: {error}")

            token = quarantine.apply_quarantine_removal(link)
            with self.assertRaises(quarantine.QuarantineError):
                quarantine.finalize_quarantine(token)

            self.assertEqual(secret.read_bytes(), b"keep")
            self.assertFalse(link.exists())
            self.assertTrue(token.previous.is_symlink())

    def test_explicit_purge_mode_unlinks_a_symlink_without_following_target(self) -> None:
        """Purge may remove the link object, never the path it references."""

        quarantine = self.runtime()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            personal = root / "personal"
            personal.mkdir()
            secret = personal / "keep.txt"
            secret.write_bytes(b"keep")
            link = root / "managed-link"
            try:
                link.symlink_to(personal, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"symlink indisponível: {error}")

            token = quarantine.apply_quarantine_removal(
                link, allow_non_regular=True,
            )
            quarantine.finalize_quarantine(token)

            self.assertFalse(link.exists())
            self.assertFalse(token.quarantine.exists())
            self.assertEqual(b"keep", secret.read_bytes())

    def test_finalization_preserves_directory_swapped_at_private_move(self) -> None:
        """An empty replacement must not be removed after directory validation."""

        quarantine = self.runtime()
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "cache"
            target.mkdir()
            token = quarantine.apply_quarantine_removal(target)
            parked = token.quarantine / "original-directory"
            managed_files = importlib.import_module(
                "x86qw_runtime.io.managed_files"
            )
            rename_api = managed_files._get_posix_rename_api()
            if rename_api is None:
                self.skipTest("rename POSIX exclusivo indisponível")
            real_move = rename_api.move_no_replace
            replacement_identity: tuple[int, int] | None = None
            injected = False

            def replace_before_private_move(
                source_directory: int,
                source_name: str,
                destination_directory: int,
                destination_name: str,
            ) -> None:
                nonlocal injected, replacement_identity
                if source_name == token.previous.name and not injected:
                    injected = True
                    token.previous.rename(parked)
                    token.previous.mkdir()
                    metadata = token.previous.lstat()
                    replacement_identity = (
                        int(metadata.st_dev), int(metadata.st_ino),
                    )
                real_move(
                    source_directory,
                    source_name,
                    destination_directory,
                    destination_name,
                )

            with mock.patch.object(
                rename_api, "move_no_replace",
                side_effect=replace_before_private_move,
            ), self.assertRaises(quarantine.QuarantineError):
                quarantine.finalize_quarantine(token)

            self.assertTrue(injected)
            self.assertTrue(token.previous.is_dir())
            metadata = token.previous.lstat()
            self.assertEqual(
                replacement_identity,
                (int(metadata.st_dev), int(metadata.st_ino)),
            )
            self.assertTrue(parked.is_dir())


if __name__ == "__main__":
    unittest.main()
