#!/usr/bin/env python3
"""Canonical, bounded ZIP/PK3 inspection and extraction boundary.

The scanner treats every archive as hostile.  It validates and streams every
member before an :class:`ArchivePlan` can exist.  Reads and extraction bind
that plan to the original source identity and SHA-256 digest.
"""

from __future__ import annotations

import argparse
import binascii
import contextlib
import ctypes
import errno
import hashlib
import io
import json
import math
import os
import re
import secrets
import stat
import struct
import sys
import tempfile
import unicodedata
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO


_CHUNK_SIZE = 1024 * 1024
_ALLOWED_COMPRESSION_METHODS = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
_ALLOWED_GENERAL_PURPOSE_FLAGS = 0x080E
_LINK_CAPABLE_UNIX_EXTRA_FIELDS = frozenset({0x000D, 0x756E})
_WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>"|?*')
_WINDOWS_DIRECTORY_ATTRIBUTE = 0x0010
_WINDOWS_DEVICE_ATTRIBUTE = 0x0040
_WINDOWS_REPARSE_POINT_ATTRIBUTE = 0x0400
_WINDOWS_RESERVED_NAMES = frozenset({
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CLOCK$",
    "CONIN$",
    "CONOUT$",
    *(f"COM{suffix}" for suffix in (*range(1, 10), "¹", "²", "³")),
    *(f"LPT{suffix}" for suffix in (*range(1, 10), "¹", "²", "³")),
})
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_LOCAL_FILE_HEADER = b"PK\x03\x04"
_CENTRAL_DIRECTORY_HEADER = b"PK\x01\x02"
_END_OF_CENTRAL_DIRECTORY = b"PK\x05\x06"
_ZIP64_END_OF_CENTRAL_DIRECTORY = b"PK\x06\x06"
_ZIP64_END_LOCATOR = b"PK\x06\x07"


class ArchiveError(ValueError):
    """An archive failed the canonical safety or integrity contract."""


def _private_filesystem_boundary():
    """Return the runtime boundary, or ``None`` for the standalone bootstrap helper.

    Public bootstraps materialize this archive module as a deliberately small
    helper package.  Keeping that historical projection executable lets older
    immutable bootstraps validate newer archives while the complete runtime
    uses the canonical private-filesystem implementation.
    """
    try:
        import x86qw_runtime.io.private_fs as private_fs
    except ModuleNotFoundError as error:
        if error.name not in {
            "x86qw_runtime",
            "x86qw_runtime.io",
            "x86qw_runtime.io.private_fs",
        }:
            raise
        return None
    return private_fs


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    """Hard limits applied before and while any member is decompressed."""

    max_members: int = 4096
    max_source_size: int = 512 * 1024 * 1024
    max_metadata_size: int = 32 * 1024 * 1024
    max_member_size: int = 128 * 1024 * 1024
    max_total_size: int = 512 * 1024 * 1024
    max_depth: int = 16
    max_path_utf16_units: int = 240
    max_compression_ratio: float = 500.0
    allowed_compression_methods: frozenset[int] = _ALLOWED_COMPRESSION_METHODS

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_members,
            self.max_source_size,
            self.max_metadata_size,
            self.max_member_size,
            self.max_total_size,
            self.max_depth,
            self.max_path_utf16_units,
        )
        if any(type(value) is not int or value <= 0 for value in integer_limits):
            raise ValueError("archive count and size limits must be positive integers")
        ratio = self.max_compression_ratio
        if (
            isinstance(ratio, bool)
            or not isinstance(ratio, (int, float))
            or not math.isfinite(float(ratio))
            or ratio <= 0
        ):
            raise ValueError("archive compression ratio must be finite and positive")
        object.__setattr__(self, "max_compression_ratio", float(ratio))
        methods = frozenset(self.allowed_compression_methods)
        if (
            not methods
            or any(type(method) is not int for method in methods)
            or not methods.issubset(_ALLOWED_COMPRESSION_METHODS)
        ):
            raise ValueError("only stored and deflated ZIP members are supported")
        object.__setattr__(self, "allowed_compression_methods", methods)


DEFAULT_ARCHIVE_LIMITS = ArchiveLimits()


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    """Immutable metadata established by a complete streamed scan."""

    name: str
    is_dir: bool
    size: int
    compressed_size: int
    crc32: int
    sha256: str
    compression_method: int
    mode: int

    @property
    def kind(self) -> str:
        return "directory" if self.is_dir else "file"

    @property
    def path(self) -> PurePosixPath:
        return PurePosixPath(self.name)

    @property
    def file_size(self) -> int:
        return self.size

    @property
    def compress_size(self) -> int:
        return self.compressed_size

    @property
    def compress_type(self) -> int:
        return self.compression_method


_SourceIdentity = tuple[int, int, int, int, int, int]
_PathHandleIdentity = tuple[int, ...]
_NodeIdentity = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class ArchivePlan:
    """A fully scanned archive bound to its source bytes and identity."""

    source: Path | bytes = field(repr=False)
    source_sha256: str
    source_size: int
    source_identity: _SourceIdentity | None
    members: tuple[ArchiveMember, ...]
    required_members: tuple[str, ...]
    executable_members: frozenset[str]
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS

    def member(self, name: str) -> ArchiveMember:
        canonical = _requested_name(name, "member", self.limits)
        for member in self.members:
            if member.name == canonical:
                return member
        raise ArchiveError(f"archive member is absent: {name!r}")

    @property
    def member_names(self) -> tuple[str, ...]:
        return tuple(member.name for member in self.members)


def _stat_identity(value: os.stat_result) -> _SourceIdentity:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_size),
        int(getattr(value, "st_mtime_ns", value.st_mtime * 1_000_000_000)),
        int(getattr(value, "st_ctime_ns", value.st_ctime * 1_000_000_000)),
    )


def _nanoseconds(value: os.stat_result, field: str, fallback: str) -> int:
    exact = getattr(value, field, None)
    if exact is not None:
        return int(exact)
    return int(getattr(value, fallback) * 1_000_000_000)


def _windows_path_handle_identity(value: os.stat_result) -> _PathHandleIdentity:
    """Normalize fields whose Windows path/fstat semantics are identical."""
    birthtime = getattr(value, "st_birthtime_ns", None)
    if birthtime is None:
        birthtime = _nanoseconds(value, "st_ctime_ns", "st_ctime")
    return (
        int(value.st_dev),
        int(value.st_ino),
        stat.S_IFMT(int(value.st_mode)),
        int(value.st_size),
        _nanoseconds(value, "st_mtime_ns", "st_mtime"),
        int(birthtime),
        int(getattr(value, "st_file_attributes", 0)),
    )


def _path_handle_identity(value: os.stat_result) -> _PathHandleIdentity:
    """Return fields with identical semantics for path stat and fstat.

    CPython 3.13 reports Windows ``st_ctime`` as creation time for a path but
    as change time for a descriptor.  Windows also synthesizes executable mode
    bits only for path-based stats.  Keep the full descriptor identity for
    handle-to-handle checks and use this normalized key only across that API
    boundary.
    """
    if os.name == "nt":
        return _windows_path_handle_identity(value)
    return _stat_identity(value)


def _checked_lstat(path: Path) -> _PathHandleIdentity:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ArchiveError(f"archive source is unavailable: {path}") from error
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if (
        stat.S_ISLNK(metadata.st_mode)
        or attributes & _WINDOWS_REPARSE_POINT_ATTRIBUTE
        or not stat.S_ISREG(metadata.st_mode)
    ):
        raise ArchiveError(f"archive source must be a regular non-symlink file: {path}")
    return _path_handle_identity(metadata)


