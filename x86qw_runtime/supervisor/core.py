"""Cross-platform process-tree ownership for supervised x86QW services."""

from __future__ import annotations

import ctypes
import errno
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from x86qw_runtime.errors import InstallerError

from .models import ProcessSpec, ServiceReadiness, StartupRcon
from .readiness import apply_startup_rcon, wait_http_readiness, wait_udp_readiness


POPEN_TYPE = subprocess.Popen


class Reporter(Protocol):
    def detail(self, message: str) -> None: ...
    def info(self, message: str) -> None: ...
    def warning(self, message: str) -> None: ...


class Journal(Protocol):
    def record_process(
        self, spec: ProcessSpec, process: Any, process_group: int,
    ) -> None: ...
    def set_status(self, status: str) -> None: ...
    def consume_stop_request(self) -> bool: ...


class ServiceSignal(Exception):
    def __init__(self, signum: int) -> None:
        self.signum = signum


def stop_processes(processes: list[subprocess.Popen[bytes]]) -> None:
    groups: list[int] = []
    for process in reversed(processes):
        if os.name != "nt" and isinstance(process, POPEN_TYPE):
            process_group = int(getattr(process, "_x86qw_process_group", process.pid))
            if process_group > 1 and process_group not in groups:
                groups.append(process_group)
            status = posix_process_group_status(process_group)
            if status == "inconclusive":
                raise InstallerError(
                    f"Não foi possível confirmar o grupo de processos {process_group}; estado preservado."
                )
            if status == "alive":
                try:
                    os.killpg(process_group, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except OSError as error:
                    raise InstallerError(
                        f"Não foi possível encerrar o grupo de processos {process_group}."
                    ) from error
        elif process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
    deadline = time.monotonic() + 4
    for process in reversed(processes):
        if process.poll() is None:
            try:
                process.wait(max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                if os.name == "nt" or not isinstance(process, POPEN_TYPE):
                    try:
                        process.kill()
                        process.wait()
                    except OSError:
                        pass
    for process_group in groups:
        status = posix_process_group_status(process_group)
        if status == "inconclusive":
            raise InstallerError(
                f"Não foi possível confirmar o encerramento do grupo {process_group}."
            )
        if status == "alive":
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                continue
            except OSError as error:
                raise InstallerError(
                    f"Não foi possível forçar o encerramento do grupo {process_group}."
                ) from error
            group_deadline = time.monotonic() + 1.0
            while time.monotonic() < group_deadline:
                if posix_process_group_status(process_group) == "dead":
                    break
                time.sleep(0.05)
            else:
                raise InstallerError(
                    f"O grupo de processos {process_group} permaneceu ativo após SIGKILL."
                )


def posix_process_group_status(process_group: int) -> str:
    if sys.platform.startswith("linux"):
        proc = Path("/proc")
        try:
            members = False
            for candidate in proc.iterdir():
                if not candidate.name.isdigit():
                    continue
                try:
                    stat_text = (candidate / "stat").read_text(encoding="ascii")
                    closing = stat_text.rfind(")")
                    fields = stat_text[closing + 2:].split() if closing >= 0 else []
                    if len(fields) > 2 and int(fields[2]) == process_group:
                        members = True
                        if fields[0] != "Z":
                            return "alive"
                except (FileNotFoundError, ProcessLookupError):
                    continue
                except (OSError, UnicodeError, ValueError):
                    return "inconclusive"
            if members:
                return "dead"
        except (OSError, UnicodeError):
            return "inconclusive"
    try:
        os.killpg(process_group, 0)
        return "alive"
    except ProcessLookupError:
        return "dead"
    except PermissionError:
        return "inconclusive"
    except OSError as error:
        if error.errno == errno.ESRCH:
            return "dead"
        if error.errno == errno.EPERM:
            return "inconclusive"
        return "inconclusive"


def _windows_job_kernel32():
    from ctypes import wintypes

    class ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD),
            ("usage", wintypes.DWORD),
            ("thread_id", wintypes.DWORD),
            ("owner_process_id", wintypes.DWORD),
            ("base_priority", wintypes.LONG),
            ("delta_priority", wintypes.LONG),
            ("flags", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32._x86qw_thread_entry_type = ThreadEntry32
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry32)]
    kernel32.Thread32First.restype = wintypes.BOOL
    kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry32)]
    kernel32.Thread32Next.restype = wintypes.BOOL
    kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


