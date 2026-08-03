"""Descoberta de upstreams publicos sem API, conta ou token."""

from __future__ import annotations

import errno
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import BinaryIO

try:
    from .downloader import BoundedMetadata, MAX_ARTIFACT_BYTES, download, safe_url_for_log
except ImportError:  # Execucao direta
    from downloader import BoundedMetadata, MAX_ARTIFACT_BYTES, download, safe_url_for_log


USER_AGENT = "x86qw-maintenance/1"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
DISCOVERY_MAX_BYTES = 4 * 1024 * 1024
REMOTE_ASSET_MAX_BYTES = MAX_ARTIFACT_BYTES
GIT_EXECUTABLE = "git"
GIT_COMMAND_DEADLINE_SECONDS = 60.0
GIT_TREE_DEADLINE_SECONDS = 300.0
GIT_STDOUT_MAX_BYTES = 32 * 1024 * 1024
GIT_STDERR_MAX_BYTES = 1 * 1024 * 1024
GIT_TREE_MAX_BYTES = 128 * 1024 * 1024
GIT_PIPE_CHUNK_BYTES = 64 * 1024
GIT_POLL_SECONDS = 0.05
GIT_TERM_GRACE_SECONDS = 0.5
GIT_KILL_GRACE_SECONDS = 2.0
WINDOWS_CREATE_SUSPENDED = 0x00000004
WINDOWS_CREATE_NEW_PROCESS_GROUP = 0x00000200
SAFE_GIT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
NETWORK_ENVIRONMENT = (
    "ALL_PROXY", "all_proxy",
    "HTTP_PROXY", "http_proxy",
    "HTTPS_PROXY", "https_proxy",
    "NO_PROXY", "no_proxy",
    "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "GIT_ASKPASS", "SSH_ASKPASS", "SSH_ASKPASS_REQUIRE",
)


@dataclass(frozen=True)
class GitTreeEntry:
    path: str
    sha1: str
    size: int | None


class _BoundedPipeReader:
    """Drena um pipe sem permitir que a captura cresça sem limite."""

    def __init__(self, stream: BinaryIO, maximum_size: int) -> None:
        self.stream = stream
        self.maximum_size = maximum_size
        self.data = bytearray()
        self.overflow = threading.Event()
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._read, daemon=True)

    def _read(self) -> None:
        try:
            while chunk := self.stream.read(GIT_PIPE_CHUNK_BYTES):
                remaining = self.maximum_size - len(self.data)
                if remaining > 0:
                    self.data.extend(chunk[:remaining])
                if len(chunk) > max(remaining, 0):
                    self.overflow.set()
        except BaseException as error:  # pragma: no cover - erro de pipe dependente do SO
            self.error = error
        finally:
            self.stream.close()

    def start(self) -> None:
        self.thread.start()

    def join(self) -> None:
        self.thread.join(timeout=GIT_KILL_GRACE_SECONDS)


def _safe_https_proxy(environment: dict[str, str]) -> str | None:
    candidates = {
        value
        for name in ("HTTPS_PROXY", "https_proxy")
        if (value := environment.get(name))
    }
    if len(candidates) != 1:
        return None
    proxy = candidates.pop()
    if any(character == "\\" or ord(character) < 32 or character.isspace() for character in proxy):
        return None
    parsed = urllib.parse.urlsplit(proxy)
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or port is not None and not 1 <= port <= 65535
    ):
        return None
    return proxy


def _git_environment() -> dict[str, str]:
    inherited = os.environ.copy()
    safe_proxy = _safe_https_proxy(inherited)
    environment = {
        name: value
        for name, value in inherited.items()
        if not name.startswith(("GIT_", "GCM_")) and name not in NETWORK_ENVIRONMENT
    }
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["GCM_INTERACTIVE"] = "never"
    if safe_proxy is not None:
        environment["HTTPS_PROXY"] = safe_proxy
        environment["https_proxy"] = safe_proxy
    return environment


def _validate_git_repository(repository: str) -> str:
    if not isinstance(repository, str) or not repository:
        raise ValueError("o upstream Git precisa ser uma URL HTTPS")
    if any(character == "\\" or ord(character) < 32 or character.isspace() for character in repository):
        raise ValueError("URL de upstream Git insegura")
    parsed = urllib.parse.urlsplit(repository)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("URL de upstream Git invalida") from error
    decoded_path = urllib.parse.unquote(parsed.path)
    if (
        parsed.scheme.casefold() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or parsed.path == "/"
        or any(character == "\\" or ord(character) < 32 for character in decoded_path)
    ):
        raise ValueError("o upstream Git precisa usar HTTPS sem credenciais")
    return repository