@contextlib.contextmanager
def _open_path(path: Path) -> Iterator[tuple[BinaryIO, _SourceIdentity]]:
    before = _checked_lstat(path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ArchiveError(f"archive source could not be opened safely: {path}") from error
    try:
        opened = os.fstat(descriptor)
        identity = _stat_identity(opened)
        if not stat.S_ISREG(opened.st_mode) or _path_handle_identity(opened) != before:
            raise ArchiveError(f"archive source changed while opening: {path}")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            yield stream, identity
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _source_value(source: Path | bytes) -> Path | bytes:
    if isinstance(source, bytes):
        return bytes(source)
    if not isinstance(source, Path):
        raise TypeError("archive source must be pathlib.Path or bytes")
    return source.absolute()


@contextlib.contextmanager
def _open_source(source: Path | bytes) -> Iterator[tuple[BinaryIO, _SourceIdentity | None]]:
    if isinstance(source, bytes):
        with io.BytesIO(source) as stream:
            yield stream, None
        return
    with _open_path(source) as opened:
        yield opened


def _stream_sha256(
    stream: BinaryIO,
    max_size: int,
    output: BinaryIO | None = None,
) -> tuple[str, int]:
    stream.seek(0)
    digest = hashlib.sha256()
    size = 0
    while True:
        request_size = min(_CHUNK_SIZE, max_size - size + 1)
        block = stream.read(request_size)
        if not block:
            break
        digest.update(block)
        size += len(block)
        if size > max_size:
            raise ArchiveError(f"archive source exceeds the {max_size}-byte size limit")
        if output is not None:
            output.write(block)
    stream.seek(0)
    return digest.hexdigest(), size


def _preflight_source_size(
    stream: BinaryIO,
    source: Path | bytes,
    limit: int,
) -> int:
    size = len(source) if isinstance(source, bytes) else int(os.fstat(stream.fileno()).st_size)
    if size > limit:
        raise ArchiveError(f"archive source exceeds the {limit}-byte size limit")
    return size


def _ensure_source_stable(
    stream: BinaryIO,
    source: Path | bytes,
    identity: _SourceIdentity | None,
    expected_sha256: str,
    expected_size: int,
    max_source_size: int,
) -> None:
    actual_sha256, actual_size = _stream_sha256(stream, max_source_size)
    if actual_sha256 != expected_sha256 or actual_size != expected_size:
        raise ArchiveError("archive source changed during validation")
    if isinstance(source, Path):
        current_metadata = os.fstat(stream.fileno())
        current = _stat_identity(current_metadata)
        if (
            current != identity
            or _checked_lstat(source) != _path_handle_identity(current_metadata)
        ):
            raise ArchiveError("archive source identity changed during validation")


@contextlib.contextmanager
def _private_snapshot(source: Path | bytes) -> Iterator[BinaryIO]:
    boundary = _private_filesystem_boundary()
    if boundary is None:
        # The historical bootstrap helper contains only archive.py.  Its
        # archive lives inside the bootstrap's private work directory, so keep
        # the fallback snapshot there instead of the process-wide temp root.
        directory = source.parent if isinstance(source, Path) else None
        with tempfile.TemporaryFile(
            mode="w+b",
            prefix=".x86qw-archive-",
            suffix=".snapshot",
            dir=directory,
        ) as snapshot:
            if os.name != "nt":
                os.fchmod(snapshot.fileno(), 0o600)
            yield snapshot
        return

    descriptor, path = boundary.private_mkstemp(
        directory=Path(tempfile.gettempdir()),
        prefix=".x86qw-archive-",
        suffix=".snapshot",
    )
    identity: tuple[int, int] | None = None
    snapshot: BinaryIO | None = None
    try:
        identity = boundary.persistent_descriptor_identity(
            descriptor, directory=False,
        )
        if os.name != "nt":
            # POSIX keeps the open inode usable after unlink, eliminating a
            # pathname cleanup race for the archive snapshot.
            path.unlink()
            identity = None
        snapshot = os.fdopen(descriptor, "w+b")
        descriptor = -1
        yield snapshot
    finally:
        try:
            if snapshot is not None:
                snapshot.close()
            elif descriptor >= 0:
                os.close(descriptor)
        finally:
            if identity is not None:
                boundary.unlink_private_file(path, expected_identity=identity)


@contextlib.contextmanager
def _source_snapshot(
    source: Path | bytes,
    limits: ArchiveLimits,
) -> Iterator[tuple[BinaryIO, _SourceIdentity | None, str, int]]:
    """Yield one private immutable snapshot after bounded source validation."""
    with _open_source(source) as (original, identity):
        declared_size = _preflight_source_size(
            original,
            source,
            limits.max_source_size,
        )
        with _private_snapshot(source) as snapshot:
            source_sha256, source_size = _stream_sha256(
                original,
                limits.max_source_size,
                snapshot,
            )
            if source_size != declared_size:
                raise ArchiveError("archive source changed while creating its snapshot")
            snapshot.flush()
            os.fsync(snapshot.fileno())
            snapshot.seek(0)
            _ensure_source_stable(
                original,
                source,
                identity,
                source_sha256,
                source_size,
                limits.max_source_size,
            )
            yield snapshot, identity, source_sha256, source_size
            _ensure_source_stable(
                original,
                source,
                identity,
                source_sha256,
                source_size,
                limits.max_source_size,
            )


def _read_exact_at(stream: BinaryIO, offset: int, size: int, label: str) -> bytes:
    if offset < 0 or size < 0:
        raise ArchiveError(f"archive has an invalid {label} offset")
    stream.seek(offset)
    value = stream.read(size)
    if len(value) != size:
        raise ArchiveError(f"archive has a truncated {label}")
    return value


def _validate_zip_envelope(
    stream: BinaryIO,
    source_size: int,
    infos: Sequence[zipfile.ZipInfo] | None = None,
    *,
    max_members: int | None = None,
    max_metadata_size: int | None = None,
) -> int:
    """Reject malformed envelopes and return their declared member count.

    Passing ``infos=None`` is the allocation preflight: the EOCD is inspected
    before ``zipfile`` is allowed to materialize one ``ZipInfo`` per member.
    """
    if source_size < 22:
        raise ArchiveError("archive is too short to contain a complete ZIP envelope")
    tail_size = min(source_size, 22 + 0xFFFF)
    tail_offset = source_size - tail_size
    tail = _read_exact_at(stream, tail_offset, tail_size, "ZIP tail")
    search_end = len(tail)
    eocd_relative = -1
    while True:
        candidate = tail.rfind(_END_OF_CENTRAL_DIRECTORY, 0, search_end)
        if candidate < 0:
            break
        if candidate + 22 <= len(tail):
            comment_size = struct.unpack_from("<H", tail, candidate + 20)[0]
            if candidate + 22 + comment_size == len(tail):
                eocd_relative = candidate
                break
        search_end = candidate
    if eocd_relative < 0:
        raise ArchiveError("archive EOCD is absent, truncated, or followed by data")
    eocd_offset = tail_offset + eocd_relative
    eocd = tail[eocd_relative:eocd_relative + 22]
    (
        disk_number,
        central_disk,
        disk_entries,
        total_entries,
        central_size_32,
        central_offset_32,
    ) = struct.unpack_from("<HHHHII", eocd, 4)
    if disk_number or central_disk or disk_entries != total_entries:
        raise ArchiveError("multi-disk ZIP archives are unsupported")
    zip64 = (
        disk_entries == 0xFFFF
        or total_entries == 0xFFFF
        or central_size_32 == 0xFFFFFFFF
        or central_offset_32 == 0xFFFFFFFF
    )
    if zip64:
        locator_offset = eocd_offset - 20
        locator = _read_exact_at(stream, locator_offset, 20, "ZIP64 locator")
        if locator[:4] != _ZIP64_END_LOCATOR:
            raise ArchiveError("ZIP64 archive lacks its adjacent locator")
        locator_disk, zip64_offset, disk_count = struct.unpack_from("<IQI", locator, 4)
        if locator_disk != 0 or disk_count != 1:
            raise ArchiveError("multi-disk ZIP64 archives are unsupported")
        prefix = _read_exact_at(stream, zip64_offset, 56, "ZIP64 EOCD")
        if prefix[:4] != _ZIP64_END_OF_CENTRAL_DIRECTORY:
            raise ArchiveError("ZIP64 EOCD signature is invalid")
        record_size = struct.unpack_from("<Q", prefix, 4)[0]
        if record_size < 44 or zip64_offset + 12 + record_size != locator_offset:
            raise ArchiveError("ZIP64 EOCD has an invalid extent")
        (
            zip64_disk,
            zip64_central_disk,
            zip64_disk_entries,
            zip64_total_entries,
            central_size,
            central_offset,
        ) = struct.unpack_from("<IIQQQQ", prefix, 16)
        if (
            zip64_disk != 0
            or zip64_central_disk != 0
            or zip64_disk_entries != zip64_total_entries
        ):
            raise ArchiveError("multi-disk ZIP64 archives are unsupported")
        entry_count = zip64_total_entries
        central_end = zip64_offset
    else:
        entry_count = total_entries
        central_size = central_size_32
        central_offset = central_offset_32
        central_end = eocd_offset
    if infos is not None and entry_count != len(infos):
        raise ArchiveError("ZIP EOCD member count does not match the central directory")
    if central_offset + central_size != central_end:
        raise ArchiveError("ZIP central directory does not exactly fill its envelope")
    if max_metadata_size is not None and central_size > max_metadata_size:
        raise ArchiveError(
            f"ZIP central directory exceeds the {max_metadata_size}-byte metadata limit"
        )
    if max_members is not None and entry_count > max_members:
        raise ArchiveError(f"archive exceeds {max_members} members")
    _validate_central_directory_structure(
        stream,
        central_offset,
        central_size,
        entry_count,
        max_members,
    )
    first_signature = _read_exact_at(stream, 0, 4, "initial ZIP signature")
    if entry_count:
        if first_signature != _LOCAL_FILE_HEADER:
            raise ArchiveError("ZIP archive has a prepended payload or missing first local header")
        if infos is not None and (not infos or infos[0].header_offset != 0):
            raise ArchiveError("ZIP archive has a missing first local header")
        if _read_exact_at(
            stream, central_offset, 4, "central directory signature",
        ) != _CENTRAL_DIRECTORY_HEADER:
            raise ArchiveError("ZIP central directory does not start at its declared offset")
    elif central_offset != 0 or central_size != 0 or eocd_offset != 0:
        raise ArchiveError("empty ZIP archive has data outside its EOCD")
    stream.seek(0)
    return entry_count


def _validate_central_directory_structure(
    stream: BinaryIO,
    offset: int,
    size: int,
    declared_members: int,
    max_members: int | None,
) -> None:
    """Count exact central records without letting ``zipfile`` allocate them."""
    cursor = offset
    end = offset + size
    count = 0
    while cursor < end:
        if end - cursor < 46:
            raise ArchiveError("ZIP central directory has a truncated file header")
        header = _read_exact_at(stream, cursor, 46, "central directory file header")
        if header[:4] != _CENTRAL_DIRECTORY_HEADER:
            raise ArchiveError("ZIP central directory contains an unsupported record")
        name_size, extra_size, comment_size = struct.unpack_from("<HHH", header, 28)
        if name_size == 0:
            raise ArchiveError("ZIP central directory contains an empty member name")
        record_size = 46 + name_size + extra_size + comment_size
        if record_size > end - cursor:
            raise ArchiveError("ZIP central directory file record exceeds its envelope")
        cursor += record_size
        count += 1
        if max_members is not None and count > max_members:
            raise ArchiveError(f"archive exceeds {max_members} members")
    if cursor != end:
        raise ArchiveError("ZIP central directory has an invalid extent")
    if count != declared_members:
        raise ArchiveError(
            "ZIP EOCD member count does not match the central directory structure"
        )


def _utf16_units(value: str) -> int:
    return len(value.encode("utf-16-le", "surrogatepass")) // 2


def _validated_name(
    name: str,
    *,
    is_dir: bool,
    limits: ArchiveLimits,
    label: str = "archive member",
) -> tuple[str, tuple[str, ...], str]:
    if not isinstance(name, str) or not name:
        raise ArchiveError(f"{label} has an empty name")
    if _utf16_units(name) > limits.max_path_utf16_units:
        raise ArchiveError(f"{label} path exceeds {limits.max_path_utf16_units} UTF-16 units: {name!r}")
    if (
        name.startswith("/")
        or name.startswith("//")
        or "\\" in name
        or ":" in name
        or any(unicodedata.category(character).startswith("C") for character in name)
    ):
        raise ArchiveError(f"{label} path is not portable POSIX: {name!r}")
    if is_dir:
        if not name.endswith("/"):
            raise ArchiveError(f"directory member lacks its POSIX slash: {name!r}")
        canonical = name[:-1]
    else:
        if name.endswith("/"):
            raise ArchiveError(f"file member has a directory suffix: {name!r}")
        canonical = name
    parts = tuple(canonical.split("/"))
    if (
        not canonical
        or len(parts) > limits.max_depth
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ArchiveError(f"{label} path has an unsafe component: {name!r}")
    for part in parts:
        if any(character in _WINDOWS_FORBIDDEN_CHARACTERS for character in part):
            raise ArchiveError(f"{label} uses a Windows-forbidden character: {name!r}")
        if part.endswith((".", " ")):
            raise ArchiveError(f"{label} path has a trailing dot or space: {name!r}")
        reserved_stem = part.split(".", 1)[0].rstrip(". ").upper()
        if reserved_stem in _WINDOWS_RESERVED_NAMES:
            raise ArchiveError(f"{label} uses a Windows reserved name: {name!r}")
    posix = PurePosixPath(*parts)
    if posix.is_absolute() or posix.drive or posix.root or posix.as_posix() != canonical:
        raise ArchiveError(f"{label} path is not canonical POSIX: {name!r}")
    semantic = "/".join(unicodedata.normalize("NFC", part).casefold() for part in parts)
    return canonical, parts, semantic


def _requested_name(name: str, label: str, limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS) -> str:
    canonical, _, _ = _validated_name(
        name,
        is_dir=name.endswith("/") if isinstance(name, str) else False,
        limits=limits,
        label=label,
    )
    return canonical


def _requested_names(
    names: Iterable[str], label: str, limits: ArchiveLimits,
) -> tuple[str, ...]:
    try:
        values = tuple(names)
    except TypeError as error:
        raise TypeError(f"{label} must be an iterable of POSIX paths") from error
    canonical = tuple(_requested_name(name, label, limits) for name in values)
    if len(set(canonical)) != len(canonical):
        raise ArchiveError(f"{label} contains duplicate paths")
    return canonical


def _validate_member_layout(
    infos: Sequence[zipfile.ZipInfo], limits: ArchiveLimits,
) -> list[tuple[zipfile.ZipInfo, str, bool]]:
    if len(infos) > limits.max_members:
        raise ArchiveError(f"archive exceeds {limits.max_members} members")
    exact_names: set[str] = set()
    semantic_names: dict[str, str] = {}
    node_spellings: dict[str, str] = {}
    node_types: dict[str, str] = {}
    header_offsets: set[int] = set()
    validated: list[tuple[zipfile.ZipInfo, str, bool]] = []
    declared_total = 0
    for info in infos:
        original = info.orig_filename
        if info.header_offset < 0 or info.header_offset in header_offsets:
            raise ArchiveError(f"archive member has a duplicate local header: {original!r}")
        header_offsets.add(info.header_offset)
        extra_offset = 0
        while extra_offset < len(info.extra):
            if len(info.extra) - extra_offset < 4:
                raise ArchiveError(f"archive member has a truncated extra field: {original!r}")
            field_id = int.from_bytes(info.extra[extra_offset:extra_offset + 2], "little")
            field_size = int.from_bytes(info.extra[extra_offset + 2:extra_offset + 4], "little")
            extra_offset += 4
            field_end = extra_offset + field_size
            if field_end > len(info.extra):
                raise ArchiveError(f"archive member has a truncated extra field: {original!r}")
            if field_id in _LINK_CAPABLE_UNIX_EXTRA_FIELDS:
                raise ArchiveError(f"archive member has a link-capable Unix extra field: {original!r}")
            extra_offset = field_end
        if original != info.filename:
            raise ArchiveError(f"archive member name was altered while decoding: {original!r}")
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(unix_mode)
        if stat.S_ISLNK(unix_mode) or file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise ArchiveError(f"archive contains a special member: {original!r}")
        is_dir = info.is_dir()
        dos_attributes = info.external_attr & 0xFFFF
        if dos_attributes & (_WINDOWS_DEVICE_ATTRIBUTE | _WINDOWS_REPARSE_POINT_ATTRIBUTE):
            raise ArchiveError(f"archive contains a special Windows member: {original!r}")
        if dos_attributes & _WINDOWS_DIRECTORY_ATTRIBUTE and not is_dir:
            raise ArchiveError(f"archive member type conflicts with its DOS attributes: {original!r}")
        if (is_dir and file_type not in {0, stat.S_IFDIR}) or (
            not is_dir and file_type == stat.S_IFDIR
        ):
            raise ArchiveError(f"archive member type conflicts with its name: {original!r}")
        canonical, parts, semantic = _validated_name(
            original, is_dir=is_dir, limits=limits,
        )
        if canonical in exact_names:
            raise ArchiveError(f"archive contains an exact path collision: {canonical!r}")
        exact_names.add(canonical)
        previous = semantic_names.get(semantic)
        if previous is not None:
            raise ArchiveError(
                f"archive contains a casefold/NFC path collision: {previous!r}, {canonical!r}"
            )
        semantic_names[semantic] = canonical
        for index in range(1, len(parts) + 1):
            prefix_parts = parts[:index]
            spelling = "/".join(prefix_parts)
            key = "/".join(
                unicodedata.normalize("NFC", part).casefold()
                for part in prefix_parts
            )
            prior_spelling = node_spellings.get(key)
            if prior_spelling is not None and prior_spelling != spelling:
                raise ArchiveError(
                    f"archive contains a casefold/NFC prefix collision: {prior_spelling!r}, {spelling!r}"
                )
            node_spellings[key] = spelling
            wanted_type = "directory" if index < len(parts) or is_dir else "file"
            prior_type = node_types.get(key)
            if prior_type is not None and prior_type != wanted_type:
                raise ArchiveError(f"archive contains a file/directory prefix conflict: {canonical!r}")
            node_types[key] = wanted_type
        if info.flag_bits & 0x2041:
            raise ArchiveError(f"archive contains an encrypted member: {canonical!r}")
        if info.flag_bits & ~_ALLOWED_GENERAL_PURPOSE_FLAGS:
            raise ArchiveError(f"archive member uses unsupported ZIP flags: {canonical!r}")
        if info.compress_type not in limits.allowed_compression_methods:
            raise ArchiveError(f"archive uses unsupported compression: {canonical!r}")
        if getattr(info, "volume", 0) != 0:
            raise ArchiveError(f"multi-volume archive member is unsupported: {canonical!r}")
        if info.file_size < 0 or info.file_size > limits.max_member_size:
            raise ArchiveError(f"archive member exceeds the size limit: {canonical!r}")
        if info.compress_size < 0:
            raise ArchiveError(f"archive member has an invalid compressed size: {canonical!r}")
        if is_dir and info.file_size != 0:
            raise ArchiveError(f"archive directory has a payload: {canonical!r}")
        declared_total += info.file_size
        if declared_total > limits.max_total_size:
            raise ArchiveError("archive exceeds the total uncompressed size limit")
        if info.file_size and (
            info.compress_size <= 0
            or info.file_size / info.compress_size > limits.max_compression_ratio
        ):
            raise ArchiveError(f"archive member exceeds the compression-ratio limit: {canonical!r}")
        validated.append((info, canonical, is_dir))
    return validated


def _stream_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    canonical: str,
    limits: ArchiveLimits,
    output: BinaryIO | None = None,
) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    crc32 = 0
    size = 0
    try:
        with archive.open(info, "r") as member_source:
            for block in iter(lambda: member_source.read(_CHUNK_SIZE), b""):
                size += len(block)
                if size > info.file_size or size > limits.max_member_size:
                    raise ArchiveError(f"archive member expanded beyond its declared size: {canonical!r}")
                digest.update(block)
                crc32 = binascii.crc32(block, crc32)
                if output is not None:
                    output.write(block)
    except ArchiveError:
        raise
    except (OSError, RuntimeError, EOFError, zipfile.BadZipFile) as error:
        raise ArchiveError(f"archive member could not be read safely: {canonical!r}") from error
    crc32 &= 0xFFFFFFFF
    if size != info.file_size or crc32 != info.CRC:
        raise ArchiveError(f"archive member integrity mismatch: {canonical!r}")
    return digest.hexdigest(), size, crc32


def scan_archive(
    source: Path | bytes,
    required_members: Iterable[str] = (),
    executable_members: Iterable[str] = (),
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
) -> ArchivePlan:
    """Fully inspect an archive without exposing or mutating a payload destination."""
    if not isinstance(limits, ArchiveLimits):
        raise TypeError("limits must be ArchiveLimits")
    source_value = _source_value(source)
    required = _requested_names(required_members, "required member", limits)
    executables = frozenset(_requested_names(executable_members, "executable member", limits))
    try:
        with _source_snapshot(source_value, limits) as (
            stream,
            identity,
            source_sha256,
            source_size,
        ):
            _validate_zip_envelope(
                stream,
                source_size,
                max_members=limits.max_members,
                max_metadata_size=limits.max_metadata_size,
            )
            with zipfile.ZipFile(stream, "r") as archive:
                infos = archive.infolist()
                _validate_zip_envelope(
                    stream,
                    source_size,
                    infos,
                    max_members=limits.max_members,
                    max_metadata_size=limits.max_metadata_size,
                )
                validated = _validate_member_layout(infos, limits)
                members: list[ArchiveMember] = []
                actual_total = 0
                for info, canonical, is_dir in validated:
                    digest, size, crc32 = _stream_member(archive, info, canonical, limits)
                    actual_total += size
                    if actual_total > limits.max_total_size:
                        raise ArchiveError("archive exceeds the total uncompressed size limit")
                    members.append(ArchiveMember(
                        name=canonical,
                        is_dir=is_dir,
                        size=size,
                        compressed_size=info.compress_size,
                        crc32=crc32,
                        sha256=digest,
                        compression_method=info.compress_type,
                        mode=0o755 if is_dir or canonical in executables else 0o644,
                    ))
    except ArchiveError:
        raise
    except (OSError, RuntimeError, EOFError, UnicodeError, zipfile.BadZipFile) as error:
        raise ArchiveError("source is not a valid supported ZIP archive") from error
    by_name = {member.name: member for member in members}
    absent = [name for name in required if name not in by_name]
    if absent:
        raise ArchiveError(f"archive lacks required members: {', '.join(absent)}")
    invalid_executables = [
        name for name in executables if name not in by_name or by_name[name].is_dir
    ]
    if invalid_executables:
        raise ArchiveError(
            f"archive lacks executable file members: {', '.join(sorted(invalid_executables))}"
        )
    return ArchivePlan(
        source=source_value,
        source_sha256=source_sha256,
        source_size=source_size,
        source_identity=identity,
        members=tuple(members),
        required_members=required,
        executable_members=executables,
        limits=limits,
    )


@contextlib.contextmanager
def _validated_plan_archive(
    plan: ArchivePlan,
) -> Iterator[tuple[BinaryIO, zipfile.ZipFile, dict[str, zipfile.ZipInfo]]]:
    if not isinstance(plan, ArchivePlan):
        raise TypeError("plan must be ArchivePlan")
    if not isinstance(plan.limits, ArchiveLimits):
        raise ArchiveError("archive plan has invalid limits")
    names = {member.name for member in plan.members}
    if not set(plan.required_members).issubset(names):
        raise ArchiveError("archive plan no longer contains all required members")
    if not set(plan.executable_members).issubset(names):
        raise ArchiveError("archive plan no longer contains all executable members")
    for member in plan.members:
        expected_mode = 0o755 if member.is_dir or member.name in plan.executable_members else 0o644
        if member.mode != expected_mode:
            raise ArchiveError(f"archive plan has a non-canonical mode: {member.name!r}")
    try:
        with _source_snapshot(plan.source, plan.limits) as (
            stream,
            identity,
            source_sha256,
            source_size,
        ):
            if identity != plan.source_identity:
                raise ArchiveError("archive source identity no longer matches its plan")
            if source_size != plan.source_size or source_sha256 != plan.source_sha256:
                raise ArchiveError("archive source size no longer matches its plan")
            _validate_zip_envelope(
                stream,
                plan.source_size,
                max_members=plan.limits.max_members,
                max_metadata_size=plan.limits.max_metadata_size,
            )
            with zipfile.ZipFile(stream, "r") as archive:
                infos = archive.infolist()
                _validate_zip_envelope(
                    stream,
                    plan.source_size,
                    infos,
                    max_members=plan.limits.max_members,
                    max_metadata_size=plan.limits.max_metadata_size,
                )
                validated = _validate_member_layout(infos, plan.limits)
                info_by_name = {canonical: info for info, canonical, _ in validated}
                metadata = tuple(
                    (
                        canonical,
                        is_dir,
                        info.file_size,
                        info.compress_size,
                        info.CRC,
                        info.compress_type,
                    )
                    for info, canonical, is_dir in validated
                )
                planned_metadata = tuple(
                    (
                        member.name,
                        member.is_dir,
                        member.size,
                        member.compressed_size,
                        member.crc32,
                        member.compression_method,
                    )
                    for member in plan.members
                )
                if metadata != planned_metadata:
                    raise ArchiveError("archive metadata no longer matches its plan")
                yield stream, archive, info_by_name
    except ArchiveError:
        raise
    except (OSError, RuntimeError, EOFError, UnicodeError, zipfile.BadZipFile) as error:
        raise ArchiveError("archive source could not be revalidated") from error


def read_archive_members(
    plan: ArchivePlan, names: Iterable[str] | None = None,
) -> dict[str, bytes]:
    """Read selected members after revalidating the source and each payload."""
    if not isinstance(plan, ArchivePlan):
        raise TypeError("plan must be ArchivePlan")
    requested = (
        tuple(member.name for member in plan.members if not member.is_dir)
        if names is None
        else _requested_names(names, "requested member", plan.limits)
    )
    planned = {member.name: member for member in plan.members}
    for name in requested:
        if name not in planned or planned[name].is_dir:
            raise ArchiveError(f"archive file member is absent: {name!r}")
    result: dict[str, bytes] = {}
    with _validated_plan_archive(plan) as (_, archive, info_by_name):
        for name in requested:
            output = io.BytesIO()
            digest, size, crc32 = _stream_member(
                archive, info_by_name[name], name, plan.limits, output,
            )
            member = planned[name]
            if (digest, size, crc32) != (member.sha256, member.size, member.crc32):
                raise ArchiveError(f"archive member no longer matches its plan: {name!r}")
            result[name] = output.getvalue()
    return result


def read_archive_member(plan: ArchivePlan, name: str) -> bytes:
    """Read one file member after revalidating its plan."""
    canonical = _requested_name(name, "requested member", plan.limits)
    return read_archive_members(plan, (canonical,))[canonical]


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        if os.name == "nt":
            return
        raise
    try:
        os.fsync(descriptor)
    except OSError:
        if os.name != "nt":
            raise
    finally:
        os.close(descriptor)


def _node_identity_from_stat(metadata: os.stat_result) -> _NodeIdentity:
    return int(metadata.st_dev), int(metadata.st_ino), stat.S_IFMT(metadata.st_mode)


def _node_identity(path: Path) -> _NodeIdentity | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    return _node_identity_from_stat(metadata)


@dataclass(frozen=True, slots=True)
class _DirectoryAnchor:
    path: Path
    identity: _NodeIdentity
    descriptor: int | None


def _supports_anchored_directories() -> bool:
    required = (os.mkdir, os.open, os.stat, os.unlink, os.rmdir)
    return os.name != "nt" and all(function in os.supports_dir_fd for function in required)


@contextlib.contextmanager
def _directory_anchor(
    path: Path,
    expected_identity: _NodeIdentity | None = None,
) -> Iterator[_DirectoryAnchor]:
    before = _node_identity(path)
    if before is None or before[2] != stat.S_IFDIR:
        raise ArchiveError(f"extraction parent is not a stable directory: {path}")
    if expected_identity is not None and before != expected_identity:
        raise ArchiveError(f"extraction parent changed before it could be anchored: {path}")
    descriptor: int | None = None
    if _supports_anchored_directories():
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
            opened = _node_identity_from_stat(os.fstat(descriptor))
            if opened != before or _node_identity(path) != before:
                raise ArchiveError(f"extraction parent changed while opening: {path}")
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            raise
    anchor = _DirectoryAnchor(path=path, identity=before, descriptor=descriptor)
    try:
        yield anchor
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _assert_parent_path_stable(anchor: _DirectoryAnchor) -> None:
    if _node_identity(anchor.path) != anchor.identity:
        raise ArchiveError(f"extraction parent changed during extraction: {anchor.path}")


def _fsync_anchor(anchor: _DirectoryAnchor) -> None:
    if anchor.descriptor is not None:
        os.fsync(anchor.descriptor)
    else:
        _fsync_directory(anchor.path)


def _entry_identity(anchor: _DirectoryAnchor, name: str) -> _NodeIdentity | None:
    try:
        if anchor.descriptor is not None:
            metadata = os.stat(name, dir_fd=anchor.descriptor, follow_symlinks=False)
        else:
            metadata = (anchor.path / name).lstat()
    except FileNotFoundError:
        return None
    return _node_identity_from_stat(metadata)


def _open_anchored_directory(parent_descriptor: int, name: str) -> int:
    before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    if not stat.S_ISDIR(before.st_mode):
        raise ArchiveError(f"extraction path is not a directory: {name!r}")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    if _node_identity_from_stat(os.fstat(descriptor)) != _node_identity_from_stat(before):
        os.close(descriptor)
        raise ArchiveError(f"extraction directory changed while opening: {name!r}")
    return descriptor


def _create_staging(anchor: _DirectoryAnchor, target_name: str) -> tuple[str, Path, int | None, _NodeIdentity]:
    prefix = f".{target_name}."
    if anchor.descriptor is not None:
        for _ in range(128):
            name = f"{prefix}{secrets.token_hex(8)}.tmp"
            try:
                os.mkdir(name, 0o700, dir_fd=anchor.descriptor)
            except FileExistsError:
                continue
            try:
                descriptor = _open_anchored_directory(anchor.descriptor, name)
            except BaseException:
                try:
                    os.rmdir(name, dir_fd=anchor.descriptor)
                except OSError:
                    pass
                raise
            identity = _node_identity_from_stat(os.fstat(descriptor))
            return name, anchor.path / name, descriptor, identity
        raise ArchiveError("could not allocate a unique private extraction staging")
    _assert_parent_path_stable(anchor)
    boundary = _private_filesystem_boundary()
    if boundary is None:
        # Standalone bootstrap compatibility: its destination parent is the
        # already-private bootstrap work directory.
        path = Path(tempfile.mkdtemp(prefix=prefix, suffix=".tmp", dir=anchor.path))
        if os.name != "nt":
            path.chmod(0o700)
    else:
        path = boundary.private_mkdtemp(
            directory=anchor.path,
            prefix=prefix,
            suffix=".tmp",
        )
    identity = _node_identity(path)
    if identity is None or identity[2] != stat.S_IFDIR:
        raise ArchiveError("private extraction staging could not be verified")
    _assert_parent_path_stable(anchor)
    return path.name, path, None, identity


@contextlib.contextmanager
def _relative_directory(root_descriptor: int, parts: Sequence[str], *, create: bool) -> Iterator[int]:
    current = os.dup(root_descriptor)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=current)
                except FileExistsError:
                    pass
            child = _open_anchored_directory(current, part)
            os.close(current)
            current = child
        yield current
    finally:
        os.close(current)


