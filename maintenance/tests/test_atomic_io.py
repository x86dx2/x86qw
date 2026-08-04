from __future__ import annotations

import importlib
import importlib.util
import errno
import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class AtomicWriteTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "fsync de diretório é uma barreira POSIX")
    def test_public_directory_barrier_opens_fsyncs_and_closes_directory(self) -> None:
        atomic = importlib.import_module("x86qw_runtime.io.atomic")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            descriptors: list[int] = []
            real_open = os.open
            real_fsync = os.fsync

            def record_open(path: Path, flags: int) -> int:
                descriptor = real_open(path, flags)
                descriptors.append(descriptor)
                return descriptor

            with mock.patch.object(atomic.os, "open", side_effect=record_open), mock.patch.object(
                atomic.os,
                "fsync",
                wraps=real_fsync,
            ) as fsync:
                atomic.sync_directory(root)

            self.assertEqual(1, len(descriptors))
            fsync.assert_called_once_with(descriptors[0])
            with self.assertRaises(OSError):
                os.fstat(descriptors[0])

    def test_public_directory_barrier_does_not_open_directories_on_windows(self) -> None:
        atomic = importlib.import_module("x86qw_runtime.io.atomic")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(
                atomic.os,
                "name",
                "nt",
            ), mock.patch.object(
                atomic.os,
                "open",
                side_effect=AssertionError("Windows must not open a directory for fsync"),
            ) as opened:
                atomic.sync_directory(root)

        opened.assert_not_called()

    def test_stream_copy_promotes_only_the_expected_complete_payload(self) -> None:
        """Managed component payloads need bounded-memory atomic promotion."""

        atomic = importlib.import_module("x86qw_runtime.io.atomic")
        payload = (b"x86qw-component\n" * 131_072) + b"complete\n"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pk3"
            destination = root / "installed.pk3"
            source.write_bytes(payload)
            destination.write_bytes(b"old\n")

            result = atomic.atomic_copy_file(
                source, destination, expected_sha256=digest,
            )

            self.assertEqual(payload, destination.read_bytes())
            self.assertEqual(len(payload), result.bytes_written)
            self.assertTrue(result.replaced)
            self.assertEqual(list(root.glob(".installed.pk3.*.tmp")), [])

    def test_stream_copy_hash_mismatch_preserves_previous_destination(self) -> None:
        """A divergent staged source must never replace the installed payload."""

        atomic = importlib.import_module("x86qw_runtime.io.atomic")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pk3"
            destination = root / "installed.pk3"
            source.write_bytes(b"unexpected\n")
            destination.write_bytes(b"stable\n")

            with self.assertRaises(atomic.AtomicWriteError) as captured:
                atomic.atomic_copy_file(
                    source, destination, expected_sha256="a" * 64,
                )

            self.assertFalse(captured.exception.committed)
            self.assertEqual(b"stable\n", destination.read_bytes())
            self.assertEqual(list(root.glob(".installed.pk3.*.tmp")), [])

    def test_stream_copy_staging_fsync_failure_preserves_previous_destination(self) -> None:
        """A fully or partially staged copy is not visible before durable promotion."""

        atomic = importlib.import_module("x86qw_runtime.io.atomic")
        payload = b"new component payload\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pk3"
            destination = root / "installed.pk3"
            source.write_bytes(payload)
            destination.write_bytes(b"stable\n")

            with mock.patch.object(
                atomic.os,
                "fsync",
                side_effect=OSError(errno.EIO, "injected staging fsync failure"),
            ):
                with self.assertRaises(atomic.AtomicWriteError) as captured:
                    atomic.atomic_copy_file(
                        source,
                        destination,
                        expected_sha256=hashlib.sha256(payload).hexdigest(),
                    )

            self.assertFalse(captured.exception.committed)
            self.assertEqual(b"stable\n", destination.read_bytes())
            self.assertEqual(list(root.glob(".installed.pk3.*.tmp")), [])

    def test_bytes_replace_existing_file_and_leave_no_staging_file(self) -> None:
        """Skipping promotion or cleanup would expose old bytes or staging debris."""

        spec = importlib.util.find_spec("x86qw_runtime.io.atomic")
        self.assertIsNotNone(spec, "atomic writes must be owned by x86qw_runtime")
        atomic = importlib.import_module("x86qw_runtime.io.atomic")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "state.json"
            destination.write_bytes(b"old\n")

            result = atomic.atomic_write_bytes(destination, b"new\n")

            self.assertEqual(destination.read_bytes(), b"new\n")
            self.assertEqual(result.path, destination)
            self.assertEqual(result.bytes_written, 4)
            self.assertTrue(result.replaced)
            self.assertEqual(list(root.glob(".state.json.*.tmp")), [])
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o644)

    def test_private_json_is_canonical_and_owner_only(self) -> None:
        """Using public mode or unstable JSON would break journal confidentiality/goldens."""

        atomic = importlib.import_module("x86qw_runtime.io.atomic")
        self.assertTrue(
            hasattr(atomic, "atomic_write_json"),
            "the atomic boundary must serialize canonical private JSON",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            if os.name != "nt":
                root.chmod(0o700)
            destination = root / "session.json"

            atomic.atomic_write_json(
                destination,
                {"z": "ação", "a": 1},
                private=True,
            )

            self.assertEqual(
                destination.read_bytes(),
                b'{\n  "a": 1,\n  "z": "a\xc3\xa7\xc3\xa3o"\n}\n',
            )
            self.assertEqual(json.loads(destination.read_text("utf-8"))["z"], "ação")
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    def test_file_fsync_failure_preserves_previous_destination(self) -> None:
        """Promoting bytes that were not fsynced would violate the atomic write contract."""

        atomic = importlib.import_module("x86qw_runtime.io.atomic")
        self.assertTrue(
            hasattr(atomic, "AtomicWriteError"),
            "atomic failures must report whether promotion happened",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "receipt.tsv"
            destination.write_bytes(b"stable\n")

            with mock.patch.object(
                atomic.os,
                "fsync",
                side_effect=OSError(errno.EIO, "injected file fsync failure"),
            ):
                with self.assertRaises(atomic.AtomicWriteError) as captured:
                    atomic.atomic_write_bytes(destination, b"candidate\n")

            self.assertFalse(captured.exception.committed)
            self.assertEqual(destination.read_bytes(), b"stable\n")
            self.assertEqual(list(root.glob(".receipt.tsv.*.tmp")), [])

    def test_replace_failure_preserves_previous_destination(self) -> None:
        """A failed promotion must never truncate or delete the last valid state."""

        atomic = importlib.import_module("x86qw_runtime.io.atomic")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "state.json"
            destination.write_bytes(b"stable\n")

            with mock.patch.object(
                atomic.private_fs,
                "replace_open_private_file",
                side_effect=OSError(errno.EACCES, "injected replace failure"),
            ):
                try:
                    atomic.atomic_write_bytes(destination, b"candidate\n")
                except atomic.AtomicWriteError as error:
                    captured = error
                except OSError as error:
                    self.fail(f"raw OS failure escaped the atomic boundary: {error}")
                else:
                    self.fail("replace failure was reported as success")

            self.assertFalse(captured.committed)
            self.assertEqual(destination.read_bytes(), b"stable\n")
            self.assertEqual(list(root.glob(".state.json.*.tmp")), [])

    @unittest.skipIf(os.name == "nt", "directory fsync is a POSIX durability primitive")
    def test_parent_fsync_failure_reports_that_replacement_committed(self) -> None:
        """A post-rename durability failure must not be reported as a clean rollback."""

        atomic = importlib.import_module("x86qw_runtime.io.atomic")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "state.json"
            destination.write_bytes(b"old\n")
            real_fsync = os.fsync

            def fail_directory_fsync(descriptor: int) -> None:
                if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                    raise OSError(errno.EIO, "injected parent fsync failure")
                real_fsync(descriptor)

            with mock.patch.object(atomic.os, "fsync", side_effect=fail_directory_fsync):
                with self.assertRaises(atomic.AtomicWriteError) as captured:
                    atomic.atomic_write_bytes(destination, b"new\n")

            self.assertTrue(captured.exception.committed)
            self.assertEqual(destination.read_bytes(), b"new\n")
            self.assertEqual(list(root.glob(".state.json.*.tmp")), [])

    def test_destination_symlink_is_rejected_without_touching_target(self) -> None:
        """Following or replacing an untrusted managed symlink would cross ownership."""

        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        atomic = importlib.import_module("x86qw_runtime.io.atomic")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            personal = root / "personal.txt"
            personal.write_bytes(b"personal\n")
            destination = root / "state.json"
            try:
                destination.symlink_to(personal.name)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")

            with self.assertRaises(atomic.AtomicWriteError) as captured:
                atomic.atomic_write_bytes(destination, b"managed\n")

            self.assertFalse(captured.exception.committed)
            self.assertTrue(destination.is_symlink())
            self.assertEqual(personal.read_bytes(), b"personal\n")
            self.assertEqual(list(root.glob(".state.json.*.tmp")), [])

    def test_staging_identity_change_is_preserved_for_inspection(self) -> None:
        """Cleanup must not unlink an attacker replacement occupying the temp name."""

        atomic = importlib.import_module("x86qw_runtime.io.atomic")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "state.json"
            destination.write_bytes(b"stable\n")
            replacement: Path | None = None

            def replace_with_foreign_file(
                _descriptor: int,
                source: os.PathLike[str],
                _target: os.PathLike[str],
            ) -> None:
                nonlocal replacement
                replacement = Path(source)
                replacement.unlink()
                replacement.write_bytes(b"foreign\n")
                raise OSError(errno.EACCES, "injected identity swap")

            with mock.patch.object(
                atomic.private_fs,
                "replace_open_private_file",
                side_effect=replace_with_foreign_file,
            ):
                try:
                    atomic.atomic_write_bytes(destination, b"candidate\n")
                except atomic.AtomicWriteError as error:
                    captured = error
                except OSError as error:
                    self.fail(f"cleanup escaped the atomic error contract: {error}")
                else:
                    self.fail("identity swap was reported as success")

            self.assertFalse(captured.committed)
            self.assertIsNotNone(captured.cleanup_error)
            self.assertEqual(destination.read_bytes(), b"stable\n")
            self.assertIsNotNone(replacement)
            assert replacement is not None
            self.assertEqual(replacement.read_bytes(), b"foreign\n")

    @unittest.skipIf(os.name == "nt", "POSIX mode durability is not a Windows contract")
    def test_final_mode_is_fsynced_before_success(self) -> None:
        """Returning before syncing chmod could lose the declared managed mode."""

        atomic = importlib.import_module("x86qw_runtime.io.atomic")
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "inventory.tsv"
            real_fsync = os.fsync
            synced_regular_modes: list[int] = []

            def record_mode(descriptor: int) -> None:
                metadata = os.fstat(descriptor)
                if stat.S_ISREG(metadata.st_mode):
                    synced_regular_modes.append(stat.S_IMODE(metadata.st_mode))
                real_fsync(descriptor)

            with mock.patch.object(atomic.os, "fsync", side_effect=record_mode):
                atomic.atomic_write_bytes(destination, b"entry\n", mode=0o644)

            self.assertEqual(synced_regular_modes, [0o600, 0o644])
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o644)


if __name__ == "__main__":
    unittest.main()
