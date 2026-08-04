"""Bounded hashing, durable identity and race-safe managed-file cleanup."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import secrets
import stat
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from x86qw_runtime.errors import InstallerError
from x86qw_runtime.io import private_fs
from x86qw_runtime.io.archive import (
    DEFAULT_ARCHIVE_LIMITS,
    ArchiveError,
    extract_archive,
    scan_archive,
)
from x86qw_runtime.io.paths import lexists


_HASH_CHUNK_SIZE = 1024 * 1024
MAX_MANAGED_FILE_SIZE = DEFAULT_ARCHIVE_LIMITS.max_member_size


@dataclass(frozen=True)
class MaterializedFile:
    path: Path
    expected_hash: str
    origin: str
    created_by_session: bool
    existed: bool
    root: Path | None = None
    identity: tuple[int, int] | None = None
    expected_size: int | None = None


@dataclass(frozen=True)
class MaterializedDirectory:
    path: Path
    root: Path
    identity: tuple[int, int]


@dataclass(frozen=True)
class MaterializedArchive:
    files: tuple[MaterializedFile, ...]
    directories: tuple[MaterializedDirectory, ...]
    root: Path


def _bounded_hash_limit(expected_size: int | None) -> int:
    if expected_size is None:
        return MAX_MANAGED_FILE_SIZE
    if (
        type(expected_size) is not int
        or not 0 <= expected_size <= MAX_MANAGED_FILE_SIZE
    ):
        raise ValueError("tamanho esperado inválido para hashing gerenciado")
    return expected_size


def _assert_hashable_size(actual_size: int, expected_size: int | None) -> int:
    limit = _bounded_hash_limit(expected_size)
    if actual_size > limit:
        raise OSError(errno.EFBIG, "arquivo excede o limite de hashing gerenciado")
    if expected_size is not None and actual_size != expected_size:
        raise OSError(errno.EIO, "arquivo diverge do tamanho gerenciado")
    return limit


def file_sha256(path: Path, *, expected_size: int | None = None) -> str:
    """Hash one regular file without ever reading beyond its managed bound."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(errno.EINVAL, "hashing exige arquivo regular")
        limit = _assert_hashable_size(metadata.st_size, expected_size)
        total = 0
        while True:
            block = source.read(min(_HASH_CHUNK_SIZE, limit - total + 1))
            if not block:
                break
            total += len(block)
            if total > limit:
                raise OSError(errno.EFBIG, "arquivo cresceu além do limite de hashing")
            digest.update(block)
        if expected_size is not None and total != expected_size:
            raise OSError(errno.EIO, "arquivo diverge do tamanho gerenciado")
    return digest.hexdigest()


def file_matches_sha256(path: Path, expected_hash: str, expected_size: int) -> bool:
    """Return false, without an unbounded retry, for changed or unreadable files."""

    try:
        return file_sha256(path, expected_size=expected_size) == expected_hash
    except (OSError, ValueError):
        return False


def describe_non_sensitive_temporary(
    path: Path,
    root: Path,
    origin: str,
) -> MaterializedFile:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise InstallerError(f"Temporário não sensível ausente ou inseguro: {path}")
    expected_size = metadata.st_size
    try:
        _bounded_hash_limit(expected_size)
    except ValueError as error:
        raise InstallerError(
            f"Tamanho inválido do temporário não sensível {path}."
        ) from error
    expected_hash = file_sha256(path, expected_size=expected_size)
    try:
        identity = persistent_path_identity(path, directory=False)
    except OSError as error:
        raise InstallerError(
            f"Não foi possível registrar a identidade do temporário {path}."
        ) from error
    return MaterializedFile(
        path,
        expected_hash,
        origin,
        True,
        False,
        root,
        identity,
        expected_size,
    )


_SECURE_ARCHIVE_DIR_FD_SUPPORTED = (
    os.name == "posix"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and all(
        function in os.supports_dir_fd
        for function in (os.open, os.mkdir, os.link, os.unlink, os.stat, os.rmdir)
    )
    and os.link in os.supports_follow_symlinks
)


def _secure_archive_dir_fd_supported() -> bool:
    return _SECURE_ARCHIVE_DIR_FD_SUPPORTED


def _directory_open_flags() -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _file_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


_WINDOWS_FILE_API_UNSET = object()
_WINDOWS_FILE_API: object | None = _WINDOWS_FILE_API_UNSET
_POSIX_RENAME_API_UNSET = object()
_POSIX_RENAME_API: object | None = _POSIX_RENAME_API_UNSET


class _PosixRenameApi:
    """Descriptor-relative, no-replace rename on Linux and Darwin."""

    RENAME_NOREPLACE = 0x00000001
    RENAME_EXCL = 0x00000004

    def __init__(self) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "darwin":
            function = libc.renameatx_np
            flag = self.RENAME_EXCL
        elif sys.platform.startswith("linux"):
            function = libc.renameat2
            flag = self.RENAME_NOREPLACE
        else:
            raise OSError("rename exclusivo indisponível nesta plataforma POSIX")
        function.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        function.restype = ctypes.c_int
        self.function = function
        self.flag = flag

    def move_no_replace(
        self,
        source_directory: int,
        source_name: str,
        destination_directory: int,
        destination_name: str,
    ) -> None:
        if self.function(
            source_directory,
            os.fsencode(source_name),
            destination_directory,
            os.fsencode(destination_name),
            self.flag,
        ) == 0:
            return
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(error, os.strerror(error), destination_name)
        if error == errno.ENOENT:
            raise FileNotFoundError(error, os.strerror(error), source_name)
        raise OSError(error, os.strerror(error), source_name)


def _get_posix_rename_api() -> object | None:
    global _POSIX_RENAME_API
    if _POSIX_RENAME_API is not _POSIX_RENAME_API_UNSET:
        return _POSIX_RENAME_API
    if os.name != "posix":
        _POSIX_RENAME_API = None
        return None
    try:
        _POSIX_RENAME_API = _PosixRenameApi()
    except (AttributeError, OSError):
        _POSIX_RENAME_API = None
    return _POSIX_RENAME_API


