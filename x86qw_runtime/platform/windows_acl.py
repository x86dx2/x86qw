"""Native Windows private-DACL primitives.

The module is importable on every platform, but its public operations are
available only on Windows.  Managed private objects use a protected DACL with
exactly two allow ACEs: the current user and LOCAL SYSTEM.
"""

from __future__ import annotations

import ctypes
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator


SYSTEM_SID = "S-1-5-18"
PRIVATE_FILE_MASK = 0x001F01FF  # FILE_ALL_ACCESS
PRIVATE_DIRECTORY_ACE_FLAGS = 0x01 | 0x02  # OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE
PRIVATE_FILE_ACE_FLAGS = 0
SID_TEXT = re.compile(r"S-[0-9]+(?:-[0-9]+)+\Z", re.ASCII)


class WindowsAclError(OSError):
    """A Windows ACL could not be applied or proved private."""


@dataclass(frozen=True)
class PrivateAcl:
    owner: str
    principals: tuple[str, ...]
    protected: bool
    directory: bool
    readable_by_current_user: bool = True


class WindowsPathLease:
    """Keep one validated private object open without delete sharing."""

    def __init__(self, handle: int, path: Path) -> None:
        self._handle = handle
        self.path = path

    def close(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        if not _api().kernel32.CloseHandle(handle):
            raise _api().error(f"CloseHandle failed for private lease {self.path}")

    def __enter__(self) -> "WindowsPathLease":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def private_sddl(user_sid: str, *, directory: bool) -> str:
    """Return the canonical protected DACL used by x86QW."""
    if SID_TEXT.fullmatch(user_sid) is None:
        raise ValueError("invalid current-user SID")
    flags = "OICI" if directory else ""
    return (
        f"O:{user_sid}D:P"
        f"(A;{flags};FA;;;{user_sid})"
        f"(A;{flags};FA;;;{SYSTEM_SID})"
    )


def _require_windows() -> None:
    if os.name != "nt":
        raise WindowsAclError("Windows ACL operations are unavailable on this platform")


class _SecurityAttributes(ctypes.Structure):
    _fields_ = (
        ("nLength", ctypes.c_ulong),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", ctypes.c_int),
    )


class _FileAttributeTagInfo(ctypes.Structure):
    _fields_ = (("file_attributes", ctypes.c_ulong), ("reparse_tag", ctypes.c_ulong))


class _FileDispositionInfo(ctypes.Structure):
    # FILE_DISPOSITION_INFO.DeleteFile is the Win32 BOOLEAN typedef (BYTE),
    # not BOOL.  Passing a four-byte c_int changes the native ABI.
    _fields_ = (("delete_file", ctypes.c_ubyte),)


class _AclSizeInformation(ctypes.Structure):
    _fields_ = (
        ("ace_count", ctypes.c_ulong),
        ("acl_bytes_in_use", ctypes.c_ulong),
        ("acl_bytes_free", ctypes.c_ulong),
    )


class _AceHeader(ctypes.Structure):
    _fields_ = (
        ("ace_type", ctypes.c_ubyte),
        ("ace_flags", ctypes.c_ubyte),
        ("ace_size", ctypes.c_ushort),
    )


class _AccessAllowedAce(ctypes.Structure):
    _fields_ = (("header", _AceHeader), ("mask", ctypes.c_ulong), ("sid_start", ctypes.c_ulong))


class _TokenUser(ctypes.Structure):
    _fields_ = (("sid", ctypes.c_void_p), ("attributes", ctypes.c_ulong))


class _WindowsApi:
    ERROR_ALREADY_EXISTS = 183
    ERROR_FILE_EXISTS = 80
    ERROR_INSUFFICIENT_BUFFER = 122
    ERROR_NO_TOKEN = 1008
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    TOKEN_QUERY = 0x0008
    TOKEN_USER = 1
    SDDL_REVISION_1 = 1

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_READ_DATA = 0x00000001
    FILE_APPEND_DATA = 0x00000004
    READ_CONTROL = 0x00020000
    WRITE_DAC = 0x00040000
    FILE_READ_ATTRIBUTES = 0x00000080
    DELETE = 0x00010000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    CREATE_NEW = 1
    OPEN_EXISTING = 3
    OPEN_ALWAYS = 4
    FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_ATTRIBUTE_TAG_INFO = 9
    FILE_DISPOSITION_INFO = 4
    FILE_TYPE_DISK = 1

    SE_FILE_OBJECT = 1
    OWNER_SECURITY_INFORMATION = 0x00000001
    DACL_SECURITY_INFORMATION = 0x00000004
    PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    SE_DACL_PROTECTED = 0x1000
    ACL_SIZE_INFORMATION = 2
    ACCESS_ALLOWED_ACE_TYPE = 0
    FS_PERSISTENT_ACLS = 0x00000008

    def __init__(self) -> None:
        _require_windows()
        from ctypes import wintypes

        self.wintypes = wintypes
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

        self.kernel32.GetCurrentProcess.argtypes = []
        self.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel32.LocalFree.restype = ctypes.c_void_p
        self.kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(_SecurityAttributes), wintypes.DWORD, wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self.kernel32.CreateFileW.restype = wintypes.HANDLE
        self.kernel32.CreateDirectoryW.argtypes = [
            wintypes.LPCWSTR, ctypes.POINTER(_SecurityAttributes),
        ]
        self.kernel32.CreateDirectoryW.restype = wintypes.BOOL
        self.kernel32.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        self.kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        self.kernel32.GetFileType.argtypes = [wintypes.HANDLE]
        self.kernel32.GetFileType.restype = wintypes.DWORD
        self.kernel32.GetFileSizeEx.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong),
        ]
        self.kernel32.GetFileSizeEx.restype = wintypes.BOOL
        self.kernel32.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        self.kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
        self.kernel32.GetVolumePathNameW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
        ]
        self.kernel32.GetVolumePathNameW.restype = wintypes.BOOL
        self.kernel32.GetVolumeInformationW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD,
        ]
        self.kernel32.GetVolumeInformationW.restype = wintypes.BOOL

        self.advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
        ]
        self.advapi32.OpenProcessToken.restype = wintypes.BOOL
        self.advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.advapi32.GetTokenInformation.restype = wintypes.BOOL
        self.advapi32.ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR),
        ]
        self.advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
        self.advapi32.GetSecurityDescriptorDacl.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL),
        ]
        self.advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
        self.advapi32.GetSecurityDescriptorControl.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD),
        ]
        self.advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
        self.advapi32.GetSecurityInfo.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.advapi32.GetSecurityInfo.restype = wintypes.DWORD
        self.advapi32.SetSecurityInfo.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]
        self.advapi32.SetSecurityInfo.restype = wintypes.DWORD
        self.advapi32.GetAclInformation.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int,
        ]
        self.advapi32.GetAclInformation.restype = wintypes.BOOL
        self.advapi32.GetAce.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ]
        self.advapi32.GetAce.restype = wintypes.BOOL
        self.advapi32.IsValidSid.argtypes = [ctypes.c_void_p]
        self.advapi32.IsValidSid.restype = wintypes.BOOL
        self.advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
        self.advapi32.GetLengthSid.restype = wintypes.DWORD

    def error(self, action: str, code: int | None = None) -> WindowsAclError:
        number = ctypes.get_last_error() if code is None else code
        return WindowsAclError(number, f"{action}: {ctypes.FormatError(number).strip()}")


