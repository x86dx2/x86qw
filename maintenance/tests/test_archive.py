from __future__ import annotations

import contextlib
import io
import json
import math
import os
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from x86qw_runtime.io import private_fs
from x86qw_runtime.io import archive as archive_module
from x86qw_runtime.io.archive import (
    ArchiveError,
    ArchiveLimits,
    DEFAULT_ARCHIVE_LIMITS,
    extract_archive,
    read_archive_member,
    read_archive_members,
    scan_archive,
    validate_installer_bundle,
    validate_installer_history_bundle,
)

ROOT = Path(__file__).resolve().parents[2]


class ArchiveTests(unittest.TestCase):
    def archive_bytes(
        self,
        members: list[tuple[str | zipfile.ZipInfo, bytes]],
        *,
        compression: int = zipfile.ZIP_STORED,
    ) -> bytes:
        output = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(output, "w", compression=compression, allowZip64=True) as archive:
                for name, payload in members:
                    if isinstance(name, str) and "\\" in name:
                        # ZipInfo normalizes the host separator in its
                        # constructor.  Assign the original spelling after
                        # construction so hostile-name fixtures are identical
                        # on Unix and Windows.
                        member = zipfile.ZipInfo("placeholder")
                        member.filename = name
                        member.orig_filename = name
                        member.compress_type = compression
                        archive.writestr(member, payload)
                    else:
                        archive.writestr(name, payload)
        return output.getvalue()

    def identity_bytes(self, version: str) -> bytes:
        return json.dumps(
            {"format": 1, "project": "x86qw", "version": version},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8") + b"\n"

    def installer_bundle(self, version: str = "1.2.3") -> bytes:
        prefix = f"x86qw-installer-{version}"
        identity = self.identity_bytes(version)
        legal = {
            name: (ROOT / name).read_bytes()
            for name in ("LICENSE", "NOTICE")
        }
        application = self.archive_bytes([
            ("__main__.py", b"print('x86QW')\n"),
            ("_x86qw/installer.json", identity),
            ("_x86qw/LICENSE", legal["LICENSE"]),
            ("_x86qw/NOTICE", legal["NOTICE"]),
        ])
        return self.archive_bytes([
            (f"{prefix}/x86qw.pyz", application),
            (f"{prefix}/VERSION", f"{version}\n".encode("ascii")),
            (f"{prefix}/LICENSE", legal["LICENSE"]),
            (f"{prefix}/NOTICE", legal["NOTICE"]),
            (f"{prefix}/x86qw.sh", b"#!/bin/sh\n"),
            (f"{prefix}/x86qw.cmd", b"@echo off\r\n"),
            (f"{prefix}/installer.json", identity),
            (f"{prefix}/dist/installer/bin/manager.py", b"#!/usr/bin/env python3\n"),
            (f"{prefix}/_x86qw/installer.json", identity),
        ])

    def historical_installer_bundle(self, version: str = "0.1.19") -> bytes:
        prefix = f"x86qw-installer-{version}"
        identity = self.identity_bytes(version)
        application = self.archive_bytes([
            ("__main__.py", b"print('x86QW')\n"),
            ("_x86qw/installer.json", identity),
        ])
        return self.archive_bytes([
            (f"{prefix}/x86qw.pyz", application),
            (f"{prefix}/x86qw.sh", b"#!/bin/sh\n"),
            (f"{prefix}/x86qw.cmd", b"@echo off\r\n"),
            (f"{prefix}/installer.json", identity),
            (f"{prefix}/dist/installer/bin/manager.py", b"#!/usr/bin/env python3\n"),
            (f"{prefix}/_x86qw/installer.json", identity),
        ])

    def empty_name_archive_bytes(self) -> bytes:
        local = struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50,
            20,
            0,
            zipfile.ZIP_STORED,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        central = struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50,
            20,
            20,
            0,
            zipfile.ZIP_STORED,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        eocd = struct.pack(
            "<IHHHHIIH",
            0x06054B50,
            0,
            0,
            1,
            1,
            len(central),
            len(local),
            0,
        )
        return local + central + eocd

    def assert_rejected_names(self, *names: str) -> None:
        for name in names:
            with self.subTest(name=name), self.assertRaises(ArchiveError):
                scan_archive(self.archive_bytes([(name, b"payload")]))

    def test_default_limits_are_the_canonical_values(self) -> None:
        self.assertEqual(4096, DEFAULT_ARCHIVE_LIMITS.max_members)
        self.assertEqual(512 * 1024 * 1024, DEFAULT_ARCHIVE_LIMITS.max_source_size)
        self.assertEqual(32 * 1024 * 1024, DEFAULT_ARCHIVE_LIMITS.max_metadata_size)
        self.assertEqual(128 * 1024 * 1024, DEFAULT_ARCHIVE_LIMITS.max_member_size)
        self.assertEqual(512 * 1024 * 1024, DEFAULT_ARCHIVE_LIMITS.max_total_size)
        self.assertEqual(16, DEFAULT_ARCHIVE_LIMITS.max_depth)
        self.assertEqual(240, DEFAULT_ARCHIVE_LIMITS.max_path_utf16_units)
        self.assertEqual(500, DEFAULT_ARCHIVE_LIMITS.max_compression_ratio)
        self.assertEqual(
            {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED},
            DEFAULT_ARCHIVE_LIMITS.allowed_compression_methods,
        )

    def test_archive_limits_reject_non_integral_and_non_finite_values(self) -> None:
        for field in (
            "max_members", "max_source_size", "max_metadata_size", "max_member_size",
            "max_total_size", "max_depth", "max_path_utf16_units",
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                ArchiveLimits(**{field: 1.5})
        for ratio in (math.nan, math.inf, -math.inf, "500", True):
            with self.subTest(ratio=ratio), self.assertRaises(ValueError):
                ArchiveLimits(max_compression_ratio=ratio)  # type: ignore[arg-type]
        for methods in (
            frozenset({False}),
            frozenset({0.0}),
            frozenset({"0"}),
            frozenset({zipfile.ZIP_BZIP2}),
        ):
            with self.subTest(methods=methods), self.assertRaises(ValueError):
                ArchiveLimits(allowed_compression_methods=methods)  # type: ignore[arg-type]

    def test_source_size_limit_is_enforced_before_hashing_or_zip_parsing(self) -> None:
        payload = self.archive_bytes([("member", b"payload")])
        with mock.patch.object(
            archive_module,
            "_stream_sha256",
            side_effect=AssertionError("oversized bytes must not be hashed"),
        ), self.assertRaisesRegex(ArchiveError, "source exceeds"):
            scan_archive(payload, limits=ArchiveLimits(max_source_size=len(payload) - 1))

        with tempfile.TemporaryDirectory() as temporary:
            sparse = Path(temporary) / "oversized.zip"
            with sparse.open("wb") as stream:
                stream.truncate(DEFAULT_ARCHIVE_LIMITS.max_source_size + 1)
            with mock.patch.object(
                archive_module,
                "_stream_sha256",
                side_effect=AssertionError("oversized path must not be hashed"),
            ), self.assertRaisesRegex(ArchiveError, "source exceeds"):
                scan_archive(sparse)

    def test_scan_aborts_when_private_snapshot_cannot_be_created(self) -> None:
        payload = self.archive_bytes([("member", b"payload")])
        with mock.patch.object(
            private_fs,
            "private_mkstemp",
            side_effect=private_fs.PrivateFilesystemError("private snapshot unavailable"),
        ), self.assertRaisesRegex(ArchiveError, "valid supported ZIP archive"):
            scan_archive(payload)

    def test_standalone_archive_helper_does_not_require_private_fs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "x86qw_runtime" / "io"
            package.mkdir(parents=True)
            (package.parent / "__init__.py").write_bytes(b"")
            (package / "__init__.py").write_bytes(b"")
            (package / "archive.py").write_bytes(Path(archive_module.__file__).read_bytes())
            source = root / "source.zip"
            source.write_bytes(self.archive_bytes([("member", b"payload")]))
            script = (
                "import sys\n"
                "from pathlib import Path\n"
                "sys.path.insert(0, sys.argv[1])\n"
                "from x86qw_runtime.io.archive import scan_archive\n"
                "plan = scan_archive(Path(sys.argv[2]))\n"
                "raise SystemExit(0 if plan.member_names == ('member',) else 2)\n"
            )
            completed = subprocess.run(
                [sys.executable, "-I", "-c", script, str(root), str(source)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)

    def test_growing_source_reads_at_most_the_limit_plus_one_byte(self) -> None:
        class CountingStream(io.BytesIO):
            bytes_read = 0

            def read(self, size: int = -1) -> bytes:
                block = super().read(size)
                self.bytes_read += len(block)
                return block

        stream = CountingStream(b"x" * 1024)

        @contextlib.contextmanager
        def fake_open_source(source: Path | bytes) -> object:
            yield stream, None

        with mock.patch.object(
            archive_module,
            "_open_source",
            side_effect=fake_open_source,
        ), mock.patch.object(
            archive_module,
            "_preflight_source_size",
            return_value=1,
        ), self.assertRaises(ArchiveError):
            scan_archive(Path("growing.zip"), limits=ArchiveLimits(max_source_size=64))
        self.assertLessEqual(stream.bytes_read, 65)

    def test_source_mutation_after_prescan_never_reaches_zipfile(self) -> None:
        original = self.archive_bytes([("member", b"original")])
        replacement = self.archive_bytes([("first", b"a"), ("second", b"b")])
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "mutable.zip"
            source.write_bytes(original)
            real_validate = archive_module._validate_zip_envelope
            real_zipfile = archive_module.zipfile.ZipFile
            mutated = False

            def mutate_after_prescan(
                stream: object,
                source_size: int,
                infos: object = None,
                **keywords: object,
            ) -> int:
                nonlocal mutated
                result = real_validate(stream, source_size, infos, **keywords)
                if infos is None and not mutated:
                    mutated = True
                    descriptor = stream.fileno()
                    opened = os.fstat(descriptor)
                    source_stat = source.stat()
                    if (opened.st_dev, opened.st_ino) == (
                        source_stat.st_dev,
                        source_stat.st_ino,
                    ):
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        os.ftruncate(descriptor, 0)
                        os.write(descriptor, replacement)
                        os.fsync(descriptor)
                        stream.seek(0, os.SEEK_END)
                        stream.seek(0)
                    else:
                        source.write_bytes(replacement)
                return result

            def reject_mutated_zipfile(stream: object, *args: object, **kwargs: object) -> object:
                position = stream.tell()
                stream.seek(0)
                payload = stream.read()
                stream.seek(position)
                if payload == replacement:
                    raise AssertionError("mutable source reached ZipFile after its pre-scan")
                return real_zipfile(stream, *args, **kwargs)

            @contextlib.contextmanager
            def open_unbuffered(source_value: Path | bytes) -> object:
                descriptor = os.open(source, os.O_RDWR)
                try:
                    identity = archive_module._stat_identity(os.fstat(descriptor))
                    with os.fdopen(descriptor, "rb", buffering=0, closefd=True) as stream:
                        descriptor = -1
                        yield stream, identity
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)

            with mock.patch.object(
                archive_module,
                "_validate_zip_envelope",
                side_effect=mutate_after_prescan,
            ), mock.patch.object(
                archive_module,
                "_open_source",
                side_effect=open_unbuffered,
            ), mock.patch.object(
                archive_module.zipfile,
                "ZipFile",
                side_effect=reject_mutated_zipfile,
            ), self.assertRaises(ArchiveError):
                scan_archive(source, limits=ArchiveLimits(max_members=1))
            self.assertTrue(mutated)

    def test_scan_reads_all_files_and_assigns_only_declared_modes(self) -> None:
        directory = zipfile.ZipInfo("bin/")
        directory.external_attr = (stat.S_IFDIR | 0o777) << 16
        executable = zipfile.ZipInfo("bin/run")
        executable.external_attr = (stat.S_IFREG | 0o600) << 16
        ordinary = zipfile.ZipInfo("config.cfg")
        ordinary.external_attr = (stat.S_IFREG | 0o777) << 16
        payload = self.archive_bytes([
            (directory, b""),
            (executable, b"run"),
            (ordinary, b"config"),
        ])
        plan = scan_archive(
            payload,
            required_members=("bin/run", "config.cfg"),
            executable_members=("bin/run",),
        )
        self.assertEqual(["directory", "file", "file"], [member.kind for member in plan.members])
        self.assertEqual([0o755, 0o755, 0o644], [member.mode for member in plan.members])
        self.assertEqual(
            {"bin/run": b"run", "config.cfg": b"config"},
            read_archive_members(plan),
        )
        self.assertEqual(b"run", read_archive_member(plan, "bin/run"))
        self.assertEqual(64, len(plan.source_sha256))

    def test_extract_creates_private_tree_with_canonical_modes(self) -> None:
        plan = scan_archive(
            self.archive_bytes([("bin/run", b"run"), ("share/data", b"data")]),
            executable_members=("bin/run",),
        )
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "installed"
            self.assertEqual(destination, extract_archive(plan, destination))
            self.assertEqual(b"run", (destination / "bin/run").read_bytes())
            self.assertEqual(b"data", (destination / "share/data").read_bytes())
            if os.name != "nt":
                self.assertEqual(0o755, destination.stat().st_mode & 0o777)
                self.assertEqual(0o755, (destination / "bin").stat().st_mode & 0o777)
                self.assertEqual(0o755, (destination / "bin/run").stat().st_mode & 0o777)
                self.assertEqual(0o644, (destination / "share/data").stat().st_mode & 0o777)

    def test_rejects_absolute_drive_unc_backslash_and_noncanonical_paths(self) -> None:
        backslash = self.archive_bytes([("a\\b", b"payload")])
        self.assertEqual(2, backslash.count(b"a\\b"))
        with self.assertRaises(ArchiveError):
            scan_archive(backslash)
        self.assert_rejected_names(
            "/absolute", "//server/share", "C:/drive", "c:relative",
            "a//b", "./a", "a/../b", "a/", "name.", "name ",
        )

    def test_windows_path_handle_key_normalizes_ctime_and_executable_bits(self) -> None:
        common = {
            "st_dev": 7,
            "st_ino": 11,
            "st_size": 13,
            "st_mtime": 17.0,
            "st_mtime_ns": 17,
            "st_birthtime_ns": 19,
            "st_file_attributes": 0x20,
        }
        path_metadata = SimpleNamespace(
            **common,
            st_mode=stat.S_IFREG | 0o777,
            st_ctime=23.0,
            st_ctime_ns=23,
        )
        handle_metadata = SimpleNamespace(
            **common,
            st_mode=stat.S_IFREG | 0o666,
            st_ctime=29.0,
            st_ctime_ns=29,
        )
        self.assertEqual(
            archive_module._windows_path_handle_identity(path_metadata),
            archive_module._windows_path_handle_identity(handle_metadata),
        )
        self.assertNotEqual(
            archive_module._stat_identity(path_metadata),
            archive_module._stat_identity(handle_metadata),
        )

    @unittest.skipUnless(os.name == "nt", "identidade path/fstat exercitada no Windows")
    def test_windows_archive_source_accepts_executable_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "fixture.exe"
            source.write_bytes(self.archive_bytes([("payload", b"safe")]))
            self.assertEqual(("payload",), scan_archive(source).member_names)

    @unittest.skipUnless(os.name == "nt", "reparse point exercitado no Windows")
    def test_windows_archive_source_rejects_file_reparse_point(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.zip"
            source.write_bytes(self.archive_bytes([("payload", b"safe")]))
            link = root / "link.zip"
            try:
                link.symlink_to(source)
            except OSError as error:
                self.skipTest(f"privilégio para symlink indisponível: {error}")
            with self.assertRaisesRegex(ArchiveError, "regular non-symlink"):
                scan_archive(link)

    def test_rejects_empty_member_name_before_zipfile_parsing(self) -> None:
        with mock.patch.object(
            archive_module.zipfile,
            "ZipFile",
            side_effect=AssertionError("empty name must be rejected before ZipFile"),
        ), self.assertRaises(ArchiveError):
            scan_archive(self.empty_name_archive_bytes())

    def test_rejects_all_unicode_control_categories_and_utf16_overflow(self) -> None:
        self.assert_rejected_names(
            "line\nbreak", "format\N{ZERO WIDTH SPACE}name", "private\ue000name",
            "unassigned\u0378name", "U0001F600" * 121,
        )
        surrogate = bytearray(self.archive_bytes([("aaa", b"payload")]))
        local = surrogate.index(b"PK\x03\x04")
        central = surrogate.index(b"PK\x01\x02")
        surrogate[local + 30:local + 33] = b"\xed\xa0\x80"
        surrogate[central + 46:central + 49] = b"\xed\xa0\x80"
        struct.pack_into("<H", surrogate, local + 6, struct.unpack_from("<H", surrogate, local + 6)[0] | 0x800)
        struct.pack_into("<H", surrogate, central + 8, struct.unpack_from("<H", surrogate, central + 8)[0] | 0x800)
        with self.assertRaises(ArchiveError):
            scan_archive(bytes(surrogate))

    def test_rejects_windows_reserved_names_including_extensions_and_superscripts(self) -> None:
        self.assert_rejected_names(
            "CON", "con.cfg", "NUL.txt", "CONIN$.log", "conout$", "COM9.dat",
            "LPT1", "COM¹.cfg", "lpt².txt", "aux.anything",
        )

    def test_rejects_every_windows_forbidden_filename_character(self) -> None:
        self.assert_rejected_names(
            "less<than", "greater>than", 'double"quote', "vertical|bar",
            "question?mark", "asterisk*name",
        )

    def test_rejects_exact_casefold_nfc_and_prefix_collisions(self) -> None:
        unsafe_sets = (
            [("same", b"a"), ("same", b"b")],
            [("Config.cfg", b"a"), ("config.cfg", b"b")],
            [("café.cfg", b"a"), ("cafe\N{COMBINING ACUTE ACCENT}.cfg", b"b")],
            [("File", b"a"), ("file/child", b"b")],
            [("Foo/a", b"a"), ("foo/b", b"b")],
            [("café/a", b"a"), ("cafe\N{COMBINING ACUTE ACCENT}/b", b"b")],
        )
        for members in unsafe_sets:
            with self.subTest(members=[name for name, _ in members]), self.assertRaises(ArchiveError):
                scan_archive(self.archive_bytes(members))

    def test_rejects_encryption_unsupported_compression_symlinks_and_specials(self) -> None:
        encrypted = bytearray(self.archive_bytes([("secret", b"payload")]))
        local = encrypted.index(b"PK\x03\x04")
        central = encrypted.index(b"PK\x01\x02")
        struct.pack_into("<H", encrypted, local + 6, struct.unpack_from("<H", encrypted, local + 6)[0] | 1)
        struct.pack_into("<H", encrypted, central + 8, struct.unpack_from("<H", encrypted, central + 8)[0] | 1)
        with self.assertRaises(ArchiveError):
            scan_archive(bytes(encrypted))
        strong_encryption = bytearray(self.archive_bytes([("secret", b"payload")]))
        local = strong_encryption.index(b"PK\x03\x04")
        central = strong_encryption.index(b"PK\x01\x02")
        struct.pack_into(
            "<H",
            strong_encryption,
            local + 6,
            struct.unpack_from("<H", strong_encryption, local + 6)[0] | 0x40,
        )
        struct.pack_into(
            "<H",
            strong_encryption,
            central + 8,
            struct.unpack_from("<H", strong_encryption, central + 8)[0] | 0x40,
        )
        with self.assertRaises(ArchiveError):
            scan_archive(bytes(strong_encryption))
        for unsafe_flag in (0x20, 0x2000):
            flagged = bytearray(self.archive_bytes([("flagged", b"payload")]))
            local = flagged.index(b"PK\x03\x04")
            central = flagged.index(b"PK\x01\x02")
            struct.pack_into(
                "<H",
                flagged,
                local + 6,
                struct.unpack_from("<H", flagged, local + 6)[0] | unsafe_flag,
            )
            struct.pack_into(
                "<H",
                flagged,
                central + 8,
                struct.unpack_from("<H", flagged, central + 8)[0] | unsafe_flag,
            )
            with self.subTest(unsafe_flag=hex(unsafe_flag)), self.assertRaises(ArchiveError):
                scan_archive(bytes(flagged))
        with self.assertRaises(ArchiveError):
            scan_archive(self.archive_bytes([("bzip", b"payload")], compression=zipfile.ZIP_BZIP2))
        for member_type in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFSOCK):
            info = zipfile.ZipInfo("special")
            info.external_attr = (member_type | 0o600) << 16
            with self.subTest(member_type=member_type), self.assertRaises(ArchiveError):
                scan_archive(self.archive_bytes([(info, b"target")]))

    def test_rejects_windows_reparse_device_and_conflicting_directory_attributes(self) -> None:
        for attributes in (0x0400, 0x0040, 0x0010):
            info = zipfile.ZipInfo("ordinary-file")
            info.create_system = 0
            info.external_attr = attributes
            with self.subTest(attributes=hex(attributes)), self.assertRaises(ArchiveError):
                scan_archive(self.archive_bytes([(info, b"payload")]))

    def test_rejects_link_capable_and_truncated_extra_fields(self) -> None:
        for extra in (struct.pack("<HH", 0x000D, 0), struct.pack("<HH", 0x756E, 0), b"\x0a"):
            info = zipfile.ZipInfo("member")
            info.extra = extra
            with self.subTest(extra=extra.hex()), self.assertRaises(ArchiveError):
                scan_archive(self.archive_bytes([(info, b"payload")]))

    def test_rejects_duplicate_local_header_offsets(self) -> None:
        payload = bytearray(self.archive_bytes([("first", b"a"), ("other", b"b")]))
        first = payload.index(b"PK\x01\x02")
        second = payload.index(b"PK\x01\x02", first + 4)
        first_offset = struct.unpack_from("<I", payload, first + 42)[0]
        struct.pack_into("<I", payload, second + 42, first_offset)
        with self.assertRaises(ArchiveError):
            scan_archive(bytes(payload))

    def test_rejects_data_before_or_after_the_exact_zip_envelope(self) -> None:
        payload = self.archive_bytes([("member", b"payload")])
        with self.assertRaises(ArchiveError):
            scan_archive(b"prefix" + payload)
        with self.assertRaises(ArchiveError):
            scan_archive(payload + b"suffix")
        commented = io.BytesIO()
        with zipfile.ZipFile(commented, "w") as archive:
            archive.comment = b"allowed exact comment"
            archive.writestr("member", b"payload")
        self.assertEqual(b"payload", read_archive_member(scan_archive(commented.getvalue()), "member"))
        self.assertEqual((), scan_archive(self.archive_bytes([])).members)

    def test_rejects_crc_mismatch_and_abnormal_compression_ratio(self) -> None:
        corrupt = bytearray(self.archive_bytes([("member", b"payload")]))
        central = corrupt.index(b"PK\x01\x02")
        crc = struct.unpack_from("<I", corrupt, central + 16)[0]
        struct.pack_into("<I", corrupt, central + 16, crc ^ 0xFFFFFFFF)
        with self.assertRaises(ArchiveError):
            scan_archive(bytes(corrupt))
        bomb = self.archive_bytes(
            [("bomb", b"0" * (2 * 1024 * 1024))], compression=zipfile.ZIP_DEFLATED,
        )
        with self.assertRaises(ArchiveError):
            scan_archive(bomb)

    def test_custom_member_count_size_total_depth_and_path_limits(self) -> None:
        cases = (
            (
                self.archive_bytes([("a", b""), ("b", b"")]),
                ArchiveLimits(max_members=1),
            ),
            (
                self.archive_bytes([("a", b"12")]),
                ArchiveLimits(max_member_size=1),
            ),
            (
                self.archive_bytes([("a", b"1"), ("b", b"2")]),
                ArchiveLimits(max_total_size=1),
            ),
            (
                self.archive_bytes([("a/b", b"")]),
                ArchiveLimits(max_depth=1),
            ),
            (
                self.archive_bytes([("abcd", b"")]),
                ArchiveLimits(max_path_utf16_units=3),
            ),
        )
        for payload, limits in cases:
            with self.subTest(limits=limits), self.assertRaises(ArchiveError):
                scan_archive(payload, limits=limits)

    def test_member_limit_is_enforced_before_zipfile_allocates_member_objects(self) -> None:
        payload = bytearray(self.archive_bytes([("only", b"one")]))
        eocd = payload.rindex(b"PK\x05\x06")
        struct.pack_into("<HH", payload, eocd + 8, 4097, 4097)
        with mock.patch.object(
            archive_module.zipfile,
            "ZipFile",
            side_effect=AssertionError("ZipFile must not run before the count limit"),
        ), self.assertRaisesRegex(ArchiveError, "exceeds 4096 members"):
            scan_archive(bytes(payload))

    def test_structural_member_count_is_enforced_before_zipfile_allocation(self) -> None:
        payload = bytearray(self.archive_bytes([("first", b"a"), ("second", b"b")]))
        eocd = payload.rindex(b"PK\x05\x06")
        struct.pack_into("<HH", payload, eocd + 8, 1, 1)
        with mock.patch.object(
            archive_module.zipfile,
            "ZipFile",
            side_effect=AssertionError("ZipFile must not parse an over-limit central directory"),
        ), self.assertRaisesRegex(ArchiveError, "exceeds 1 members"):
            scan_archive(bytes(payload), limits=ArchiveLimits(max_members=1))

    def test_central_metadata_limit_is_enforced_before_zipfile_allocation(self) -> None:
        payload = self.archive_bytes([("member-with-metadata", b"payload")])
        eocd = payload.rindex(b"PK\x05\x06")
        central_size = struct.unpack_from("<I", payload, eocd + 12)[0]
        self.assertGreater(central_size, 1)
        with mock.patch.object(
            archive_module.zipfile,
            "ZipFile",
            side_effect=AssertionError("ZipFile must not parse oversized metadata"),
        ), self.assertRaisesRegex(ArchiveError, "metadata limit"):
            scan_archive(
                payload,
                limits=ArchiveLimits(max_metadata_size=central_size - 1),
            )

    def test_required_and_executable_members_must_be_exact_present_files(self) -> None:
        payload = self.archive_bytes([("bin/", b""), ("bin/run", b"run")])
        with self.assertRaises(ArchiveError):
            scan_archive(payload, required_members=("BIN/run",))
        with self.assertRaises(ArchiveError):
            scan_archive(payload, executable_members=("bin/",))
        with self.assertRaises(ArchiveError):
            read_archive_member(scan_archive(payload), "bin/")

    def test_plan_member_uses_the_limits_that_created_the_plan(self) -> None:
        name = "a" * 241
        limits = ArchiveLimits(max_path_utf16_units=300)
        plan = scan_archive(self.archive_bytes([(name, b"payload")]), limits=limits)
        self.assertEqual(name, plan.member(name).name)

    def test_path_source_must_be_regular_and_not_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ArchiveError):
                scan_archive(root)
            source = root / "archive.zip"
            source.write_bytes(self.archive_bytes([("member", b"payload")]))
            if os.name != "nt":
                link = root / "link.zip"
                link.symlink_to(source)
                with self.assertRaises(ArchiveError):
                    scan_archive(link)

    def test_changed_source_invalidates_read_and_extract_without_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "archive.zip"
            source.write_bytes(self.archive_bytes([("member", b"payload")]))
            plan = scan_archive(source)
            source.write_bytes(self.archive_bytes([("member", b"changed")]))
            with self.assertRaises(ArchiveError):
                read_archive_member(plan, "member")
            destination = root / "destination"
            with self.assertRaises(ArchiveError):
                extract_archive(plan, destination)
            self.assertFalse(destination.exists())
            self.assertEqual([], list(root.glob(".destination.*.tmp")))

    def test_extraction_failure_rolls_back_private_staging(self) -> None:
        plan = scan_archive(self.archive_bytes([("member", b"payload")]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"
            with mock.patch.object(
                archive_module, "_stream_member", side_effect=ArchiveError("injected failure"),
            ), self.assertRaises(ArchiveError):
                extract_archive(plan, destination)
            self.assertFalse(destination.exists())
            self.assertEqual([], list(root.glob(".destination.*.tmp")))

    def test_unanchored_extraction_aborts_when_private_staging_cannot_be_created(self) -> None:
        plan = scan_archive(self.archive_bytes([("member", b"payload")]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"
            with mock.patch.object(
                archive_module,
                "_supports_anchored_directories",
                return_value=False,
            ), mock.patch.object(
                private_fs,
                "private_mkdtemp",
                side_effect=private_fs.PrivateFilesystemError("private staging unavailable"),
            ), self.assertRaises(ArchiveError):
                extract_archive(plan, destination)
            self.assertFalse(destination.exists())
            self.assertEqual([], list(root.glob(".destination.*.tmp")))

    def test_parent_fsync_failure_after_commit_preserves_destination_and_personal_file(self) -> None:
        plan = scan_archive(self.archive_bytes([("member", b"payload")]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"

            def fail_after_personal_file(anchor: object) -> None:
                (destination / "personal.txt").write_bytes(b"personal")
                raise OSError("injected parent fsync failure")

            with mock.patch.object(
                archive_module,
                "_fsync_anchor",
                side_effect=fail_after_personal_file,
            ), self.assertRaises(ArchiveError):
                extract_archive(plan, destination)
            self.assertEqual(b"payload", (destination / "member").read_bytes())
            self.assertEqual(b"personal", (destination / "personal.txt").read_bytes())
            self.assertEqual([], list(root.glob(".destination.*.tmp")))

    def test_final_source_revalidation_failure_removes_staging_before_promotion(self) -> None:
        plan = scan_archive(self.archive_bytes([("member", b"payload")]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"
            real_ensure = archive_module._ensure_source_stable
            calls = 0

            def fail_final(*arguments: object, **keywords: object) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise ArchiveError("injected final source drift")
                real_ensure(*arguments, **keywords)

            with mock.patch.object(
                archive_module, "_ensure_source_stable", side_effect=fail_final,
            ), self.assertRaises(ArchiveError):
                extract_archive(plan, destination)
            self.assertEqual(2, calls)
            self.assertFalse(destination.exists())
            self.assertEqual([], list(root.glob(".destination.*.tmp")))

    def test_promotion_error_after_rename_preserves_committed_destination(self) -> None:
        plan = scan_archive(self.archive_bytes([("member", b"payload")]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"
            real_promote = archive_module._atomic_promote

            def promote_then_fail(
                staging: Path,
                target: Path,
                *,
                parent_descriptor: int | None = None,
            ) -> None:
                real_promote(
                    staging,
                    target,
                    parent_descriptor=parent_descriptor,
                )
                (target / "personal.txt").write_bytes(b"personal")
                raise OSError("injected error after successful rename")

            with mock.patch.object(
                archive_module,
                "_atomic_promote",
                side_effect=promote_then_fail,
            ), self.assertRaises(ArchiveError):
                extract_archive(plan, destination)
            self.assertEqual(b"payload", (destination / "member").read_bytes())
            self.assertEqual(b"personal", (destination / "personal.txt").read_bytes())
            self.assertEqual([], list(root.glob(".destination.*.tmp")))

    def test_moved_destination_before_confirmation_is_never_rolled_back(self) -> None:
        plan = scan_archive(self.archive_bytes([("member", b"payload")]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"
            moved = root / "destination-moved"
            real_promote = archive_module._atomic_promote

            def promote_then_move(
                staging: Path,
                target: Path,
                *,
                parent_descriptor: int | None = None,
            ) -> None:
                real_promote(
                    staging,
                    target,
                    parent_descriptor=parent_descriptor,
                )
                target.rename(moved)
                (moved / "personal.txt").write_bytes(b"personal")
                target.mkdir()
                (target / "replacement.txt").write_bytes(b"replacement")

            with mock.patch.object(
                archive_module,
                "_atomic_promote",
                side_effect=promote_then_move,
            ), self.assertRaises(ArchiveError):
                extract_archive(plan, destination)
            self.assertEqual(b"payload", (moved / "member").read_bytes())
            self.assertEqual(b"personal", (moved / "personal.txt").read_bytes())
            self.assertEqual(
                b"replacement",
                (destination / "replacement.txt").read_bytes(),
            )

    def test_error_after_published_destination_moves_preserves_both_trees(self) -> None:
        plan = scan_archive(self.archive_bytes([("member", b"payload")]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"
            moved = root / "destination-moved"
            real_promote = archive_module._atomic_promote

            def promote_move_then_fail(
                staging: Path,
                target: Path,
                *,
                parent_descriptor: int | None = None,
            ) -> None:
                real_promote(
                    staging,
                    target,
                    parent_descriptor=parent_descriptor,
                )
                target.rename(moved)
                (moved / "personal.txt").write_bytes(b"personal")
                target.mkdir()
                (target / "replacement.txt").write_bytes(b"replacement")
                raise OSError("injected error after destination move")

            with mock.patch.object(
                archive_module,
                "_atomic_promote",
                side_effect=promote_move_then_fail,
            ), self.assertRaises(ArchiveError):
                extract_archive(plan, destination)
            self.assertEqual(b"payload", (moved / "member").read_bytes())
            self.assertEqual(b"personal", (moved / "personal.txt").read_bytes())
            self.assertEqual(
                b"replacement",
                (destination / "replacement.txt").read_bytes(),
            )

    def test_inconclusive_post_promotion_identity_preserves_destination(self) -> None:
        plan = scan_archive(self.archive_bytes([("member", b"payload")]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"
            real_entry_identity = archive_module._entry_identity
            failed = False

            def fail_published_identity(anchor: object, name: str) -> object:
                nonlocal failed
                if name == destination.name and destination.exists() and not failed:
                    failed = True
                    (destination / "personal.txt").write_bytes(b"personal")
                    raise OSError("injected inconclusive destination identity")
                return real_entry_identity(anchor, name)

            with mock.patch.object(
                archive_module,
                "_entry_identity",
                side_effect=fail_published_identity,
            ), self.assertRaises(ArchiveError):
                extract_archive(plan, destination)
            self.assertTrue(failed)
            self.assertEqual(b"payload", (destination / "member").read_bytes())
            self.assertEqual(b"personal", (destination / "personal.txt").read_bytes())
            self.assertEqual([], list(root.glob(".destination.*.tmp")))

    def test_existing_destination_is_never_overwritten(self) -> None:
        plan = scan_archive(self.archive_bytes([("member", b"payload")]))
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "destination"
            destination.mkdir()
            sentinel = destination / "personal.cfg"
            sentinel.write_bytes(b"personal")
            with self.assertRaises(ArchiveError):
                extract_archive(plan, destination)
            self.assertEqual(b"personal", sentinel.read_bytes())

    def test_concurrent_destination_is_not_replaced_by_atomic_promotion(self) -> None:
        plan = scan_archive(self.archive_bytes([("member", b"payload")]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"
            real_promote = archive_module._atomic_promote

            def race(
                staging: Path,
                target: Path,
                *,
                parent_descriptor: int | None = None,
            ) -> None:
                target.mkdir()
                (target / "personal").write_bytes(b"personal")
                real_promote(
                    staging,
                    target,
                    parent_descriptor=parent_descriptor,
                )

            with mock.patch.object(
                archive_module, "_atomic_promote", side_effect=race,
            ), self.assertRaises(ArchiveError):
                extract_archive(plan, destination)
            self.assertEqual(b"personal", (destination / "personal").read_bytes())
            self.assertEqual([], list(root.glob(".destination.*.tmp")))

    @unittest.skipUnless(
        archive_module._supports_anchored_directories(),
        "requires descriptor-relative directory operations",
    )
    def test_staging_name_replacement_during_promotion_preserves_both_trees(self) -> None:
        plan = scan_archive(self.archive_bytes([("member", b"payload")]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"
            moved: Path | None = None

            def replace_staging(
                staging: Path,
                target: Path,
                *,
                parent_descriptor: int | None = None,
            ) -> None:
                nonlocal moved
                self.assertIsNotNone(parent_descriptor)
                moved = staging.with_name(f"{staging.name}.moved")
                staging.rename(moved)
                staging.mkdir()
                (staging / "personal.txt").write_bytes(b"personal")
                raise OSError("injected failure after staging replacement")

            with mock.patch.object(
                archive_module,
                "_atomic_promote",
                side_effect=replace_staging,
            ), self.assertRaises(ArchiveError):
                extract_archive(plan, destination)
            self.assertIsNotNone(moved)
            self.assertEqual(b"payload", (moved / "member").read_bytes())
            replacements = list(root.glob(".destination.*.tmp"))
            replacement = [path for path in replacements if path != moved]
            self.assertEqual(1, len(replacement))
            self.assertEqual(b"personal", (replacement[0] / "personal.txt").read_bytes())
            self.assertFalse(destination.exists())

    @unittest.skipUnless(
        archive_module._supports_anchored_directories(),
        "requires descriptor-relative directory operations",
    )
    def test_parent_path_replacement_does_not_redirect_promotion_or_cleanup(self) -> None:
        plan = scan_archive(self.archive_bytes([("member", b"payload")]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "parent"
            parent.mkdir()
            destination = parent / "destination"
            moved_parent = root / "parent-moved"
            real_promote = archive_module._atomic_promote

            def replace_parent(
                staging: Path,
                target: Path,
                *,
                parent_descriptor: int | None = None,
            ) -> None:
                self.assertIsNotNone(parent_descriptor)
                parent.rename(moved_parent)
                parent.mkdir()
                (parent / "personal.txt").write_bytes(b"personal")
                real_promote(
                    staging,
                    target,
                    parent_descriptor=parent_descriptor,
                )

            with mock.patch.object(
                archive_module,
                "_atomic_promote",
                side_effect=replace_parent,
            ), self.assertRaisesRegex(ArchiveError, "parent changed"):
                extract_archive(plan, destination)
            self.assertEqual(b"personal", (parent / "personal.txt").read_bytes())
            self.assertEqual(
                b"payload",
                (moved_parent / "destination" / "member").read_bytes(),
            )
            self.assertEqual([], list(moved_parent.glob(".destination.*.tmp")))

    def test_parent_replacement_between_validation_and_anchor_is_rejected(self) -> None:
        plan = scan_archive(self.archive_bytes([("member", b"payload")]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parent = root / "parent"
            parent.mkdir()
            destination = parent / "destination"
            moved_parent = root / "parent-moved"
            real_anchor = archive_module._directory_anchor

            def replace_before_anchor(
                path: Path,
                expected_identity: tuple[int, int, int] | None = None,
            ) -> object:
                parent.rename(moved_parent)
                parent.mkdir()
                (parent / "personal.txt").write_bytes(b"personal")
                return real_anchor(path, expected_identity)

            with mock.patch.object(
                archive_module,
                "_directory_anchor",
                side_effect=replace_before_anchor,
            ), self.assertRaisesRegex(ArchiveError, "before it could be anchored"):
                extract_archive(plan, destination)
            self.assertEqual(b"personal", (parent / "personal.txt").read_bytes())
            self.assertEqual([], list(parent.glob(".destination.*.tmp")))
            self.assertEqual([], list(moved_parent.glob(".destination.*.tmp")))

    def test_unanchored_cleanup_preserves_replaced_and_moved_staging(self) -> None:
        plan = scan_archive(self.archive_bytes([("member", b"payload")]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "destination"
            moved: Path | None = None

            def replace_staging(
                staging: Path,
                target: Path,
                *,
                parent_descriptor: int | None = None,
            ) -> None:
                nonlocal moved
                self.assertIsNone(parent_descriptor)
                moved = staging.with_name(f"{staging.name}.moved")
                staging.rename(moved)
                staging.mkdir()
                (staging / "personal.txt").write_bytes(b"personal")
                raise OSError("injected failure after staging replacement")

            with mock.patch.object(
                archive_module,
                "_supports_anchored_directories",
                return_value=False,
            ), mock.patch.object(
                archive_module,
                "_atomic_promote",
                side_effect=replace_staging,
            ), self.assertRaises(ArchiveError):
                extract_archive(plan, destination)
            self.assertIsNotNone(moved)
            self.assertEqual(b"payload", (moved / "member").read_bytes())
            replacements = [
                path for path in root.glob(".destination.*.tmp")
                if not path.name.endswith(".moved")
            ]
            self.assertEqual(1, len(replacements))
            self.assertEqual(b"personal", (replacements[0] / "personal.txt").read_bytes())

    def test_tampered_plan_cannot_expand_extracted_permissions(self) -> None:
        plan = scan_archive(self.archive_bytes([("member", b"payload")]))
        altered_member = replace(plan.members[0], mode=0o777)
        altered_plan = replace(plan, members=(altered_member,))
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "destination"
            with self.assertRaises(ArchiveError):
                extract_archive(altered_plan, destination)
            self.assertFalse(destination.exists())

    def test_installer_bundle_contract_validates_exact_layout_and_identities(self) -> None:
        plan = validate_installer_bundle(self.installer_bundle(), "1.2.3")
        self.assertEqual(9, len(plan.members))
        self.assertEqual(
            {"x86qw-installer-1.2.3/x86qw.sh", "x86qw-installer-1.2.3/dist/installer/bin/manager.py"},
            plan.executable_members,
        )
        with self.assertRaises(ArchiveError):
            validate_installer_bundle(self.installer_bundle() + b"unexpected", "1.2.3")

        prefix = "x86qw-installer-1.2.3"
        with zipfile.ZipFile(io.BytesIO(self.installer_bundle()), "r") as source:
            members = [(info.filename, source.read(info)) for info in source.infolist()]
        without_notice = [item for item in members if item[0] != f"{prefix}/NOTICE"]
        with self.assertRaises(ArchiveError):
            validate_installer_bundle(self.archive_bytes(without_notice), "1.2.3")

        nested = self.archive_bytes([
            ("__main__.py", b"print('x86QW')\n"),
            ("_x86qw/installer.json", self.identity_bytes("1.2.3")),
        ])
        without_nested_notice = [
            (name, nested if name == f"{prefix}/x86qw.pyz" else payload)
            for name, payload in members
        ]
        with self.assertRaises(ArchiveError):
            validate_installer_bundle(
                self.archive_bytes(without_nested_notice), "1.2.3",
            )

    def test_installer_history_contract_preserves_the_six_member_legacy_layout(self) -> None:
        legacy = self.historical_installer_bundle()
        plan = validate_installer_history_bundle(legacy, "0.1.19")
        self.assertEqual(6, len(plan.members))
        with self.assertRaises(ArchiveError):
            validate_installer_bundle(legacy, "0.1.19")
        with self.assertRaises(ArchiveError):
            validate_installer_history_bundle(
                self.historical_installer_bundle("0.1.20"), "0.1.20",
            )

    def test_installer_bundle_rejects_extra_member_and_nested_identity_drift(self) -> None:
        version = "1.2.3"
        prefix = f"x86qw-installer-{version}"
        identity = self.identity_bytes(version)
        valid = self.installer_bundle(version)
        with zipfile.ZipFile(io.BytesIO(valid)) as source:
            members = [(info.filename, source.read(info)) for info in source.infolist()]
        with self.assertRaises(ArchiveError):
            validate_installer_bundle(
                self.archive_bytes([*members, (f"{prefix}/extra", b"no")]), version,
            )
        wrong_application = self.archive_bytes([
            ("_x86qw/installer.json", self.identity_bytes("9.9.9")),
        ])
        drifted = [
            (name, wrong_application if name == f"{prefix}/x86qw.pyz" else payload)
            for name, payload in members
        ]
        self.assertIn(identity, [payload for _, payload in members])
        with self.assertRaises(ArchiveError):
            validate_installer_bundle(self.archive_bytes(drifted), version)

    def test_every_repository_zip_pk3_and_zipapp_passes_the_canonical_scan(self) -> None:
        archives = sorted(
            path
            for path in (ROOT / "dist").rglob("*")
            if path.is_file() and path.suffix.casefold() in {".zip", ".pk3", ".pyz"}
        )
        self.assertGreaterEqual(len(archives), 1, "the repository archive inventory is empty")
        for path in archives:
            with self.subTest(path=path.relative_to(ROOT).as_posix()):
                scan_archive(path)


if __name__ == "__main__":
    unittest.main()
