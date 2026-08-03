"""Private staging and no-replace publication for generated artifacts."""

from __future__ import annotations

import contextlib
import hashlib
import os
import stat
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, TypeVar


_CHUNK_SIZE = 1024 * 1024
_NodeIdentity = tuple[int, int, int]
_Validation = TypeVar("_Validation")
_WINDOWS_REPARSE_POINT = 0x0400


def _node_identity(metadata: os.stat_result) -> _NodeIdentity:
    return int(metadata.st_dev), int(metadata.st_ino), stat.S_IFMT(metadata.st_mode)


def _path_identity(path: Path) -> _NodeIdentity | None:
    try:
        return _node_identity(path.lstat())
    except FileNotFoundError:
        return None


def _is_plain_directory(metadata: os.stat_result) -> bool:
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    return (
        stat.S_ISDIR(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not attributes & _WINDOWS_REPARSE_POINT
    )


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _prepare_output_parent(root: Path, target: Path) -> tuple[Path, _NodeIdentity]:
    """Create target parents below one explicit trust root without following links."""
    root = _absolute(root)
    target = _absolute(target)
    try:
        relative_parent = target.parent.relative_to(root)
    except ValueError as error:
        raise ValueError(f"build target escapes its output root: {target}") from error
    try:
        root.mkdir(parents=True, exist_ok=True)
        root_metadata = root.lstat()
    except OSError as error:
        raise ValueError(f"build output root is unavailable: {root}") from error
    if not _is_plain_directory(root_metadata):
        raise ValueError(f"build output root must be a non-link directory: {root}")
    current = root
    for component in relative_parent.parts:
        current /= component
        try:
            current.mkdir()
        except FileExistsError:
            pass
        except OSError as error:
            raise ValueError(f"build output directory could not be created safely: {current}") from error
        try:
            metadata = current.lstat()
        except OSError as error:
            raise ValueError(f"build output directory is unavailable: {current}") from error
        if not _is_plain_directory(metadata):
            raise ValueError(f"build output path contains a link or reparse point: {current}")
    parent_metadata = target.parent.lstat()
    return target, _node_identity(parent_metadata)


def _full_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_size),
        int(getattr(metadata, "st_mtime_ns", metadata.st_mtime * 1_000_000_000)),
        int(getattr(metadata, "st_ctime_ns", metadata.st_ctime * 1_000_000_000)),
    )