_API: _WindowsApi | None = None
_CURRENT_USER_SID: str | None = None


def _api() -> _WindowsApi:
    global _API
    if _API is None:
        _API = _WindowsApi()
    return _API


def _sid_string(api: _WindowsApi, sid: ctypes.c_void_p | int) -> str:
    if not sid or not api.advapi32.IsValidSid(sid):
        raise WindowsAclError("security descriptor contains an invalid SID")
    rendered = api.wintypes.LPWSTR()
    if not api.advapi32.ConvertSidToStringSidW(sid, ctypes.byref(rendered)):
        raise api.error("ConvertSidToStringSidW failed")
    try:
        return str(rendered.value)
    finally:
        api.kernel32.LocalFree(ctypes.cast(rendered, ctypes.c_void_p))


def current_user_sid() -> str:
    """Return the SID for the token running this process."""
    global _CURRENT_USER_SID
    if _CURRENT_USER_SID is not None:
        return _CURRENT_USER_SID
    api = _api()
    token = api.wintypes.HANDLE()
    if not api.advapi32.OpenProcessToken(
        api.kernel32.GetCurrentProcess(), api.TOKEN_QUERY, ctypes.byref(token),
    ):
        raise api.error("OpenProcessToken failed")
    try:
        required = api.wintypes.DWORD()
        api.advapi32.GetTokenInformation(
            token, api.TOKEN_USER, None, 0, ctypes.byref(required),
        )
        if ctypes.get_last_error() != api.ERROR_INSUFFICIENT_BUFFER or not required.value:
            raise api.error("GetTokenInformation size query failed")
        buffer = ctypes.create_string_buffer(required.value)
        if not api.advapi32.GetTokenInformation(
            token, api.TOKEN_USER, buffer, required, ctypes.byref(required),
        ):
            raise api.error("GetTokenInformation failed")
        _CURRENT_USER_SID = _sid_string(api, ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents.sid)
        return _CURRENT_USER_SID
    finally:
        api.kernel32.CloseHandle(token)


