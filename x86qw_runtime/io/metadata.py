"""Stable bounded reads for small persisted runtime metadata."""

from __future__ import annotations

import os
import stat


_WINDOWS_REPARSE_POINT = 0x0400


class MetadataFileError(OSError):
    """A metadata file was unsafe, unstable, unavailable, or oversized."""


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        stat.S_IFMT(metadata.st_mode),
        int(metadata.st_size),
        int(getattr(metadata, "st_mtime_ns", metadata.st_mtime * 1_000_000_000)),
    )


def read_bounded_regular_file(
    path: os.PathLike[str] | str,
    *,
    maximum_size: int,
) -> bytes:
    """Read one non-link regular file while binding path and descriptor identity."""

    if type(maximum_size) is not int or maximum_size < 0:
        raise ValueError("maximum_size must be a non-negative integer")
    path = os.fspath(path)
    try:
        before = os.lstat(path)
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or int(getattr(before, "st_file_attributes", 0)) & _WINDOWS_REPARSE_POINT
            or before.st_size > maximum_size
        ):
            raise MetadataFileError(f"unsafe or oversized metadata file: {path}")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
    except MetadataFileError:
        raise
    except OSError as error:
        raise MetadataFileError(f"metadata file is unavailable: {path}") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _file_identity(opened) != _file_identity(before)
        ):
            raise MetadataFileError(f"metadata file changed while opening: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            payload = source.read(maximum_size + 1)
        after = os.fstat(descriptor)
        current = os.lstat(path)
        if (
            len(payload) > maximum_size
            or len(payload) != before.st_size
            or _file_identity(after) != _file_identity(opened)
            or _file_identity(current) != _file_identity(after)
        ):
            raise MetadataFileError(f"metadata file changed while reading: {path}")
        return payload
    except MetadataFileError:
        raise
    except OSError as error:
        raise MetadataFileError(f"metadata file could not be read safely: {path}") from error
    finally:
        os.close(descriptor)
