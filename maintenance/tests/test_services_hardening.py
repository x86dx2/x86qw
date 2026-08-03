import argparse
import contextlib
import io
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dist/installer/bin"))
import services  # noqa: E402


class FakeWindowsFileApi:
    """Portable filesystem-backed stand-in for the narrow Win32 handle API."""

    GENERIC_READ = services._WindowsFileApi.GENERIC_READ
    GENERIC_WRITE = services._WindowsFileApi.GENERIC_WRITE
    DELETE = services._WindowsFileApi.DELETE
    FILE_READ_ATTRIBUTES = services._WindowsFileApi.FILE_READ_ATTRIBUTES
    CREATE_NEW = services._WindowsFileApi.CREATE_NEW
    OPEN_EXISTING = services._WindowsFileApi.OPEN_EXISTING

    def __init__(self):
        self.paths = {}
        self.moves = []
        self.deleted = []
        self.next_handle = 1

    def open_handle(self, path, *, access, creation, directory):
        path = Path(path)
        stream = None
        if directory:
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
                raise OSError("diretório inseguro")
        else:
            mode = "x+b" if creation == self.CREATE_NEW else "rb"
            stream = path.open(mode)
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                stream.close()
                raise OSError("arquivo inseguro")
        handle = self.next_handle
        self.next_handle += 1
        self.paths[handle] = {
            "path": path,
            "directory": directory,
            "stream": stream,
            "identity": (metadata.st_dev, metadata.st_ino),
            "delete": False,
        }
        return handle

    def close(self, handle):
        opened = self.paths.pop(handle)
        stream = opened["stream"]
        if stream is not None:
            stream.close()
        if opened["delete"]:
            path = opened["path"]
            metadata = path.lstat()
            if (metadata.st_dev, metadata.st_ino) != opened["identity"]:
                raise OSError("nome substituído")
            if opened["directory"]:
                path.rmdir()
            else:
                path.unlink()
            self.deleted.append(path)

    def checked_identity(self, handle, *, directory):
        opened = self.paths[handle]
        if opened["directory"] != directory:
            raise OSError("tipo incompatível")
        return opened["identity"]

    def write(self, handle, payload):
        self.paths[handle]["stream"].write(payload)

    def flush(self, handle):
        stream = self.paths[handle]["stream"]
        stream.flush()
        os.fsync(stream.fileno())

    def size(self, handle):
        return os.fstat(self.paths[handle]["stream"].fileno()).st_size

    def hash(self, handle, *, expected_size):
        stream = self.paths[handle]["stream"]
        limit = services._assert_hashable_size(self.size(handle), expected_size)
        stream.seek(0)
        digest = services.hashlib.sha256()
        total = 0
        while True:
            block = stream.read(min(1024 * 1024, limit - total + 1))
            if not block:
                break
            total += len(block)
            if total > limit:
                raise OSError("arquivo excedeu o limite")
            digest.update(block)
        if expected_size is not None and total != expected_size:
            raise OSError("tamanho divergente")
        if self.size(handle) != total:
            raise OSError("arquivo mudou durante o hashing")
        return digest.hexdigest()

    def move_no_replace(self, source, destination):
        if os.path.lexists(destination):
            raise FileExistsError(destination)
        os.rename(source, destination)
        self.moves.append((Path(source), Path(destination)))

    def mark_delete(self, handle):
        opened = self.paths[handle]
        path = opened["path"]
        current = path.lstat()
        if opened["identity"] != (current.st_dev, current.st_ino):
            raise OSError("nome substituído")
        opened["delete"] = True


