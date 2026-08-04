"""Bounded hashing, durable identity and race-safe managed-file cleanup."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import secrets
import stat
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from x86qw_runtime.errors import InstallerError
from x86qw_runtime.io.archive import DEFAULT_ARCHIVE_LIMITS
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


# Temporary aliases let existing service consumers switch ownership mechanically.
_MAX_MANAGED_FILE_SIZE = MAX_MANAGED_FILE_SIZE
_describe_non_sensitive_temporary = describe_non_sensitive_temporary
_persistent_path_identity = persistent_path_identity
_cleanup_materialized_file = cleanup_materialized_file
_cleanup_materialized_directory = cleanup_materialized_directory


__all__ = (
    "MAX_MANAGED_FILE_SIZE",
    "MaterializedDirectory",
    "MaterializedFile",
    "cleanup_materialized_directory",
    "cleanup_materialized_file",
    "describe_non_sensitive_temporary",
    "file_matches_sha256",
    "file_sha256",
    "persistent_path_identity",
    "unlink_sensitive_temporary",
)
