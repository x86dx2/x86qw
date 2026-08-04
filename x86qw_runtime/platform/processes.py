"""Native process identity and termination adapters used by locks and services."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    creation_token: str
    executable: str


@dataclass(frozen=True)
class ProcessProbe:
    status: str
    identity: ProcessIdentity | None = None
    detail: str = ""


def _linux_process_identity(pid: int) -> ProcessProbe:
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        closing = stat_text.rfind(")")
        fields = stat_text[closing + 2:].split() if closing >= 0 else []
        if len(fields) <= 19:
            return ProcessProbe("inconclusive", detail="/proc stat incompleto")
        if fields[0] == "Z":
            return ProcessProbe("dead")
        boot_id_path = Path("/proc/sys/kernel/random/boot_id")
        boot_id = (
            boot_id_path.read_text(encoding="ascii").strip()
            if boot_id_path.is_file() else "boot-unknown"
        )
        executable = os.path.realpath(os.readlink(f"/proc/{pid}/exe"))
        return ProcessProbe(
            "alive", ProcessIdentity(pid, f"linux:{boot_id}:{fields[19]}", executable),
        )
    except FileNotFoundError:
        return ProcessProbe("dead")
    except (OSError, UnicodeError, ValueError) as error:
        return ProcessProbe("inconclusive", detail=str(error))


def _macos_process_identity(pid: int) -> ProcessProbe:
    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    try:
        started = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="], check=False,
            capture_output=True, text=True, timeout=2, env=environment,
        )
        if started.returncode == 1 and not started.stdout.strip():
            return ProcessProbe("dead")
        if started.returncode != 0 or not started.stdout.strip():
            return ProcessProbe(
                "inconclusive", detail="ps não confirmou o início do processo",
            )
        command = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="], check=False,
            capture_output=True, text=True, timeout=2, env=environment,
        )
        if command.returncode != 0 or not command.stdout.strip():
            return ProcessProbe(
                "inconclusive", detail="ps não confirmou o executável do processo",
            )
        identity = ProcessIdentity(
            pid,
            "macos:" + " ".join(started.stdout.split()),
            os.path.realpath(command.stdout.strip()),
        )
        return ProcessProbe("alive", identity)
    except (OSError, subprocess.SubprocessError) as error:
        return ProcessProbe("inconclusive", detail=str(error))


def _windows_kernel32():
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p,
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _windows_process_identity(pid: int) -> ProcessProbe:
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    invalid_parameter = 87

    class FileTime(ctypes.Structure):
        _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))

    kernel32 = _windows_kernel32()
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == invalid_parameter:
            return ProcessProbe("dead")
        return ProcessProbe("inconclusive", detail=f"OpenProcess falhou ({error})")
    try:
        creation, exit_time, kernel, user = (
            FileTime(), FileTime(), FileTime(), FileTime(),
        )
        if not kernel32.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exit_time),
            ctypes.byref(kernel), ctypes.byref(user),
        ):
            return ProcessProbe("inconclusive", detail="GetProcessTimes falhou")
        if int(exit_time.high) != 0 or int(exit_time.low) != 0:
            return ProcessProbe("dead")
        capacity = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(capacity.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(capacity),
        ):
            return ProcessProbe(
                "inconclusive", detail="QueryFullProcessImageNameW falhou",
            )
        created = (int(creation.high) << 32) | int(creation.low)
        executable = os.path.normcase(os.path.realpath(buffer.value))
        return ProcessProbe(
            "alive", ProcessIdentity(pid, f"windows:{created}", executable),
        )
    finally:
        kernel32.CloseHandle(handle)


def process_identity(pid: int) -> ProcessProbe:
    if pid <= 0:
        return ProcessProbe("dead")
    if sys.platform.startswith("linux"):
        return _linux_process_identity(pid)
    if sys.platform == "darwin":
        return _macos_process_identity(pid)
    if os.name == "nt":
        return _windows_process_identity(pid)
    return ProcessProbe(
        "inconclusive", detail="plataforma sem identidade de processo confiável",
    )


def probe_expected_process(
    pid: int, creation_token: str, executable: str,
) -> ProcessProbe:
    actual = process_identity(pid)
    if actual.status != "alive" or actual.identity is None:
        return actual
    actual_executable = os.path.normcase(os.path.realpath(actual.identity.executable))
    expected_executable = os.path.normcase(os.path.realpath(executable))
    if (
        actual.identity.creation_token != creation_token
        or actual_executable != expected_executable
    ):
        return ProcessProbe("identity_mismatch", actual.identity)
    return actual


def terminate_windows_process(pid: int, exit_code: int) -> bool:
    process_terminate = 0x0001
    kernel32 = _windows_kernel32()
    handle = kernel32.OpenProcess(process_terminate, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, exit_code))
    finally:
        kernel32.CloseHandle(handle)
