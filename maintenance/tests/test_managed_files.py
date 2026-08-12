"""Canonical managed-file identity, hashing and cleanup contracts."""

from __future__ import annotations

import hashlib
import os
import socket
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from x86qw_runtime.errors import InstallerError
from x86qw_runtime.io.managed_files import (
    MAX_HASHABLE_FILE_SIZE,
    MAX_MANAGED_FILE_SIZE,
    MaterializedDirectory,
    cleanup_materialized_directory,
    cleanup_materialized_file,
    describe_non_sensitive_temporary,
    file_matches_sha256,
    file_sha256,
    persistent_path_identity,
    remove_identity_bound_path,
    unlink_sensitive_temporary,
)


class ManagedFileHashTests(unittest.TestCase):
    """A managed hash must stay bounded and describe the exact regular file."""

    def test_hashes_a_regular_file_at_its_exact_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "conteudo.bin"
            path.write_bytes(b"x86QW")

            self.assertEqual(
                file_sha256(path, expected_size=5),
                hashlib.sha256(b"x86QW").hexdigest(),
            )
            self.assertTrue(
                file_matches_sha256(
                    path, hashlib.sha256(b"x86QW").hexdigest(), 5,
                )
            )

    def test_hashes_an_archive_larger_than_one_extracted_member(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "component.zip"
            archive_size = 128 * 1024 * 1024 + 1
            with path.open("wb") as archive:
                archive.truncate(archive_size)

            digest = file_sha256(path, expected_size=archive_size)

            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_rejects_a_size_that_exceeds_the_managed_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "conteudo.bin"
            path.write_bytes(b"x")

            with self.assertRaises(ValueError):
                file_sha256(path, expected_size=MAX_MANAGED_FILE_SIZE + 1)

    def test_artifact_hash_can_use_the_explicit_archive_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "pacote.zip"
            path.write_bytes(b"x86QW")

            with self.assertRaises(OSError):
                file_sha256(path, maximum_size=4)

            self.assertEqual(
                file_sha256(path, maximum_size=5),
                hashlib.sha256(b"x86QW").hexdigest(),
            )

    def test_artifact_hash_accepts_a_file_larger_than_one_archive_member(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "qrp-hires.zip"
            with path.open("wb") as stream:
                stream.truncate(MAX_MANAGED_FILE_SIZE + 1)

            with self.assertRaises(OSError):
                file_sha256(path)

            digest = file_sha256(path, maximum_size=MAX_HASHABLE_FILE_SIZE)
            self.assertEqual(len(digest), 64)

    def test_hash_match_returns_false_for_a_changed_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "conteudo.bin"
            path.write_bytes(b"changed")

            self.assertFalse(file_matches_sha256(path, "0" * 64, 1))


class ManagedFileIdentityTests(unittest.TestCase):
    """Persistent identity must reject aliases and incompatible file types."""

    def test_describes_one_non_sensitive_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "session.cfg"
            path.write_bytes(b"hostname x86QW\n")

            entry = describe_non_sensitive_temporary(path, root, "host config")

            self.assertEqual(entry.path, path)
            self.assertEqual(entry.root, root)
            self.assertEqual(entry.expected_size, 15)
            self.assertEqual(
                entry.expected_hash,
                hashlib.sha256(b"hostname x86QW\n").hexdigest(),
            )
            self.assertEqual(
                entry.identity,
                persistent_path_identity(path, directory=False),
            )
            self.assertTrue(entry.created_by_session)
            self.assertFalse(entry.existed)

    def test_rejects_a_symlink_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_bytes(b"personal")
            alias = root / "alias"
            alias.symlink_to(target)

            with self.assertRaises(OSError):
                persistent_path_identity(alias, directory=False)

    def test_windows_removal_marks_the_validated_handle_for_deletion(self) -> None:
        """Windows cleanup must delete through its guarded object handle."""

        class FakeWindowsApi:
            FILE_READ_ATTRIBUTES = 0x80
            DELETE = 0x10000
            OPEN_EXISTING = 3

            def __init__(self) -> None:
                self.deleted: list[int] = []
                self.closed: list[int] = []

            def open_handle(self, path, *, access, creation, directory):
                return 17

            def checked_identity(self, handle, *, directory):
                return (1, 2)

            def mark_delete(self, handle):
                self.deleted.append(handle)

            def close(self, handle):
                self.closed.append(handle)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "managed.bin"
            path.write_bytes(b"managed")
            metadata = path.lstat()
            expected = (
                int(metadata.st_dev),
                int(metadata.st_ino),
                int(stat.S_IFMT(metadata.st_mode)),
                1,
                2,
            )
            api = FakeWindowsApi()

            with mock.patch(
                "x86qw_runtime.io.managed_files._get_windows_file_api",
                return_value=api,
            ):
                self.assertTrue(
                    remove_identity_bound_path(
                        path, expected, directory=False,
                    )
                )

            self.assertEqual([17], api.deleted)
            self.assertEqual([17], api.closed)

    def test_windows_removal_rejects_a_divergent_handle_identity(self) -> None:
        """A path match cannot authorize deletion of a different handle object."""

        class DivergentWindowsApi:
            FILE_READ_ATTRIBUTES = 0x80
            DELETE = 0x10000
            OPEN_EXISTING = 3

            def __init__(self) -> None:
                self.deleted: list[int] = []

            def open_handle(self, path, *, access, creation, directory):
                return 29

            def checked_identity(self, handle, *, directory):
                return (9, 9)

            def mark_delete(self, handle):
                self.deleted.append(handle)

            def close(self, handle):
                pass

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "managed.bin"
            path.write_bytes(b"managed")
            metadata = path.lstat()
            expected = (
                int(metadata.st_dev),
                int(metadata.st_ino),
                int(stat.S_IFMT(metadata.st_mode)),
                1,
                2,
            )
            api = DivergentWindowsApi()

            with mock.patch(
                "x86qw_runtime.io.managed_files._get_windows_file_api",
                return_value=api,
            ):
                self.assertFalse(
                    remove_identity_bound_path(
                        path, expected, directory=False,
                    )
                )

            self.assertEqual([], api.deleted)


class ManagedCleanupTests(unittest.TestCase):
    """Cleanup removes only the exact unchanged object recorded by the session."""

    def test_removes_the_unchanged_materialized_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "managed.pk3"
            payload = b"managed"
            path.write_bytes(payload)
            entry = describe_non_sensitive_temporary(path, root, "fixture")

            self.assertTrue(cleanup_materialized_file(entry))
            self.assertFalse(os.path.lexists(path))

    def test_preserves_a_replacement_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "managed.pk3"
            path.write_bytes(b"managed")
            entry = describe_non_sensitive_temporary(path, root, "fixture")
            path.unlink()
            path.write_bytes(b"personal")

            self.assertFalse(cleanup_materialized_file(entry))
            self.assertEqual(path.read_bytes(), b"personal")

    def test_removes_only_the_empty_directory_with_matching_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "created"
            directory.mkdir()
            entry = MaterializedDirectory(
                directory,
                root,
                persistent_path_identity(directory, directory=True),
            )

            self.assertTrue(cleanup_materialized_directory(entry))
            self.assertFalse(directory.exists())


class SensitiveTemporaryTests(unittest.TestCase):
    """Sensitive cleanup unlinks names but never traverses replacements."""

    def test_unlinks_a_symlink_without_touching_its_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "personal.cfg"
            target.write_bytes(b"password")
            alias = root / "session.cfg"
            alias.symlink_to(target)

            unlink_sensitive_temporary(alias)

            self.assertFalse(os.path.lexists(alias))
            self.assertEqual(target.read_bytes(), b"password")

    def test_preserves_a_directory_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "session.cfg"
            path.mkdir()
            personal = path / "personal.cfg"
            personal.write_bytes(b"keep")

            with self.assertRaises(InstallerError):
                unlink_sensitive_temporary(path)

            self.assertEqual(personal.read_bytes(), b"keep")

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "socket Unix indisponível")
    def test_preserves_a_special_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "session.sock"
            server = socket.socket(socket.AF_UNIX)
            try:
                server.bind(os.fspath(path))
                with self.assertRaises(InstallerError):
                    unlink_sensitive_temporary(path)
                self.assertTrue(os.path.lexists(path))
            finally:
                server.close()


if __name__ == "__main__":
    unittest.main()