def _extract_members_anchored(
    root_descriptor: int,
    plan: ArchivePlan,
    archive: zipfile.ZipFile,
    info_by_name: dict[str, zipfile.ZipInfo],
) -> None:
    directories: set[tuple[str, ...]] = set()
    for member in plan.members:
        parts = PurePosixPath(member.name).parts
        directory_parts = parts if member.is_dir else parts[:-1]
        directories.update(tuple(directory_parts[:index]) for index in range(1, len(directory_parts) + 1))
        with _relative_directory(root_descriptor, directory_parts, create=True) as parent_descriptor:
            if member.is_dir:
                continue
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(parts[-1], flags, 0o600, dir_fd=parent_descriptor)
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode):
                    raise ArchiveError(f"extraction output is not a regular file: {member.name!r}")
                with os.fdopen(descriptor, "wb", closefd=True) as output:
                    descriptor = -1
                    digest, size, crc32 = _stream_member(
                        archive, info_by_name[member.name], member.name, plan.limits, output,
                    )
                    output.flush()
                    os.fsync(output.fileno())
                    os.fchmod(output.fileno(), member.mode)
                if (digest, size, crc32) != (member.sha256, member.size, member.crc32):
                    raise ArchiveError(
                        f"archive member no longer matches its plan: {member.name!r}"
                    )
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
    for parts in sorted(directories, key=len, reverse=True):
        with _relative_directory(root_descriptor, parts, create=False) as descriptor:
            os.fchmod(descriptor, 0o755)
            os.fsync(descriptor)
    os.fchmod(root_descriptor, 0o755)
    os.fsync(root_descriptor)


