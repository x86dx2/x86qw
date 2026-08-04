"""Durable, identity-bound replacement of small managed files."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from . import private_fs


class AtomicWriteError(OSError):
    """A managed write failed before or after its atomic promotion."""

    def __init__(
        self,
        message: str,
        *,
        committed: bool = False,
        cleanup_error: OSError | None = None,
    ) -> None:
        super().__init__(message)
        self.committed = committed
        self.cleanup_error = cleanup_error


@dataclass(frozen=True)
class AtomicWriteResult:
    path: Path
    bytes_written: int
    replaced: bool


def _fsync_directory(path: Path) -> None:
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
    metadata = os.fstat(descriptor)
    temporary_identity = (int(metadata.st_dev), int(metadata.st_ino))
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