class _WindowsFileApi:
    """Small handle-based Win32 surface used by managed materialization."""

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    DELETE = 0x00010000
    FILE_READ_ATTRIBUTES = 0x00000080
    FILE_SHARE_READ = 0x00000001
    CREATE_NEW = 1
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FILE_BEGIN = 0
    FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
    FILE_ID_INFO_CLASS = 18
    FILE_DISPOSITION_INFO_CLASS = 4
    MOVEFILE_WRITE_THROUGH = 0x00000008
    ERROR_FILE_NOT_FOUND = 2
    ERROR_PATH_NOT_FOUND = 3
    ERROR_FILE_EXISTS = 80
    ERROR_ALREADY_EXISTS = 183

    class _FileId128(ctypes.Structure):
        _fields_ = (("identifier", ctypes.c_ubyte * 16),)

    class _FileIdInfo(ctypes.Structure):
        pass

    class _FileAttributeTagInfo(ctypes.Structure):
        _fields_ = (
            ("file_attributes", ctypes.c_ulong),
            ("reparse_tag", ctypes.c_ulong),
        )

    class _FileDispositionInfo(ctypes.Structure):
        _fields_ = (("delete_file", ctypes.c_ubyte),)

    _FileIdInfo._fields_ = (
        ("volume_serial_number", ctypes.c_ulonglong),
        ("file_id", _FileId128),
    )

    def __init__(self) -> None:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.GetFileInformationByHandleEx.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        kernel32.SetFileInformationByHandle.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        )
        kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
        kernel32.MoveFileExW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
        )
        kernel32.MoveFileExW.restype = wintypes.BOOL
        kernel32.ReadFile.argtypes = (
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        )
        kernel32.ReadFile.restype = wintypes.BOOL
        kernel32.WriteFile.argtypes = (
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        )
        kernel32.WriteFile.restype = wintypes.BOOL
        kernel32.SetFilePointerEx.argtypes = (
            wintypes.HANDLE,
            ctypes.c_longlong,
            ctypes.POINTER(ctypes.c_longlong),
            wintypes.DWORD,
        )
        kernel32.SetFilePointerEx.restype = wintypes.BOOL
        kernel32.GetFileSizeEx.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_longlong),
        )
        kernel32.GetFileSizeEx.restype = wintypes.BOOL
        kernel32.FlushFileBuffers.argtypes = (wintypes.HANDLE,)
        kernel32.FlushFileBuffers.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32 = kernel32
        self.invalid_handle = ctypes.c_void_p(-1).value

    def _raise_last_error(self, path: Path | None = None) -> None:
        error = ctypes.get_last_error()
        filename = os.fspath(path) if path is not None else None
        message = ctypes.FormatError(error)
        if error in {self.ERROR_FILE_NOT_FOUND, self.ERROR_PATH_NOT_FOUND}:
            raise FileNotFoundError(error, message, filename)
        if error in {self.ERROR_FILE_EXISTS, self.ERROR_ALREADY_EXISTS}:
            raise FileExistsError(error, message, filename)
        raise ctypes.WinError(error)

    def open_handle(
        self,
        path: Path,
        *,
        access: int,
        creation: int,
        directory: bool,
    ) -> int:
        flags = self.FILE_FLAG_OPEN_REPARSE_POINT
        if directory:
            flags |= self.FILE_FLAG_BACKUP_SEMANTICS
        else:
            flags |= self.FILE_ATTRIBUTE_NORMAL
        handle = self.kernel32.CreateFileW(
            os.fspath(path),
            access,
            self.FILE_SHARE_READ,
            None,
            creation,
            flags,
            None,
        )
        if handle == self.invalid_handle:
            self._raise_last_error(path)
        return handle

    def close(self, handle: int) -> None:
        if not self.kernel32.CloseHandle(handle):
            self._raise_last_error()

    def attributes(self, handle: int) -> int:
        information = self._FileAttributeTagInfo()
        if not self.kernel32.GetFileInformationByHandleEx(
            handle,
            self.FILE_ATTRIBUTE_TAG_INFO_CLASS,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            self._raise_last_error()
        return int(information.file_attributes)

    def identity(self, handle: int) -> tuple[int, int]:
        information = self._FileIdInfo()
        if not self.kernel32.GetFileInformationByHandleEx(
            handle,
            self.FILE_ID_INFO_CLASS,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            self._raise_last_error()
        identifier = int.from_bytes(bytes(information.file_id.identifier), "little")
        return int(information.volume_serial_number), identifier

    def checked_identity(self, handle: int, *, directory: bool) -> tuple[int, int]:
        attributes = self.attributes(handle)
        if attributes & self.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError("ponto de nova análise recusado")
        is_directory = bool(attributes & self.FILE_ATTRIBUTE_DIRECTORY)
        if is_directory != directory:
            raise OSError("tipo de arquivo incompatível")
        return self.identity(handle)

    def write(self, handle: int, payload: bytes) -> None:
        from ctypes import wintypes

        offset = 0
        while offset < len(payload):
            block = payload[offset:]
            written = wintypes.DWORD()
            buffer = ctypes.create_string_buffer(block)
            if not self.kernel32.WriteFile(
                handle, buffer, len(block), ctypes.byref(written), None,
            ):
                self._raise_last_error()
            if written.value <= 0:
                raise OSError("gravação Win32 não avançou")
            offset += written.value

    def flush(self, handle: int) -> None:
        if not self.kernel32.FlushFileBuffers(handle):
            self._raise_last_error()

    def rewind(self, handle: int) -> None:
        position = ctypes.c_longlong()
        if not self.kernel32.SetFilePointerEx(
            handle, 0, ctypes.byref(position), self.FILE_BEGIN,
        ):
            self._raise_last_error()

    def size(self, handle: int) -> int:
        size = ctypes.c_longlong()
        if not self.kernel32.GetFileSizeEx(handle, ctypes.byref(size)):
            self._raise_last_error()
        if size.value < 0:
            raise OSError("tamanho Win32 inválido")
        return int(size.value)

    def hash(self, handle: int, *, expected_size: int) -> str:
        from ctypes import wintypes

        limit = _assert_hashable_size(self.size(handle), expected_size)
        self.rewind(handle)
        digest = hashlib.sha256()
        buffer = ctypes.create_string_buffer(_HASH_CHUNK_SIZE)
        total = 0
        while True:
            read = wintypes.DWORD()
            requested = min(_HASH_CHUNK_SIZE, limit - total + 1)
            if not self.kernel32.ReadFile(
                handle, buffer, requested, ctypes.byref(read), None,
            ):
                self._raise_last_error()
            if read.value == 0:
                break
            total += read.value
            if total > limit:
                raise OSError(errno.EFBIG, "arquivo cresceu além do limite de hashing")
            digest.update(buffer.raw[:read.value])
        if expected_size is not None and total != expected_size:
            raise OSError(errno.EIO, "arquivo diverge do tamanho gerenciado")
        if self.size(handle) != total:
            raise OSError(errno.EIO, "arquivo mudou durante o hashing gerenciado")
        return digest.hexdigest()

    def move_no_replace(self, source: Path, destination: Path) -> None:
        if not self.kernel32.MoveFileExW(
            os.fspath(source), os.fspath(destination), self.MOVEFILE_WRITE_THROUGH,
        ):
            self._raise_last_error(destination)

    def mark_delete(self, handle: int) -> None:
        information = self._FileDispositionInfo(1)
        if not self.kernel32.SetFileInformationByHandle(
            handle,
            self.FILE_DISPOSITION_INFO_CLASS,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            self._raise_last_error()


def _get_windows_file_api() -> object | None:
    global _WINDOWS_FILE_API
    if _WINDOWS_FILE_API is not _WINDOWS_FILE_API_UNSET:
        return _WINDOWS_FILE_API
    if os.name != "nt":
        return None
    try:
        _WINDOWS_FILE_API = _WindowsFileApi()
    except (AttributeError, OSError) as error:
        raise InstallerError(
            "O Windows não oferece as operações por handle exigidas para preparar o KTX."
        ) from error
    return _WINDOWS_FILE_API


def _close_windows_handle(api: object, handle: int) -> None:
    try:
        api.close(handle)
    except OSError:
        pass


def persistent_path_identity(path: Path, *, directory: bool) -> tuple[int, int]:
    api = _get_windows_file_api()
    if api is not None:
        handle = api.open_handle(
            path,
            access=api.FILE_READ_ATTRIBUTES,
            creation=api.OPEN_EXISTING,
            directory=directory,
        )
        try:
            return api.checked_identity(handle, directory=directory)
        finally:
            _close_windows_handle(api, handle)
    metadata = path.lstat()
    valid_type = (
        stat.S_ISDIR(metadata.st_mode)
        if directory
        else stat.S_ISREG(metadata.st_mode)
    )
    if stat.S_ISLNK(metadata.st_mode) or not valid_type:
        raise OSError(f"tipo de arquivo materializado inseguro: {path}")
    return _file_identity(metadata)


@contextmanager
def _windows_archive_parent(
    api: object,
    destination_root: Path,
    parent_parts: tuple[str, ...],
    *,
    create: bool,
    created_directories: list[MaterializedDirectory] | None = None,
    journal: Any | None = None,
) -> Iterator[int]:
    """Hold a non-reparse Win32 handle for every ancestor in the member path."""

    handles: list[tuple[Path, int, tuple[int, int]]] = []
    current = destination_root
    try:
        handle: int | None = None
        try:
            handle = api.open_handle(
                current,
                access=api.FILE_READ_ATTRIBUTES,
                creation=api.OPEN_EXISTING,
                directory=True,
            )
            identity = api.checked_identity(handle, directory=True)
        except OSError as error:
            if handle is not None:
                _close_windows_handle(api, handle)
            raise InstallerError(
                f"Diretório inseguro ao preparar o pacote: {current}"
            ) from error
        handles.append((current, handle, identity))
        for part in parent_parts:
            current /= part
            created = False
            try:
                handle = api.open_handle(
                    current,
                    access=api.FILE_READ_ATTRIBUTES,
                    creation=api.OPEN_EXISTING,
                    directory=True,
                )
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    current.mkdir()
                    created = True
                except FileExistsError:
                    pass
                try:
                    handle = api.open_handle(
                        current,
                        access=api.FILE_READ_ATTRIBUTES,
                        creation=api.OPEN_EXISTING,
                        directory=True,
                    )
                except OSError as error:
                    raise InstallerError(
                        f"Diretório inseguro ao preparar o pacote: {current}"
                    ) from error
            except OSError as error:
                raise InstallerError(
                    f"Diretório inseguro ao preparar o pacote: {current}"
                ) from error
            try:
                identity = api.checked_identity(handle, directory=True)
            except OSError as error:
                _close_windows_handle(api, handle)
                raise InstallerError(
                    f"Diretório inseguro ao preparar o pacote: {current}"
                ) from error
            handles.append((current, handle, identity))
            if created:
                entry = MaterializedDirectory(current, destination_root, identity)
                if created_directories is not None:
                    created_directories.append(entry)
                if journal is not None:
                    journal.record_directory(entry)
        yield handles[-1][1]
        for path, held, identity in handles:
            if api.checked_identity(held, directory=True) != identity:
                raise InstallerError(
                    f"Diretório foi alterado durante a preparação: {path}"
                )
    finally:
        for _, held, _ in reversed(handles):
            _close_windows_handle(api, held)


def _hash_open_file(descriptor: int, *, expected_size: int) -> str:
    digest = hashlib.sha256()
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(errno.EINVAL, "hashing exige arquivo regular")
    limit = _assert_hashable_size(metadata.st_size, expected_size)
    os.lseek(descriptor, 0, os.SEEK_SET)
    total = 0
    while True:
        block = os.read(descriptor, min(_HASH_CHUNK_SIZE, limit - total + 1))
        if not block:
            break
        total += len(block)
        if total > limit:
            raise OSError(errno.EFBIG, "arquivo cresceu além do limite de hashing")
        digest.update(block)
    if expected_size is not None and total != expected_size:
        raise OSError(errno.EIO, "arquivo diverge do tamanho gerenciado")
    return digest.hexdigest()


def _open_relative_directory(root_descriptor: int, parts: tuple[str, ...]) -> int:
    descriptor = os.dup(root_descriptor)
    try:
        for part in parts:
            child = os.open(part, _directory_open_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


@contextmanager
def _secure_archive_parent(
    destination_root: Path,
    parent_parts: tuple[str, ...],
    *,
    create: bool,
    created_directories: list[MaterializedDirectory] | None = None,
    journal: Any | None = None,
) -> Iterator[tuple[int, int]]:
    """Open one archive parent beneath a stable root without following symlinks."""

    if not _secure_archive_dir_fd_supported():
        raise InstallerError(
            "O sistema não oferece operações relativas seguras para materializar o pacote."
        )
    try:
        root_descriptor = os.open(destination_root, _directory_open_flags())
    except OSError as error:
        raise InstallerError(
            f"Diretório inseguro ao preparar o pacote: {destination_root}"
        ) from error
    descriptor = os.dup(root_descriptor)
    current_parts: list[str] = []
    try:
        for part in parent_parts:
            current_parts.append(part)
            try:
                child = os.open(part, _directory_open_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, mode=0o755, dir_fd=descriptor)
                    created = True
                except FileExistsError:
                    created = False
                try:
                    child = os.open(part, _directory_open_flags(), dir_fd=descriptor)
                except OSError as error:
                    raise InstallerError(
                        "Diretório inseguro ao preparar o pacote: "
                        f"{destination_root.joinpath(*current_parts)}"
                    ) from error
                if created:
                    directory = destination_root.joinpath(*current_parts)
                    entry = MaterializedDirectory(
                        directory,
                        destination_root,
                        _file_identity(os.fstat(child)),
                    )
                    if created_directories is not None:
                        created_directories.append(entry)
                    if journal is not None:
                        journal.record_directory(entry)
            except OSError as error:
                raise InstallerError(
                    "Diretório inseguro ao preparar o pacote: "
                    f"{destination_root.joinpath(*current_parts)}"
                ) from error
            os.close(descriptor)
            descriptor = child
        yield root_descriptor, descriptor
    finally:
        os.close(descriptor)
        os.close(root_descriptor)


def _assert_archive_parent_stable(
    root_descriptor: int,
    parent_parts: tuple[str, ...],
    parent_descriptor: int,
    path: Path,
) -> None:
    try:
        current = _open_relative_directory(root_descriptor, parent_parts)
    except OSError as error:
        raise InstallerError(
            f"Diretório foi alterado durante a preparação: {path}"
        ) from error
    try:
        if _file_identity(os.fstat(current)) != _file_identity(
            os.fstat(parent_descriptor)
        ):
            raise InstallerError(
                f"Diretório foi alterado durante a preparação: {path}"
            )
    finally:
        os.close(current)


def _windows_cleanup_materialized_file(
    api: object,
    entry: MaterializedFile,
) -> bool:
    root = entry.root
    if root is None or entry.identity is None or entry.expected_size is None:
        return not lexists(entry.path)
    try:
        relative = _relative_managed_path(entry.path, root)
        with _windows_archive_parent(
            api, root, tuple(relative.parts[:-1]), create=False,
        ):
            try:
                handle = api.open_handle(
                    entry.path,
                    access=api.GENERIC_READ | api.DELETE,
                    creation=api.OPEN_EXISTING,
                    directory=False,
                )
            except FileNotFoundError:
                return True
            try:
                if (
                    api.checked_identity(handle, directory=False) != entry.identity
                    or api.hash(
                        handle, expected_size=entry.expected_size,
                    ) != entry.expected_hash
                ):
                    return False
                api.mark_delete(handle)
                return True
            finally:
                _close_windows_handle(api, handle)
    except FileNotFoundError:
        return True
    except (InstallerError, OSError, ValueError):
        return not lexists(entry.path)


def _windows_cleanup_materialized_directory(
    api: object,
    entry: MaterializedDirectory,
) -> bool:
    try:
        relative = _relative_managed_path(entry.path, entry.root)
        with _windows_archive_parent(
            api, entry.root, tuple(relative.parts[:-1]), create=False,
        ):
            try:
                handle = api.open_handle(
                    entry.path,
                    access=api.FILE_READ_ATTRIBUTES | api.DELETE,
                    creation=api.OPEN_EXISTING,
                    directory=True,
                )
            except FileNotFoundError:
                return True
            try:
                if api.checked_identity(handle, directory=True) != entry.identity:
                    return False
                api.mark_delete(handle)
                return True
            finally:
                _close_windows_handle(api, handle)
    except FileNotFoundError:
        return True
    except (InstallerError, OSError, ValueError):
        return not lexists(entry.path)


def _restore_posix_quarantine(
    api: object,
    parent_descriptor: int,
    quarantine_name: str,
    destination_name: str,
    identity: tuple[int, int],
) -> bool:
    """Restore a quarantined file without replacing a concurrently created name."""

    try:
        quarantine = os.stat(
            quarantine_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if _file_identity(quarantine) != identity:
            return False
        api.move_no_replace(
            parent_descriptor,
            quarantine_name,
            parent_descriptor,
            destination_name,
        )
        restored = os.stat(
            destination_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if _file_identity(restored) != identity:
            return False
        os.fsync(parent_descriptor)
        return True
    except (FileExistsError, FileNotFoundError, OSError):
        return False


def _relative_managed_path(path: Path, root: Path) -> Path:
    """Resolve ancestors, never the final name, before anchoring a cleanup path."""

    canonical_root = root.resolve()
    canonical_parent = path.parent.resolve(strict=True)
    return (canonical_parent / path.name).relative_to(canonical_root)


def unlink_identity_bound_regular(
    path: Path,
    expected_identity: tuple[int, int],
) -> bool:
    """Atomically quarantine and unlink only the expected POSIX regular file."""

    if (
        not isinstance(expected_identity, tuple)
        or len(expected_identity) != 2
        or not all(type(value) is int and value >= 0 for value in expected_identity)
    ):
        raise ValueError("expected_identity must be a device/inode pair")
    rename_api = _get_posix_rename_api()
    if rename_api is None:
        return False
    path = Path(path)
    parent_descriptor = -1
    descriptor = -1
    quarantine_name: str | None = None
    quarantined_identity = (-1, -1)
    try:
        parent_descriptor = os.open(path.parent, _directory_open_flags())
        flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or _file_identity(metadata) != expected_identity
        ):
            return False
        for _ in range(128):
            candidate = f".x86qw-unlink-{secrets.token_hex(12)}"
            try:
                rename_api.move_no_replace(
                    parent_descriptor,
                    path.name,
                    parent_descriptor,
                    candidate,
                )
            except FileExistsError:
                continue
            quarantine_name = candidate
            break
        if quarantine_name is None:
            return False
        quarantined = os.stat(
            quarantine_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        quarantined_identity = _file_identity(quarantined)
        if (
            not stat.S_ISREG(quarantined.st_mode)
            or quarantined_identity != expected_identity
            or _file_identity(os.fstat(descriptor)) != expected_identity
        ):
            if _restore_posix_quarantine(
                rename_api,
                parent_descriptor,
                quarantine_name,
                path.name,
                quarantined_identity,
            ):
                quarantine_name = None
            return False
        os.unlink(quarantine_name, dir_fd=parent_descriptor)
        quarantine_name = None
        os.fsync(parent_descriptor)
        return True
    except FileNotFoundError:
        return False
    finally:
        if quarantine_name is not None and parent_descriptor >= 0:
            _restore_posix_quarantine(
                rename_api,
                parent_descriptor,
                quarantine_name,
                path.name,
                quarantined_identity,
            )
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _rmdir_identity_bound_directory(
    path: Path,
    expected_identity: tuple[int, int],
) -> bool:
    """Move an expected POSIX directory to a private name before rmdir."""

    rename_api = _get_posix_rename_api()
    if rename_api is None:
        return False
    path = Path(path)
    parent_descriptor = -1
    descriptor = -1
    quarantine_name: str | None = None
    quarantined_identity = (-1, -1)
    try:
        parent_descriptor = os.open(path.parent, _directory_open_flags())
        descriptor = os.open(
            path.name, _directory_open_flags(), dir_fd=parent_descriptor,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or _file_identity(metadata) != expected_identity
        ):
            return False
        for _ in range(128):
            candidate = f".x86qw-rmdir-{secrets.token_hex(12)}"
            try:
                rename_api.move_no_replace(
                    parent_descriptor,
                    path.name,
                    parent_descriptor,
                    candidate,
                )
            except FileExistsError:
                continue
            quarantine_name = candidate
            break
        if quarantine_name is None:
            return False
        quarantined = os.stat(
            quarantine_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        quarantined_identity = _file_identity(quarantined)
        if (
            not stat.S_ISDIR(quarantined.st_mode)
            or quarantined_identity != expected_identity
            or _file_identity(os.fstat(descriptor)) != expected_identity
        ):
            if _restore_posix_quarantine(
                rename_api,
                parent_descriptor,
                quarantine_name,
                path.name,
                quarantined_identity,
            ):
                quarantine_name = None
            return False
        os.rmdir(quarantine_name, dir_fd=parent_descriptor)
        quarantine_name = None
        os.fsync(parent_descriptor)
        return True
    except (FileNotFoundError, OSError):
        return False
    finally:
        if quarantine_name is not None and parent_descriptor >= 0:
            _restore_posix_quarantine(
                rename_api,
                parent_descriptor,
                quarantine_name,
                path.name,
                quarantined_identity,
            )
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def remove_identity_bound_path(
    path: Path,
    expected_identity: tuple[int, ...],
    *,
    directory: bool,
) -> bool:
    """Remove only the expected regular file or empty directory object."""

    if (
        not isinstance(expected_identity, tuple)
        or len(expected_identity) not in {3, 5}
        or not all(type(value) is int and value >= 0 for value in expected_identity)
    ):
        raise ValueError("expected_identity must include device, inode and type")
    expected_type = stat.S_IFDIR if directory else stat.S_IFREG
    if expected_identity[2] != expected_type:
        return False
    path = Path(path)
    windows_api = _get_windows_file_api()
    if windows_api is not None:
        if len(expected_identity) != 5:
            return False
        try:
            handle = windows_api.open_handle(
                path,
                access=windows_api.FILE_READ_ATTRIBUTES | windows_api.DELETE,
                creation=windows_api.OPEN_EXISTING,
                directory=directory,
            )
        except (FileNotFoundError, OSError):
            return False
        try:
            native_identity = windows_api.checked_identity(
                handle, directory=directory,
            )
            metadata = path.lstat()
            actual = (
                int(metadata.st_dev),
                int(metadata.st_ino),
                int(stat.S_IFMT(metadata.st_mode)),
            )
            if (
                actual != expected_identity[:3]
                or native_identity != expected_identity[3:]
            ):
                return False
            windows_api.mark_delete(handle)
            return True
        except OSError:
            return False
        finally:
            _close_windows_handle(windows_api, handle)
    if len(expected_identity) != 3:
        return False
    if directory:
        return _rmdir_identity_bound_directory(path, expected_identity[:2])
    return unlink_identity_bound_regular(path, expected_identity[:2])


def cleanup_materialized_file(entry: MaterializedFile) -> bool:
    if entry.expected_size is None:
        return not lexists(entry.path)
    try:
        _bounded_hash_limit(entry.expected_size)
    except ValueError:
        return False
    windows_api = _get_windows_file_api()
    if windows_api is not None:
        return _windows_cleanup_materialized_file(windows_api, entry)
    root = entry.root
    if root is None or not _secure_archive_dir_fd_supported():
        return not lexists(entry.path)
    rename_api = _get_posix_rename_api()
    if rename_api is None:
        return not lexists(entry.path)
    try:
        relative = _relative_managed_path(entry.path, root)
        parent_parts = tuple(relative.parts[:-1])
        with _secure_archive_parent(root, parent_parts, create=False) as (
            root_descriptor,
            parent_descriptor,
        ):
            _assert_archive_parent_stable(
                root_descriptor,
                parent_parts,
                parent_descriptor,
                entry.path.parent,
            )
            flags = os.O_RDONLY | os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            descriptor = os.open(relative.name, flags, dir_fd=parent_descriptor)
            quarantine_name: str | None = None
            original_unlinked = False
            identity = (-1, -1)
            try:
                metadata = os.fstat(descriptor)
                identity = _file_identity(metadata)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or entry.identity is not None and identity != entry.identity
                    or _hash_open_file(
                        descriptor, expected_size=entry.expected_size,
                    ) != entry.expected_hash
                ):
                    return False
                _assert_archive_parent_stable(
                    root_descriptor,
                    parent_parts,
                    parent_descriptor,
                    entry.path.parent,
                )
                for _ in range(128):
                    candidate = f".x86qw_cleanup_{secrets.token_hex(12)}"
                    try:
                        rename_api.move_no_replace(
                            parent_descriptor,
                            relative.name,
                            parent_descriptor,
                            candidate,
                        )
                    except FileExistsError:
                        continue
                    quarantine_name = candidate
                    original_unlinked = True
                    break
                if quarantine_name is None:
                    return False
                quarantine = os.stat(
                    quarantine_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if _file_identity(quarantine) != identity:
                    if _restore_posix_quarantine(
                        rename_api,
                        parent_descriptor,
                        quarantine_name,
                        relative.name,
                        _file_identity(quarantine),
                    ):
                        quarantine_name = None
                    return False
                os.fsync(parent_descriptor)
                if (
                    _hash_open_file(
                        descriptor, expected_size=entry.expected_size,
                    ) != entry.expected_hash
                    or _file_identity(os.fstat(descriptor)) != identity
                ):
                    if _restore_posix_quarantine(
                        rename_api,
                        parent_descriptor,
                        quarantine_name,
                        relative.name,
                        identity,
                    ):
                        quarantine_name = None
                    return False
                final = os.stat(
                    quarantine_name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISREG(final.st_mode)
                    or _file_identity(final) != identity
                    or final.st_nlink != 1
                ):
                    if _restore_posix_quarantine(
                        rename_api,
                        parent_descriptor,
                        quarantine_name,
                        relative.name,
                        identity,
                    ):
                        quarantine_name = None
                    return False
                os.unlink(quarantine_name, dir_fd=parent_descriptor)
                quarantine_name = None
                os.fsync(parent_descriptor)
                return True
            finally:
                if quarantine_name is not None and original_unlinked:
                    _restore_posix_quarantine(
                        rename_api,
                        parent_descriptor,
                        quarantine_name,
                        relative.name,
                        identity,
                    )
                os.close(descriptor)
    except FileNotFoundError:
        return True
    except (InstallerError, OSError, ValueError):
        return False


def cleanup_materialized_directory(entry: MaterializedDirectory) -> bool:
    windows_api = _get_windows_file_api()
    if windows_api is not None:
        return _windows_cleanup_materialized_directory(windows_api, entry)
    if not _secure_archive_dir_fd_supported():
        return not lexists(entry.path)
    root = entry.root
    directory = entry.path
    try:
        relative = _relative_managed_path(directory, root)
        parent_parts = tuple(relative.parts[:-1])
        with _secure_archive_parent(root, parent_parts, create=False) as (
            root_descriptor,
            parent_descriptor,
        ):
            _assert_archive_parent_stable(
                root_descriptor,
                parent_parts,
                parent_descriptor,
                directory.parent,
            )
            metadata = os.stat(
                relative.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or _file_identity(metadata) != entry.identity
            ):
                return False
            _assert_archive_parent_stable(
                root_descriptor,
                parent_parts,
                parent_descriptor,
                directory.parent,
            )
            current = os.stat(
                relative.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if _file_identity(current) != entry.identity:
                return False
            os.rmdir(relative.name, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
            return True
    except FileNotFoundError:
        return True
    except (InstallerError, OSError, ValueError):
        return False



def _open_verified_archive_member(
    parent_descriptor: int,
    name: str,
    expected_hash: str,
    expected_size: int,
    path: Path,
) -> tuple[int, int]:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        descriptor = os.open(name, flags, dir_fd=parent_descriptor)
    except OSError as error:
        raise InstallerError(f"Arquivo local conflita com a carga dedicada: {path}") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or _hash_open_file(descriptor, expected_size=expected_size) != expected_hash
        ):
            raise InstallerError(f"Arquivo local conflita com a carga dedicada: {path}")
        return _file_identity(metadata)
    finally:
        os.close(descriptor)


def _fallback_safe_chain(root: Path, parent: Path) -> None:
    """Best-effort path check for platforms without POSIX *at operations."""
    try:
        relative = parent.relative_to(root)
    except ValueError as error:
        raise InstallerError(f"Diretório fora da instalação: {parent}") from error
    cursor = root
    for part in relative.parts:
        cursor /= part
        try:
            metadata = cursor.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise InstallerError(f"Diretório inseguro ao preparar o pacote: {cursor}")


def _windows_remove_confirmed_file(
    api: object,
    path: Path,
    expected_identity: tuple[int, int],
    expected_hash: str,
    expected_size: int,
) -> bool:
    """Delete only the exact, unchanged file object named by ``path``."""
    try:
        handle = api.open_handle(
            path,
            access=api.GENERIC_READ | api.DELETE,
            creation=api.OPEN_EXISTING,
            directory=False,
        )
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        if (
            api.checked_identity(handle, directory=False) != expected_identity
            or api.hash(handle, expected_size=expected_size) != expected_hash
        ):
            return False
        api.mark_delete(handle)
        return True
    except OSError:
        return False
    finally:
        _close_windows_handle(api, handle)


def _windows_existing_member(
    api: object,
    destination_root: Path,
    member: object,
    destination: Path,
    label: str,
    *,
    package: Path | None = None,
    journal: Any | None = None,
) -> MaterializedFile | None | bool:
    parent_parts = tuple(member.path.parts[:-1])
    try:
        with _windows_archive_parent(
            api, destination_root, parent_parts, create=False,
        ):
            try:
                handle = api.open_handle(
                    destination,
                    access=api.GENERIC_READ,
                    creation=api.OPEN_EXISTING,
                    directory=False,
                )
            except FileNotFoundError:
                return False
            try:
                identity = api.checked_identity(handle, directory=False)
                digest = api.hash(handle, expected_size=member.size)
                if digest != member.sha256:
                    raise InstallerError(
                        f"Arquivo local conflita com a carga dedicada de {label}: {destination}"
                    )
                if package is None:
                    return True
                entry = MaterializedFile(
                    destination,
                    member.sha256,
                    package.as_posix(),
                    False,
                    True,
                    destination_root,
                    identity,
                    member.size,
                )
                if journal is not None:
                    journal.record_materialized(entry)
                return entry
            except InstallerError:
                raise
            except OSError as error:
                raise InstallerError(
                    f"Arquivo local conflita com a carga dedicada de {label}: {destination}"
                ) from error
            finally:
                _close_windows_handle(api, handle)
    except FileNotFoundError:
        return False


def _windows_materialize_member(
    api: object,
    source_path: Path,
    destination: Path,
    member: object,
    label: str,
    destination_root: Path,
    package: Path,
    journal: Any | None,
    created_directories: list[MaterializedDirectory],
    materialized_files: list[MaterializedFile],
) -> MaterializedFile:
    """Stage, verify and atomically rename one member without replacing a name."""
    parent_parts = tuple(member.path.parts[:-1])
    with _windows_archive_parent(
        api,
        destination_root,
        parent_parts,
        create=True,
        created_directories=created_directories,
        journal=journal,
    ):
        temporary: Path | None = None
        temporary_handle: int | None = None
        for _ in range(128):
            candidate = destination.parent / f".x86qw_ktx_{secrets.token_hex(12)}"
            try:
                temporary_handle = api.open_handle(
                    candidate,
                    access=api.GENERIC_READ | api.GENERIC_WRITE | api.DELETE,
                    creation=api.CREATE_NEW,
                    directory=False,
                )
            except FileExistsError:
                continue
            temporary = candidate
            break
        if temporary is None or temporary_handle is None:
            raise InstallerError(
                f"Não foi possível reservar staging para a carga dedicada de {label}: {destination}"
            )

        temporary_identity: tuple[int, int] | None = None
        copied_size = 0
        digest_builder = hashlib.sha256()
        try:
            temporary_identity = api.checked_identity(temporary_handle, directory=False)
            with source_path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    copied_size += len(block)
                    if copied_size > member.size:
                        raise InstallerError(
                            f"Tamanho divergente no pacote {label}: {member.path.as_posix()}"
                        )
                    digest_builder.update(block)
                    api.write(temporary_handle, block)
            api.flush(temporary_handle)
            digest = digest_builder.hexdigest()
            if (
                copied_size != member.size
                or digest != member.sha256
                or api.hash(temporary_handle, expected_size=member.size) != member.sha256
            ):
                raise InstallerError(
                    f"Conteúdo divergente no pacote {label}: {member.path.as_posix()}"
                )
        except Exception:
            if temporary_identity is not None:
                try:
                    if api.checked_identity(
                        temporary_handle, directory=False,
                    ) == temporary_identity:
                        api.mark_delete(temporary_handle)
                except OSError:
                    pass
            raise
        finally:
            _close_windows_handle(api, temporary_handle)

        assert temporary_identity is not None
        entry = MaterializedFile(
            destination,
            member.sha256,
            package.as_posix(),
            True,
            False,
            destination_root,
            temporary_identity,
            member.size,
        )
        try:
            if journal is not None:
                journal.record_materialized_intent(entry)
        except BaseException:
            _windows_remove_confirmed_file(
                api, temporary, temporary_identity, member.sha256, member.size,
            )
            raise
        try:
            api.move_no_replace(temporary, destination)
        except FileExistsError as error:
            _windows_remove_confirmed_file(
                api, temporary, temporary_identity, member.sha256, member.size,
            )
            raise InstallerError(
                f"Arquivo surgiu durante a preparação e foi preservado: {destination}"
            ) from error
        except OSError as error:
            _windows_remove_confirmed_file(
                api, temporary, temporary_identity, member.sha256, member.size,
            )
            raise InstallerError(
                f"Não foi possível promover a carga dedicada de {label}: {destination}"
            ) from error
        materialized_files.append(entry)
        try:
            destination_handle = api.open_handle(
                destination,
                access=api.GENERIC_READ | api.DELETE,
                creation=api.OPEN_EXISTING,
                directory=False,
            )
        except OSError as error:
            removed = _windows_remove_confirmed_file(
                api, destination, temporary_identity, member.sha256, member.size,
            )
            detail = (
                "a carga promovida inalterada foi revertida"
                if removed else "o arquivo inconclusivo ou alterado foi preservado"
            )
            raise InstallerError(
                f"Não foi possível confirmar a carga dedicada em {destination}; {detail}."
            ) from error
        rollback = True
        try:
            promoted_identity = api.checked_identity(destination_handle, directory=False)
            if (
                promoted_identity != temporary_identity
                or api.hash(
                    destination_handle, expected_size=member.size,
                ) != member.sha256
            ):
                rollback = False
                raise InstallerError(
                    f"Arquivo foi substituído durante a preparação e foi preservado: {destination}"
                )
            if journal is not None:
                journal.record_materialized(entry)
            rollback = False
            return entry
        except Exception:
            if rollback:
                try:
                    if (
                        api.checked_identity(
                            destination_handle, directory=False,
                        ) == temporary_identity
                        and api.hash(
                            destination_handle, expected_size=member.size,
                        ) == member.sha256
                    ):
                        api.mark_delete(destination_handle)
                except OSError:
                    pass
            raise
        finally:
            _close_windows_handle(api, destination_handle)


def _fallback_materialize_member(
    source_path: Path,
    destination: Path,
    member: object,
    label: str,
    destination_root: Path,
    before_promote: Callable[[tuple[int, int]], None] | None = None,
) -> tuple[str, tuple[int, int]]:
    """Use atomic no-replace promotion and abort on every observable path race."""
    _fallback_safe_chain(destination_root, destination.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".x86qw_ktx_", dir=destination.parent)
    temporary = Path(temporary_name)
    temporary_identity: tuple[int, int] | None = None
    try:
        digest_builder = hashlib.sha256()
        copied_size = 0
        with os.fdopen(descriptor, "wb") as output, source_path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                copied_size += len(block)
                if copied_size > member.size:
                    raise InstallerError(
                        f"Tamanho divergente no pacote {label}: {member.path.as_posix()}"
                    )
                digest_builder.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
            temporary_identity = _file_identity(os.fstat(output.fileno()))
        digest = digest_builder.hexdigest()
        if copied_size != member.size or digest != member.sha256:
            raise InstallerError(
                f"Conteúdo divergente no pacote {label}: {member.path.as_posix()}"
            )
        if os.name != "nt":
            temporary.chmod(0o644)
        _fallback_safe_chain(destination_root, destination.parent)
        assert temporary_identity is not None
        if before_promote is not None:
            before_promote(temporary_identity)
        try:
            os.link(temporary, destination, follow_symlinks=False)
        except FileExistsError as error:
            raise InstallerError(
                f"Arquivo surgiu durante a preparação e foi preservado: {destination}"
            ) from error
        _fallback_safe_chain(destination_root, destination.parent)
        metadata = destination.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or temporary_identity is None
            or _file_identity(metadata) != temporary_identity
        ):
            raise InstallerError(
                f"Arquivo foi substituído durante a preparação e foi preservado: {destination}"
            )
        temporary.unlink()
        return digest, temporary_identity
    finally:
        try:
            _fallback_safe_chain(destination_root, temporary.parent)
            metadata = temporary.lstat()
            if (
                stat.S_ISREG(metadata.st_mode)
                and (
                    temporary_identity is None
                    or _file_identity(metadata) == temporary_identity
                )
            ):
                temporary.unlink()
        except (FileNotFoundError, InstallerError, OSError):
            pass


def materialize_archive(
    package: Path,
    destination_root: Path,
    label: str,
    journal: Any | None = None,
    *,
    warning: Callable[[str], None] | None = None,
) -> MaterializedArchive:
    """Expose verified PK3 members to MVDSV, which does not implement PK3 loading."""
    if not package.is_file() or package.is_symlink():
        raise InstallerError(f"Pacote {label} ausente ou inseguro: {package}")
    if not destination_root.is_dir() or destination_root.is_symlink():
        raise InstallerError(f"Diretório de {label} ausente ou inseguro: {destination_root}")
    materialized_files: list[MaterializedFile] = []
    created_directories: list[MaterializedDirectory] = []
    windows_api = _get_windows_file_api()
    secure_dir_fd = _secure_archive_dir_fd_supported()
    try:
        plan = scan_archive(package)
        with tempfile.TemporaryDirectory(prefix="x86qw-pk3-") as temporary_root:
            extracted = Path(temporary_root) / "payload"
            extract_archive(plan, extracted)
            prepared: list[tuple[object, Path, bool]] = []
            for member in plan.members:
                if member.kind != "file":
                    continue
                destination = destination_root.joinpath(*member.path.parts)
                parent_parts = tuple(member.path.parts[:-1])
                if windows_api is not None:
                    existed = bool(_windows_existing_member(
                        windows_api,
                        destination_root,
                        member,
                        destination,
                        label,
                    ))
                elif secure_dir_fd:
                    try:
                        with _secure_archive_parent(
                            destination_root, parent_parts, create=False,
                        ) as (root_descriptor, parent_descriptor):
                            _assert_archive_parent_stable(
                                root_descriptor, parent_parts, parent_descriptor, destination.parent,
                            )
                            try:
                                metadata = os.stat(
                                    member.path.name,
                                    dir_fd=parent_descriptor,
                                    follow_symlinks=False,
                                )
                            except FileNotFoundError:
                                existed = False
                            else:
                                if not stat.S_ISREG(metadata.st_mode):
                                    raise InstallerError(
                                        f"Arquivo local conflita com a carga dedicada de {label}: "
                                        f"{destination}"
                                    )
                                _open_verified_archive_member(
                                    parent_descriptor,
                                    member.path.name,
                                    member.sha256,
                                    member.size,
                                    destination,
                                )
                                existed = True
                    except FileNotFoundError:
                        existed = False
                else:
                    _fallback_safe_chain(destination_root, destination.parent)
                    existed = lexists(destination)
                    if existed:
                        metadata = destination.lstat()
                        if (
                            not stat.S_ISREG(metadata.st_mode)
                            or not file_matches_sha256(
                                destination, member.sha256, member.size,
                            )
                        ):
                            raise InstallerError(
                                f"Arquivo local conflita com a carga dedicada de {label}: {destination}"
                            )
                prepared.append((member, destination, existed))

            for member, destination, existed in prepared:
                relative = member.path
                parent_parts = tuple(relative.parts[:-1])
                source_path = extracted.joinpath(*relative.parts)
                if windows_api is not None:
                    if existed:
                        entry = _windows_existing_member(
                            windows_api,
                            destination_root,
                            member,
                            destination,
                            label,
                            package=package,
                            journal=journal,
                        )
                        if not isinstance(entry, MaterializedFile):
                            raise InstallerError(
                                f"Arquivo desapareceu durante a preparação: {destination}"
                            )
                    else:
                        entry = _windows_materialize_member(
                            windows_api,
                            source_path,
                            destination,
                            member,
                            label,
                            destination_root,
                            package,
                            journal,
                            created_directories,
                            materialized_files,
                        )
                    if existed:
                        materialized_files.append(entry)
                    continue
                if secure_dir_fd:
                    with _secure_archive_parent(
                        destination_root,
                        parent_parts,
                        create=True,
                        created_directories=created_directories,
                        journal=journal,
                    ) as (root_descriptor, parent_descriptor):
                        _assert_archive_parent_stable(
                            root_descriptor, parent_parts, parent_descriptor, destination.parent,
                        )
                        if existed:
                            identity = _open_verified_archive_member(
                                parent_descriptor,
                                relative.name,
                                member.sha256,
                                member.size,
                                destination,
                            )
                            _assert_archive_parent_stable(
                                root_descriptor, parent_parts, parent_descriptor, destination.parent,
                            )
                            entry = MaterializedFile(
                                destination, member.sha256, package.as_posix(), False, True,
                                destination_root, identity, member.size,
                            )
                            if journal is not None:
                                journal.record_materialized(entry)
                            materialized_files.append(entry)
                            continue
                        try:
                            os.stat(
                                relative.name,
                                dir_fd=parent_descriptor,
                                follow_symlinks=False,
                            )
                        except FileNotFoundError:
                            pass
                        else:
                            raise InstallerError(
                                f"Arquivo surgiu durante a preparação e foi preservado: {destination}"
                            )
                        temporary_name = f".x86qw_ktx_{secrets.token_hex(12)}"
                        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                        if hasattr(os, "O_CLOEXEC"):
                            flags |= os.O_CLOEXEC
                        descriptor = os.open(
                            temporary_name, flags, 0o600, dir_fd=parent_descriptor,
                        )
                        promoted_identity: tuple[int, int] | None = None
                        try:
                            digest_builder = hashlib.sha256()
                            copied_size = 0
                            temporary_identity: tuple[int, int] | None = None
                            with os.fdopen(descriptor, "wb") as output, source_path.open("rb") as source:
                                for block in iter(lambda: source.read(1024 * 1024), b""):
                                    copied_size += len(block)
                                    if copied_size > member.size:
                                        raise InstallerError(
                                            f"Tamanho divergente no pacote {label}: {relative.as_posix()}"
                                        )
                                    digest_builder.update(block)
                                    output.write(block)
                                output.flush()
                                os.fsync(output.fileno())
                                os.fchmod(output.fileno(), 0o644)
                                temporary_identity = _file_identity(os.fstat(output.fileno()))
                            digest = digest_builder.hexdigest()
                            if copied_size != member.size or digest != member.sha256:
                                raise InstallerError(
                                    f"Conteúdo divergente no pacote {label}: {relative.as_posix()}"
                                )
                            _assert_archive_parent_stable(
                                root_descriptor, parent_parts, parent_descriptor, destination.parent,
                            )
                            assert temporary_identity is not None
                            entry = MaterializedFile(
                                destination, digest, package.as_posix(), True, False,
                                destination_root, temporary_identity, member.size,
                            )
                            if journal is not None:
                                journal.record_materialized_intent(entry)
                            try:
                                os.link(
                                    temporary_name,
                                    relative.name,
                                    src_dir_fd=parent_descriptor,
                                    dst_dir_fd=parent_descriptor,
                                    follow_symlinks=False,
                                )
                            except FileExistsError as error:
                                raise InstallerError(
                                    f"Arquivo surgiu durante a preparação e foi preservado: {destination}"
                                ) from error
                            promoted_identity = temporary_identity
                            # Once the hardlink is public, every subsequent
                            # failure must flow through the identity+hash cleanup
                            # policy.  Recording it before journal I/O prevents
                            # an exception from falling back to inode-only unlink.
                            materialized_files.append(entry)
                            metadata = os.stat(
                                relative.name,
                                dir_fd=parent_descriptor,
                                follow_symlinks=False,
                            )
                            if _file_identity(metadata) != promoted_identity:
                                raise InstallerError(
                                    f"Arquivo foi substituído durante a preparação e foi preservado: "
                                    f"{destination}"
                                )
                            _assert_archive_parent_stable(
                                root_descriptor, parent_parts, parent_descriptor, destination.parent,
                            )
                            os.unlink(temporary_name, dir_fd=parent_descriptor)
                            os.fsync(parent_descriptor)
                            if journal is not None:
                                journal.record_materialized(entry)
                        finally:
                            try:
                                os.unlink(temporary_name, dir_fd=parent_descriptor)
                            except FileNotFoundError:
                                pass
                    continue

                missing_parents: list[Path] = []
                cursor = destination.parent
                while cursor != destination_root and not lexists(cursor):
                    missing_parents.append(cursor)
                    cursor = cursor.parent
                _fallback_safe_chain(destination_root, cursor)
                for directory in reversed(missing_parents):
                    try:
                        directory.mkdir()
                        created = True
                    except FileExistsError:
                        created = False
                    _fallback_safe_chain(destination_root, directory)
                    if created:
                        metadata = directory.lstat()
                        entry = MaterializedDirectory(
                            directory, destination_root, _file_identity(metadata),
                        )
                        created_directories.append(entry)
                        if journal is not None:
                            journal.record_directory(entry)
                if existed:
                    _fallback_safe_chain(destination_root, destination.parent)
                    metadata = destination.lstat()
                    if (
                        not stat.S_ISREG(metadata.st_mode)
                        or not file_matches_sha256(
                            destination, member.sha256, member.size,
                        )
                    ):
                        raise InstallerError(
                            f"Arquivo local conflita com a carga dedicada de {label}: {destination}"
                        )
                    entry = MaterializedFile(
                        destination, member.sha256, package.as_posix(), False, True,
                        destination_root, _file_identity(metadata), member.size,
                    )
                else:
                    def record_fallback_intent(identity: tuple[int, int]) -> None:
                        if journal is not None:
                            journal.record_materialized_intent(MaterializedFile(
                                destination,
                                member.sha256,
                                package.as_posix(),
                                True,
                                False,
                                destination_root,
                                identity,
                                member.size,
                            ))

                    digest, identity = _fallback_materialize_member(
                        source_path,
                        destination,
                        member,
                        label,
                        destination_root,
                        before_promote=record_fallback_intent,
                    )
                    entry = MaterializedFile(
                        destination, digest, package.as_posix(), True, False,
                        destination_root, identity, member.size,
                    )
                tracked_before_journal = entry.created_by_session
                if tracked_before_journal:
                    materialized_files.append(entry)
                try:
                    if journal is not None:
                        journal.record_materialized(entry)
                except Exception:
                    if entry.created_by_session:
                        cleanup_materialized_file(entry)
                    raise
                if not tracked_before_journal:
                    materialized_files.append(entry)
    except InstallerError:
        cleanup_materialized_archive(MaterializedArchive(
            tuple(materialized_files), tuple(created_directories), destination_root,
        ), warning=warning)
        raise
    except (ArchiveError, OSError, RuntimeError) as error:
        cleanup_materialized_archive(MaterializedArchive(
            tuple(materialized_files), tuple(created_directories), destination_root,
        ), warning=warning)
        raise InstallerError(
            f"Não foi possível preparar a carga de {label} para o MVDSV: {error}"
        ) from error
    return MaterializedArchive(
        tuple(materialized_files), tuple(created_directories), destination_root,
    )


def cleanup_materialized_archive(
    materialized: MaterializedArchive,
    *,
    warning: Callable[[str], None] | None = None,
) -> None:
    """Remove only unchanged files and directories created for one session."""

    for entry in reversed(materialized.files):
        if not entry.created_by_session:
            continue
        try:
            removed = cleanup_materialized_file(entry)
        except (InstallerError, OSError):
            removed = False
        if not removed and warning is not None:
            warning(
                "Arquivo materializado alterado durante a sessão foi preservado: "
                f"{entry.path}"
            )
    for directory in reversed(materialized.directories):
        try:
            removed = cleanup_materialized_directory(directory)
        except (InstallerError, OSError, ValueError):
            removed = False
        if not removed and warning is not None:
            warning(
                "Diretório materializado alterado ou não removível com segurança foi "
                f"preservado: {directory.path}"
            )


def unlink_sensitive_temporary(path: Path) -> None:
    """Unlink one sensitive temporary without traversing a replacement path."""

    if not lexists(path):
        return
    metadata = path.lstat()
    if stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        path.unlink()
        return
    kind = "diretório" if stat.S_ISDIR(metadata.st_mode) else "arquivo especial"
    raise InstallerError(
        "Temporário sensível foi substituído por "
        f"{kind} e foi preservado para inspeção: {path}"
    )


def cleanup_sensitive_temporary(
    path: Path,
    expected_identity: tuple[int, int] | None,
) -> bool:
    """Remove only the original sensitive regular file by recorded identity."""

    if not lexists(path):
        return True
    if expected_identity is None:
        return False
    try:
        metadata = path.lstat()
        identity = int(metadata.st_dev), int(metadata.st_ino)
        if identity != expected_identity or not stat.S_ISREG(metadata.st_mode):
            return False
        private_fs.unlink_private_file(path, expected_identity=expected_identity)
        return not lexists(path)
    except OSError:
        return False


# Temporary aliases let existing service consumers switch ownership mechanically.
_MAX_MANAGED_FILE_SIZE = MAX_MANAGED_FILE_SIZE
_describe_non_sensitive_temporary = describe_non_sensitive_temporary
_persistent_path_identity = persistent_path_identity
_cleanup_materialized_file = cleanup_materialized_file
_cleanup_materialized_directory = cleanup_materialized_directory


__all__ = (
    "MAX_MANAGED_FILE_SIZE",
    "MaterializedArchive",
    "MaterializedDirectory",
    "MaterializedFile",
    "cleanup_materialized_archive",
    "cleanup_materialized_directory",
    "cleanup_materialized_file",
    "cleanup_sensitive_temporary",
    "describe_non_sensitive_temporary",
    "file_matches_sha256",
    "file_sha256",
    "materialize_archive",
    "persistent_path_identity",
    "remove_identity_bound_path",
    "unlink_sensitive_temporary",
)