@contextmanager
def _descriptor_from_sddl(sddl: str) -> Iterator[tuple[ctypes.c_void_p, ctypes.c_void_p]]:
    api = _api()
    descriptor = ctypes.c_void_p()
    size = api.wintypes.DWORD()
    if not api.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, api.SDDL_REVISION_1, ctypes.byref(descriptor), ctypes.byref(size),
    ):
        raise api.error("ConvertStringSecurityDescriptorToSecurityDescriptorW failed")
    try:
        present = api.wintypes.BOOL()
        defaulted = api.wintypes.BOOL()
        dacl = ctypes.c_void_p()
        if not api.advapi32.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted),
        ):
            raise api.error("GetSecurityDescriptorDacl failed")
        if not present.value or not dacl.value:
            raise WindowsAclError("canonical private security descriptor has no DACL")
        yield descriptor, dacl
    finally:
        api.kernel32.LocalFree(descriptor)


@contextmanager
def _security_descriptor(*, directory: bool) -> Iterator[tuple[ctypes.c_void_p, ctypes.c_void_p]]:
    with _descriptor_from_sddl(
        private_sddl(current_user_sid(), directory=directory),
    ) as values:
        yield values


def _assert_persistent_acls(path: Path) -> None:
    api = _api()
    existing = Path(os.path.abspath(os.fspath(path)))
    while not existing.exists():
        if existing.parent == existing:
            raise WindowsAclError(f"cannot resolve a filesystem for {path}")
        existing = existing.parent
    volume = ctypes.create_unicode_buffer(32768)
    if not api.kernel32.GetVolumePathNameW(str(existing), volume, len(volume)):
        raise api.error("GetVolumePathNameW failed")
    flags = api.wintypes.DWORD()
    if not api.kernel32.GetVolumeInformationW(
        volume.value, None, 0, None, None, ctypes.byref(flags), None, 0,
    ):
        raise api.error("GetVolumeInformationW failed")
    if not flags.value & api.FS_PERSISTENT_ACLS:
        raise WindowsAclError(f"filesystem does not persist Windows ACLs: {volume.value}")


def _handle_attributes(handle: int) -> _FileAttributeTagInfo:
    api = _api()
    information = _FileAttributeTagInfo()
    if not api.kernel32.GetFileInformationByHandleEx(
        handle, api.FILE_ATTRIBUTE_TAG_INFO, ctypes.byref(information), ctypes.sizeof(information),
    ):
        raise api.error("GetFileInformationByHandleEx failed")
    return information


def _assert_handle_type(handle: int, *, directory: bool) -> None:
    api = _api()
    if api.kernel32.GetFileType(handle) != api.FILE_TYPE_DISK:
        raise WindowsAclError("private path is not a disk object")
    attributes = _handle_attributes(handle).file_attributes
    if attributes & api.FILE_ATTRIBUTE_REPARSE_POINT:
        raise WindowsAclError("private path must not be a reparse point")
    actual_directory = bool(attributes & api.FILE_ATTRIBUTE_DIRECTORY)
    if actual_directory != directory:
        expected = "directory" if directory else "regular file"
        raise WindowsAclError(f"private path is not a {expected}")