def _validate_git_ref(value: str, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SAFE_GIT_REF.fullmatch(value) is None
        or ".." in value
        or "@{" in value
        or "//" in value
        or value.endswith(("/", ".", ".lock"))
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise ValueError(f"{label} Git invalida: {value!r}")
    return value


def _directory_size(path: Path, maximum_size: int, *, deadline: float) -> int:
    """Conta tamanho logico/alocado sem seguir links e para ao ultrapassar a cota."""
    if not path.exists():
        return 0
    total = 0
    pending = [path]
    while pending:
        if time.monotonic() >= deadline:
            raise TimeoutError("prazo excedido ao medir o clone Git")
        directory = pending.pop()
        try:
            entries = os.scandir(directory)
        except FileNotFoundError:
            continue
        with entries:
            for entry in entries:
                if time.monotonic() >= deadline:
                    raise TimeoutError("prazo excedido ao medir o clone Git")
                try:
                    metadata = entry.stat(follow_symlinks=False)
                    is_directory = entry.is_dir(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                allocated = getattr(metadata, "st_blocks", 0) * 512
                total += max(metadata.st_size, allocated)
                if total > maximum_size:
                    return total
                if is_directory:
                    pending.append(Path(entry.path))
    return total


def _posix_process_group_status(process_group: int) -> str:
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


def _wait_for_posix_group(
    process: subprocess.Popen[bytes],
    process_group: int,
    deadline: float,
) -> str:
    while time.monotonic() < deadline:
        process.poll()
        status = _posix_process_group_status(process_group)
        if status == "dead":
            return status
        time.sleep(min(GIT_POLL_SECONDS, max(deadline - time.monotonic(), 0.001)))
    return _posix_process_group_status(process_group)


def _terminate_posix_group(process: subprocess.Popen[bytes]) -> None:
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        try:
            process.wait(timeout=GIT_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.wait(timeout=GIT_KILL_GRACE_SECONDS)
            raise ValueError(
                f"o processo Git {process.pid} nao permaneceu no grupo proprio"
            ) from error
        return
    except PermissionError as error:
        raise ValueError(
            f"nao foi possivel encerrar o grupo Git {process_group}: permissao negada"
        ) from error

    status = _wait_for_posix_group(
        process,
        process_group,
        time.monotonic() + GIT_TERM_GRACE_SECONDS,
    )
    process.poll()  # coleta o lider antes da verificacao posterior do grupo
    if status != "dead":
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            status = "dead"
        except PermissionError as error:
            process.poll()
            status = _posix_process_group_status(process_group)
            if status != "dead":
                raise ValueError(
                    f"nao foi possivel forcar o encerramento do grupo Git {process_group}"
                ) from error
        else:
            status = _wait_for_posix_group(
                process,
                process_group,
                time.monotonic() + GIT_KILL_GRACE_SECONDS,
            )
    try:
        process.wait(timeout=GIT_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"o lider do grupo Git {process_group} permaneceu ativo") from error
    status = _posix_process_group_status(process_group)
    if status == "inconclusive":
        raise ValueError(f"nao foi possivel confirmar o grupo Git {process_group}")
    if status == "alive":
        raise ValueError(f"o grupo Git {process_group} permaneceu ativo apos SIGKILL")


def _windows_job_kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _windows_ntdll():
    import ctypes
    from ctypes import wintypes

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = wintypes.LONG
    return ntdll


class _WindowsJobObject:
    """Impede que descendentes Git sobrevivam ao controlador no Windows."""

    def __init__(self) -> None:
        import ctypes
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

        self.kernel32 = _windows_job_kernel32()
        self.handle = self.kernel32.CreateJobObjectW(None, None)
        if not self.handle:
            raise ValueError(
                f"nao foi possivel criar o Job Object do Git ({ctypes.get_last_error()})"
            )
        information = ExtendedLimitInformation()
        information.basic.limit_flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel32.SetInformationJobObject(
            self.handle, 9, ctypes.byref(information), ctypes.sizeof(information),
        ):
            error = ctypes.get_last_error()
            self.kernel32.CloseHandle(self.handle)
            self.handle = None
            raise ValueError(f"nao foi possivel configurar o Job Object do Git ({error})")

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        import ctypes

        if self.handle is None or not self.kernel32.AssignProcessToJobObject(
            self.handle, int(process._handle),  # type: ignore[attr-defined]
        ):
            raise ValueError(
                f"nao foi possivel associar PID {process.pid} ao Job Object do Git "
                f"({ctypes.get_last_error()})"
            )

    def resume(self, process: subprocess.Popen[bytes]) -> None:
        status = int(_windows_ntdll().NtResumeProcess(int(process._handle)))  # type: ignore[attr-defined]
        if status != 0:
            raise ValueError(
                f"nao foi possivel iniciar o processo Git suspenso (NTSTATUS {status:#x})"
            )

    def close(self) -> None:
        import ctypes

        if self.handle is None:
            return
        handle = self.handle
        self.handle = None
        if not self.kernel32.CloseHandle(handle):
            raise ValueError(
                f"nao foi possivel fechar o Job Object do Git ({ctypes.get_last_error()})"
            )


def _terminate_process(
    process: subprocess.Popen[bytes],
    windows_job: _WindowsJobObject | None = None,
) -> None:
    if os.name == "posix":
        _terminate_posix_group(process)
        return
    if windows_job is None:
        raise ValueError("processo Git no Windows nao possui Job Object")
    windows_job.close()
    try:
        process.wait(timeout=GIT_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise ValueError("o Job Object nao encerrou o processo Git") from error


def _poll_pause(seconds: float) -> None:
    time.sleep(seconds)


def _run_bounded_command(
    command: list[str],
    *,
    deadline: float,
    stdout_limit: int = GIT_STDOUT_MAX_BYTES,
    stderr_limit: int = GIT_STDERR_MAX_BYTES,
    workspace: Path | None = None,
    workspace_limit: int | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if not command or any(not isinstance(argument, str) or "\0" in argument for argument in command):
        raise ValueError("comando Git invalido")
    if stdout_limit <= 0 or stderr_limit <= 0:
        raise ValueError("os limites de saida do Git precisam ser positivos")
    if workspace is not None and (workspace_limit is None or workspace_limit <= 0):
        raise ValueError("o workspace Git precisa de uma cota positiva")
    if time.monotonic() >= deadline:
        raise ValueError("o prazo total da consulta Git foi excedido")

    environment = _git_environment()
    process_options: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
        "env": environment,
    }
    windows_job = _WindowsJobObject() if os.name == "nt" else None
    if os.name == "posix":
        process_options["start_new_session"] = True
    elif os.name == "nt":
        process_options["creationflags"] = (
            WINDOWS_CREATE_NEW_PROCESS_GROUP | WINDOWS_CREATE_SUSPENDED
        )

    try:
        process = subprocess.Popen(command, **process_options)  # type: ignore[arg-type]
    except BaseException as error:
        cleanup_error: BaseException | None = None
        if windows_job is not None:
            try:
                windows_job.close()
            except BaseException as close_error:
                cleanup_error = close_error
        if cleanup_error is not None:
            raise ValueError(
                f"falha ao iniciar e liberar o Job Object do Git: {cleanup_error}"
            ) from error
        if isinstance(error, OSError):
            raise ValueError(f"nao foi possivel iniciar a consulta Git: {error}") from error
        raise
    if windows_job is not None:
        try:
            windows_job.assign(process)
            windows_job.resume(process)
        except BaseException as startup_error:
            cleanup_errors: list[str] = []
            try:
                windows_job.close()
            except BaseException as error:
                cleanup_errors.append(str(error))
            if process.poll() is None:
                try:
                    process.kill()
                except OSError as error:
                    cleanup_errors.append(str(error))
            try:
                process.wait(timeout=GIT_KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired as error:
                cleanup_errors.append(str(error))
            if cleanup_errors:
                raise ValueError(
                    "falha critica ao abortar o startup Git: " + "; ".join(cleanup_errors)
                ) from startup_error
            raise
    assert process.stdout is not None and process.stderr is not None
    stdout = _BoundedPipeReader(process.stdout, stdout_limit)
    stderr = _BoundedPipeReader(process.stderr, stderr_limit)
    termination_attempted = False
    try:
        stdout.start()
        stderr.start()
        failure: str | None = None
        next_workspace_check = 0.0
        while process.poll() is None:
            now = time.monotonic()
            if stdout.overflow.is_set() or stderr.overflow.is_set():
                failure = "a saida do Git excedeu o limite permitido"
                break
            if workspace is not None and now >= next_workspace_check:
                try:
                    if _directory_size(
                        workspace,
                        workspace_limit or 0,
                        deadline=deadline,
                    ) > (workspace_limit or 0):
                        failure = "o clone Git excedeu a cota temporaria permitida"
                        break
                except OSError as error:
                    failure = f"nao foi possivel medir o clone Git: {error}"
                    break
                next_workspace_check = now + GIT_POLL_SECONDS
            if now >= deadline:
                failure = "o prazo total da consulta Git foi excedido"
                break
            _poll_pause(min(GIT_POLL_SECONDS, max(deadline - now, 0.001)))

        if failure is not None:
            termination_attempted = True
            _terminate_process(process, windows_job)
        else:
            process.wait()
            if os.name == "posix":
                group_status = _posix_process_group_status(process.pid)
                if group_status != "dead":
                    termination_attempted = True
                    _terminate_process(process)
                    failure = "um descendente Git permaneceu ativo apos o lider"
            elif windows_job is not None:
                termination_attempted = True
                windows_job.close()
    except BaseException as operation_error:
        cleanup_error: BaseException | None = operation_error if termination_attempted else None
        if not termination_attempted:
            try:
                _terminate_process(process, windows_job)
            except BaseException as error:
                cleanup_error = error
        stdout.join()
        stderr.join()
        if cleanup_error is not None:
            context = f"{failure}. " if failure is not None else ""
            raise ValueError(
                f"{context}falha critica ao encerrar a arvore Git: {cleanup_error}"
            ) from operation_error
        raise
    stdout.join()
    stderr.join()
    if stdout.thread.is_alive() or stderr.thread.is_alive():
        _terminate_process(process, windows_job)
        failure = failure or "os pipes da consulta Git nao foram encerrados"
    if stdout.error is not None or stderr.error is not None:
        failure = failure or "falha ao ler a saida da consulta Git"
    if stdout.overflow.is_set() or stderr.overflow.is_set():
        failure = failure or "a saida do Git excedeu o limite permitido"
    if time.monotonic() > deadline:
        failure = failure or "o prazo total da consulta Git foi excedido"
    if workspace is not None:
        try:
            if _directory_size(
                workspace,
                workspace_limit or 0,
                deadline=deadline,
            ) > (workspace_limit or 0):
                failure = failure or "o clone Git excedeu a cota temporaria permitida"
        except OSError as error:
            failure = failure or f"nao foi possivel medir o clone Git: {error}"
    if failure is not None:
        raise ValueError(failure)
    return subprocess.CompletedProcess(command, process.returncode, bytes(stdout.data), bytes(stderr.data))


class GitHubCommitMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "meta":
            return
        values = {name.casefold(): value for name, value in attrs}
        if values.get("property") == "og:url" and isinstance(values.get("content"), str):
            self.urls.append(str(values["content"]))


def run_git(
    arguments: list[str],
    *,
    binary: bool = False,
    deadline: float | None = None,
    stdout_limit: int = GIT_STDOUT_MAX_BYTES,
    workspace: Path | None = None,
    workspace_limit: int | None = None,
) -> str | bytes:
    result = _run_bounded_command(
        [
            GIT_EXECUTABLE,
            "-c", "protocol.allow=never",
            "-c", "protocol.https.allow=always",
            "-c", "http.sslVerify=true",
            "-c", "http.followRedirects=false",
            *arguments,
        ],
        deadline=(
            deadline
            if deadline is not None
            else time.monotonic() + GIT_COMMAND_DEADLINE_SECONDS
        ),
        stdout_limit=stdout_limit,
        workspace=workspace,
        workspace_limit=workspace_limit,
    )
    if result.returncode != 0:
        error = result.stderr[:4096].decode("utf-8", "replace").strip()
        error = "".join(
            character if character in "\n\t" or ord(character) >= 32 else "?"
            for character in error
        )
        raise ValueError(f"falha ao consultar upstream Git: {error or f'codigo {result.returncode}'}")
    return result.stdout if binary else result.stdout.decode("utf-8", "replace")


def git_remote_revision(repository: str, ref: str) -> str:
    repository = _validate_git_repository(repository)
    ref = _validate_git_ref(ref, label="referencia")
    deadline = time.monotonic() + GIT_COMMAND_DEADLINE_SECONDS
    output = str(run_git(
        ["ls-remote", "--exit-code", repository, ref],
        deadline=deadline,
        stdout_limit=DISCOVERY_MAX_BYTES,
    )).strip().splitlines()
    revisions = {line.split()[0] for line in output if len(line.split()) == 2}
    if len(revisions) != 1:
        raise ValueError(f"o upstream Git nao resolveu uma revisao unica para {ref}: {repository}")
    revision = revisions.pop()
    if not HEX40.fullmatch(revision):
        raise ValueError(f"o upstream Git retornou uma revisao invalida: {repository}")
    return revision


def git_remote_tree(repository: str, branch: str) -> tuple[str, list[GitTreeEntry]]:
    """Baixa apenas commits e arvores; os blobs permanecem no upstream."""
    repository = _validate_git_repository(repository)
    branch = _validate_git_ref(branch, label="branch")
    deadline = time.monotonic() + GIT_TREE_DEADLINE_SECONDS
    with tempfile.TemporaryDirectory(prefix="x86qw-git-tree-") as temporary:
        checkout = Path(temporary) / "repository"
        run_git([
            "-c", "protocol.version=2", "clone", "--quiet", "--depth", "1",
            "--filter=blob:none", "--no-checkout", "--no-tags", "--single-branch",
            "--branch", branch, repository, str(checkout),
        ], deadline=deadline, workspace=Path(temporary), workspace_limit=GIT_TREE_MAX_BYTES)
        revision = str(run_git(
            ["-C", str(checkout), "rev-parse", "HEAD"],
            deadline=deadline,
            stdout_limit=4096,
        )).strip()
        raw = run_git(
            ["-C", str(checkout), "ls-tree", "-r", "-z", "HEAD"],
            binary=True,
            deadline=deadline,
            stdout_limit=GIT_STDOUT_MAX_BYTES,
            workspace=Path(temporary),
            workspace_limit=GIT_TREE_MAX_BYTES,
        )
    if not isinstance(raw, bytes) or not HEX40.fullmatch(revision):
        raise ValueError(f"arvore Git invalida: {repository}")
    entries: list[GitTreeEntry] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, separator, encoded_path = record.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise ValueError(f"entrada invalida na arvore Git: {repository}")
        if fields[1] != b"blob":
            continue
        try:
            sha1 = fields[2].decode("ascii")
        except UnicodeDecodeError as error:
            raise ValueError(f"entrada invalida na arvore Git: {repository}") from error
        if HEX40.fullmatch(sha1) is None:
            raise ValueError(f"entrada invalida na arvore Git: {repository}")
        entries.append(GitTreeEntry(
            encoded_path.decode("utf-8", "surrogateescape"),
            sha1,
            None,
        ))
    return revision, entries


def github_latest_release(repository: str) -> str:
    url = f"https://github.com/{repository}/releases/latest"
    result = download(BoundedMetadata(
        url=url,
        maximum_size=DISCOVERY_MAX_BYTES,
        deadline_seconds=60,
        headers={"User-Agent": USER_AGENT},
        label="release latest do GitHub",
        method="HEAD",
    ))
    final_path = urllib.parse.urlsplit(result.url).path
    match = re.search(r"/releases/tag/([^/]+)$", final_path)
    if match is None:
        raise ValueError(f"o upstream nao publicou uma release latest: {repository}")
    return urllib.parse.unquote(match.group(1))


def github_commit_revision(repository: str, abbreviation: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{7,40}", abbreviation):
        raise ValueError(f"commit abreviado invalido: {abbreviation}")
    url = f"https://github.com/{repository}/commit/{abbreviation}"
    result = download(BoundedMetadata(
        url=url,
        maximum_size=DISCOVERY_MAX_BYTES,
        deadline_seconds=60,
        headers={"User-Agent": USER_AGENT},
        label="página de commit do GitHub",
    ))
    assert result.data is not None
    document = result.data
    parser = GitHubCommitMetaParser()
    parser.feed(document.decode("utf-8", "replace"))
    expected_path = re.compile(
        rf"^/{re.escape(repository)}/commit/([0-9a-f]{{40}})$"
    )
    revisions: set[str] = set()
    for url in parser.urls:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme and (
            parsed.scheme.casefold() != "https"
            or parsed.hostname is None
            or parsed.hostname.casefold() != "github.com"
        ):
            continue
        match = expected_path.fullmatch(parsed.path)
        if match is not None:
            revisions.add(match.group(1))
    if len(revisions) != 1:
        raise ValueError(f"nao foi possivel resolver publicamente o commit {abbreviation}: {repository}")
    return revisions.pop()


def remote_content_length(url: str) -> int:
    result = download(BoundedMetadata(
        url=url,
        maximum_size=REMOTE_ASSET_MAX_BYTES,
        deadline_seconds=60,
        headers={"User-Agent": USER_AGENT},
        label="metadados do artefato remoto",
        method="HEAD",
    ))
    length = next(
        (value for name, value in result.headers.items() if name.casefold() == "content-length"),
        None,
    )
    if not isinstance(length, str) or not length.isdigit() or int(length) <= 0:
        raise ValueError(
            "o upstream nao informou o tamanho do artefato: "
            f"{safe_url_for_log(url)}"
        )
    return int(length)
