#!/usr/bin/env python3
"""Cross-platform exclusive operation lock for one x86QW installation."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
import stat
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from x86qw_runtime.io import private_fs


SERVICE_COMMANDS = frozenset({"host", "proxy", "qtv"})
MAINTENANCE_COMMANDS = frozenset({
    "install", "components", "presets", "update", "upgrade", "repair",
    "cleanup", "uninstall", "purge",
})
LOCK_COMMANDS = SERVICE_COMMANDS | MAINTENANCE_COMMANDS
_LEGACY_ACL_MIGRATED = "_x86qw_legacy_acl_migrated"


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


def _windows_acquisition_mutex_name(target: Path) -> str:
    normalized = os.path.normcase(os.path.abspath(os.fspath(target)))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    # The global namespace coordinates Terminal Services sessions. Windows
    # requires SeCreateGlobalPrivilege for global file mappings, not mutexes.
    return f"Global\\x86QW-install-{digest}"


@contextmanager
def _windows_acquisition_mutex(target: Path) -> Iterator[None]:
    """Serialize the stale-lock transition without leaving filesystem state."""
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
        wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.ULONG),
    ]
    convert_sddl.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    kernel32.CreateMutexW.argtypes = [
        ctypes.POINTER(SecurityAttributes), wintypes.BOOL, wintypes.LPCWSTR,
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
        sddl, 1, ctypes.byref(security_descriptor), ctypes.byref(descriptor_size),
    ):
        raise SessionControlError(
            f"Não foi possível proteger o mutex da instalação ({ctypes.get_last_error()})."
        )
    try:
        attributes = SecurityAttributes(
            ctypes.sizeof(SecurityAttributes), security_descriptor, False,
        )
        handle = kernel32.CreateMutexW(
            ctypes.byref(attributes), False, _windows_acquisition_mutex_name(target),
        )
    finally:
        kernel32.LocalFree(security_descriptor)
    if not handle:
        raise SessionControlError(
            f"Não foi possível abrir o mutex da instalação ({ctypes.get_last_error()})."
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
                    f"Não foi possível adquirir o mutex da instalação ({ctypes.get_last_error()})."
                )
        yield
    finally:
        if acquired:
            kernel32.ReleaseMutex(handle)
        kernel32.CloseHandle(handle)


@contextmanager
def _installation_acquisition_mutex(target: Path, sessions: Path) -> Iterator[None]:
    """Serialize observation, reclaim and replacement of ``active.lock``."""
    if os.name == "nt":
        with _windows_acquisition_mutex(target):
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


def read_lock_owner(path: Path) -> dict[str, object]:
    def approved_legacy(payload: bytes) -> bool:
        try:
            candidate = json.loads(payload.decode("utf-8"))
        except (UnicodeError, ValueError, json.JSONDecodeError):
            return False
        return (
            isinstance(candidate, dict)
            and type(candidate.get("format")) is int
            and candidate.get("format") in {1, 2}
            and "private_filesystem" not in candidate
        )

    try:
        payload, migrated_legacy_acl = (
            private_fs.read_private_file_with_legacy_windows_migration(
                path, maximum_size=65536, approve_legacy=approved_legacy,
            )
        )
        data = json.loads(payload.decode("utf-8"))
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
    lock_format = data.get("format")
    expected = set(common)
    if type(lock_format) is int and lock_format in {2, 3}:
        expected |= {"operation_kind"}
    if type(lock_format) is int and lock_format == 3:
        expected |= {"private_filesystem"}
    valid_kind = type(lock_format) is int and (
        lock_format == 1 and data.get("command") in SERVICE_COMMANDS
        or lock_format == 2
        and data.get("operation_kind") in {"service", "maintenance"}
        and data.get("command") in LOCK_COMMANDS
        or lock_format == 3
        and type(data.get("private_filesystem")) is int
        and data.get("private_filesystem") == 1
        and data.get("operation_kind") in {"service", "maintenance"}
        and data.get("command") in LOCK_COMMANDS
    )
    if (
        set(data) != expected
        or data.get("project") != "x86qw"
        or not valid_kind
        or type(data.get("controller_pid")) is not int
        or not all(
            isinstance(data.get(field), str) and data.get(field)
            for field in expected - {"format", "controller_pid", "private_filesystem"}
        )
    ):
        raise SessionControlError(f"Lock de operação inválido foi preservado para inspeção: {path}")
    if migrated_legacy_acl:
        data[_LEGACY_ACL_MIGRATED] = True
    if os.name == "nt" and lock_format < 3:
        # A pre-contract lock can be made private for inspection, but its
        # historical bytes never become authoritative on a later read.
        data[_LEGACY_ACL_MIGRATED] = True
    return data


def _ensure_private_directory(path: Path, created: list[Path]) -> None:
    existed = lexists(path)
    try:
        private_fs.ensure_private_directory(path)
    except OSError as error:
        raise SessionControlError(f"Diretório de sessões ausente ou inseguro: {path}") from error
    if not existed:
        created.append(path)


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
        control_lease: object | None = None,
    ) -> None:
        self.target = target
        self.path = path
        self.owner = owner
        self.session_id = str(owner["session_id"])
        self.reclaimed_path = reclaimed_path
        self.created_directories = created_directories
        self.control_lease = control_lease

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
            "format": 3,
            "project": "x86qw",
            "session_id": session_id,
            "operation_kind": kind,
            "command": command,
            "controller_pid": identity.pid,
            "controller_start_token": identity.creation_token,
            "controller_executable": identity.executable,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "installation": str(target),
            "private_filesystem": 1,
        }
        path = sessions / "active.lock"
        try:
            with _installation_acquisition_mutex(target, sessions):
                return cls._acquire_serialized(
                    target, sessions, path, owner, created,
                )
        except BaseException:
            for directory in reversed(created):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise

    @classmethod
    def _acquire_serialized(
        cls,
        target: Path,
        sessions: Path,
        path: Path,
        owner: dict[str, object],
        created: list[Path],
    ) -> "InstallationLock":
        session_id = str(owner["session_id"])
        reclaimed_path: Path | None = None
        while True:
            try:
                descriptor = private_fs.create_private_file(path)
            except FileExistsError:
                existing = read_lock_owner(path)
                if existing.get(_LEGACY_ACL_MIGRATED) is True:
                    raise SessionControlError(
                        "Lock legado do Windows foi protegido, mas seu conteúdo histórico "
                        f"não autoriza recuperação automática. Inspecione e encerre a operação em {path}."
                    )
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
            except OSError as error:
                for directory in reversed(created):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
                raise SessionControlError(
                    f"Não foi possível criar o lock privado da instalação: {path}"
                ) from error
            created_identity: tuple[int, int] | None = None
            failure: OSError | None = None
            try:
                metadata = os.fstat(descriptor)
                created_identity = (int(metadata.st_dev), int(metadata.st_ino))
                payload = (
                    json.dumps(owner, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                ).encode("utf-8")
                pending = memoryview(payload)
                while pending:
                    written = os.write(descriptor, pending)
                    if written <= 0:
                        raise OSError("gravação incompleta do lock")
                    pending = pending[written:]
                os.fsync(descriptor)
            except OSError as error:
                failure = error
            finally:
                try:
                    os.close(descriptor)
                except OSError as error:
                    if failure is None:
                        failure = error
            if failure is None:
                try:
                    private_fs.validate_private_file(path)
                except OSError as error:
                    failure = error
            if failure is not None:
                cleanup_detail = ""
                if created_identity is None:
                    cleanup_detail = (
                        " O lock privado foi preservado porque sua identidade "
                        "não pôde ser comprovada."
                    )
                else:
                    try:
                        private_fs.unlink_private_file(
                            path, expected_identity=created_identity,
                        )
                    except OSError as cleanup_error:
                        cleanup_detail = (
                            " O lock privado não pôde ser removido e foi preservado "
                            f"para inspeção ({cleanup_error})."
                        )
                for directory in reversed(created):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
                raise SessionControlError(
                    "Lock de operação não pôde ser gravado e validado com segurança: "
                    f"{path}.{cleanup_detail}"
                ) from failure
            try:
                control_lease = private_fs.hold_private_path(
                    sessions.parent, directory=True,
                )
            except OSError as error:
                cleanup_detail = ""
                try:
                    private_fs.unlink_private_file(
                        path, expected_identity=created_identity,
                    )
                except OSError as cleanup_error:
                    cleanup_detail = (
                        " O lock privado foi preservado para inspeção "
                        f"({cleanup_error})."
                    )
                if reclaimed_path is not None and lexists(reclaimed_path):
                    try:
                        if lexists(path):
                            raise OSError("o novo lock ainda ocupa o caminho ativo")
                        os.replace(reclaimed_path, path)
                        reclaimed_path = None
                    except OSError as restore_error:
                        cleanup_detail += (
                            " O lock anterior também não pôde ser restaurado "
                            f"({restore_error})."
                        )
                raise SessionControlError(
                    "A raiz privada da instalação não pôde ser protegida durante o uso: "
                    f"{sessions.parent}.{cleanup_detail}"
                ) from error
            return cls(
                target, path, owner, reclaimed_path=reclaimed_path,
                created_directories=tuple(created),
                control_lease=control_lease,
            )

    def confirm_recovery(self) -> None:
        if self.reclaimed_path is not None and lexists(self.reclaimed_path):
            private_fs.unlink_private_file(self.reclaimed_path)
        self.reclaimed_path = None

    def release(self, *, restore_reclaimed: bool = False) -> None:
        try:
            if lexists(self.path):
                current = read_lock_owner(self.path)
                if current.get("session_id") == self.session_id:
                    private_fs.unlink_private_file(self.path)
            if self.reclaimed_path is not None and lexists(self.reclaimed_path):
                if restore_reclaimed and not lexists(self.path):
                    os.replace(self.reclaimed_path, self.path)
                else:
                    private_fs.unlink_private_file(self.reclaimed_path)
            self.reclaimed_path = None
        finally:
            lease = self.control_lease
            self.control_lease = None
            try:
                if lease is not None:
                    lease.close()
            finally:
                for directory in reversed(self.created_directories):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