def _extract_members_by_path(
    staging: Path,
    plan: ArchivePlan,
    archive: zipfile.ZipFile,
    info_by_name: dict[str, zipfile.ZipInfo],
) -> None:
    for member in plan.members:
        output_path = staging.joinpath(*PurePosixPath(member.name).parts)
        if member.is_dir:
            output_path.mkdir(parents=True, exist_ok=True)
            continue
        output_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(output_path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                descriptor = -1
                digest, size, crc32 = _stream_member(
                    archive, info_by_name[member.name], member.name, plan.limits, output,
                )
                output.flush()
                os.fsync(output.fileno())
            if (digest, size, crc32) != (member.sha256, member.size, member.crc32):
                raise ArchiveError(
                    f"archive member no longer matches its plan: {member.name!r}"
                )
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if os.name != "nt":
            output_path.chmod(member.mode)
    directories = [path for path in staging.rglob("*") if path.is_dir()]
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        if os.name != "nt":
            directory.chmod(0o755)
        _fsync_directory(directory)
    if os.name != "nt":
        staging.chmod(0o755)
    _fsync_directory(staging)


def _clear_directory_descriptor(descriptor: int) -> None:
    for name in os.listdir(descriptor):
        metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            child = _open_anchored_directory(descriptor, name)
            try:
                _clear_directory_descriptor(child)
            finally:
                os.close(child)
            if _node_identity_from_stat(
                os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            ) != _node_identity_from_stat(metadata):
                raise ArchiveError(f"staging child changed during cleanup: {name!r}")
            os.rmdir(name, dir_fd=descriptor)
        else:
            os.unlink(name, dir_fd=descriptor)
    os.fsync(descriptor)


def _find_anchored_entry(
    anchor: _DirectoryAnchor,
    identity: _NodeIdentity,
    preferred: Sequence[str],
    excluded: frozenset[str] = frozenset(),
) -> str | None:
    if anchor.descriptor is None:
        for name in preferred:
            if name not in excluded and _entry_identity(anchor, name) == identity:
                return name
        return None
    checked: set[str] = set()
    for name in (*preferred, *os.listdir(anchor.descriptor)):
        if name in excluded or name in checked:
            continue
        checked.add(name)
        if _entry_identity(anchor, name) == identity:
            return name
    return None


def _cleanup_staging(
    anchor: _DirectoryAnchor,
    staging: Path,
    staging_name: str,
    staging_descriptor: int | None,
    identity: _NodeIdentity,
    published_name: str | None,
) -> None:
    preferred = (staging_name,)
    excluded = frozenset((published_name,)) if published_name is not None else frozenset()
    if staging_descriptor is not None and anchor.descriptor is not None:
        if _node_identity_from_stat(os.fstat(staging_descriptor)) != identity:
            raise ArchiveError("private extraction staging identity changed before cleanup")
        entry = _find_anchored_entry(anchor, identity, preferred, excluded)
        if entry is None:
            raise ArchiveError("private extraction staging moved outside its anchored parent; preserved")
        if _entry_identity(anchor, entry) != identity:
            raise ArchiveError("private extraction staging name changed during cleanup; preserved")
        # Resolve and confirm a non-published name before traversing the open
        # descriptor.  After a rename that descriptor may already be the
        # public destination and can contain concurrently-created personal data.
        _clear_directory_descriptor(staging_descriptor)
        os.rmdir(entry, dir_fd=anchor.descriptor)
        os.fsync(anchor.descriptor)
        return
    _assert_parent_path_stable(anchor)
    entry = _find_anchored_entry(anchor, identity, preferred, excluded)
    if entry is None:
        raise ArchiveError("private extraction staging identity is inconclusive; preserved")
    candidate = anchor.path / entry
    if _node_identity(candidate) != identity:
        raise ArchiveError("private extraction staging name changed during cleanup; preserved")
    _clear_confirmed_path(candidate, identity)


def _clear_confirmed_path(path: Path, identity: _NodeIdentity) -> None:
    """Best-effort fallback which never recursively enters an unconfirmed name."""
    if _node_identity(path) != identity or identity[2] != stat.S_IFDIR:
        raise ArchiveError("private extraction staging identity is inconclusive; preserved")
    try:
        entries = tuple(path.iterdir())
    except OSError as error:
        raise ArchiveError("private extraction staging could not be inspected; preserved") from error
    for entry in entries:
        child_identity = _node_identity(entry)
        if child_identity is None:
            continue
        if child_identity[2] == stat.S_IFDIR:
            _clear_confirmed_path(entry, child_identity)
        else:
            if _node_identity(entry) != child_identity:
                raise ArchiveError(f"staging child changed during cleanup: {entry.name!r}")
            try:
                entry.unlink()
            except OSError as error:
                raise ArchiveError("private extraction staging could not be safely removed; preserved") from error
    if _node_identity(path) != identity:
        raise ArchiveError("private extraction staging changed during cleanup; preserved")
    try:
        path.rmdir()
    except OSError as error:
        raise ArchiveError("private extraction staging could not be safely removed; preserved") from error


def _atomic_promote(
    source: Path,
    destination: Path,
    *,
    parent_descriptor: int | None = None,
) -> None:
    """Rename a directory atomically while refusing an existing destination."""
    if os.name == "nt":
        os.rename(source, destination)
        return
    if parent_descriptor is not None:
        if source.parent != destination.parent:
            raise OSError(errno.EXDEV, "anchored promotion requires one parent directory")
        encoded_source = os.fsencode(source.name)
        encoded_destination = os.fsencode(destination.name)
        source_descriptor = destination_descriptor = parent_descriptor
    else:
        encoded_source = os.fsencode(source)
        encoded_destination = os.fsencode(destination)
        source_descriptor = destination_descriptor = -100
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        rename = getattr(library, "renameat2", None)
        if rename is None:
            raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
        rename.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename.restype = ctypes.c_int
        result = rename(
            source_descriptor,
            encoded_source,
            destination_descriptor,
            encoded_destination,
            1,
        )
    elif sys.platform == "darwin":
        if parent_descriptor is not None:
            rename = getattr(library, "renameatx_np", None)
            if rename is None:
                raise OSError(errno.ENOTSUP, "anchored atomic exclusive rename is unavailable")
            rename.argtypes = (
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            )
            rename.restype = ctypes.c_int
            result = rename(
                source_descriptor,
                encoded_source,
                destination_descriptor,
                encoded_destination,
                0x00000004,
            )
        else:
            rename = getattr(library, "renamex_np", None)
            if rename is None:
                raise OSError(errno.ENOTSUP, "atomic exclusive rename is unavailable")
            rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
            rename.restype = ctypes.c_int
            result = rename(encoded_source, encoded_destination, 0x00000004)
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename is unsupported")
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), destination)