def _open_path(path: Path, *, directory: bool, writable_dacl: bool) -> int:
    api = _api()
    access = api.READ_CONTROL | api.FILE_READ_ATTRIBUTES
    if writable_dacl:
        access |= api.WRITE_DAC
    flags = api.FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= api.FILE_FLAG_BACKUP_SEMANTICS
    handle = api.kernel32.CreateFileW(
        str(path), access,
        api.FILE_SHARE_READ | api.FILE_SHARE_WRITE,
        None, api.OPEN_EXISTING, flags, None,
    )
    if handle == api.INVALID_HANDLE_VALUE:
        raise api.error(f"could not open private path {path}")
    try:
        _assert_handle_type(handle, directory=directory)
        return int(handle)
    except BaseException:
        api.kernel32.CloseHandle(handle)
        raise


@contextmanager
def _hold_plain_directory_chain(path: Path) -> Iterator[None]:
    """Hold every lexical directory component without following a reparse point."""
    if ".." in path.parts:
        raise WindowsAclError(f"private directory contains a parent traversal: {path}")
    absolute = Path(os.path.abspath(os.fspath(path)))
    anchor = Path(absolute.anchor)
    if not absolute.anchor:
        raise WindowsAclError(f"private directory is not absolute: {path}")
    candidates = [anchor]
    current = anchor
    try:
        relative = absolute.relative_to(anchor)
    except ValueError as error:
        raise WindowsAclError(f"private directory has an invalid root: {path}") from error
    for component in relative.parts:
        current /= component
        candidates.append(current)
    api = _api()
    handles: list[int] = []
    try:
        for candidate in candidates:
            handles.append(_open_path(candidate, directory=True, writable_dacl=False))
        yield
    finally:
        for handle in reversed(handles):
            api.kernel32.CloseHandle(handle)


def validate_plain_directory(path: Path) -> None:
    """Reject a directory path containing any reparse-point component."""
    with _hold_plain_directory_chain(path):
        return


def _acl_from_handle(handle: int, *, directory: bool, canonical: bool = True) -> PrivateAcl:
    api = _api()
    owner = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = api.advapi32.GetSecurityInfo(
        handle, api.SE_FILE_OBJECT,
        api.OWNER_SECURITY_INFORMATION | api.DACL_SECURITY_INFORMATION,
        ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor),
    )
    if result:
        raise api.error("GetSecurityInfo failed", int(result))
    try:
        control = api.wintypes.WORD()
        revision = api.wintypes.DWORD()
        if not api.advapi32.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision),
        ):
            raise api.error("GetSecurityDescriptorControl failed")
        protected = bool(control.value & api.SE_DACL_PROTECTED)
        if not dacl.value:
            raise WindowsAclError("private path has a null or missing DACL")
        information = _AclSizeInformation()
        if not api.advapi32.GetAclInformation(
            dacl, ctypes.byref(information), ctypes.sizeof(information), api.ACL_SIZE_INFORMATION,
        ):
            raise api.error("GetAclInformation failed")
        principals: list[str] = []
        current_sid = current_user_sid()
        readable_by_current_user = False
        expected_flags = PRIVATE_DIRECTORY_ACE_FLAGS if directory else PRIVATE_FILE_ACE_FLAGS
        for index in range(information.ace_count):
            pointer = ctypes.c_void_p()
            if not api.advapi32.GetAce(dacl, index, ctypes.byref(pointer)):
                raise api.error("GetAce failed")
            header = ctypes.cast(pointer, ctypes.POINTER(_AceHeader)).contents
            if int(header.ace_size) < ctypes.sizeof(_AccessAllowedAce):
                raise WindowsAclError("private path has a truncated access entry")
            ace = ctypes.cast(pointer, ctypes.POINTER(_AccessAllowedAce)).contents
            ace_flags = int(ace.header.ace_flags)
            mask = int(ace.mask)
            if ace.header.ace_type != api.ACCESS_ALLOWED_ACE_TYPE:
                raise WindowsAclError("private path has a non-canonical access entry")
            if canonical and (ace_flags != expected_flags or mask != PRIVATE_FILE_MASK):
                raise WindowsAclError("private path has a non-canonical access entry")
            if not canonical and (directory or ace_flags != 0):
                raise WindowsAclError("private external file has inheritance or object ACE flags")
            sid_offset = _AccessAllowedAce.sid_start.offset
            sid_pointer = ctypes.c_void_p(pointer.value + sid_offset)
            if not api.advapi32.IsValidSid(sid_pointer):
                raise WindowsAclError("private path has an invalid access-entry SID")
            sid_length = int(api.advapi32.GetLengthSid(sid_pointer))
            if sid_length < 8 or sid_offset + sid_length > int(header.ace_size):
                raise WindowsAclError("private path has a truncated access-entry SID")
            principal = _sid_string(api, sid_pointer)
            principals.append(principal)
            if principal == current_sid and mask & (api.FILE_READ_DATA | api.GENERIC_READ):
                readable_by_current_user = True
        return PrivateAcl(
            owner=_sid_string(api, owner),
            principals=tuple(principals),
            protected=protected,
            directory=directory,
            readable_by_current_user=readable_by_current_user or canonical,
        )
    finally:
        api.kernel32.LocalFree(descriptor)


