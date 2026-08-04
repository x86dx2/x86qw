"""Native atomic mutex adapters for one x86QW installation.

The filesystem lock schema and recovery policy stay with the session controller;
this module owns only the operating-system critical section that serializes the
observation, reclaim, and replacement of ``active.lock``.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class SessionControlError(RuntimeError):
    """A lock cannot be acquired or safely reconciled."""


def windows_acquisition_mutex_name(target: Path) -> str:
    normalized = os.path.normcase(os.path.abspath(os.fspath(target)))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    # The global namespace coordinates Terminal Services sessions. Windows
    # requires SeCreateGlobalPrivilege for global file mappings, not mutexes.
    return f"Global\\x86QW-install-{digest}"


@contextmanager
def windows_acquisition_mutex(target: Path) -> Iterator[None]:
    """Serialize the stale-lock transition without filesystem state."""

    from ctypes import wintypes

    from x86qw_runtime.platform import windows_acl

    convert_sddl = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class SecurityAttributes(ctypes.Structure):
        _fields_ = (
            ("length", wintypes.DWORD),
            ("security_descriptor", ctypes.c_void_p),
            ("inherit_handle", wintypes.BOOL),
        )

    convert_sddl.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.ULONG),
    ]
    convert_sddl.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    kernel32.CreateMutexW.argtypes = [
        ctypes.POINTER(SecurityAttributes),
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    user_sid = windows_acl.current_user_sid()
    security_descriptor = ctypes.c_void_p()
    descriptor_size = wintypes.ULONG()
    sddl = f"O:{user_sid}D:P(A;;GA;;;{user_sid})(A;;GA;;;S-1-5-18)"
    if not convert_sddl.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl,
        1,
        ctypes.byref(security_descriptor),
        ctypes.byref(descriptor_size),
    ):
        raise SessionControlError(
            "Não foi possível proteger o mutex da instalação "
            f"({ctypes.get_last_error()})."
        )
    try:
        attributes = SecurityAttributes(
            ctypes.sizeof(SecurityAttributes), security_descriptor, False,
        )
        handle = kernel32.CreateMutexW(
            ctypes.byref(attributes), False, windows_acquisition_mutex_name(target),
        )
    finally:
        kernel32.LocalFree(security_descriptor)
    if not handle:
        raise SessionControlError(
            "Não foi possível abrir o mutex da instalação "
            f"({ctypes.get_last_error()})."
        )
    try:
        windows_acl.validate_private_kernel_object(int(handle))
    except OSError as error:
        kernel32.CloseHandle(handle)
        raise SessionControlError(
            "O mutex global da instalação não possui uma DACL privada comprovável."
        ) from error
    acquired = False
    try:
        wait_object_0 = 0
        wait_abandoned = 0x80
        wait_timeout = 0x102
        while True:
            result = int(kernel32.WaitForSingleObject(handle, 250))
            if result in {wait_object_0, wait_abandoned}:
                acquired = True
                break
            if result != wait_timeout:
                raise SessionControlError(
                    "Não foi possível adquirir o mutex da instalação "
                    f"({ctypes.get_last_error()})."
                )
        yield
    finally:
        if acquired:
            kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


@contextmanager
def installation_acquisition_mutex(target: Path, sessions: Path) -> Iterator[None]:
    """Serialize observation, reclaim, and replacement of ``active.lock``."""

    if os.name == "nt":
        with windows_acquisition_mutex(target):
            yield
        return

    import fcntl

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(sessions, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise SessionControlError(f"Diretório de sessões inseguro: {sessions}")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


__all__ = [
    "SessionControlError",
    "installation_acquisition_mutex",
    "windows_acquisition_mutex",
    "windows_acquisition_mutex_name",
]