def _destination_path(destination: Path) -> tuple[Path, _NodeIdentity]:
    if not isinstance(destination, Path):
        raise TypeError("destination must be pathlib.Path")
    result = destination.absolute()
    if result.name in {"", ".", ".."}:
        raise ArchiveError(f"invalid extraction destination: {destination}")
    try:
        result.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise ArchiveError(f"extraction destination is unavailable: {result}") from error
    else:
        raise ArchiveError(f"extraction destination already exists: {result}")
    try:
        parent = result.parent.lstat()
    except OSError as error:
        raise ArchiveError(f"extraction parent is unavailable: {result.parent}") from error
    if not stat.S_ISDIR(parent.st_mode):
        raise ArchiveError(f"extraction parent is not a directory: {result.parent}")
    return result, _node_identity_from_stat(parent)


def extract_archive(plan: ArchivePlan, destination: Path) -> Path:
    """Revalidate and atomically promote a private, fully fsynced extraction."""
    if not isinstance(plan, ArchivePlan):
        raise TypeError("plan must be ArchivePlan")
    target, parent_identity = _destination_path(destination)
    try:
        with _directory_anchor(target.parent, parent_identity) as anchor:
            staging: Path | None = None
            staging_name: str | None = None
            staging_descriptor: int | None = None
            staging_identity: _NodeIdentity | None = None
            promotion_attempted = False
            rollback_safe = True
            committed = False
            complete = False
            operation_error: BaseException | None = None
            cleanup_error: ArchiveError | None = None
            try:
                # Source identity and full SHA are checked through a bounded
                # private snapshot before the first extraction write.  Parent
                # and staging remain anchored by descriptors until promotion
                # or rollback completes.
                with _validated_plan_archive(plan) as (stream, archive, info_by_name):
                    if _entry_identity(anchor, target.name) is not None:
                        raise ArchiveError(
                            f"extraction destination appeared concurrently: {target}"
                        )
                    (
                        staging_name,
                        staging,
                        staging_descriptor,
                        staging_identity,
                    ) = _create_staging(anchor, target.name)
                    if staging_descriptor is not None:
                        _extract_members_anchored(
                            staging_descriptor, plan, archive, info_by_name,
                        )
                    else:
                        _extract_members_by_path(staging, plan, archive, info_by_name)
                # Leaving the context closes the ZIP and performs the final
                # complete source revalidation.  No published destination
                # exists until every source check has succeeded.
                if _entry_identity(anchor, target.name) is not None:
                    raise ArchiveError(
                        f"extraction destination appeared concurrently: {target}"
                    )
                if staging_descriptor is not None:
                    current_staging_identity = _node_identity_from_stat(
                        os.fstat(staging_descriptor)
                    )
                else:
                    current_staging_identity = _node_identity(staging)
                if current_staging_identity != staging_identity:
                    raise ArchiveError(
                        "private extraction staging disappeared before promotion"
                    )
                if _entry_identity(anchor, staging_name) != staging_identity:
                    raise ArchiveError(
                        "private extraction staging name changed before promotion"
                    )
                _assert_parent_path_stable(anchor)
                promotion_attempted = True
                rollback_safe = False
                try:
                    _atomic_promote(
                        staging,
                        target,
                        parent_descriptor=anchor.descriptor,
                    )
                except BaseException as promotion_error:
                    # A platform primitive may report an error after completing
                    # the rename.  Once the destination is the exact staging
                    # inode it is public and rollback must never traverse it.
                    if _entry_identity(anchor, target.name) == staging_identity:
                        committed = True
                    elif (
                        isinstance(promotion_error, OSError)
                        and promotion_error.errno in {errno.EEXIST, errno.ENOTEMPTY}
                        and _entry_identity(anchor, staging_name) == staging_identity
                    ):
                        # The documented no-replace collision result proves
                        # that the staging name never became public.
                        rollback_safe = True
                    raise
                if _entry_identity(anchor, target.name) != staging_identity:
                    raise ArchiveError(
                        "extraction destination identity changed during promotion"
                    )
                # This identity confirmation is the irreversible commit point.
                # Any later durability or path-stability error preserves the
                # complete destination, including concurrent personal files.
                committed = True
                _assert_parent_path_stable(anchor)
                _fsync_anchor(anchor)
                complete = True
            except BaseException as error:
                operation_error = error
            finally:
                try:
                    if (
                        staging is not None
                        and staging_name is not None
                        and staging_identity is not None
                        and not committed
                        and rollback_safe
                    ):
                        _cleanup_staging(
                            anchor,
                            staging,
                            staging_name,
                            staging_descriptor,
                            staging_identity,
                            target.name if promotion_attempted else None,
                        )
                except ArchiveError as error:
                    cleanup_error = error
                finally:
                    if staging_descriptor is not None:
                        os.close(staging_descriptor)
            if operation_error is not None:
                if cleanup_error is not None and isinstance(operation_error, Exception):
                    raise ArchiveError(
                        f"{operation_error}; rollback warning: {cleanup_error}"
                    ) from operation_error
                raise operation_error
            if cleanup_error is not None:
                raise cleanup_error
    except ArchiveError:
        raise
    except (OSError, RuntimeError, EOFError, zipfile.BadZipFile) as error:
        raise ArchiveError(f"archive extraction failed: {target}") from error
    return target