def _assert_acl(acl: PrivateAcl, *, exact: bool = True) -> None:
    allowed = {current_user_sid(), SYSTEM_SID}
    if not acl.protected:
        raise WindowsAclError("private path DACL still inherits access")
    if acl.owner not in allowed:
        raise WindowsAclError("private path owner is not the current user or LOCAL SYSTEM")
    if exact and (len(acl.principals) != 2 or set(acl.principals) != allowed):
        raise WindowsAclError("private path grants access to an unauthorized principal")
    if not exact and (
        not acl.principals
        or len(acl.principals) != len(set(acl.principals))
        or set(acl.principals) - allowed
        or current_user_sid() not in acl.principals
        or not acl.readable_by_current_user
    ):
        raise WindowsAclError("private external file grants unsafe or insufficient access")


def _owner_from_handle(handle: int) -> str:
    api = _api()
    owner = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = api.advapi32.GetSecurityInfo(
        handle, api.SE_FILE_OBJECT, api.OWNER_SECURITY_INFORMATION,
        ctypes.byref(owner), None, None, None, ctypes.byref(descriptor),
    )
    if result:
        raise api.error("GetSecurityInfo owner query failed", int(result))
    try:
        return _sid_string(api, owner)
    finally:
        api.kernel32.LocalFree(descriptor)


def _assert_managed_owner(handle: int) -> None:
    if _owner_from_handle(handle) not in {current_user_sid(), SYSTEM_SID}:
        raise WindowsAclError("managed private path has an unexpected owner")


def _private_read_share_mode(*, exact: bool) -> int:
    """Return the sharing policy for managed metadata or external secrets."""
    mode = _WindowsApi.FILE_SHARE_READ
    if exact:
        mode |= _WindowsApi.FILE_SHARE_WRITE | _WindowsApi.FILE_SHARE_DELETE
    return mode


def validate_private_path(path: Path, *, directory: bool, exact: bool = True) -> PrivateAcl:
    """Prove that an existing non-reparse path has the canonical private DACL."""
    with _hold_plain_directory_chain(path.parent):
        _assert_persistent_acls(path.parent)
        api = _api()
        handle = _open_path(path, directory=directory, writable_dacl=False)
        try:
            acl = _acl_from_handle(handle, directory=directory, canonical=exact)
            _assert_acl(acl, exact=exact)
            return acl
        finally:
            api.kernel32.CloseHandle(handle)


def hold_private_path(path: Path, *, directory: bool) -> WindowsPathLease:
    """Pin a canonical private object so its parent cannot delete or rename it."""
    with _hold_plain_directory_chain(path.parent):
        _assert_persistent_acls(path.parent)
        handle = _open_path(path, directory=directory, writable_dacl=False)
        try:
            _assert_acl(_acl_from_handle(handle, directory=directory))
            return WindowsPathLease(handle, path)
        except BaseException:
            _api().kernel32.CloseHandle(handle)
            raise