class ServiceHardeningTests(unittest.TestCase):
    def package(self, root: Path, members: list[tuple[str, bytes]]) -> tuple[Path, Path]:
        destination = root / "qw"
        destination.mkdir()
        package = destination / "ktx.pk3"
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in members:
                archive.writestr(name, payload)
        return package, destination

    def assert_unsafe_archive(self, names: list[str]) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_names = [name.replace("\\", "/") for name in names]
            package, destination = self.package(
                Path(temporary), [(name, b"payload") for name in archive_names],
            )
            # ZipInfo sanitizes the host separator while writing on Windows.
            # Patch both equal-length name records so this remains a real ZIP
            # with the hostile spelling an external archive may contain.
            contents = package.read_bytes()
            for original, archive_name in zip(names, archive_names):
                if original != archive_name:
                    self.assertIn(archive_name.encode("utf-8"), contents)
                    contents = contents.replace(
                        archive_name.encode("utf-8"), original.encode("utf-8")
                    )
            package.write_bytes(contents)
            with self.assertRaises(services.InstallerError):
                services.materialize_dedicated_pk3(package, destination, "teste")

    def test_zip_rejects_traversal_drives_backslashes_and_empty_components(self):
        for names in (["../escape"], ["C:/drive.cfg"], ["a\\b.cfg"], ["a//b.cfg"], ["./a.cfg"]):
            with self.subTest(names=names):
                self.assert_unsafe_archive(names)

    def test_zip_rejects_windows_reserved_and_trailing_names(self):
        for name in ("CON", "con.cfg", "NUL.txt", "COM9.dat", "LPT1", "name. ", "name."):
            with self.subTest(name=name):
                self.assert_unsafe_archive([name])

    def test_zip_rejects_case_and_unicode_collisions(self):
        self.assert_unsafe_archive(["Config.cfg", "config.cfg"])
        self.assert_unsafe_archive(["caf\N{LATIN SMALL LETTER E WITH ACUTE}.cfg", "cafe\N{COMBINING ACUTE ACCENT}.cfg"])

    def test_zip_rejects_symlinks_and_abnormal_compression(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination = self.package(Path(temporary), [])
            info = zipfile.ZipInfo("link.cfg")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr(info, b"target")
            with self.assertRaises(services.InstallerError):
                services.materialize_dedicated_pk3(package, destination, "teste")
        with tempfile.TemporaryDirectory() as temporary:
            package, destination = self.package(
                Path(temporary), [("bomb.cfg", b"0" * (2 * 1024 * 1024))],
            )
            with self.assertRaises(services.InstallerError):
                services.materialize_dedicated_pk3(package, destination, "teste")

    def test_pk3_materialization_and_cleanup_are_reversible(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"

            materialized = services.materialize_dedicated_pk3(
                package, destination_root, "teste",
            )
            self.assertEqual(b"managed", destination.read_bytes())
            self.assertTrue(materialized.files[0].created_by_session)

            services.cleanup_dedicated_ktx(materialized)
            self.assertFalse(destination.exists())
            self.assertFalse(destination.parent.exists())
            self.assertTrue(package.exists())

    def test_pk3_journal_records_created_directory_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, destination_root = self.package(
                root, [("configs/server.cfg", b"managed")],
            )
            journal = services.SessionJournal(root)

            materialized = services.materialize_dedicated_pk3(
                package, destination_root, "teste", journal,
            )

            recorded = journal.data["created_directories"]
            self.assertEqual(1, len(recorded))
            self.assertEqual("qw/configs", recorded[0]["path"])
            self.assertIsInstance(recorded[0]["device"], int)
            self.assertIsInstance(recorded[0]["inode"], int)
            self.assertEqual(
                materialized.directories[0].identity,
                (recorded[0]["device"], recorded[0]["inode"]),
            )
            recorded_file = journal.data["materialized_files"][0]
            self.assertEqual(len(b"managed"), recorded_file["expected_size"])
            services.cleanup_dedicated_ktx(materialized)

    def test_managed_hashing_rejects_oversize_before_reading_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "oversize.cfg"
            with path.open("wb") as output:
                output.truncate(8 * 1024 * 1024)
            descriptor = os.open(path, os.O_RDONLY)
            try:
                with mock.patch.object(services.os, "read", wraps=os.read) as read:
                    with self.assertRaises(OSError):
                        services._hash_open_file(descriptor, expected_size=7)
                read.assert_not_called()
            finally:
                os.close(descriptor)
            with self.assertRaises(OSError):
                services.file_sha256(path, expected_size=7)

    def test_managed_hashing_caps_legacy_files_without_expected_size(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "oversize.cfg"
            with path.open("wb") as output:
                output.truncate(services._MAX_MANAGED_FILE_SIZE + 1)
            with self.assertRaises(OSError):
                services.file_sha256(path)

    @unittest.skipUnless(
        services._secure_archive_dir_fd_supported() or os.name == "nt",
        "recuperação ancorada requer handles POSIX ou Win32",
    )
    def test_pk3_recovery_uses_recorded_file_and_directory_identities(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, destination_root = self.package(
                root, [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            journal = services.SessionJournal(root)
            services.materialize_dedicated_pk3(
                package, destination_root, "teste", journal,
            )

            services.recover_sessions(root)

            self.assertFalse(destination.exists())
            self.assertFalse(destination.parent.exists())
            self.assertEqual("clean", json.loads(
                journal.path.read_text(encoding="utf-8"),
            )["status"])

    @unittest.skipUnless(
        services._secure_archive_dir_fd_supported(),
        "corrida requer operações POSIX relativas a descritor",
    )
    def test_pk3_never_overwrites_personal_file_created_before_promotion(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            real_link = os.link

            def create_personal_then_link(source, target, **kwargs):
                descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=kwargs["dst_dir_fd"],
                )
                with os.fdopen(descriptor, "wb") as output:
                    output.write(b"personal")
                return real_link(source, target, **kwargs)

            with mock.patch.object(services.os, "link", side_effect=create_personal_then_link):
                with self.assertRaisesRegex(
                    services.InstallerError, "surgiu durante a preparação",
                ):
                    services.materialize_dedicated_pk3(
                        package, destination_root, "teste",
                    )

            self.assertEqual(b"personal", destination.read_bytes())
            self.assertEqual([], list(destination.parent.glob(".x86qw_ktx_*")))

    @unittest.skipUnless(
        services._secure_archive_dir_fd_supported(),
        "corrida requer operações POSIX relativas a descritor",
    )
    def test_pk3_parent_swapped_to_symlink_causes_zero_external_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, destination_root = self.package(
                root, [("configs/server.cfg", b"managed")],
            )
            parent = destination_root / "configs"
            parent.mkdir()
            original_parent = destination_root / "configs-original"
            external = root / "external"
            external.mkdir()
            marker = external / "personal.txt"
            marker.write_text("personal", encoding="utf-8")
            real_open_parent = services._secure_archive_parent
            calls = 0

            def swap_before_second_pass(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    parent.rename(original_parent)
                    parent.symlink_to(external, target_is_directory=True)
                return real_open_parent(*args, **kwargs)

            with mock.patch.object(
                services, "_secure_archive_parent", side_effect=swap_before_second_pass,
            ):
                with self.assertRaisesRegex(services.InstallerError, "Diretório inseguro"):
                    services.materialize_dedicated_pk3(
                        package, destination_root, "teste",
                    )

            self.assertEqual([marker], list(external.iterdir()))
            self.assertEqual("personal", marker.read_text(encoding="utf-8"))
            self.assertEqual([], list(original_parent.iterdir()))

    @unittest.skipUnless(
        services._secure_archive_dir_fd_supported(),
        "corrida requer operações POSIX relativas a descritor",
    )
    def test_pk3_detects_destination_replaced_after_atomic_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            real_link = os.link

            def replace_after_link(source, target, **kwargs):
                result = real_link(source, target, **kwargs)
                os.unlink(target, dir_fd=kwargs["dst_dir_fd"])
                descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=kwargs["dst_dir_fd"],
                )
                with os.fdopen(descriptor, "wb") as output:
                    output.write(b"personal")
                return result

            with mock.patch.object(services.os, "link", side_effect=replace_after_link):
                with self.assertRaisesRegex(
                    services.InstallerError, "substituído durante a preparação",
                ):
                    services.materialize_dedicated_pk3(
                        package, destination_root, "teste",
                    )

            self.assertEqual(b"personal", destination.read_bytes())

    @unittest.skipUnless(
        services._secure_archive_dir_fd_supported(),
        "rollback requer operações POSIX relativas a descritor",
    )
    def test_pk3_posix_journal_failure_preserves_modified_promoted_inode(self):
        class FailingJournal:
            def record_directory(self, entry):
                return None

            def record_materialized(self, entry):
                entry.path.write_bytes(b"personal-concurrent-data")
                raise RuntimeError("journal indisponível")

        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            output = io.StringIO()

            with contextlib.redirect_stdout(output), self.assertRaisesRegex(
                services.InstallerError, "journal indisponível",
            ):
                services.materialize_dedicated_pk3(
                    package, destination_root, "teste", FailingJournal(),
                )

            self.assertEqual(b"personal-concurrent-data", destination.read_bytes())
            self.assertIn("foi preservado", output.getvalue())

    def test_pk3_fallback_detects_destination_replaced_after_atomic_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination_root = root / "qw"
            destination_root.mkdir()
            source = root / "source.cfg"
            source.write_bytes(b"managed")
            destination = destination_root / "server.cfg"
            member = SimpleNamespace(
                path=services.PurePosixPath("server.cfg"),
                size=len(b"managed"),
                sha256=services.hashlib.sha256(b"managed").hexdigest(),
            )
            real_link = os.link

            def replace_after_link(source_path, target_path, **kwargs):
                result = real_link(source_path, target_path, **kwargs)
                Path(target_path).unlink()
                Path(target_path).write_bytes(b"personal")
                return result

            with mock.patch.object(services.os, "link", side_effect=replace_after_link):
                with self.assertRaisesRegex(
                    services.InstallerError, "substituído durante a preparação",
                ):
                    services._fallback_materialize_member(
                        source, destination, member, "teste", destination_root,
                    )

            self.assertEqual(b"personal", destination.read_bytes())
            self.assertEqual([], list(destination_root.glob(".x86qw_ktx_*")))

    @unittest.skipUnless(
        services._secure_archive_dir_fd_supported(),
        "corrida requer operações POSIX relativas a descritor",
    )
    def test_pk3_cleanup_preserves_file_replaced_after_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            materialized = services.materialize_dedicated_pk3(
                package, destination_root, "teste",
            )
            destination = destination_root / "configs/server.cfg"
            real_hash = services._hash_open_file
            replaced = False

            def replace_after_hash(descriptor, **kwargs):
                nonlocal replaced
                digest = real_hash(descriptor, **kwargs)
                if not replaced:
                    replaced = True
                    destination.unlink()
                    destination.write_bytes(b"personal")
                return digest

            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), mock.patch.object(
                services, "_hash_open_file", side_effect=replace_after_hash,
            ):
                services.cleanup_dedicated_ktx(materialized)

            self.assertEqual(b"personal", destination.read_bytes())
            self.assertEqual([], list(destination.parent.glob(".x86qw_cleanup_*")))
            self.assertIn("foi preservado", output.getvalue())

    @unittest.skipUnless(
        services._secure_archive_dir_fd_supported(),
        "quarentena requer operações POSIX relativas a descritor",
    )
    def test_pk3_cleanup_preserves_same_inode_modified_after_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            materialized = services.materialize_dedicated_pk3(
                package, destination_root, "teste",
            )
            destination = destination_root / "configs/server.cfg"
            original_identity = destination.stat().st_ino
            real_hash = services._hash_open_file
            calls = 0

            def modify_same_inode_after_hash(descriptor, **kwargs):
                nonlocal calls
                digest = real_hash(descriptor, **kwargs)
                calls += 1
                if calls == 1:
                    destination.write_bytes(b"personal-concurrent-data")
                    self.assertEqual(original_identity, destination.stat().st_ino)
                return digest

            output = io.StringIO()
            with contextlib.redirect_stdout(output), mock.patch.object(
                services, "_hash_open_file", side_effect=modify_same_inode_after_hash,
            ):
                services.cleanup_dedicated_ktx(materialized)

            self.assertEqual(b"personal-concurrent-data", destination.read_bytes())
            self.assertEqual([], list(destination.parent.glob(".x86qw_cleanup_*")))
            self.assertIn("foi preservado", output.getvalue())

    @unittest.skipUnless(
        services._secure_archive_dir_fd_supported()
        and services._get_posix_rename_api() is not None,
        "rename exclusivo requer Linux ou macOS compatível",
    )
    def test_posix_exclusive_rename_never_replaces_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.write_text("managed", encoding="utf-8")
            destination.write_text("personal", encoding="utf-8")
            descriptor = os.open(root, services._directory_open_flags())
            api = services._get_posix_rename_api()
            self.assertIsNotNone(api)
            try:
                with self.assertRaises(FileExistsError):
                    api.move_no_replace(descriptor, source.name, descriptor, destination.name)
                self.assertEqual("managed", source.read_text(encoding="utf-8"))
                self.assertEqual("personal", destination.read_text(encoding="utf-8"))
                destination.unlink()
                api.move_no_replace(descriptor, source.name, descriptor, destination.name)
            finally:
                os.close(descriptor)
            self.assertFalse(source.exists())
            self.assertEqual("managed", destination.read_text(encoding="utf-8"))

    @unittest.skipUnless(
        services._secure_archive_dir_fd_supported()
        and services._get_posix_rename_api() is not None,
        "rename exclusivo requer Linux ou macOS compatível",
    )
    def test_pk3_cleanup_preserves_public_replacement_at_atomic_move(self):
        for timing in ("before", "after"):
            with self.subTest(timing=timing), tempfile.TemporaryDirectory() as temporary:
                package, destination_root = self.package(
                    Path(temporary), [("configs/server.cfg", b"managed")],
                )
                materialized = services.materialize_dedicated_pk3(
                    package, destination_root, "teste",
                )
                destination = destination_root / "configs/server.cfg"
                api = services._get_posix_rename_api()
                self.assertIsNotNone(api)
                real_move = api.move_no_replace
                triggered = False

                def race_public_name(source_directory, source_name, destination_directory, destination_name):
                    nonlocal triggered
                    if not triggered and source_name == destination.name:
                        triggered = True
                        if timing == "before":
                            os.unlink(source_name, dir_fd=source_directory)
                            descriptor = os.open(
                                source_name,
                                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                0o600,
                                dir_fd=source_directory,
                            )
                            with os.fdopen(descriptor, "wb") as output:
                                output.write(b"personal-concurrent-data")
                            return real_move(
                                source_directory,
                                source_name,
                                destination_directory,
                                destination_name,
                            )
                        result = real_move(
                            source_directory,
                            source_name,
                            destination_directory,
                            destination_name,
                        )
                        descriptor = os.open(
                            source_name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                            0o600,
                            dir_fd=source_directory,
                        )
                        with os.fdopen(descriptor, "wb") as output:
                            output.write(b"personal-concurrent-data")
                        return result
                    return real_move(
                        source_directory,
                        source_name,
                        destination_directory,
                        destination_name,
                    )

                with mock.patch.object(api, "move_no_replace", side_effect=race_public_name):
                    services.cleanup_dedicated_ktx(materialized)

                self.assertTrue(triggered)
                self.assertEqual(b"personal-concurrent-data", destination.read_bytes())
                self.assertEqual([], list(destination.parent.glob(".x86qw_cleanup_*")))

    @unittest.skipUnless(
        services._secure_archive_dir_fd_supported(),
        "fail-closed requer operações POSIX relativas a descritor",
    )
    def test_pk3_cleanup_preserves_when_exclusive_rename_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            materialized = services.materialize_dedicated_pk3(
                package, destination_root, "teste",
            )
            destination = destination_root / "configs/server.cfg"
            output = io.StringIO()
            with contextlib.redirect_stdout(output), mock.patch.object(
                services, "_get_posix_rename_api", return_value=None,
            ):
                services.cleanup_dedicated_ktx(materialized)
            self.assertEqual(b"managed", destination.read_bytes())
            self.assertIn("foi preservado", output.getvalue())

    def test_pk3_fallback_cleanup_never_unlinks_a_replacement_by_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "server.cfg"
            destination.write_bytes(b"managed")
            metadata = destination.lstat()
            entry = services.MaterializedFile(
                destination,
                services.hashlib.sha256(b"managed").hexdigest(),
                "fixture.pk3",
                True,
                False,
                root,
                (metadata.st_dev, metadata.st_ino),
            )
            destination.unlink()
            destination.write_bytes(b"personal")
            output = io.StringIO()

            with contextlib.redirect_stdout(output), mock.patch.object(
                services, "_secure_archive_dir_fd_supported", return_value=False,
            ), mock.patch.object(
                Path, "unlink", side_effect=AssertionError("unlink por caminho proibido"),
            ):
                services.cleanup_dedicated_ktx(
                    services.MaterializedKtx((entry,), (), root),
                )

            self.assertEqual(b"personal", destination.read_bytes())
            self.assertIn("foi preservado", output.getvalue())

    def test_pk3_windows_backend_promotes_without_hardlink_and_cleans_normally(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            api = FakeWindowsFileApi()

            with mock.patch.object(services, "_WINDOWS_FILE_API", api), mock.patch.object(
                services,
                "_fallback_materialize_member",
                side_effect=AssertionError("fallback hardlink não deve ser usado"),
            ):
                materialized = services.materialize_dedicated_pk3(
                    package, destination_root, "teste",
                )
                self.assertEqual(b"managed", destination.read_bytes())
                self.assertEqual(1, len(api.moves))
                self.assertEqual([], list(destination.parent.glob(".x86qw_ktx_*")))

                services.cleanup_dedicated_ktx(materialized)

            self.assertFalse(destination.exists())
            self.assertFalse(destination.parent.exists())

    def test_pk3_windows_backend_journal_failure_does_not_orphan_promotion(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            api = FakeWindowsFileApi()
            journal = mock.Mock()
            journal.record_materialized.side_effect = RuntimeError("journal indisponível")

            with mock.patch.object(services, "_WINDOWS_FILE_API", api):
                with self.assertRaisesRegex(services.InstallerError, "journal indisponível"):
                    services.materialize_dedicated_pk3(
                        package, destination_root, "teste", journal,
                    )

            self.assertFalse(destination.exists())
            self.assertFalse(destination.parent.exists())
            self.assertEqual([], list(destination_root.rglob(".x86qw_ktx_*")))
            journal.record_materialized.assert_called_once()

    def test_pk3_windows_backend_journal_failure_preserves_modified_same_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            api = FakeWindowsFileApi()
            journal = mock.Mock()

            def modify_then_fail(entry):
                entry.path.write_bytes(b"personal-concurrent-data")
                raise RuntimeError("journal indisponível")

            journal.record_materialized.side_effect = modify_then_fail

            with mock.patch.object(services, "_WINDOWS_FILE_API", api):
                with self.assertRaisesRegex(services.InstallerError, "journal indisponível"):
                    services.materialize_dedicated_pk3(
                        package, destination_root, "teste", journal,
                    )

            self.assertEqual(b"personal-concurrent-data", destination.read_bytes())
            journal.record_materialized.assert_called_once()

    def test_pk3_windows_backend_no_replace_preserves_file_appearing_at_promotion(self):
        class AppearingDestinationApi(FakeWindowsFileApi):
            def move_no_replace(self, source, destination):
                Path(destination).write_bytes(b"personal")
                return super().move_no_replace(source, destination)

        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            api = AppearingDestinationApi()

            with mock.patch.object(services, "_WINDOWS_FILE_API", api):
                with self.assertRaisesRegex(services.InstallerError, "surgiu durante"):
                    services.materialize_dedicated_pk3(
                        package, destination_root, "teste",
                    )

            self.assertEqual(b"personal", destination.read_bytes())
            self.assertEqual([], list(destination.parent.glob(".x86qw_ktx_*")))

    def test_pk3_windows_backend_inconclusive_open_preserves_modified_same_identity(self):
        class ModifiedBeforeConfirmationApi(FakeWindowsFileApi):
            fail_path = None

            def move_no_replace(self, source, destination):
                super().move_no_replace(source, destination)
                destination = Path(destination)
                destination.write_bytes(b"personal-concurrent-data")
                self.fail_path = destination

            def open_handle(self, path, *, access, creation, directory):
                if self.fail_path is not None and Path(path) == self.fail_path:
                    self.fail_path = None
                    raise OSError("confirmação pós-promoção inconclusiva")
                return super().open_handle(
                    path, access=access, creation=creation, directory=directory,
                )

        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            api = ModifiedBeforeConfirmationApi()

            with mock.patch.object(services, "_WINDOWS_FILE_API", api):
                with self.assertRaisesRegex(
                    services.InstallerError, "alterado foi preservado",
                ):
                    services.materialize_dedicated_pk3(
                        package, destination_root, "teste",
                    )

            self.assertEqual(b"personal-concurrent-data", destination.read_bytes())
            self.assertEqual([], list(destination.parent.glob(".x86qw_ktx_*")))

    def test_pk3_windows_backend_cleanup_preserves_replacement_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            api = FakeWindowsFileApi()

            with mock.patch.object(services, "_WINDOWS_FILE_API", api):
                materialized = services.materialize_dedicated_pk3(
                    package, destination_root, "teste",
                )
                destination.unlink()
                destination.write_bytes(b"personal")
                services.cleanup_dedicated_ktx(materialized)

            self.assertEqual(b"personal", destination.read_bytes())

    def test_pk3_windows_backend_rejects_reparse_parent_without_external_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, destination_root = self.package(
                root, [("configs/server.cfg", b"managed")],
            )
            external = root / "external"
            external.mkdir()
            marker = external / "personal.txt"
            marker.write_text("personal", encoding="utf-8")
            link = destination_root / "configs"
            try:
                link.symlink_to(external, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink de diretório indisponível: {error}")

            with mock.patch.object(services, "_WINDOWS_FILE_API", FakeWindowsFileApi()):
                with self.assertRaisesRegex(services.InstallerError, "Diretório inseguro"):
                    services.materialize_dedicated_pk3(
                        package, destination_root, "teste",
                    )

            self.assertEqual([marker], list(external.iterdir()))

    @unittest.skipUnless(os.name == "nt", "handles de arquivo são exercitados no runner Windows")
    def test_pk3_windows_native_materialization_identity_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            api = services._get_windows_file_api()
            self.assertIsNotNone(api)

            materialized = services.materialize_dedicated_pk3(
                package, destination_root, "teste",
            )
            handle = api.open_handle(
                destination,
                access=api.GENERIC_READ,
                creation=api.OPEN_EXISTING,
                directory=False,
            )
            try:
                self.assertEqual(
                    materialized.files[0].identity,
                    api.checked_identity(handle, directory=False),
                )
            finally:
                api.close(handle)
            self.assertEqual([], list(destination.parent.glob(".x86qw_ktx_*")))

            services.cleanup_dedicated_ktx(materialized)

            self.assertFalse(destination.exists())
            self.assertFalse(destination.parent.exists())

    @unittest.skipUnless(os.name == "nt", "replacement é exercitado no runner Windows")
    def test_pk3_windows_native_cleanup_preserves_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            materialized = services.materialize_dedicated_pk3(
                package, destination_root, "teste",
            )
            destination.unlink()
            destination.write_bytes(b"personal")

            services.cleanup_dedicated_ktx(materialized)

            self.assertEqual(b"personal", destination.read_bytes())

    @unittest.skipUnless(os.name == "nt", "reparse point é exercitado no runner Windows")
    def test_pk3_windows_native_reparse_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, destination_root = self.package(
                root, [("configs/server.cfg", b"managed")],
            )
            external = root / "external"
            external.mkdir()
            marker = external / "personal.txt"
            marker.write_text("personal", encoding="utf-8")
            link = destination_root / "configs"
            try:
                link.symlink_to(external, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"privilégio para symlink indisponível: {error}")

            with self.assertRaisesRegex(services.InstallerError, "Diretório inseguro"):
                services.materialize_dedicated_pk3(
                    package, destination_root, "teste",
                )

            self.assertEqual([marker], list(external.iterdir()))

    @unittest.skipUnless(
        services._secure_archive_dir_fd_supported() or os.name == "nt",
        "identidade de diretório requer handles POSIX ou Win32",
    )
    def test_pk3_cleanup_preserves_replacement_directory_with_new_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            materialized = services.materialize_dedicated_pk3(
                package, destination_root, "teste",
            )
            directory_entry = materialized.directories[0]
            self.assertTrue(services._cleanup_materialized_file(materialized.files[0]))
            directory_entry.path.rmdir()
            directory_entry.path.mkdir()
            replacement_identity = services._file_identity(directory_entry.path.lstat())
            self.assertNotEqual(directory_entry.identity, replacement_identity)

            self.assertFalse(services._cleanup_materialized_directory(directory_entry))
            self.assertTrue(directory_entry.path.is_dir())

    def test_endpoint_parser_accepts_ipv4_ipv6_and_hostname(self):
        self.assertEqual("127.0.0.1:28501", services.parse_network_endpoint("127.0.0.1:28501"))
        self.assertEqual("[2001:db8::1]:28501", services.parse_network_endpoint("[2001:db8::1]:28501"))
        self.assertEqual("quake.example:28501", services.parse_network_endpoint("Quake.Example:28501"))
        for value in ("2001:db8::1:28501", "host", "host:0", "host:70000", "host:1;quit", "bad host:1"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                services.parse_network_endpoint(value)

    def test_password_prompt_and_private_file_do_not_echo_secret(self):
        options = SimpleNamespace(
            password="", prompt_password=True, password_file=None,
            spectator_password="", prompt_spectator_password=False, spectator_password_file=None,
            rcon_password="", prompt_rcon_password=False, rcon_password_file=None,
            qtv_password="", prompt_qtv_password=False, qtv_password_file=None,
        )
        output = io.StringIO()
        with mock.patch.object(services.getpass, "getpass", return_value="muito-secreta"), contextlib.redirect_stdout(output):
            services.resolve_passwords(options)
        self.assertEqual("muito-secreta", options.password)
        self.assertNotIn("muito-secreta", output.getvalue())
        with tempfile.TemporaryDirectory() as temporary:
            password_file = Path(temporary) / "secret"
            password_file.write_text("arquivo-secreto\n", encoding="utf-8")
            if os.name != "nt":
                password_file.chmod(0o600)
            self.assertEqual("arquivo-secreto", services.read_password_file(password_file, "senha"))

    def test_passwords_are_kept_out_of_child_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / "td2").mkdir()
            options = services.parse_arguments([
                "host", "td2", "--map", "dm6", "--password", "jogador-secreto",
                "--spectator-password", "espectador-secreto",
                "--rcon-password", "rcon-secreto", "--target", str(target),
            ], ROOT)
            game = next(game for game in services.gameplay.LOCAL_GAMES if game.key == "td2")
            selection = services.HostedGame(game, None, "dm6", frozenset(), options.ktx_options)
            with mock.patch.object(services, "runtime_binary", return_value=target / "mvdsv"), mock.patch.object(
                services, "materialize_hosted_game", return_value=None,
            ):
                spec = services.host_spec(SimpleNamespace(target=target), options, selection, [], [])
            command = " ".join(spec.arguments)
            self.assertNotIn("jogador-secreto", command)
            self.assertNotIn("espectador-secreto", command)
            self.assertNotIn("rcon-secreto", command)

    def test_qtv_readiness_confirms_http_and_upstream(self):
        process = mock.Mock()
        process.poll.return_value = None
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.side_effect = [
            b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n"
            b'<td class="adr">127.0.0.1:28501</td>',
            b"",
        ]
        with mock.patch.object(services.socket, "create_connection", return_value=connection):
            services.wait_http_readiness(
                process,
                services.ServiceReadiness("http", "127.0.0.1", 28000, "127.0.0.1:28501"),
                timeout=0.1,
            )
        connection.sendall.assert_called_once_with(
            b"GET /nowplaying/ HTTP/1.0\r\nHost: x86qw.local\r\n\r\n",
        )

    def test_qtv_http_readiness_requires_success_and_the_complete_upstream(self):
        page = (
            b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n"
            b'<td class="adr">[::1]:28501</td>'
        )
        self.assertTrue(services.qtv_http_response_ready(page, "[::1]:28501"))
        self.assertFalse(services.qtv_http_response_ready(page, "[::1]:28502"))
        self.assertFalse(services.qtv_http_response_ready(
            b"HTTP/1.0 301 Moved Permanently\r\nLocation: /nowplaying/\r\n\r\n",
            "[::1]:28501",
        ))

    @unittest.skipIf(os.name == "nt", "ACLs do Windows não usam bits POSIX")
    def test_password_file_rejects_open_permissions_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            password_file = root / "secret"
            password_file.write_text("segredo", encoding="utf-8")
            password_file.chmod(0o644)
            with self.assertRaisesRegex(services.InstallerError, "Permissões inseguras") as raised:
                services.read_password_file(password_file, "senha")
            self.assertNotIn("segredo", str(raised.exception))
            password_file.chmod(0o600)
            link = root / "link"
            link.symlink_to(password_file)
            with self.assertRaises(services.InstallerError):
                services.read_password_file(link, "senha")

    def test_preflight_rejects_duplicate_and_occupied_ports_before_start(self):
        with self.assertRaisesRegex(services.InstallerError, "duplicada"):
            services.preflight_ports([
                ("MVDSV", "127.0.0.1", 28501, "udp"),
                ("QTV", "127.0.0.1", 28501, "tcp"),
            ])
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind(("127.0.0.1", 0))
            port = occupied.getsockname()[1]
            with self.assertRaisesRegex(services.InstallerError, "não está disponível"):
                services.preflight_ports([("QTV", "127.0.0.1", port, "tcp")])

    def test_session_recovery_removes_only_unchanged_created_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            created = target / "qw" / "created.cfg"
            created.parent.mkdir()
            created.write_text("managed", encoding="utf-8")
            journal = services.SessionJournal(target)
            journal.record_materialized(services.MaterializedFile(
                created, services.file_sha256(created), "fixture.pk3", True, False,
            ))
            services.recover_sessions(target)
            self.assertFalse(created.exists())
            recovered = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertEqual("clean", recovered["status"])

    def test_recovery_accepts_clean_legacy_journal_without_new_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            session = target / ".x86qw/sessions/legacy-clean"
            session.mkdir(parents=True)
            path = session / "session.json"
            legacy = {
                "format": 1,
                "project": "x86qw",
                "session_id": "legacy-clean",
                "created_at": "2026-07-31T18:57:49+00:00",
                "status": "clean",
                "processes": [{"label": "QTV", "pid": os.getpid()}],
                "temporary_files": [{
                    "path": "qtv/old-session.cfg",
                    "origin": "configuração efêmera",
                    "created_by_session": True,
                    "expected_hash": "a" * 64,
                }],
                "materialized_files": [],
                "created_directories": [],
            }
            path.write_text(json.dumps(legacy), encoding="utf-8")

            with mock.patch.object(
                services, "process_identity",
                side_effect=AssertionError("sessão limpa não deve consultar PID"),
            ):
                services.recover_sessions(target)

            self.assertEqual(legacy, json.loads(path.read_text(encoding="utf-8")))

    def test_recovery_treats_unclassified_legacy_temporary_as_sensitive(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            config = target / "qw/old-session.cfg"
            config.parent.mkdir(parents=True)
            secret = "segredo-legado"
            config.write_text(secret, encoding="utf-8")
            session = target / ".x86qw/sessions/legacy-interrupted"
            session.mkdir(parents=True)
            path = session / "session.json"
            path.write_text(json.dumps({
                "format": 1,
                "project": "x86qw",
                "session_id": "legacy-interrupted",
                "created_at": "2026-07-31T18:57:49+00:00",
                "status": "interrupted",
                "processes": [{"label": "QTV", "pid": 999999999}],
                "temporary_files": [{
                    "path": "qw/old-session.cfg",
                    "origin": "configuração efêmera",
                    "created_by_session": True,
                    "expected_hash": services.file_sha256(config),
                }],
                "materialized_files": [],
                "created_directories": [],
            }), encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                services.recover_sessions(target)

            self.assertFalse(config.exists())
            recovered = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("clean", recovered["status"])
            self.assertTrue(recovered["temporary_files"][0]["sensitive"])
            self.assertNotIn("expected_hash", recovered["temporary_files"][0])
            self.assertNotIn(secret, output.getvalue())

    def test_session_recovery_preserves_modified_materialized_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            created = target / "qw" / "created.cfg"
            created.parent.mkdir()
            created.write_text("managed", encoding="utf-8")
            journal = services.SessionJournal(target)
            journal.record_materialized(services.MaterializedFile(
                created, services.file_sha256(created), "fixture.pk3", True, False,
            ))
            created.write_text("personal", encoding="utf-8")
            services.recover_sessions(target)
            self.assertEqual("personal", created.read_text(encoding="utf-8"))
            recovered = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertTrue(recovered["materialized_files"][0]["modified_during_session"])

    def test_session_recovery_preserves_legacy_materialized_file_without_size(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            created = target / "qw" / "created.cfg"
            created.parent.mkdir()
            created.write_text("managed", encoding="utf-8")
            journal = services.SessionJournal(target)
            journal.record_materialized(services.MaterializedFile(
                created, services.file_sha256(created), "fixture.pk3", True, False,
            ))
            entry = journal.data["materialized_files"][0]
            entry.pop("expected_size")
            journal._write()

            services.recover_sessions(target)

            self.assertEqual("managed", created.read_text(encoding="utf-8"))
            recovered = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertTrue(recovered["materialized_files"][0]["modified_during_session"])

    def test_session_journal_rejects_boolean_or_oversize_expected_size(self):
        for invalid_size in (True, services._MAX_MANAGED_FILE_SIZE + 1):
            with self.subTest(expected_size=invalid_size), tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary)
                (target / ".x86qw").mkdir()
                created = target / "qw" / "created.cfg"
                created.parent.mkdir()
                created.write_text("managed", encoding="utf-8")
                journal = services.SessionJournal(target)
                journal.record_materialized(services.MaterializedFile(
                    created, services.file_sha256(created), "fixture.pk3", True, False,
                ))
                journal.data["materialized_files"][0]["expected_size"] = invalid_size
                journal._write()

                with self.assertRaisesRegex(
                    services.InstallerError, "Journal de sessão inválido",
                ):
                    services.load_session_journal(journal.path)

    def test_session_recovery_removes_modified_sensitive_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(config_dir, "session-", ["hostname local"], journal)
            secret = "segredo-que-nao-pode-vazar"
            config.write_text(f'password "{secret}"\n', encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                services.recover_sessions(target)
            self.assertFalse(config.exists())
            self.assertNotIn(secret, output.getvalue())
            self.assertNotIn(secret, journal.path.read_text(encoding="utf-8"))
            entry = json.loads(journal.path.read_text(encoding="utf-8"))["temporary_files"][0]
            self.assertNotIn("expected_hash", entry)
            self.assertNotIn("expected_size", entry)

    def test_sensitive_temporary_replaced_by_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(config_dir, "session-", ["password secret"], journal)
            config.unlink()
            config.mkdir()
            personal = config / "personal.cfg"
            personal.write_text("preservar", encoding="utf-8")
            with self.assertRaisesRegex(services.InstallerError, "substituído por diretório"):
                services.recover_sessions(target)
            self.assertEqual("preservar", personal.read_text(encoding="utf-8"))

    def test_sensitive_temporary_symlink_is_unlinked_without_touching_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            personal = config_dir / "personal.cfg"
            personal.write_text("preservar", encoding="utf-8")
            journal = services.SessionJournal(target)
            config = services.temporary_config(config_dir, "session-", ["password secret"], journal)
            config.unlink()
            config.symlink_to(personal)
            services.recover_sessions(target)
            self.assertFalse(os.path.lexists(config))
            self.assertEqual("preservar", personal.read_text(encoding="utf-8"))

    @unittest.skipIf(os.name == "nt", "FIFO é uma fixture POSIX")
    def test_sensitive_temporary_special_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(config_dir, "session-", ["password secret"], journal)
            config.unlink()
            os.mkfifo(config)
            with self.assertRaisesRegex(services.InstallerError, "arquivo especial"):
                services.recover_sessions(target)
            self.assertTrue(stat.S_ISFIFO(config.lstat().st_mode))

    def test_session_recovery_preserves_modified_non_sensitive_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(
                config_dir, "session-", ["hostname local"], journal, sensitive=False,
            )
            recorded = journal.data["temporary_files"][0]
            self.assertEqual(config.stat().st_size, recorded["expected_size"])
            self.assertIsInstance(recorded["device"], int)
            self.assertIsInstance(recorded["inode"], int)
            config.write_text("// configuração pessoalizada\n", encoding="utf-8")
            services.recover_sessions(target)
            self.assertTrue(config.exists())
            self.assertIn("pessoalizada", config.read_text(encoding="utf-8"))

    def test_session_recovery_removes_unchanged_non_sensitive_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(
                config_dir, "session-", ["hostname local"], journal, sensitive=False,
            )

            services.recover_sessions(target)

            self.assertFalse(config.exists())
            self.assertEqual("clean", json.loads(
                journal.path.read_text(encoding="utf-8"),
            )["status"])

    @unittest.skipUnless(
        services._secure_archive_dir_fd_supported(),
        "corrida requer operações POSIX relativas a descritor",
    )
    def test_non_sensitive_temporary_recovery_preserves_replacement_after_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(
                config_dir, "session-", ["hostname local"], journal, sensitive=False,
            )
            real_hash = services._hash_open_file
            replaced = False

            def replace_after_hash(descriptor, **kwargs):
                nonlocal replaced
                digest = real_hash(descriptor, **kwargs)
                if not replaced:
                    replaced = True
                    config.unlink()
                    config.write_text("personal-concurrent-data", encoding="utf-8")
                return digest

            with mock.patch.object(
                services, "_hash_open_file", side_effect=replace_after_hash,
            ):
                services.recover_sessions(target)

            self.assertEqual("personal-concurrent-data", config.read_text(encoding="utf-8"))
            self.assertEqual([], list(config_dir.glob(".x86qw_cleanup_*")))
            recovered = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertTrue(recovered["temporary_files"][0]["modified_during_session"])

    def test_non_sensitive_temporary_creation_failure_preserves_replacement_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            replaced: Path | None = None

            def replace_with_directory(path, _origin, **_kwargs):
                nonlocal replaced
                replaced = Path(path)
                replaced.unlink()
                replaced.mkdir()
                (replaced / "personal.txt").write_text("personal", encoding="utf-8")
                raise RuntimeError("journal indisponível")

            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(
                output,
            ), mock.patch.object(
                journal, "record_temporary", side_effect=replace_with_directory,
            ), self.assertRaisesRegex(RuntimeError, "journal indisponível"):
                services.temporary_config(
                    config_dir,
                    "session-",
                    ["hostname local"],
                    journal,
                    sensitive=False,
                )

            self.assertIsNotNone(replaced)
            assert replaced is not None
            self.assertEqual("personal", (replaced / "personal.txt").read_text(encoding="utf-8"))
            self.assertIn("foi preservado", output.getvalue())

    def test_active_session_lock_blocks_recovery_and_preserves_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            first = services.SessionLock.acquire(target, "host")
            try:
                journal = services.SessionJournal(
                    target, session_id=first.session_id, controller=first.owner,
                )
                config_dir = target / "qw"
                config_dir.mkdir()
                config = services.temporary_config(
                    config_dir, "session-", ["hostname ativo"], journal,
                )
                with self.assertRaisesRegex(services.InstallerError, "operação x86QW ativa"):
                    services.SessionLock.acquire(target, "qtv")
                self.assertTrue(config.exists())
                self.assertEqual("starting", json.loads(journal.path.read_text(encoding="utf-8"))["status"])
            finally:
                first.release()

    def test_session_lock_acquisition_is_atomic_between_controllers(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            barrier = threading.Barrier(2)
            release = threading.Event()
            results: list[tuple[str, object]] = []
            results_lock = threading.Lock()

            def acquire(command: str) -> None:
                barrier.wait()
                try:
                    acquired = services.SessionLock.acquire(target, command)
                except services.InstallerError as error:
                    with results_lock:
                        results.append(("blocked", str(error)))
                    return
                with results_lock:
                    results.append(("acquired", acquired))
                release.wait(2)
                acquired.release()

            threads = [
                threading.Thread(target=acquire, args=(command,))
                for command in ("host", "proxy")
            ]
            for thread in threads:
                thread.start()
            deadline = time.monotonic() + 2
            while len(results) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            release.set()
            for thread in threads:
                thread.join(2)
            self.assertEqual(1, sum(kind == "acquired" for kind, _ in results))
            self.assertEqual(1, sum(kind == "blocked" for kind, _ in results))

    def test_maintenance_lock_blocks_all_service_entrypoints(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            (target / ".x86qw").mkdir()
            maintenance = services.session_control.InstallationLock.acquire(
                target, "update", "maintenance",
            )
            try:
                for command in ("host", "proxy", "qtv"):
                    with self.subTest(command=command):
                        with self.assertRaisesRegex(services.InstallerError, "operação x86QW ativa"):
                            services.SessionLock.acquire(target, command)
            finally:
                maintenance.release()

    def test_stale_controller_lock_is_reclaimed_and_journal_recovered(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            (target / ".x86qw").mkdir()
            old_session = "abandoned-session"
            journal = services.SessionJournal(target, session_id=old_session)
            lock_path = target / ".x86qw/sessions/active.lock"
            lock_path.write_text(json.dumps({
                "format": 1, "project": "x86qw", "session_id": old_session,
                "controller_pid": 999999999, "controller_start_token": "dead-token",
                "controller_executable": str(target / "dead-controller"),
                "created_at": "2026-07-31T00:00:00+00:00", "installation": str(target),
                "command": "host",
            }), encoding="utf-8")
            acquired = services.SessionLock.acquire(target, "proxy")
            try:
                services.recover_sessions(target)
                acquired.confirm_recovery()
                self.assertEqual("clean", json.loads(journal.path.read_text(encoding="utf-8"))["status"])
            finally:
                acquired.release()

    def test_missing_lock_does_not_recover_a_live_journal_controller(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            (target / ".x86qw").mkdir()
            first = services.SessionLock.acquire(target, "host")
            journal = services.SessionJournal(
                target, session_id=first.session_id, controller=first.owner,
            )
            config_dir = target / "qw"
            config_dir.mkdir()
            config = services.temporary_config(config_dir, "session-", ["hostname ativo"], journal)
            first.path.unlink()
            second = services.SessionLock.acquire(target, "proxy")
            try:
                with self.assertRaisesRegex(services.InstallerError, "controlador.*continua ativo"):
                    services.recover_sessions(target)
            finally:
                second.release()
            self.assertTrue(config.exists())
            self.assertEqual("starting", json.loads(journal.path.read_text(encoding="utf-8"))["status"])

    def test_inconclusive_controller_identity_preserves_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            sessions = target / ".x86qw/sessions"
            sessions.mkdir(parents=True)
            lock_path = sessions / "active.lock"
            lock_path.write_text(json.dumps({
                "format": 1, "project": "x86qw", "session_id": "unknown-session",
                "controller_pid": 424242, "controller_start_token": "unknown-token",
                "controller_executable": str(target / "controller"),
                "created_at": "2026-07-31T00:00:00+00:00", "installation": str(target),
                "command": "qtv",
            }), encoding="utf-8")
            with mock.patch.object(
                services.session_control, "probe_expected_process",
                return_value=services.ProcessProbe("inconclusive", detail="acesso negado"),
            ):
                with self.assertRaisesRegex(services.InstallerError, "Não foi possível confirmar"):
                    services.SessionLock.acquire(target, "host")
            self.assertTrue(lock_path.exists())

    def test_invalid_lock_is_preserved_and_never_recovered(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            sessions = target / ".x86qw/sessions"
            sessions.mkdir(parents=True)
            lock_path = sessions / "active.lock"
            lock_path.write_text("{invalid", encoding="utf-8")
            with self.assertRaisesRegex(services.InstallerError, "inválido"):
                services.SessionLock.acquire(target, "host")
            self.assertEqual("{invalid", lock_path.read_text(encoding="utf-8"))

    def test_lock_release_never_removes_another_session_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            (target / ".x86qw").mkdir()
            acquired = services.SessionLock.acquire(target, "host")
            other = dict(acquired.owner)
            other["session_id"] = "other-session"
            acquired.path.write_text(json.dumps(other), encoding="utf-8")
            acquired.release()
            self.assertTrue(acquired.path.exists())
            self.assertEqual(
                "other-session",
                json.loads(acquired.path.read_text(encoding="utf-8"))["session_id"],
            )

    def test_status_lists_every_active_service_and_only_safe_parameters(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            lock = services.SessionLock.acquire(target, "host")
            try:
                journal = services.SessionJournal(
                    target, session_id=lock.session_id, controller=lock.owner,
                )
                journal.data["status"] = "running"
                processes = journal.data["processes"]
                self.assertIsInstance(processes, list)
                for index, (label, runtime, port, parameters) in enumerate((
                    ("MVDSV", "mvdsv", 28501, {
                        "game": "KTX", "mode": "Duel", "map": "dm6",
                        "bots": "1", "bot_skill": "random",
                        "secrets": "RCON", "password": "raw-password",
                    }),
                    ("QTV", "qtv", 28000, {
                        "http": "http://127.0.0.1:28000/",
                        "upstream": "127.0.0.1:28501",
                        "upstream_secret": "configurado",
                    }),
                    ("QWFWD", "qwfwd", 30000, {
                        "bind": "127.0.0.1", "protocol": "UDP QuakeWorld",
                    }),
                ), 1):
                    processes.append({
                        "label": label,
                        "runtime": runtime,
                        "pid": 9000 + index,
                        "process_group": 9000 + index,
                        "executable": str(target / runtime),
                        "creation_token": f"token-{index}",
                        "started_at": "2026-08-02T00:00:00+00:00",
                        "address": "127.0.0.1",
                        "port": port,
                        "parameters": parameters,
                    })
                journal._write()
                alive = services.ProcessProbe(
                    "alive", services.ProcessIdentity(1, "token", sys.executable),
                )
                output = io.StringIO()
                with mock.patch.object(
                    services, "probe_expected_process", return_value=alive,
                ), contextlib.redirect_stdout(output):
                    services.show_service_status(target)
            finally:
                lock.release()
            rendered = output.getvalue()
            for value in (
                "MVDSV", "QTV", "QWFWD", "Duel", "dm6", "random",
                "127.0.0.1:28501", "http://127.0.0.1:28000/", "UDP QuakeWorld",
                "Serviços › Encerrar serviços ativos", "status --stop",
            ):
                self.assertIn(value, rendered)
            self.assertRegex(rendered, r"Segredos\s+\| RCON")
            self.assertNotIn("raw-password", rendered)

    def test_status_without_a_stack_is_read_only_and_successful(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                services.show_service_status(target)
            self.assertIn("Nenhum serviço x86QW está ativo", output.getvalue())
            self.assertFalse((target / ".x86qw").exists())

    def test_background_controller_arguments_and_request_keep_secrets_off_argv(self):
        options = services.parse_arguments([
            "qtv", "--target", "/tmp/x86qw-test", "--bind", "127.0.0.1",
            "--upstream", "127.0.0.1:28501", "--qtv-password", "segredo",
            "--background",
        ], ROOT)
        arguments = services.background_controller_arguments(
            options, None, ".x86qw/logs/service-1234.log",
        )
        self.assertIn("--background-child", arguments)
        self.assertIn("--background-log", arguments)
        self.assertNotIn("--background", arguments)
        self.assertNotIn("--prompt-qtv-password", arguments)
        self.assertNotIn("segredo", arguments)

        request = {
            "format": 1,
            "project": "x86qw",
            "secrets": {
                "password": "jogador", "spectator_password": "espectador",
                "rcon_password": "rcon", "qtv_password": "qtv",
            },
        }
        stream = SimpleNamespace(
            buffer=io.BytesIO((json.dumps(request) + "\n").encode("utf-8")),
        )
        with mock.patch.object(services.sys, "stdin", stream):
            services.read_background_request(options)
        self.assertEqual("qtv", options.qtv_password)
        self.assertTrue(options.background)
        self.assertFalse(options.prompt_qtv_password)

    def test_status_stop_request_performs_coordinated_shutdown_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            lock = services.SessionLock.acquire(target, "proxy")
            journal = services.SessionJournal(
                target, session_id=lock.session_id, controller=lock.owner,
                background=True,
                background_log=".x86qw/logs/service-test.log",
            )
            resources = services.ServiceResources([], [])
            resources.session_lock = lock
            resources.recovery_confirmed = True
            resources.journal = journal
            result: list[int] = []

            def controller() -> None:
                with services.finalize_service_operation(resources):
                    result.append(services.run_processes([
                        services.ProcessSpec(
                            "fixture",
                            (sys.executable, "-c", "import time; time.sleep(60)"),
                            target,
                        ),
                    ], journal))

            thread = threading.Thread(target=controller)
            thread.start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                state = json.loads(journal.path.read_text(encoding="utf-8"))
                if state["status"] == "running":
                    break
                time.sleep(0.05)
            else:
                self.fail("a fixture de serviço não ficou pronta")
            services.request_service_stop(target, timeout=5)
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual([0], result)
            self.assertFalse(lock.path.exists())
            self.assertFalse((journal.directory / "stop.request").exists())
            final = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertEqual("clean", final["status"])
            self.assertTrue(final["background"])
            self.assertEqual(".x86qw/logs/service-test.log", final["background_log"])

    def test_stop_request_is_published_only_after_the_writer_is_flushed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            request = directory / "stop.request"
            payload = b'{"format": 1, "project": "x86qw"}\n'
            original_fsync = services.os.fsync
            observations: list[bool] = []

            def assert_private_until_fsync(descriptor: int) -> None:
                observations.append(not request.exists())
                original_fsync(descriptor)

            with mock.patch.object(
                services.os, "fsync", side_effect=assert_private_until_fsync,
            ):
                services.publish_stop_request(request, payload)

            self.assertEqual([True], observations)
            self.assertEqual(payload, request.read_bytes())
            self.assertEqual([], list(directory.glob(".stop-*.request")))
            services.unlink_stop_request(request)
            self.assertFalse(request.exists())

    def test_orphan_with_matching_identity_is_terminated_and_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            process = subprocess.Popen(
                (sys.executable, "-c", "import time; time.sleep(30)"),
                start_new_session=True,
            )
            journal = services.SessionJournal(target, controller={
                "controller_pid": 999999999,
                "controller_start_token": "dead-controller",
                "controller_executable": str(target / "dead-controller"),
                "command": "host",
            })
            spec = services.ProcessSpec("fixture", (sys.executable,), Path.cwd())
            try:
                journal.record_process(spec, process, process.pid)
                services.recover_sessions(target)
                process.wait(timeout=2)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            recovered = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertEqual("clean", recovered["status"])
            self.assertIn(recovered["recovery_actions"][0]["result"], {"terminated", "killed"})
            recorded = recovered["processes"][0]
            for field in (
                "runtime", "process_group", "executable", "creation_token",
                "address", "port", "parameters",
            ):
                self.assertIn(field, recorded)

    def test_reused_pid_is_not_terminated(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            journal = services.SessionJournal(target)
            processes = journal.data["processes"]
            self.assertIsInstance(processes, list)
            processes.append({
                "label": "MVDSV", "runtime": "mvdsv", "pid": 12345,
                "process_group": 12345, "executable": "/old/mvdsv",
                "creation_token": "old-token", "started_at": "2026-07-31T00:00:00+00:00",
                "address": "127.0.0.1", "port": 28501,
            })
            journal._write()
            mismatch = services.ProcessProbe(
                "identity_mismatch", services.ProcessIdentity(12345, "new-token", "/other/process"),
            )
            with mock.patch.object(services, "probe_expected_process", return_value=mismatch):
                with mock.patch.object(services, "signal_recorded_process") as terminate:
                    services.recover_sessions(target)
            terminate.assert_not_called()
            recovered = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertEqual("identity_mismatch", recovered["recovery_actions"][0]["result"])

    def test_inconclusive_orphan_preserves_journal_and_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            data = target / "qw/needed.cfg"
            data.parent.mkdir()
            data.write_text("needed", encoding="utf-8")
            journal = services.SessionJournal(target)
            journal.record_materialized(services.MaterializedFile(
                data, services.file_sha256(data), "fixture.pk3", True, False,
            ))
            processes = journal.data["processes"]
            self.assertIsInstance(processes, list)
            processes.append({"label": "QTV", "pid": 12345})
            journal._write()
            with mock.patch.object(
                services, "process_identity", return_value=services.ProcessProbe("inconclusive"),
            ):
                with self.assertRaisesRegex(services.InstallerError, "Não foi possível confirmar"):
                    services.recover_sessions(target)
            self.assertTrue(data.exists())
            self.assertEqual("starting", json.loads(journal.path.read_text(encoding="utf-8"))["status"])

    @unittest.skipIf(os.name == "nt", "grupos de processos POSIX não existem no Windows")
    def test_stop_processes_kills_descendant_after_leader_exits(self):
        with tempfile.TemporaryDirectory() as temporary:
            child_pid_path = Path(temporary) / "child.pid"
            script = (
                "import pathlib,signal,subprocess,sys,time\n"
                "child=subprocess.Popen([sys.executable,'-c',"
                "'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'])\n"
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
                "time.sleep(60)\n"
            )
            leader = subprocess.Popen(
                [sys.executable, "-c", script, str(child_pid_path)],
                start_new_session=True,
            )
            setattr(leader, "_x86qw_process_group", leader.pid)
            deadline = time.monotonic() + 3
            while not child_pid_path.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(child_pid_path.exists())
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
            try:
                services.stop_processes([leader])
            finally:
                if leader.poll() is None:
                    os.killpg(leader.pid, signal.SIGKILL)
                    leader.wait()
            self.assertIsNotNone(leader.poll())
            child_deadline = time.monotonic() + 2
            while time.monotonic() < child_deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail(f"descendente PID {child_pid} permaneceu ativo")

    @unittest.skipUnless(os.name == "nt", "Job Object é exercitado no runner Windows")
    def test_windows_job_object_kills_process_and_descendant(self):
        with tempfile.TemporaryDirectory() as temporary:
            child_pid_path = Path(temporary) / "child.pid"
            script = (
                "import pathlib,subprocess,sys,time\n"
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
                "time.sleep(60)\n"
            )
            job = services.WindowsJobObject()
            leader = subprocess.Popen(
                [sys.executable, "-c", script, str(child_pid_path)],
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            try:
                job.assign(leader)
                deadline = time.monotonic() + 5
                while not child_pid_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(child_pid_path.exists())
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))
                job.close()
                leader.wait(timeout=5)
                probe = services.process_identity(child_pid)
                self.assertEqual("dead", probe.status)
            finally:
                job.close()
                if leader.poll() is None:
                    leader.kill()
                    leader.wait()

    @unittest.skipUnless(os.name == "nt", "assinaturas Win32 são exercitadas no runner Windows")
    def test_windows_api_signatures_are_explicit(self):
        groups = (
            (services.session_control._windows_kernel32(), (
                "OpenProcess", "GetProcessTimes", "QueryFullProcessImageNameW",
                "TerminateProcess", "CloseHandle",
            )),
            (services._windows_job_kernel32(), (
                "CreateJobObjectW", "SetInformationJobObject",
                "AssignProcessToJobObject", "CloseHandle",
            )),
            (services._get_windows_file_api().kernel32, (
                "CreateFileW", "GetFileInformationByHandleEx",
                "SetFileInformationByHandle", "MoveFileExW", "ReadFile",
                "WriteFile", "SetFilePointerEx", "GetFileSizeEx",
                "FlushFileBuffers", "CloseHandle",
            )),
        )
        for kernel32, names in groups:
            for name in names:
                function = getattr(kernel32, name)
                self.assertIsNotNone(function.argtypes, name)
                self.assertIsNotNone(function.restype, name)

    def test_finalization_always_attempts_lock_release_after_cleanup_failures(self):
        for failing_step in ("journal", "session", "stage", "release"):
            with self.subTest(failing_step=failing_step):
                journal = mock.Mock()
                installer = mock.Mock()
                lock = mock.Mock()
                resources = services.ServiceResources([], [])
                resources.journal = journal
                resources.installer = installer
                resources.session_lock = lock
                if failing_step == "journal":
                    journal.set_status.side_effect = [RuntimeError("journal"), None]
                if failing_step == "stage":
                    installer.cleanup_stage.side_effect = RuntimeError("stage")
                if failing_step == "release":
                    lock.release.side_effect = RuntimeError("release")
                cleanup = (
                    mock.patch.object(
                        services, "cleanup_current_session", side_effect=RuntimeError("session"),
                    )
                    if failing_step == "session"
                    else mock.patch.object(services, "cleanup_current_session")
                )
                with cleanup:
                    with self.assertRaisesRegex(services.InstallerError, "finalização"):
                        with services.finalize_service_operation(resources):
                            pass
                lock.release.assert_called_once()
                if failing_step == "session":
                    journal.set_status.assert_any_call("interrupted")

    def test_finalization_preserves_original_error_while_reporting_cleanup(self):
        resources = services.ServiceResources([], [])
        resources.session_lock = mock.Mock()
        with mock.patch.object(
            services, "cleanup_current_session", side_effect=RuntimeError("cleanup"),
        ):
            with self.assertRaisesRegex(ValueError, "original"):
                with services.finalize_service_operation(resources):
                    raise ValueError("original")
        resources.session_lock.release.assert_called_once()

    def test_host_qtv_upstream_uses_reachable_ipv4_and_ipv6_endpoint(self):
        expected = {
            "127.0.0.1": "127.0.0.1:28501",
            "0.0.0.0": "127.0.0.1:28501",
            "192.168.1.50": "192.168.1.50:28501",
            "::1": "[::1]:28501",
            "::": "[::1]:28501",
        }
        for address, endpoint in expected.items():
            with self.subTest(address=address):
                self.assertEqual(endpoint, services.host_qtv_upstream(address, 28501))

    def test_external_qtv_warning_is_independent_from_upstream_password(self):
        for password in ("", "upstream-secret"):
            options = SimpleNamespace(
                action="qtv", bind="0.0.0.0", upstream="127.0.0.1:28501",
                qtv_password=password,
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                services.warn_external_bind(options)
            self.assertIn("interface HTTP/QTV será exposta", output.getvalue())
            self.assertIn("não autentica o acesso HTTP", output.getvalue())

    def test_session_journal_is_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            journal = services.SessionJournal(target)
            self.assertTrue(journal.path.is_file())
            self.assertFalse(journal.path.is_symlink())
            if os.name != "nt":
                self.assertEqual(0o700, journal.directory.stat().st_mode & 0o777)
                self.assertEqual(0o600, journal.path.stat().st_mode & 0o777)

    def test_temporary_config_preserves_raw_quake_name_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            raw_name = b'set k_fb_name_0 "/\xa0\xd9\xe1\xed\xe1\xf4\xef"'
            config = services.temporary_config(directory, "host-", [raw_name])
            try:
                self.assertIn(raw_name + b"\n", config.read_bytes())
            finally:
                services.unlink_sensitive_temporary(config)

    def test_partial_startup_failure_stops_server_and_dependents(self):
        processes = [mock.Mock(pid=101), mock.Mock(pid=102)]
        for process in processes:
            process.poll.return_value = None
        with mock.patch.object(
            services.subprocess, "Popen", side_effect=processes,
        ) as popen, mock.patch.object(
            services, "apply_startup_rcon",
        ), mock.patch.object(
            services, "wait_http_readiness", side_effect=services.InstallerError("QTV falhou"),
        ), mock.patch.object(services, "WindowsJobObject"):
            specs = [
                services.ProcessSpec("MVDSV", ("mvdsv",), Path.cwd(), services.StartupRcon("127.0.0.1", 28501, "secret", "post.cfg", "dm6", "ktx")),
                services.ProcessSpec("QTV", ("qtv",), Path.cwd(), readiness=services.ServiceReadiness("http", "127.0.0.1", 28000)),
            ]
            with self.assertRaisesRegex(services.InstallerError, "QTV falhou"):
                services.run_processes(specs)
        for process in processes:
            process.terminate.assert_called_once()
        for call in popen.call_args_list:
            self.assertIs(call.kwargs["stdin"], subprocess.DEVNULL)

    def test_mvdsv_readiness_checks_map_gamecode_and_applies_post_map(self):
        responses = [
            b"\xff\xff\xff\xffprint\n\\map\\dm6\\*gamedir\\qw",
            b"\xff\xff\xff\xffprint\n*game qw",
            b"\xff\xff\xff\xffprint\nexecing post.cfg",
        ]
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.recvfrom.side_effect = [(response, ("127.0.0.1", 28501)) for response in responses]
        with mock.patch.object(
            services.socket, "socket", return_value=connection,
        ), mock.patch.object(services.time, "sleep") as sleep:
            services.apply_startup_rcon(services.StartupRcon(
                "127.0.0.1", 28501, "bootstrap", "post.cfg", "dm6", "qw",
            ))
        sent = b"\n".join(call.args[0] for call in connection.sendto.call_args_list)
        self.assertIn(b"status", sent)
        self.assertIn(b"serverinfo", sent)
        self.assertIn(b"exec post.cfg", sent)
        sleep.assert_called_once_with(1.05)

    @unittest.skipIf(os.name == "nt", "SIGTERM POSIX validado nos runners Unix; Windows usa terminate")
    def test_sigterm_stops_child_without_orphan(self):
        child_pid: list[int] = []
        original_popen = services.subprocess.Popen

        def capture(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            child_pid.append(process.pid)
            return process

        timer = threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGTERM))
        timer.start()
        try:
            with mock.patch.object(services.subprocess, "Popen", side_effect=capture):
                result = services.run_processes([
                    services.ProcessSpec("fixture", (sys.executable, "-c", "import time; time.sleep(30)"), Path.cwd()),
                ])
            self.assertEqual(128 + signal.SIGTERM, result)
            self.assertTrue(child_pid)
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid[0], 0)
        finally:
            timer.cancel()


if __name__ == "__main__":
    unittest.main()