def _identity_document(payload: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArchiveError(f"installer identity is invalid: {label}") from error
    if not isinstance(value, dict):
        raise ArchiveError(f"installer identity must be an object: {label}")
    return value


def _validate_installer_bundle_layout(
    source: Path | bytes,
    version: str,
    *,
    includes_version_member: bool,
    includes_legal_members: bool,
) -> ArchivePlan:
    if not isinstance(version, str) or not _VERSION_PATTERN.fullmatch(version):
        raise ArchiveError(f"invalid installer bundle version: {version!r}")
    prefix = f"x86qw-installer-{version}"
    names = [
        f"{prefix}/x86qw.pyz",
        f"{prefix}/x86qw.sh",
        f"{prefix}/x86qw.cmd",
        f"{prefix}/installer.json",
        f"{prefix}/dist/installer/bin/manager.py",
        f"{prefix}/_x86qw/installer.json",
    ]
    if includes_version_member:
        names.insert(1, f"{prefix}/VERSION")
    if includes_legal_members:
        insert_at = 2 if includes_version_member else 1
        names[insert_at:insert_at] = (
            f"{prefix}/LICENSE",
            f"{prefix}/NOTICE",
        )
    executables = (
        f"{prefix}/x86qw.sh",
        f"{prefix}/dist/installer/bin/manager.py",
    )
    plan = scan_archive(source, required_members=names, executable_members=executables)
    if len(plan.members) != len(names) or set(plan.member_names) != set(names):
        count = (
            "nine" if includes_legal_members
            else "seven" if includes_version_member
            else "six"
        )
        raise ArchiveError(f"installer bundle does not contain the exact {count}-member layout")
    identity_names = (
        f"{prefix}/installer.json",
        f"{prefix}/_x86qw/installer.json",
        f"{prefix}/x86qw.pyz",
    )
    requested = (
        (f"{prefix}/VERSION", *identity_names)
        if includes_version_member
        else identity_names
    )
    payloads = read_archive_members(plan, requested)
    if (
        includes_version_member
        and payloads[f"{prefix}/VERSION"] != f"{version}\n".encode("ascii")
    ):
        raise ArchiveError("installer bundle VERSION does not match its requested version")
    expected_identity: dict[str, object] = {
        "format": 1,
        "project": "x86qw",
        "version": version,
    }
    for name in (f"{prefix}/installer.json", f"{prefix}/_x86qw/installer.json"):
        if _identity_document(payloads[name], name) != expected_identity:
            raise ArchiveError(f"installer identity does not match the bundle: {name}")
    nested_required = ("_x86qw/installer.json",) + (
        ("_x86qw/LICENSE", "_x86qw/NOTICE")
        if includes_legal_members else ()
    )
    nested = scan_archive(
        payloads[f"{prefix}/x86qw.pyz"],
        required_members=nested_required,
    )
    nested_identity = _identity_document(
        read_archive_member(nested, "_x86qw/installer.json"),
        "x86qw.pyz:_x86qw/installer.json",
    )
    if nested_identity != expected_identity:
        raise ArchiveError("x86qw.pyz identity does not match the installer bundle")
    if includes_legal_members:
        outer_legal = read_archive_members(
            plan, (f"{prefix}/LICENSE", f"{prefix}/NOTICE"),
        )
        nested_legal = read_archive_members(
            nested, ("_x86qw/LICENSE", "_x86qw/NOTICE"),
        )
        if (
            outer_legal[f"{prefix}/LICENSE"] != nested_legal["_x86qw/LICENSE"]
            or outer_legal[f"{prefix}/NOTICE"] != nested_legal["_x86qw/NOTICE"]
        ):
            raise ArchiveError("installer legal notices differ between bundle layers")
    return plan


def validate_installer_bundle(source: Path | bytes, version: str) -> ArchivePlan:
    """Validate the immutable versioned public installer bundle contract."""
    if not isinstance(version, str) or not _VERSION_PATTERN.fullmatch(version):
        raise ArchiveError(f"invalid installer bundle version: {version!r}")
    numeric_version = tuple(int(part) for part in version.split("."))
    return _validate_installer_bundle_layout(
        source,
        version,
        includes_version_member=True,
        includes_legal_members=numeric_version >= (1, 0, 0),
    )


def validate_installer_history_bundle(source: Path | bytes, version: str) -> ArchivePlan:
    """Validate the exact layout used by every immutable published bundle."""
    if not isinstance(version, str) or not _VERSION_PATTERN.fullmatch(version):
        raise ArchiveError(f"invalid installer bundle version: {version!r}")
    numeric_version = tuple(int(part) for part in version.split("."))
    return _validate_installer_bundle_layout(
        source,
        version,
        includes_version_member=numeric_version >= (0, 1, 20),
        includes_legal_members=numeric_version >= (1, 0, 0),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely validate and extract an x86QW ZIP archive")
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--required", action="append", default=[], metavar="PATH")
    parser.add_argument("--executable", action="append", default=[], metavar="PATH")
    parser.add_argument("--bundle-version", metavar="VERSION")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(argv)
    try:
        if options.bundle_version:
            plan = validate_installer_bundle(options.archive, options.bundle_version)
            required = set(options.required)
            executable = set(options.executable)
            missing_required = required.difference(plan.member_names)
            missing_executable = executable.difference(plan.executable_members)
            if missing_required:
                raise ArchiveError(
                    f"installer bundle lacks requested members: {', '.join(sorted(missing_required))}"
                )
            if missing_executable:
                raise ArchiveError(
                    "installer bundle executable declaration does not match its contract: "
                    + ", ".join(sorted(missing_executable))
                )
        else:
            plan = scan_archive(
                options.archive,
                required_members=options.required,
                executable_members=options.executable,
            )
        extract_archive(plan, options.destination)
    except (ArchiveError, OSError) as error:
        print(f"x86QW: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