def protect_private_path(path: Path, *, directory: bool) -> PrivateAcl:
    """Replace an existing managed path DACL and verify the result by handle."""
    with _hold_plain_directory_chain(path.parent):
        _assert_persistent_acls(path.parent)
        api = _api()
        handle = _open_path(path, directory=directory, writable_dacl=True)
        try:
            _assert_managed_owner(handle)
            with _security_descriptor(directory=directory) as (_, dacl):
                result = api.advapi32.SetSecurityInfo(
                    handle, api.SE_FILE_OBJECT,
                    api.DACL_SECURITY_INFORMATION | api.PROTECTED_DACL_SECURITY_INFORMATION,
                    None, None, dacl, None,
                )
                if result:
                    raise api.error("SetSecurityInfo failed", int(result))
            acl = _acl_from_handle(handle, directory=directory)
            _assert_acl(acl)
            return acl
        finally:
            api.kernel32.CloseHandle(handle)


def create_private_directory(path: Path) -> None:
    """Atomically create one directory with the canonical protected DACL."""
    with _hold_plain_directory_chain(path.parent):
        _assert_persistent_acls(path.parent)
        api = _api()
        with _security_descriptor(directory=True) as (descriptor, _):
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            if not api.kernel32.CreateDirectoryW(str(path), ctypes.byref(attributes)):
                error = ctypes.get_last_error()
                if error in {api.ERROR_ALREADY_EXISTS, api.ERROR_FILE_EXISTS}:
                    raise FileExistsError(error, os.strerror(error), str(path))
                raise api.error(f"CreateDirectoryW failed for {path}", error)
    try:
        validate_private_path(path, directory=True)
    except BaseException:
        # Preserve the newly created private object.  Pathname cleanup after
        # closing its identity-bearing handle could remove a replacement.
        raise


def create_private_file(path: Path) -> int:
    """Atomically create one empty private file and return a Python descriptor."""
    with _hold_plain_directory_chain(path.parent):
        _assert_persistent_acls(path.parent)
        api = _api()
        with _security_descriptor(directory=False) as (descriptor, _):
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            handle = api.kernel32.CreateFileW(
                str(path), api.GENERIC_READ | api.GENERIC_WRITE | api.READ_CONTROL | api.WRITE_DAC,
                0, ctypes.byref(attributes), api.CREATE_NEW,
                api.FILE_ATTRIBUTE_NORMAL | api.FILE_FLAG_OPEN_REPARSE_POINT, None,
            )
            if handle == api.INVALID_HANDLE_VALUE:
                error = ctypes.get_last_error()
                if error in {api.ERROR_ALREADY_EXISTS, api.ERROR_FILE_EXISTS}:
                    raise FileExistsError(error, os.strerror(error), str(path))
                raise api.error(f"CreateFileW failed for {path}", error)
    transferred = False
    try:
        _assert_handle_type(handle, directory=False)
        _assert_acl(_acl_from_handle(handle, directory=False))
        import msvcrt

        descriptor_number = msvcrt.open_osfhandle(int(handle), os.O_RDWR | getattr(os, "O_BINARY", 0))
        transferred = True
        return descriptor_number
    except BaseException:
        if not transferred:
            api.kernel32.CloseHandle(handle)
        # Preserve the empty private object: deleting by pathname after the
        # handle closes could remove a concurrent replacement.
        raise


def open_private_append(path: Path) -> int:
    """Open or create one managed log with a private DACL before returning."""
    with _hold_plain_directory_chain(path.parent):
        _assert_persistent_acls(path.parent)
        api = _api()
        with _security_descriptor(directory=False) as (descriptor, _):
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
            handle = api.kernel32.CreateFileW(
                str(path),
                api.FILE_APPEND_DATA | api.FILE_READ_ATTRIBUTES | api.READ_CONTROL | api.WRITE_DAC,
                api.FILE_SHARE_READ, ctypes.byref(attributes), api.OPEN_ALWAYS,
                api.FILE_ATTRIBUTE_NORMAL | api.FILE_FLAG_OPEN_REPARSE_POINT, None,
            )
            if handle == api.INVALID_HANDLE_VALUE:
                raise api.error(f"CreateFileW failed for {path}")
    transferred = False
    try:
        _assert_handle_type(handle, directory=False)
        _assert_managed_owner(handle)
        with _security_descriptor(directory=False) as (_, dacl):
            result = api.advapi32.SetSecurityInfo(
                handle, api.SE_FILE_OBJECT,
                api.DACL_SECURITY_INFORMATION | api.PROTECTED_DACL_SECURITY_INFORMATION,
                None, None, dacl, None,
            )
            if result:
                raise api.error("SetSecurityInfo failed", int(result))
        _assert_acl(_acl_from_handle(handle, directory=False))
        import msvcrt

        descriptor_number = msvcrt.open_osfhandle(
            int(handle), os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0),
        )
        transferred = True
        return descriptor_number
    except BaseException:
        if not transferred:
            api.kernel32.CloseHandle(handle)
        # A just-created file is empty and private.  Preserve it rather than
        # deleting a pathname whose identity can no longer be proven.
        raise