def read_regular_file(path: Path, *, maximum_size: int | None = None) -> bytes:
    """Read one stable regular file without following a replacement symlink."""
    try:
        before = path.lstat()
    except OSError as error:
        raise ValueError(f"build input is unavailable: {path}") from error
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise ValueError(f"build input must be a regular non-symlink file: {path}")
    declared_size = int(before.st_size)
    if maximum_size is not None and declared_size > maximum_size:
        raise ValueError(f"build input exceeds the {maximum_size}-byte limit: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError(f"build input could not be opened safely: {path}") from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _full_identity(opened) != _full_identity(before):
            raise ValueError(f"build input changed while opening: {path}")
        limit = declared_size if maximum_size is None else min(declared_size, maximum_size)
        payload = bytearray()
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            descriptor = -1
            while len(payload) <= limit:
                block = source.read(min(_CHUNK_SIZE, limit - len(payload) + 1))
                if not block:
                    break
                payload.extend(block)
            after = os.fstat(source.fileno())
        if len(payload) != declared_size or len(payload) > limit:
            raise ValueError(f"build input changed size while reading: {path}")
        try:
            current = path.lstat()
        except OSError as error:
            raise ValueError(f"build input changed while reading: {path}") from error
        if _full_identity(after) != _full_identity(before) or _full_identity(current) != _full_identity(before):
            raise ValueError(f"build input changed while reading: {path}")
        return bytes(payload)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@dataclass
class StagedArtifact:
    """An exclusively created output kept open until the writer seals it."""

    path: Path
    target: Path
    parent_identity: _NodeIdentity
    stream: BinaryIO
    identity: _NodeIdentity
    sealed: bool = False

    def seal(self, mode: int = 0o644) -> None:
        if self.sealed or self.stream.closed:
            raise ValueError("staged artifact is already sealed")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        if os.name != "nt":
            os.fchmod(self.stream.fileno(), mode)
            os.fsync(self.stream.fileno())
        opened = os.fstat(self.stream.fileno())
        if _node_identity(opened) != self.identity or not stat.S_ISREG(opened.st_mode):
            raise ValueError("staged artifact identity changed before sealing")
        self.stream.close()
        if _path_identity(self.path) != self.identity:
            raise ValueError("staged artifact path changed before sealing")
        self.sealed = True


@contextlib.contextmanager
def staged_artifact(
    target: Path,
    *,
    root: Path,
    prefix: str = ".artifact-",
) -> Iterator[StagedArtifact]:
    """Yield a private file beside ``target`` under an explicit output root."""
    target, parent_identity = _prepare_output_parent(root, target)
    descriptor, name = tempfile.mkstemp(prefix=prefix, dir=target.parent)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        metadata = os.fstat(descriptor)
        staged = StagedArtifact(
            path=Path(name),
            target=target,
            parent_identity=parent_identity,
            stream=os.fdopen(descriptor, "w+b", closefd=True),
            identity=_node_identity(metadata),
        )
        descriptor = -1
        try:
            yield staged
        finally:
            if not staged.stream.closed:
                staged.stream.close()
            if (
                _path_identity(staged.path.parent) == staged.parent_identity
                and _path_identity(staged.path) == staged.identity
            ):
                staged.path.unlink()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_verified_file(
    staged: StagedArtifact,
    target: Path,
    *,
    validate: Callable[[Path], _Validation],
    fingerprint: Callable[[_Validation], tuple[int, str]],
    conflict_message: str,
) -> _Validation:
    """Publish a sealed file atomically without replacing an existing name."""
    if not staged.sealed or not staged.stream.closed:
        raise ValueError("staged artifact must be sealed before publication")
    target = _absolute(target)
    if target != staged.target:
        raise ValueError("published target differs from the staged target")
    if _path_identity(target.parent) != staged.parent_identity:
        raise ValueError(f"build output parent changed before publication: {target.parent}")
    if _path_identity(staged.path) != staged.identity:
        raise ValueError("staged artifact path changed before validation")
    candidate = validate(staged.path)
    if _path_identity(staged.path) != staged.identity:
        raise ValueError("staged artifact path changed during validation")
    expected = fingerprint(candidate)

    def existing_target() -> _Validation:
        before = _path_identity(target)
        if before is None:
            raise ValueError(f"published artifact disappeared before validation: {target}")
        accepted = validate(target)
        if _path_identity(target) != before:
            raise ValueError(f"published artifact changed during validation: {target}")
        if fingerprint(accepted) != expected:
            raise ValueError(conflict_message)
        return accepted

    if os.path.lexists(target):
        return existing_target()
    try:
        os.link(staged.path, target, follow_symlinks=False)
    except FileExistsError:
        return existing_target()
    if _path_identity(target) != staged.identity:
        raise ValueError(f"published artifact identity differs from staging: {target}")
    try:
        accepted = validate(target)
    except BaseException as error:
        raise ValueError(
            f"published artifact failed validation and was preserved for inspection: {target}"
        ) from error
    if _path_identity(target) != staged.identity or fingerprint(accepted) != expected:
        raise ValueError(f"published artifact changed before acceptance and was preserved: {target}")
    _fsync_directory(target.parent)
    return accepted


def exact_bytes_validator(expected: bytes) -> Callable[[Path], tuple[int, str]]:
    """Return a validator suitable for a deterministic generated metadata file."""
    expected_digest = hashlib.sha256(expected).hexdigest()

    def validate(path: Path) -> tuple[int, str]:
        payload = read_regular_file(path, maximum_size=len(expected))
        if payload != expected:
            raise ValueError(f"generated metadata already differs: {path}")
        return len(payload), expected_digest

    return validate