class WindowsJobObject:
    """Own a Windows process tree and terminate it when the controller closes."""

    def __init__(self, reporter: Reporter | None = None) -> None:
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("read_operations", ctypes.c_ulonglong),
                ("write_operations", ctypes.c_ulonglong),
                ("other_operations", ctypes.c_ulonglong),
                ("read_bytes", ctypes.c_ulonglong),
                ("write_bytes", ctypes.c_ulonglong),
                ("other_bytes", ctypes.c_ulonglong),
            ]

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("per_process_user_time", ctypes.c_longlong),
                ("per_job_user_time", ctypes.c_longlong),
                ("limit_flags", wintypes.DWORD),
                ("minimum_working_set", ctypes.c_size_t),
                ("maximum_working_set", ctypes.c_size_t),
                ("active_process_limit", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority_class", wintypes.DWORD),
                ("scheduling_class", wintypes.DWORD),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimitInformation),
                ("io", IoCounters),
                ("process_memory_limit", ctypes.c_size_t),
                ("job_memory_limit", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t),
            ]

        self.reporter = reporter
        self.kernel32 = _windows_job_kernel32()
        self.handle = self.kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise InstallerError(
                f"Não foi possível criar o Job Object dos serviços ({ctypes.get_last_error()})."
            )
        information = ExtendedLimitInformation()
        information.basic.limit_flags = 0x00002000
        if not self.kernel32.SetInformationJobObject(
            self.handle, 9, ctypes.byref(information), ctypes.sizeof(information),
        ):
            error = ctypes.get_last_error()
            self.kernel32.CloseHandle(self.handle)
            self.handle = None
            raise InstallerError(f"Não foi possível configurar o Job Object ({error}).")

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        if self.handle is None or not self.kernel32.AssignProcessToJobObject(
            self.handle, int(process._handle),  # type: ignore[attr-defined]
        ):
            raise InstallerError(
                f"Não foi possível associar PID {process.pid} ao Job Object "
                f"({ctypes.get_last_error()})."
            )

    def resume(self, process: subprocess.Popen[bytes]) -> None:
        entry_type = self.kernel32._x86qw_thread_entry_type
        invalid_handle = ctypes.c_void_p(-1).value
        snapshot = self.kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
        if not snapshot or snapshot == invalid_handle:
            raise InstallerError(
                f"Não foi possível enumerar a thread inicial do PID {process.pid} "
                f"({ctypes.get_last_error()})."
            )
        thread_ids: list[int] = []
        enumeration_error: InstallerError | None = None
        try:
            entry = entry_type()
            entry.size = ctypes.sizeof(entry_type)
            if self.kernel32.Thread32First(snapshot, ctypes.byref(entry)):
                while True:
                    if int(entry.owner_process_id) == process.pid:
                        thread_ids.append(int(entry.thread_id))
                    entry.size = ctypes.sizeof(entry_type)
                    if not self.kernel32.Thread32Next(snapshot, ctypes.byref(entry)):
                        error = ctypes.get_last_error()
                        if error != 18:
                            enumeration_error = InstallerError(
                                f"Não foi possível enumerar a thread inicial do PID "
                                f"{process.pid} ({error})."
                            )
                        break
            else:
                error = ctypes.get_last_error()
                if error != 18:
                    enumeration_error = InstallerError(
                        f"Não foi possível enumerar a thread inicial do PID "
                        f"{process.pid} ({error})."
                    )
        finally:
            if not self.kernel32.CloseHandle(snapshot) and enumeration_error is None:
                enumeration_error = InstallerError(
                    f"Não foi possível fechar o snapshot de threads "
                    f"({ctypes.get_last_error()})."
                )
        if enumeration_error is not None:
            raise enumeration_error
        if len(thread_ids) != 1:
            raise InstallerError(
                f"A thread inicial suspensa do PID {process.pid} não pôde ser "
                "identificada de forma inequívoca."
            )
        thread = self.kernel32.OpenThread(0x0002, False, thread_ids[0])
        if not thread:
            raise InstallerError(
                f"Não foi possível abrir a thread inicial do PID {process.pid} "
                f"({ctypes.get_last_error()})."
            )
        resume_error: InstallerError | None = None
        try:
            previous_count = int(self.kernel32.ResumeThread(thread))
            if previous_count == 0xFFFFFFFF:
                resume_error = InstallerError(
                    f"Não foi possível retomar o PID {process.pid} "
                    f"({ctypes.get_last_error()})."
                )
            elif previous_count != 1:
                resume_error = InstallerError(
                    f"O PID {process.pid} apresentou contagem de suspensão "
                    f"inesperada ({previous_count})."
                )
        finally:
            if not self.kernel32.CloseHandle(thread) and resume_error is None:
                resume_error = InstallerError(
                    f"Não foi possível fechar a thread inicial do PID {process.pid} "
                    f"({ctypes.get_last_error()})."
                )
        if resume_error is not None:
            raise resume_error

    def _rollback_failed_start(
        self, process: subprocess.Popen[bytes], *, assigned: bool,
    ) -> None:
        process_handle = int(process._handle)  # type: ignore[attr-defined]
        if assigned:
            if self.handle is None or not self.kernel32.TerminateJobObject(self.handle, 1):
                raise InstallerError(
                    f"Não foi possível reverter a árvore do PID {process.pid} "
                    f"({ctypes.get_last_error()})."
                )
        elif not self.kernel32.TerminateProcess(process_handle, 1):
            raise InstallerError(
                f"Não foi possível reverter o PID suspenso {process.pid} "
                f"({ctypes.get_last_error()})."
            )
        result = int(self.kernel32.WaitForSingleObject(process_handle, 4000))
        if result != 0:
            raise InstallerError(
                f"O PID {process.pid} permaneceu ativo após a reversão "
                f"(resultado {result})."
            )
        poll = getattr(process, "poll", None)
        if callable(poll):
            poll()

    def start_process(
        self, arguments: tuple[str, ...], cwd: Path,
    ) -> subprocess.Popen[bytes]:
        creation_flags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)
        )
        process: subprocess.Popen[bytes] | None = None
        assigned = False
        try:
            process = subprocess.Popen(
                arguments,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
            self.assign(process)
            assigned = True
            self.resume(process)
            return process
        except BaseException as error:
            if process is None:
                raise
            try:
                self._rollback_failed_start(process, assigned=assigned)
            except Exception as cleanup_error:
                reporter = getattr(self, "reporter", None)
                if reporter is not None:
                    reporter.warning(
                        f"Falha ao reverter PID {process.pid} após startup recusado: "
                        f"{cleanup_error}"
                    )
                if isinstance(error, Exception):
                    raise InstallerError(
                        f"{error} A reversão segura também falhou: {cleanup_error}"
                    ) from error
            raise

    def close(self) -> None:
        if self.handle is None:
            return
        handle = self.handle
        termination_error = 0
        if not self.kernel32.TerminateJobObject(handle, 1):
            termination_error = ctypes.get_last_error()
        if not self.kernel32.CloseHandle(handle):
            close_error = ctypes.get_last_error()
            detail = (
                f"; encerramento explícito também falhou ({termination_error})"
                if termination_error else ""
            )
            raise InstallerError(
                f"Não foi possível fechar o Job Object ({close_error}{detail})."
            )
        self.handle = None
        if termination_error:
            raise InstallerError(
                "O Job Object foi fechado, mas o encerramento explícito da árvore "
                f"falhou ({termination_error})."
            )


def run_processes(
    specs: list[ProcessSpec],
    journal: Journal | None = None,
    *,
    reporter: Reporter | None = None,
    process_factory: Callable[..., Any] | None = None,
    windows_job_factory: Callable[[Reporter | None], Any] | None = None,
    signal_setter: Callable[[int, Any], Any] = signal.signal,
    stopper: Callable[[list[Any]], None] = stop_processes,
    os_name: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    apply_rcon: Callable[[StartupRcon], None] = apply_startup_rcon,
    http_readiness: Callable[[Any, ServiceReadiness], None] = wait_http_readiness,
    udp_readiness: Callable[[Any, ServiceReadiness], None] = wait_udp_readiness,
) -> int:
    processes: list[Any] = []
    previous_handlers: dict[int, Any] = {}
    spawn = subprocess.Popen if process_factory is None else process_factory
    active_os_name = os.name if os_name is None else os_name
    windows_job = None
    if active_os_name == "nt":
        create_job = (
            (lambda selected_reporter: WindowsJobObject(reporter=selected_reporter))
            if windows_job_factory is None else windows_job_factory
        )
        windows_job = create_job(reporter)

    def interrupted(signum: int, _frame: object) -> None:
        raise ServiceSignal(signum)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                previous_handlers[signum] = signal_setter(signum, interrupted)
            except (ValueError, OSError):
                pass
        for spec in specs:
            if reporter is not None:
                reporter.detail(f"Iniciando {spec.label}: {spec.arguments[0]}")
            if windows_job is not None:
                process = windows_job.start_process(spec.arguments, spec.cwd)
            else:
                process = spawn(
                    spec.arguments,
                    cwd=spec.cwd,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            process_group = process.pid
            setattr(process, "_x86qw_process_group", process_group)
            processes.append(process)
            if journal is not None:
                journal.record_process(spec, process, process_group)
            if spec.startup_rcon is not None:
                apply_rcon(spec.startup_rcon)
                if reporter is not None:
                    reporter.detail(
                        "MVDSV pronto; configuração pós-map aplicada e senha RCON restaurada."
                    )
            if spec.readiness is not None:
                if spec.readiness.kind == "http":
                    http_readiness(process, spec.readiness)
                    if reporter is not None:
                        reporter.detail("QTV pronto e respondendo por HTTP.")
                elif spec.readiness.kind == "udp":
                    udp_readiness(process, spec.readiness)
                    if reporter is not None:
                        reporter.detail("QWFWD pronto e mantendo a porta UDP.")
        if journal is not None:
            journal.set_status("running")
        while True:
            if journal is not None and journal.consume_stop_request():
                if reporter is not None:
                    reporter.info("Encerramento solicitado pelo gerenciador x86QW…")
                return 0
            for spec, process in zip(specs, processes):
                code = process.poll()
                if code is not None:
                    if code != 0 and reporter is not None:
                        reporter.warning(f"{spec.label} encerrou com código {code}.")
                    return code
            sleep(0.1)
    except (KeyboardInterrupt, ServiceSignal) as error:
        if reporter is not None:
            reporter.info("Encerrando serviços x86QW…")
        if journal is not None:
            journal.set_status("interrupted")
        signum = error.signum if isinstance(error, ServiceSignal) else signal.SIGINT
        return 128 + int(signum)
    except OSError as error:
        raise InstallerError(f"Não foi possível iniciar um serviço: {error}") from error
    finally:
        original_error = sys.exc_info()[0] is not None
        finalization_errors: list[Exception] = []
        try:
            stopper(processes)
        except Exception as error:
            finalization_errors.append(error)
        try:
            if windows_job is not None:
                windows_job.close()
        except Exception as error:
            finalization_errors.append(error)
        for signum, handler in previous_handlers.items():
            try:
                signal_setter(signum, handler)
            except (ValueError, OSError) as error:
                finalization_errors.append(error)
        if reporter is not None:
            for error in finalization_errors:
                reporter.warning(f"Falha ao finalizar árvore de processos: {error}")
        if finalization_errors and not original_error:
            first = finalization_errors[0]
            if isinstance(first, InstallerError):
                raise first
            raise InstallerError("Falha ao finalizar a árvore de processos.") from first


__all__ = (
    "Journal",
    "POPEN_TYPE",
    "Reporter",
    "ServiceSignal",
    "WindowsJobObject",
    "_windows_job_kernel32",
    "posix_process_group_status",
    "run_processes",
    "stop_processes",
)