def read_validated_private_file(
    path: Path, *, maximum_size: int, exact: bool = True,
) -> bytes:
    """Validate and read a private regular file through one no-follow handle."""
    if type(maximum_size) is not int or maximum_size < 0:
        raise ValueError("maximum_size must be a non-negative integer")
    with _hold_plain_directory_chain(path.parent):
        _assert_persistent_acls(path.parent)
        api = _api()
        # Managed metadata is atomically replaced and deleted while status or
        # recovery may be reading the previous object.  Share mutation for
        # canonical x86QW files: the handle still pins the exact snapshot being
        # validated and read.  A user-supplied password file is different; keep
        # it read-shared only so it cannot change during secret ingestion.
        share_mode = _private_read_share_mode(exact=exact)
        handle = api.kernel32.CreateFileW(
            str(path), api.FILE_READ_DATA | api.READ_CONTROL | api.FILE_READ_ATTRIBUTES,
            share_mode, None, api.OPEN_EXISTING,
            api.FILE_ATTRIBUTE_NORMAL | api.FILE_FLAG_OPEN_REPARSE_POINT, None,
        )
        if handle == api.INVALID_HANDLE_VALUE:
            raise api.error(f"could not open private file {path}")
        transferred = False
        try:
            _assert_handle_type(handle, directory=False)
            _assert_acl(
                _acl_from_handle(handle, directory=False, canonical=exact), exact=exact,
            )
            size = ctypes.c_longlong()
            if not api.kernel32.GetFileSizeEx(handle, ctypes.byref(size)):
                raise api.error("GetFileSizeEx failed")
            if size.value < 0 or size.value > maximum_size:
                raise WindowsAclError(f"private file exceeds {maximum_size} bytes")
            import msvcrt

            descriptor = msvcrt.open_osfhandle(
                int(handle), os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
            transferred = True
            with os.fdopen(descriptor, "rb", closefd=True) as source:
                payload = source.read(maximum_size + 1)
            if len(payload) > maximum_size:
                raise WindowsAclError(f"private file exceeds {maximum_size} bytes")
            return payload
        finally:
            if not transferred:
                api.kernel32.CloseHandle(handle)


def read_and_protect_legacy_file(
    path: Path,
    *,
    maximum_size: int,
    approve: Callable[[bytes], bool],
) -> bytes:
    """Read and harden one approved pre-DACL file through the same handle.

    The caller decides whether the bounded payload belongs to a historical
    schema.  A current object whose DACL was broadened is therefore rejected
    without being silently converted into a canonical object on a later read.
    """
    if type(maximum_size) is not int or maximum_size < 0:
        raise ValueError("maximum_size must be a non-negative integer")
    with _hold_plain_directory_chain(path.parent):
        _assert_persistent_acls(path.parent)
        api = _api()
        handle = api.kernel32.CreateFileW(
            str(path),
            api.FILE_READ_DATA | api.READ_CONTROL | api.FILE_READ_ATTRIBUTES | api.WRITE_DAC,
            api.FILE_SHARE_READ,
            None, api.OPEN_EXISTING,
            api.FILE_ATTRIBUTE_NORMAL | api.FILE_FLAG_OPEN_REPARSE_POINT, None,
        )
        if handle == api.INVALID_HANDLE_VALUE:
            raise api.error(f"could not open legacy private file {path}")
        transferred = False
        try:
            _assert_handle_type(handle, directory=False)
            _assert_managed_owner(handle)
            size = ctypes.c_longlong()
            if not api.kernel32.GetFileSizeEx(handle, ctypes.byref(size)):
                raise api.error("GetFileSizeEx failed")
            if size.value < 0 or size.value > maximum_size:
                raise WindowsAclError(f"legacy private file exceeds {maximum_size} bytes")
            import msvcrt

            descriptor = msvcrt.open_osfhandle(
                int(handle), os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
            transferred = True
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                payload = source.read(maximum_size + 1)
            if len(payload) > maximum_size:
                raise WindowsAclError(f"legacy private file exceeds {maximum_size} bytes")
            if not approve(payload):
                raise WindowsAclError("file is not an approved legacy private object")
            native_handle = msvcrt.get_osfhandle(descriptor)
            with _security_descriptor(directory=False) as (_, dacl):
                result = api.advapi32.SetSecurityInfo(
                    native_handle, api.SE_FILE_OBJECT,
                    api.DACL_SECURITY_INFORMATION | api.PROTECTED_DACL_SECURITY_INFORMATION,
                    None, None, dacl, None,
                )
                if result:
                    raise api.error("SetSecurityInfo failed", int(result))
            _assert_acl(_acl_from_handle(native_handle, directory=False))
            return payload
        finally:
            if transferred:
                os.close(descriptor)
            else:
                api.kernel32.CloseHandle(handle)


def unlink_private_file(
    path: Path, *, expected_identity: tuple[int, int] | None = None,
) -> None:
    """Delete a validated managed file through the same identity-bearing handle."""
    with _hold_plain_directory_chain(path.parent):
        _assert_persistent_acls(path.parent)
        api = _api()
        handle = api.kernel32.CreateFileW(
            str(path), api.DELETE | api.READ_CONTROL | api.FILE_READ_ATTRIBUTES,
            api.FILE_SHARE_READ | api.FILE_SHARE_WRITE, None, api.OPEN_EXISTING,
            api.FILE_ATTRIBUTE_NORMAL | api.FILE_FLAG_OPEN_REPARSE_POINT, None,
        )
        if handle == api.INVALID_HANDLE_VALUE:
            error = ctypes.get_last_error()
            if error in {2, 3}:
                return
            raise api.error(f"could not open private cleanup file {path}", error)
        transferred = False
        descriptor = -1
        try:
            _assert_handle_type(handle, directory=False)
            _assert_acl(_acl_from_handle(handle, directory=False))
            import msvcrt

            descriptor = msvcrt.open_osfhandle(
                int(handle), os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
            transferred = True
            if expected_identity is not None:
                metadata = os.fstat(descriptor)
                actual_identity = (int(metadata.st_dev), int(metadata.st_ino))
                if actual_identity != expected_identity:
                    raise WindowsAclError("private cleanup path changed identity")
            disposition = _FileDispositionInfo(True)
            native_handle = msvcrt.get_osfhandle(descriptor)
            if not api.kernel32.SetFileInformationByHandle(
                native_handle, api.FILE_DISPOSITION_INFO,
                ctypes.byref(disposition), ctypes.sizeof(disposition),
            ):
                raise api.error(f"could not delete private file {path}")
        finally:
            if transferred:
                os.close(descriptor)
            else:
                api.kernel32.CloseHandle(handle)


def api_functions() -> tuple[object, ...]:
    """Expose configured functions for native signature regression tests."""
    api = _api()
    return (
        api.kernel32.GetCurrentProcess,
        api.kernel32.CloseHandle,
        api.kernel32.LocalFree,
        api.kernel32.CreateFileW,
        api.kernel32.CreateDirectoryW,
        api.kernel32.GetFileInformationByHandleEx,
        api.kernel32.GetFileType,
        api.kernel32.GetFileSizeEx,
        api.kernel32.SetFileInformationByHandle,
        api.kernel32.GetVolumePathNameW,
        api.kernel32.GetVolumeInformationW,
        api.advapi32.OpenProcessToken,
        api.advapi32.GetTokenInformation,
        api.advapi32.ConvertSidToStringSidW,
        api.advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW,
        api.advapi32.GetSecurityDescriptorDacl,
        api.advapi32.GetSecurityDescriptorControl,
        api.advapi32.GetSecurityInfo,
        api.advapi32.SetSecurityInfo,
        api.advapi32.GetAclInformation,
        api.advapi32.GetAce,
        api.advapi32.IsValidSid,
        api.advapi32.GetLengthSid,
    )
