"""Portable creation and validation of x86QW private filesystem objects."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path
from typing import Callable


class PrivateFilesystemError(OSError):
    """A private filesystem object could not be safely created or validated."""


class _NullPrivatePathLease:
    """Portable no-op counterpart to the Windows sharing guard."""

    def close(self) -> None:
        return

    def __enter__(self) -> "_NullPrivatePathLease":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _windows_acl():
    from x86qw_runtime.platform import windows_acl

    return windows_acl


def _regular_file(path: Path) -> os.stat_result:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PrivateFilesystemError(f"private path is not a regular file: {path}")
    return metadata


def _directory(path: Path) -> os.stat_result:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise PrivateFilesystemError(f"private path is not a directory: {path}")
    if os.name == "nt":
        _windows_acl().validate_plain_directory(path)
    return metadata


def validate_private_file(path: Path) -> None:
    if os.name == "nt":
        _regular_file(path)
        _windows_acl().validate_private_path(path, directory=False)
        return
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise PrivateFilesystemError(f"private path is not a regular file: {path}")
        if metadata.st_mode & 0o077:
            raise PrivateFilesystemError(f"private file has unsafe permissions: {path}")
    finally:
        os.close(descriptor)


def protect_private_file(path: Path) -> None:
    if os.name == "nt":
        _regular_file(path)
        _windows_acl().protect_private_path(path, directory=False)
        return
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise PrivateFilesystemError(f"private path is not a regular file: {path}")
        os.fchmod(descriptor, 0o600)
        if os.fstat(descriptor).st_mode & 0o077:
            raise PrivateFilesystemError(f"private file has unsafe permissions: {path}")
    finally:
        os.close(descriptor)


def validate_private_directory(path: Path) -> None:
    if os.name == "nt":
        _directory(path)
        _windows_acl().validate_private_path(path, directory=True)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise PrivateFilesystemError(f"private path is not a directory: {path}")
        if metadata.st_mode & 0o077:
            raise PrivateFilesystemError(f"private directory has unsafe permissions: {path}")
    finally:
        os.close(descriptor)


def protect_private_directory(path: Path) -> None:
    if os.name == "nt":
        _directory(path)
        _windows_acl().protect_private_path(path, directory=True)
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise PrivateFilesystemError(f"private path is not a directory: {path}")
        os.fchmod(descriptor, 0o700)
        if os.fstat(descriptor).st_mode & 0o077:
            raise PrivateFilesystemError(f"private directory has unsafe permissions: {path}")
    finally:
        os.close(descriptor)


def migrate_legacy_private_directory(path: Path) -> bool:
    """Harden one pre-DACL Windows directory, preserving fail-closed checks."""
    try:
        validate_private_directory(path)
        return False
    except OSError:
        if os.name != "nt":
            raise
    protect_private_directory(path)
    validate_private_directory(path)
    return True


def ensure_private_directory(path: Path) -> None:
    """Create or harden one directory without following links."""
    if lexists(path):
        protect_private_directory(path)
        return
    _directory(path.parent)
    try:
        if os.name == "nt":
            _windows_acl().create_private_directory(path)
        else:
            path.mkdir(mode=0o700)
            protect_private_directory(path)
    except FileExistsError:
        protect_private_directory(path)


def create_private_directory(path: Path) -> None:
    """Atomically create exactly ``path`` and reject an existing entry."""
    _directory(path.parent)
    if os.name == "nt":
        _windows_acl().create_private_directory(path)
        return
    path.mkdir(mode=0o700)
    protect_private_directory(path)


def ensure_private_directories(path: Path, *, stop: Path) -> tuple[Path, ...]:
    """Create/harden descendants of ``stop`` while leaving ``stop`` itself unchanged."""
    stop = stop.absolute()
    candidate = path.absolute()
    if ".." in stop.parts or ".." in candidate.parts:
        raise PrivateFilesystemError(f"private directory contains a parent traversal: {path}")
    _directory(stop)
    try:
        relative = candidate.relative_to(stop)
    except ValueError as error:
        raise PrivateFilesystemError(f"private directory escapes its root: {path}") from error
    current = stop
    created: list[Path] = []
    for part in relative.parts:
        current = current / part
        existed = lexists(current)
        ensure_private_directory(current)
        if not existed:
            created.append(current)
    return tuple(created)


def private_mkstemp(
    *, directory: Path, prefix: str = "", suffix: str = "", attempts: int = 128,
) -> tuple[int, Path]:
    """Atomically create an empty private regular file in ``directory``."""
    _directory(directory)
    for _ in range(attempts):
        path = directory / f"{prefix}{secrets.token_hex(12)}{suffix}"
        try:
            if os.name == "nt":
                descriptor = _windows_acl().create_private_file(path)
            else:
                flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
                flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(path, flags, 0o600)
                try:
                    os.fchmod(descriptor, 0o600)
                except BaseException:
                    os.close(descriptor)
                    # The file was born owner-only.  Preserve it rather than
                    # deleting a pathname after its open identity is lost.
                    raise
            return descriptor, path
        except FileExistsError:
            continue
    raise PrivateFilesystemError(f"could not allocate a unique private file in {directory}")


def create_private_file(path: Path) -> int:
    """Atomically create exactly ``path`` as a private regular file."""
    _directory(path.parent)
    if os.name == "nt":
        return _windows_acl().create_private_file(path)
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        return descriptor
    except BaseException:
        os.close(descriptor)
        # Preserve the empty owner-only object on a protection failure.
        raise


def private_mkdtemp(
    *, directory: Path, prefix: str = "", suffix: str = "", attempts: int = 128,
) -> Path:
    """Atomically create a private directory in ``directory``."""
    _directory(directory)
    for _ in range(attempts):
        path = directory / f"{prefix}{secrets.token_hex(12)}{suffix}"
        try:
            if os.name == "nt":
                _windows_acl().create_private_directory(path)
            else:
                path.mkdir(mode=0o700)
                try:
                    protect_private_directory(path)
                except BaseException:
                    # Preserve the empty 0700 directory; pathname cleanup
                    # cannot prove that the entry was not replaced.
                    raise
            return path
        except FileExistsError:
            continue
    raise PrivateFilesystemError(f"could not allocate a unique private directory in {directory}")


def open_private_append(path: Path) -> int:
    """Open/create a managed append-only file without a broad-ACL creation window."""
    _directory(path.parent)
    if os.name == "nt":
        return _windows_acl().open_private_append(path)
    common = os.O_WRONLY | os.O_APPEND
    common |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, common | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        descriptor = os.open(path, common)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise PrivateFilesystemError(f"private path is not a regular file: {path}")
        os.fchmod(descriptor, 0o600)
        return descriptor
    except BaseException:
        os.close(descriptor)
        # Preserve an empty owner-only file rather than unlinking a pathname
        # after its identity-bearing descriptor has been closed.
        raise


def read_private_file(path: Path, *, maximum_size: int) -> bytes:
    """Read a bounded private file without a validation/read pathname race."""
    if type(maximum_size) is not int or maximum_size < 0:
        raise ValueError("maximum_size must be a non-negative integer")
    if os.name == "nt":
        return _windows_acl().read_validated_private_file(path, maximum_size=maximum_size)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_mode & 0o077
            or metadata.st_size > maximum_size
        ):
            raise PrivateFilesystemError(f"private file exceeds {maximum_size} bytes: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            payload = source.read(maximum_size + 1)
        if len(payload) > maximum_size:
            raise PrivateFilesystemError(f"private file exceeds {maximum_size} bytes: {path}")
        return payload
    finally:
        os.close(descriptor)


def read_private_file_with_legacy_windows_migration(
    path: Path,
    *,
    maximum_size: int,
    approve_legacy: Callable[[bytes], bool],
) -> tuple[bytes, bool]:
    """Read canonical metadata or migrate one explicitly approved Windows legacy file."""
    try:
        return read_private_file(path, maximum_size=maximum_size), False
    except OSError:
        if os.name != "nt":
            raise
    payload = _windows_acl().read_and_protect_legacy_file(
        path, maximum_size=maximum_size, approve=approve_legacy,
    )
    return payload, True


def read_private_user_file(path: Path, *, maximum_size: int) -> bytes:
    """Read a user-supplied secret after validating its private access policy."""
    if type(maximum_size) is not int or maximum_size < 0:
        raise ValueError("maximum_size must be a non-negative integer")
    if os.name == "nt":
        return _windows_acl().read_validated_private_file(
            path, maximum_size=maximum_size, exact=False,
        )
    return read_private_file(path, maximum_size=maximum_size)


def hold_private_path(path: Path, *, directory: bool):
    """Keep a private object stable against Windows parent DELETE_CHILD rights."""
    if os.name == "nt":
        return _windows_acl().hold_private_path(path, directory=directory)
    if directory:
        validate_private_directory(path)
    else:
        validate_private_file(path)
    return _NullPrivatePathLease()


def replace_open_private_file(descriptor: int, source: Path, destination: Path) -> None:
    """Promote a validated private file without releasing its open identity."""
    if os.name == "nt":
        _windows_acl().replace_open_private_file(descriptor, source, destination)
        return
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
        raise PrivateFilesystemError(f"private promotion descriptor is unsafe: {source}")
    source_metadata = source.lstat()
    if (
        stat.S_ISLNK(source_metadata.st_mode)
        or not stat.S_ISREG(source_metadata.st_mode)
        or (int(source_metadata.st_dev), int(source_metadata.st_ino))
        != (int(metadata.st_dev), int(metadata.st_ino))
    ):
        raise PrivateFilesystemError(f"private promotion source changed identity: {source}")
    _directory(destination.parent)
    os.replace(source, destination)


def unlink_private_file(
    path: Path, *, expected_identity: tuple[int, int] | None = None,
) -> None:
    """Remove one private regular file, optionally bound to its creation identity."""
    if os.name == "nt":
        _windows_acl().unlink_private_file(path, expected_identity=expected_identity)
        return
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PrivateFilesystemError(f"private cleanup path changed type: {path}")
    if expected_identity is not None and (
        int(metadata.st_dev), int(metadata.st_ino)
    ) != expected_identity:
        raise PrivateFilesystemError(f"private cleanup path changed identity: {path}")
    validate_private_file(path)
    path.unlink()
