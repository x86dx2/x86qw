from __future__ import annotations

import importlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


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

    def test_symlink_is_removed_as_a_leaf_without_following_its_target(self) -> None:
        """Explicit purge may remove a link name but never traverse its destination."""

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
            quarantine.finalize_quarantine(token)

            self.assertEqual(secret.read_bytes(), b"keep")
            self.assertFalse(link.exists())


if __name__ == "__main__":
    unittest.main()
