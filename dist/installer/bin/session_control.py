#!/usr/bin/env python3
"""Cross-platform exclusive operation lock for one x86QW installation."""

from __future__ import annotations

import ctypes
import json
import os
import secrets
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


SERVICE_COMMANDS = frozenset({"host", "proxy", "qtv"})
MAINTENANCE_COMMANDS = frozenset({
    "install", "components", "presets", "update", "upgrade", "repair",
    "cleanup", "uninstall", "purge",
})
LOCK_COMMANDS = SERVICE_COMMANDS | MAINTENANCE_COMMANDS


class SessionControlError(RuntimeError):
    """A lock cannot be acquired or safely reconciled."""


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


def lexists(path: Path) -> bool:
    return os.path.lexists(path)


def new_session_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{secrets.token_hex(6)}"


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
            return ProcessProbe("inconclusive", detail="ps não confirmou o início do processo")
        command = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="], check=False,
            capture_output=True, text=True, timeout=2, env=environment,
        )
        if command.returncode != 0 or not command.stdout.strip():
            return ProcessProbe("inconclusive", detail="ps não confirmou o executável do processo")
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
        wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
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
        creation, exit_time, kernel, user = FileTime(), FileTime(), FileTime(), FileTime()
        if not kernel32.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exit_time),
            ctypes.byref(kernel), ctypes.byref(user),
        ):
            return ProcessProbe("inconclusive", detail="GetProcessTimes falhou")
        capacity = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(capacity.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(capacity)):
            return ProcessProbe("inconclusive", detail="QueryFullProcessImageNameW falhou")
        created = (int(creation.high) << 32) | int(creation.low)
        executable = os.path.normcase(os.path.realpath(buffer.value))
        return ProcessProbe("alive", ProcessIdentity(pid, f"windows:{created}", executable))
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
    return ProcessProbe("inconclusive", detail="plataforma sem identidade de processo confiável")


def probe_expected_process(pid: int, creation_token: str, executable: str) -> ProcessProbe:
    actual = process_identity(pid)
    if actual.status != "alive" or actual.identity is None:
        return actual
    actual_executable = os.path.normcase(os.path.realpath(actual.identity.executable))
    expected_executable = os.path.normcase(os.path.realpath(executable))
    if actual.identity.creation_token != creation_token or actual_executable != expected_executable:
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


def read_lock_owner(path: Path) -> dict[str, object]:
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 65536:
            raise ValueError("lock inseguro")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise SessionControlError(
            f"Lock de operação inválido foi preservado para inspeção: {path}"
        ) from error
    if not isinstance(data, dict):
        raise SessionControlError(f"Lock de operação inválido foi preservado para inspeção: {path}")
    common = {
        "format", "project", "session_id", "controller_pid", "controller_start_token",
        "controller_executable", "created_at", "installation", "command",
    }
    expected = common if data.get("format") == 1 else common | {"operation_kind"}
    valid_kind = (
        data.get("format") == 1 and data.get("command") in SERVICE_COMMANDS
        or data.get("format") == 2
        and data.get("operation_kind") in {"service", "maintenance"}
        and data.get("command") in LOCK_COMMANDS
    )
    if (
        set(data) != expected
        or data.get("project") != "x86qw"
        or not valid_kind
        or not isinstance(data.get("controller_pid"), int)
        or not all(
            isinstance(data.get(field), str) and data.get(field)
            for field in expected - {"format", "controller_pid"}
        )
    ):
        raise SessionControlError(f"Lock de operação inválido foi preservado para inspeção: {path}")
    return data


def _ensure_private_directory(path: Path, created: list[Path]) -> None:
    if lexists(path):
        if path.is_symlink() or not path.is_dir():
            raise SessionControlError(f"Diretório de sessões ausente ou inseguro: {path}")
    else:
        try:
            path.mkdir(mode=0o700, parents=True)
            created.append(path)
        except FileExistsError:
            if path.is_symlink() or not path.is_dir():
                raise SessionControlError(f"Diretório de sessões ausente ou inseguro: {path}")
    if os.name != "nt":
        path.chmod(0o700)


