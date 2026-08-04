"""Durable, identity-bound replacement of small managed files."""

from __future__ import annotations

import json
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from . import private_fs
from .managed_files import (
    persistent_descriptor_identity,
    remove_persistent_identity_bound_path,
)


class AtomicWriteError(OSError):
    """A managed write failed before or after its atomic promotion."""

    def __init__(
        self,
        message: str,
        *,
        committed: bool = False,
        cleanup_error: OSError | None = None,
        committed_identity: tuple[int, int] | None = None,
    ) -> None:
        super().__init__(message)
        self.committed = committed
        self.cleanup_error = cleanup_error
        self.committed_identity = committed_identity


@dataclass(frozen=True)
class AtomicWriteResult:
    path: Path
    bytes_written: int
    replaced: bool


def atomic_copy_file(
    source: Path,
    path: Path,
    *,
    expected_sha256: str,
    mode: int = 0o644,
    chunk_size: int = 1024 * 1024,
) -> AtomicWriteResult:
    """Stream one verified regular file into an atomic managed replacement."""

    if (
        len(expected_sha256) != 64
        or expected_sha256 != expected_sha256.casefold()
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ValueError("expected_sha256 must be a lowercase SHA-256 digest")
    if mode not in {0o644, 0o755}:
        raise ValueError("managed copied file mode must be 0644 or 0755")
    if not isinstance(chunk_size, int) or chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer")
    source = Path(source)
    path = Path(path)
    try:
        source_metadata = source.lstat()
    except OSError as error:
        raise AtomicWriteError(
            f"managed source could not be inspected: {source}", committed=False,
        ) from error
    if stat.S_ISLNK(source_metadata.st_mode) or not stat.S_ISREG(
        source_metadata.st_mode
    ):
        raise AtomicWriteError(
            f"managed source is not a regular file: {source}", committed=False,
        )
    source_identity = (int(source_metadata.st_dev), int(source_metadata.st_ino))

    parent = path.parent
    replaced = os.path.lexists(path)
    if replaced:
        try:
            destination_metadata = path.lstat()
        except OSError as error:
            raise AtomicWriteError(
                f"managed destination could not be inspected: {path}",
                committed=False,
            ) from error
        if stat.S_ISLNK(destination_metadata.st_mode) or not stat.S_ISREG(
            destination_metadata.st_mode
        ):
            raise AtomicWriteError(
                f"managed destination is not a regular file: {path}", committed=False,
            )

    source_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    source_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        source_descriptor = os.open(source, source_flags)
    except OSError as error:
        raise AtomicWriteError(
            f"managed source could not be opened: {source}", committed=False,
        ) from error
    opened_source = os.fstat(source_descriptor)
    if (
        not stat.S_ISREG(opened_source.st_mode)
        or (int(opened_source.st_dev), int(opened_source.st_ino)) != source_identity
    ):
        os.close(source_descriptor)
        raise AtomicWriteError(
            f"managed source changed before copy: {source}", committed=False,
        )

    try:
        descriptor, temporary = private_fs.private_mkstemp(
            directory=parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
    except BaseException:
        os.close(source_descriptor)
        raise
    temporary_identity = persistent_descriptor_identity(
        descriptor, directory=False,
    )
    committed = False
    copied = 0
    result: AtomicWriteResult | None = None
    primary_error: BaseException | None = None
    try:
        try:
            digest = hashlib.sha256()
            with os.fdopen(source_descriptor, "rb", closefd=False) as input_file, os.fdopen(
                descriptor, "wb", closefd=False,
            ) as output_file:
                while True:
                    block = input_file.read(chunk_size)
                    if not block:
                        break
                    output_file.write(block)
                    digest.update(block)
                    copied += len(block)
                if digest.hexdigest() != expected_sha256:
                    raise AtomicWriteError(
                        f"managed source SHA-256 mismatch: {source}", committed=False,
                    )
                output_file.flush()
                os.fsync(descriptor)
            try:
                private_fs.replace_open_private_file(descriptor, temporary, path)
            except OSError as error:
                raise AtomicWriteError(
                    f"managed file promotion failed: {path}", committed=False,
                ) from error
            committed = True
            if os.name != "nt":
                os.fchmod(descriptor, mode)
                os.fsync(descriptor)
            try:
                _fsync_directory(parent)
            except OSError as error:
                raise AtomicWriteError(
                    f"managed file directory sync failed: {path}", committed=True,
                ) from error
            result = AtomicWriteResult(path=path, bytes_written=copied, replaced=replaced)
        except BaseException as error:
            primary_error = error
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        os.close(descriptor)
        try:
            private_fs.unlink_private_file(
                temporary,
                expected_identity=temporary_identity,
            )
        except FileNotFoundError:
            cleanup_error: OSError | None = None
        except OSError as error:
            cleanup_error = error
        else:
            cleanup_error = None

    if primary_error is not None:
        if isinstance(primary_error, AtomicWriteError):
            primary_error.cleanup_error = cleanup_error
            raise primary_error
        if isinstance(primary_error, OSError):
            raise AtomicWriteError(
                f"managed file copy failed: {path}",
                committed=committed,
                cleanup_error=cleanup_error,
            ) from primary_error
        raise primary_error
    if cleanup_error is not None:
        raise AtomicWriteError(
            f"managed file staging cleanup failed: {path}",
            committed=committed,
            cleanup_error=cleanup_error,
        ) from cleanup_error
    assert result is not None
    return result


def sync_directory(path: Path) -> None:
    """Flush one directory entry set on POSIX and no-op on Windows."""

    path = Path(path)
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(f"managed file parent is not a directory: {path}")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


# Compatibility name for callers and tests that predate the public boundary.
_fsync_directory = sync_directory


def atomic_write_bytes(
    path: Path,
    payload: bytes,
    *,
    mode: int = 0o644,
) -> AtomicWriteResult:
    """Replace one regular file after its complete bytes are durably staged."""

    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if mode not in {0o600, 0o644}:
        raise ValueError("managed file mode must be 0600 or 0644")
    path = Path(path)
    parent = path.parent
    replaced = os.path.lexists(path)
    if replaced:
        try:
            destination_metadata = path.lstat()
        except OSError as error:
            raise AtomicWriteError(
                f"managed destination could not be inspected: {path}", committed=False,
            ) from error
        if stat.S_ISLNK(destination_metadata.st_mode) or not stat.S_ISREG(
            destination_metadata.st_mode
        ):
            raise AtomicWriteError(
                f"managed destination is not a regular file: {path}", committed=False,
            )
    descriptor, temporary = private_fs.private_mkstemp(
        directory=parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_identity = persistent_descriptor_identity(
        descriptor, directory=False,
    )
    committed = False
    result: AtomicWriteResult | None = None
    primary_error: BaseException | None = None
    try:
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as output:
                output.write(payload)
                output.flush()
                try:
                    os.fsync(descriptor)
                except OSError as error:
                    raise AtomicWriteError(
                        f"managed file write failed: {path}", committed=False,
                    ) from error
            try:
                private_fs.replace_open_private_file(descriptor, temporary, path)
            except OSError as error:
                raise AtomicWriteError(
                    f"managed file promotion failed: {path}", committed=False,
                ) from error
            committed = True
            if os.name != "nt":
                os.fchmod(descriptor, mode)
                os.fsync(descriptor)
            try:
                _fsync_directory(parent)
            except OSError as error:
                raise AtomicWriteError(
                    f"managed file directory sync failed: {path}", committed=True,
                ) from error
            result = AtomicWriteResult(
                path=path,
                bytes_written=len(payload),
                replaced=replaced,
            )
        except BaseException as error:
            primary_error = error
    finally:
        os.close(descriptor)
        try:
            private_fs.unlink_private_file(
                temporary,
                expected_identity=temporary_identity,
            )
        except FileNotFoundError:
            cleanup_error: OSError | None = None
        except OSError as error:
            cleanup_error = error
        else:
            cleanup_error = None

    if primary_error is not None:
        if isinstance(primary_error, AtomicWriteError):
            primary_error.cleanup_error = cleanup_error
            raise primary_error
        if isinstance(primary_error, OSError):
            raise AtomicWriteError(
                f"managed file write failed: {path}",
                committed=committed,
                cleanup_error=cleanup_error,
            ) from primary_error
        raise primary_error
    if cleanup_error is not None:
        raise AtomicWriteError(
            f"managed file staging cleanup failed: {path}",
            committed=committed,
            cleanup_error=cleanup_error,
        ) from cleanup_error
    assert result is not None
    return result


def atomic_create_bytes(
    path: Path,
    payload: bytes,
    *,
    mode: int = 0o644,
) -> AtomicWriteResult:
    """Publish a new regular file atomically without replacing an existing entry."""

    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if mode not in {0o600, 0o644}:
        raise ValueError("managed file mode must be 0600 or 0644")
    path = Path(path)
    if os.path.lexists(path):
        raise AtomicWriteError(
            f"managed destination already exists: {path}", committed=False,
        )
    parent = path.parent
    descriptor, temporary = private_fs.private_mkstemp(
        directory=parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_identity = persistent_descriptor_identity(
        descriptor, directory=False,
    )
    committed = False
    result: AtomicWriteResult | None = None
    primary_error: BaseException | None = None
    try:
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as output:
                output.write(payload)
                output.flush()
                os.fsync(descriptor)
            if os.name != "nt":
                os.fchmod(descriptor, mode)
                os.fsync(descriptor)
            try:
                os.link(temporary, path, follow_symlinks=False)
            except OSError as error:
                raise AtomicWriteError(
                    f"managed file creation failed: {path}", committed=False,
                ) from error
            committed = True
            try:
                _fsync_directory(parent)
            except OSError as error:
                raise AtomicWriteError(
                    f"managed file directory sync failed: {path}", committed=True,
                ) from error
            result = AtomicWriteResult(
                path=path,
                bytes_written=len(payload),
                replaced=False,
            )
        except BaseException as error:
            primary_error = error
    finally:
        os.close(descriptor)
        try:
            if not remove_persistent_identity_bound_path(
                temporary, temporary_identity, directory=False,
            ) and os.path.lexists(temporary):
                raise OSError(
                    f"managed staging path changed identity: {temporary}"
                )
        except FileNotFoundError:
            cleanup_error: OSError | None = None
        except OSError as error:
            cleanup_error = error
        else:
            cleanup_error = None

    if primary_error is not None:
        if isinstance(primary_error, AtomicWriteError):
            primary_error.cleanup_error = cleanup_error
            if primary_error.committed:
                primary_error.committed_identity = temporary_identity
            raise primary_error
        if isinstance(primary_error, OSError):
            raise AtomicWriteError(
                f"managed file creation failed: {path}",
                committed=committed,
                cleanup_error=cleanup_error,
                committed_identity=temporary_identity if committed else None,
            ) from primary_error
        raise primary_error
    if cleanup_error is not None:
        raise AtomicWriteError(
            f"managed file staging cleanup failed: {path}",
            committed=committed,
            cleanup_error=cleanup_error,
            committed_identity=temporary_identity if committed else None,
        ) from cleanup_error
    assert result is not None
    return result


def atomic_write_json(
    path: Path,
    value: object,
    *,
    private: bool = False,
) -> AtomicWriteResult:
    """Serialize deterministic UTF-8 JSON and promote it through the same boundary."""

    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return atomic_write_bytes(path, payload, mode=0o600 if private else 0o644)