class InstallationLock:
    """Atomic lock shared by service stacks and installation maintenance."""

    def __init__(
        self,
        target: Path,
        path: Path,
        owner: dict[str, object],
        *,
        reclaimed_path: Path | None = None,
        created_directories: tuple[Path, ...] = (),
    ) -> None:
        self.target = target
        self.path = path
        self.owner = owner
        self.session_id = str(owner["session_id"])
        self.reclaimed_path = reclaimed_path
        self.created_directories = created_directories

    @classmethod
    def acquire(
        cls, target: Path, command: str, operation_kind: str | None = None,
    ) -> "InstallationLock":
        if command not in LOCK_COMMANDS:
            raise SessionControlError(f"Operação não participa do lock x86QW: {command}")
        kind = operation_kind or ("service" if command in SERVICE_COMMANDS else "maintenance")
        if kind not in {"service", "maintenance"}:
            raise SessionControlError(f"Tipo de operação inválido: {kind}")
        target = target.expanduser().resolve(strict=False)
        created: list[Path] = []
        target_preexisting = lexists(target)
        if not target_preexisting:
            try:
                target.mkdir(mode=0o700, parents=True)
                created.append(target)
            except FileExistsError:
                if target.is_symlink() or not target.is_dir():
                    raise SessionControlError(f"Destino ausente ou inseguro: {target}")
        sessions = target / ".x86qw" / "sessions"
        _ensure_private_directory(sessions.parent, created)
        _ensure_private_directory(sessions, created)
        identity_probe = process_identity(os.getpid())
        if identity_probe.status != "alive" or identity_probe.identity is None:
            for directory in reversed(created):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise SessionControlError(
                "Não foi possível confirmar a identidade desta CLI; nenhuma sessão foi recuperada."
            )
        identity = identity_probe.identity
        session_id = new_session_id()
        owner: dict[str, object] = {
            "format": 2,
            "project": "x86qw",
            "session_id": session_id,
            "operation_kind": kind,
            "command": command,
            "controller_pid": identity.pid,
            "controller_start_token": identity.creation_token,
            "controller_executable": identity.executable,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "installation": str(target),
        }
        path = sessions / "active.lock"
        reclaimed_path: Path | None = None
        while True:
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                existing = read_lock_owner(path)
                if existing["installation"] != str(target):
                    raise SessionControlError(f"Lock de operação pertence a outra instalação: {path}")
                probe = probe_expected_process(
                    int(existing["controller_pid"]),
                    str(existing["controller_start_token"]),
                    str(existing["controller_executable"]),
                )
                if probe.status == "alive":
                    raise SessionControlError(
                        "Existe uma operação x86QW ativa nesta instalação.\n"
                        f"Operação: {existing['command']}\n"
                        f"Sessão: {existing['session_id']}\n"
                        f"Controlador: PID {existing['controller_pid']}\n\n"
                        "Encerre a operação antes de modificar o conteúdo instalado."
                    )
                if probe.status == "inconclusive":
                    raise SessionControlError(
                        "Não foi possível confirmar se a operação x86QW existente terminou; "
                        f"lock e journals foram preservados em {sessions}."
                    )
                candidate = sessions / f".active.lock.reclaimed.{session_id}"
                try:
                    os.replace(path, candidate)
                except FileNotFoundError:
                    continue
                reclaimed_path = candidate
                continue
            try:
                payload = (
                    json.dumps(owner, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                ).encode("utf-8")
                os.write(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            if os.name != "nt":
                path.chmod(0o600)
            return cls(
                target, path, owner, reclaimed_path=reclaimed_path,
                created_directories=tuple(created),
            )

    def confirm_recovery(self) -> None:
        if self.reclaimed_path is not None and lexists(self.reclaimed_path):
            self.reclaimed_path.unlink()
        self.reclaimed_path = None

    def release(self, *, restore_reclaimed: bool = False) -> None:
        if lexists(self.path):
            current = read_lock_owner(self.path)
            if current.get("session_id") == self.session_id:
                self.path.unlink()
        if self.reclaimed_path is not None and lexists(self.reclaimed_path):
            if restore_reclaimed and not lexists(self.path):
                os.replace(self.reclaimed_path, self.path)
            else:
                self.reclaimed_path.unlink()
        self.reclaimed_path = None
        for directory in reversed(self.created_directories):
            try:
                directory.rmdir()
            except OSError:
                pass
