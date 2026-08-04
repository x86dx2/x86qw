"""Private service journals and conservative abandoned-session recovery."""

from __future__ import annotations

import json
import os
import re
import signal
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Protocol

from x86qw_runtime import session_control
from x86qw_runtime.errors import InstallerError
from x86qw_runtime.io import private_fs
from x86qw_runtime.io.atomic import AtomicWriteError, atomic_write_json
from x86qw_runtime.io.managed_files import (
    MAX_MANAGED_FILE_SIZE,
    MaterializedDirectory,
    MaterializedFile,
    cleanup_materialized_directory,
    cleanup_materialized_file,
    cleanup_sensitive_temporary,
    describe_non_sensitive_temporary,
    persistent_descriptor_identity,
    persistent_path_identity,
)
from x86qw_runtime.io.paths import lexists

from .core import posix_process_group_status
from .models import ProcessSpec


_MAX_SESSION_JOURNAL_BYTES = 2 * 1024 * 1024
_MAX_STOP_REQUEST_BYTES = 64 * 1024
_LEGACY_ACL_MIGRATED = "_x86qw_legacy_acl_migrated"
_SESSION_DIRECTORY_NAME = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}\Z")
_INITIAL_JOURNAL_STAGING_NAME = re.compile(r"\.session\.json\.[0-9a-f]{24}\.tmp\Z")


class RecoveryReporter(Protocol):
    """Minimal diagnostic surface required by session recovery."""

    def warning(self, message: str) -> None: ...


def _ensure_private_directory(path: Path) -> None:
    try:
        private_fs.ensure_private_directory(path)
    except OSError as error:
        raise InstallerError(f"Diretório de serviço ausente ou inseguro: {path}") from error


def unlink_stop_request(
    request: Path, *, expected_identity: tuple[int, int] | None = None,
) -> None:
    """Remove an ephemeral transactional request after it is consumed or cancelled."""

    for attempt in range(20):
        try:
            private_fs.unlink_private_file(
                request, expected_identity=expected_identity,
            )
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if os.name == "nt" and attempt < 19:
                time.sleep(0.025)
                continue
            raise
        except OSError as error:
            raise InstallerError(
                f"Pedido de encerramento sem privacidade comprovada foi preservado: {request}"
            ) from error


def _cleanup_stop_request_staging(
    path: Path,
    *,
    expected_identity: tuple[int, int],
    primary_error: BaseException | None,
) -> None:
    if not lexists(path):
        return
    try:
        private_fs.unlink_private_file(path, expected_identity=expected_identity)
    except OSError as cleanup_error:
        message = (
            "Falha ao remover o temporário privado do pedido de encerramento; "
            f"arquivo preservado: {path} ({cleanup_error})"
        )
        if primary_error is not None:
            add_note = getattr(primary_error, "add_note", None)
            if add_note is not None:
                add_note(message)
            return
        raise InstallerError(message) from cleanup_error


def publish_stop_request(request: Path, payload: bytes) -> tuple[int, int]:
    """Publish an ephemeral transactional stop request with create-only commit.

    The complete private payload becomes visible only after ``fsync``.  A
    concurrent request wins without replacement; callers remove the request
    through :func:`unlink_stop_request` after consumption or cancellation.
    """

    try:
        descriptor, temporary = private_fs.private_mkstemp(
            prefix=".stop-", suffix=".request", directory=request.parent,
        )
        try:
            temporary_identity = persistent_descriptor_identity(
                descriptor, directory=False,
            )
        except OSError:
            os.close(descriptor)
            raise
    except OSError as error:
        raise InstallerError(
            "Não foi possível criar o pedido privado de encerramento."
        ) from error
    primary_error: BaseException | None = None
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, request)
            private_fs.validate_private_file(request)
        except FileExistsError as error:
            raise InstallerError(
                "Já existe um pedido de encerramento para esta stack."
            ) from error
        except OSError as error:
            raise InstallerError(
                "Não foi possível publicar o pedido privado de encerramento."
            ) from error
    except BaseException as error:
        primary_error = error
        raise
    finally:
        _cleanup_stop_request_staging(
            temporary,
            expected_identity=temporary_identity,
            primary_error=primary_error,
        )
    return temporary_identity


def activate_background_log(target: Path, relative: str) -> Path:
    """Attach stdout/stderr to one private append-only background log.

    Existing bytes are never truncated.  A failed redirection restores both
    descriptors and removes only unchanged artifacts created by this call.
    """

    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or pure.parts[:2] != (".x86qw", "logs")
        or len(pure.parts) != 3
        or any(part in {"", ".", ".."} for part in pure.parts)
        or not pure.name.startswith("service-")
        or pure.suffix != ".log"
    ):
        raise InstallerError("Caminho interno do log em segundo plano é inválido.")
    directory = target / ".x86qw" / "logs"
    path = target.joinpath(*pure.parts)
    stdout = sys.stdout.fileno()
    stderr = sys.stderr.fileno()
    backups: list[tuple[int, int, bool]] = []
    descriptor: int | None = None
    created_directory: MaterializedDirectory | None = None
    created_log_identity: tuple[int, int] | None = None
    failure: BaseException | None = None
    try:
        backups.append((os.dup(stdout), stdout, os.get_inheritable(stdout)))
        backups.append((os.dup(stderr), stderr, os.get_inheritable(stderr)))
        try:
            private_fs.create_private_directory(directory)
            created_directory = MaterializedDirectory(
                directory,
                target,
                persistent_path_identity(directory, directory=True),
            )
        except FileExistsError:
            _ensure_private_directory(directory)
        if lexists(path) and (path.is_symlink() or not path.is_file()):
            raise InstallerError(f"Log em segundo plano ausente ou inseguro: {path}")
        try:
            created = private_fs.create_private_file(path)
        except FileExistsError:
            descriptor = private_fs.open_private_append(path)
        else:
            try:
                created_log_identity = persistent_descriptor_identity(
                    created, directory=False,
                )
            finally:
                os.close(created)
            descriptor = private_fs.open_private_append(path)
            if persistent_descriptor_identity(
                descriptor, directory=False,
            ) != created_log_identity:
                raise InstallerError(
                    f"Log em segundo plano mudou de identidade durante a abertura: {path}"
                )
        os.dup2(descriptor, sys.stdout.fileno())
        os.dup2(descriptor, sys.stderr.fileno())
    except BaseException as error:
        failure = error
        for backup, destination, inheritable in backups:
            try:
                os.dup2(backup, destination, inheritable=inheritable)
            except OSError as rollback_error:
                add_note = getattr(error, "add_note", None)
                if add_note is not None:
                    add_note(
                        f"Falha ao restaurar descritor {destination}: {rollback_error}"
                    )
        if isinstance(error, OSError) and descriptor is None:
            raise InstallerError(
                f"Log em segundo plano não pôde ser protegido: {path}"
            ) from error
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for backup, _destination, _inheritable in backups:
            os.close(backup)
        if failure is not None and created_log_identity is not None:
            try:
                private_fs.unlink_private_file(
                    path, expected_identity=created_log_identity,
                )
            except OSError as cleanup_error:
                add_note = getattr(failure, "add_note", None)
                if add_note is not None:
                    add_note(f"Log concorrente preservado após falha: {cleanup_error}")
        if failure is not None and created_directory is not None:
            cleanup_materialized_directory(created_directory)
    return path


class SessionJournal:
    """Private, append-safe description of ephemeral service session state."""

    def __init__(
        self,
        target: Path,
        *,
        session_id: str | None = None,
        controller: dict[str, object] | None = None,
        background: bool = False,
        background_log: str | None = None,
    ) -> None:
        self.target = target.resolve()
        sessions = self.target / ".x86qw" / "sessions"
        _ensure_private_directory(sessions.parent)
        _ensure_private_directory(sessions)
        self.session_id = session_id or session_control.new_session_id()
        self._sensitive_guards: dict[str, object] = {}
        self.directory = sessions / self.session_id
        try:
            private_fs.create_private_directory(self.directory)
        except OSError as error:
            raise InstallerError(
                f"Diretório exclusivo da sessão não pôde ser criado: {self.directory}"
            ) from error
        created_directory = MaterializedDirectory(
            self.directory,
            self.target,
            persistent_path_identity(self.directory, directory=True),
        )
        self.path = self.directory / "session.json"
        self.data: dict[str, object] = {
            "format": 1,
            "project": "x86qw",
            "private_filesystem": 1,
            "session_id": self.session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "starting",
            "controller": None if controller is None else {
                "pid": controller["controller_pid"],
                "creation_token": controller["controller_start_token"],
                "executable": controller["controller_executable"],
                "command": controller["command"],
            },
            "background": background,
            "background_log": background_log,
            "processes": [],
            "temporary_files": [],
            "materialized_files": [],
            "created_directories": [],
        }
        try:
            self._write()
        except BaseException:
            cleanup_materialized_directory(created_directory)
            raise

    def _relative(self, path: Path) -> str:
        try:
            absolute = path if path.is_absolute() else self.target / path
            canonical_parent = absolute.parent.resolve(strict=False)
            return (canonical_parent / absolute.name).relative_to(self.target).as_posix()
        except ValueError as error:
            raise InstallerError("O journal recusou um caminho fora da instalação.") from error

    def _write(self) -> None:
        try:
            atomic_write_json(self.path, self.data, private=True)
            private_fs.validate_private_file(self.path)
        except AtomicWriteError as error:
            raise InstallerError("O journal privado da sessão não pôde ser gravado.") from error
        except OSError as error:
            raise InstallerError("O journal privado da sessão não pôde ser validado.") from error

    def set_status(self, status: str) -> None:
        if status not in {"starting", "running", "stopping", "interrupted", "clean"}:
            raise InstallerError(f"Estado inválido do journal: {status}")
        self.data["status"] = status
        self._write()

    def consume_stop_request(self) -> bool:
        request = self.directory / "stop.request"
        if not lexists(request):
            return False
        try:
            before = persistent_path_identity(request, directory=False)
            payload = json.loads(
                private_fs.read_private_file(
                    request, maximum_size=_MAX_STOP_REQUEST_BYTES,
                ).decode("utf-8")
            )
            identity = persistent_path_identity(request, directory=False)
            if identity != before:
                raise OSError("o pedido mudou de identidade durante a leitura")
            if (
                not isinstance(payload, dict)
                or set(payload) != {"format", "project", "session_id", "requested_at"}
                or payload.get("format") != 1
                or payload.get("project") != "x86qw"
                or payload.get("session_id") != self.session_id
                or not isinstance(payload.get("requested_at"), str)
            ):
                raise ValueError("pedido inválido")
            unlink_stop_request(request, expected_identity=identity)
            return True
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            raise InstallerError(
                f"Pedido de encerramento inválido preservado para inspeção: {request}"
            ) from error

    def record_process(
        self,
        spec: ProcessSpec,
        process: object,
        process_group: int,
    ) -> None:
        pid = getattr(process, "pid", None)
        if type(pid) is not int or pid <= 0:
            raise InstallerError(f"PID inválido para o processo {spec.label}.")
        probe = session_control.process_identity(pid)
        if probe.status != "alive" or probe.identity is None:
            raise InstallerError(f"Não foi possível registrar a identidade do processo {spec.label}.")
        identity = probe.identity
        runtime_executable = (
            spec.launch_target.executable
            if spec.launch_target is not None
            else spec.arguments[0] if spec.arguments else None
        )
        if runtime_executable is None or not os.fspath(runtime_executable):
            raise InstallerError(f"Executável inválido para o processo {spec.label}.")
        pending = getattr(process, "_x86qw_runtime_pending", False) is True
        address = None
        port = None
        if spec.startup_rcon is not None:
            address, port = spec.startup_rcon.address, spec.startup_rcon.port
        elif spec.readiness is not None:
            address, port = spec.readiness.address, spec.readiness.port
        processes = self.data["processes"]
        assert isinstance(processes, list)
        processes.append({
            "label": spec.label,
            "runtime": spec.label.casefold(),
            "pid": pid,
            "process_group": process_group,
            "executable": identity.executable,
            "creation_token": identity.creation_token,
            "runtime_executable": os.fspath(runtime_executable),
            "runtime_pid": None if pending else pid,
            "state": "pending" if pending else "ready",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "address": address,
            "port": port,
            "parameters": dict(spec.parameters),
        })
        self._write()

    def record_process_started(self, process: object, runtime_pid: int) -> None:
        if type(runtime_pid) is not int or runtime_pid <= 0:
            raise InstallerError("O guardian informou um PID de runtime inválido.")
        pid = getattr(process, "pid", None)
        if type(pid) is not int or pid <= 0:
            raise InstallerError("O guardian não possui PID válido.")
        probe = session_control.process_identity(pid)
        if probe.status != "alive" or probe.identity is None:
            raise InstallerError("A identidade do guardian não pôde ser confirmada.")
        identity = probe.identity
        processes = self.data["processes"]
        assert isinstance(processes, list)
        matches = [
            entry for entry in processes
            if isinstance(entry, dict)
            and entry.get("pid") == pid
            and entry.get("creation_token") == identity.creation_token
            and entry.get("executable") == identity.executable
            and entry.get("state") == "pending"
            and entry.get("runtime_pid") is None
        ]
        if len(matches) != 1:
            raise InstallerError("Entrada pending do guardian ausente ou ambígua.")
        entry = matches[0]
        entry["runtime_pid"] = runtime_pid
        entry["state"] = "ready"
        try:
            self._write()
        except BaseException:
            entry["runtime_pid"] = None
            entry["state"] = "pending"
            raise

    def record_temporary_intent(
        self,
        path: Path,
        origin: str,
        *,
        sensitive: bool,
        identity: tuple[int, int],
        expected_hash: str | None = None,
        expected_size: int | None = None,
    ) -> None:
        entries = self.data["temporary_files"]
        assert isinstance(entries, list)
        entry: dict[str, object] = {
            "path": self._relative(path),
            "origin": origin,
            "created_by_session": True,
            "type": "temporary-config",
            "sensitive": sensitive,
            "state": "pending",
            "device": identity[0],
            "inode": identity[1],
        }
        if not sensitive:
            if (
                not isinstance(expected_hash, str)
                or len(expected_hash) != 64
                or type(expected_size) is not int
                or not 0 <= expected_size <= MAX_MANAGED_FILE_SIZE
            ):
                raise InstallerError(f"Intenção incoerente do temporário {path}.")
            entry["expected_hash"] = expected_hash
            entry["expected_size"] = expected_size
        entries.append(entry)
        self._write()

    def record_temporary(
        self,
        path: Path,
        origin: str,
        *,
        sensitive: bool,
        tracked: MaterializedFile | None = None,
    ) -> None:
        entries = self.data["temporary_files"]
        assert isinstance(entries, list)
        entry: dict[str, object] = {
            "path": self._relative(path),
            "origin": origin,
            "created_by_session": True,
            "type": "temporary-config",
            "sensitive": sensitive,
            "state": "ready",
        }
        if not sensitive:
            tracked = tracked or describe_non_sensitive_temporary(
                path, self.target, origin,
            )
            if (
                tracked.path != path
                or tracked.root != self.target
                or tracked.identity is None
                or type(tracked.expected_size) is not int
            ):
                raise InstallerError(f"Identidade incoerente do temporário {path}.")
            entry["expected_size"] = tracked.expected_size
            entry["expected_hash"] = tracked.expected_hash
            identity = tracked.identity
            entry["device"] = identity[0]
            entry["inode"] = identity[1]
        relative = entry["path"]
        for index, recorded in enumerate(entries):
            if (
                isinstance(recorded, dict)
                and recorded.get("path") == relative
                and recorded.get("state") == "pending"
            ):
                if (
                    sensitive
                    and type(recorded.get("device")) is int
                    and type(recorded.get("inode")) is int
                ):
                    entry["device"] = recorded["device"]
                    entry["inode"] = recorded["inode"]
                entries[index] = entry
                break
        else:
            entries.append(entry)
        self._write()

    def hold_sensitive_temporary(self, path: Path, lease: object) -> None:
        key = self._relative(path)
        previous = self._sensitive_guards.pop(key, None)
        if previous is not None:
            previous.close()
        self._sensitive_guards[key] = lease

    def release_sensitive_temporary(self, path: Path) -> None:
        lease = self._sensitive_guards.pop(self._relative(path), None)
        if lease is not None:
            lease.close()

    def release_all_sensitive_temporaries(self) -> None:
        guards = tuple(self._sensitive_guards.values())
        self._sensitive_guards.clear()
        first_error: BaseException | None = None
        for lease in guards:
            try:
                lease.close()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    def _materialized_record(
        self, entry: MaterializedFile, *, state: str,
    ) -> dict[str, object]:
        identity = entry.identity
        if identity is None:
            try:
                identity = persistent_path_identity(entry.path, directory=False)
            except OSError as error:
                raise InstallerError(
                    f"Não foi possível registrar a identidade de {entry.path}."
                ) from error
        expected_size = entry.expected_size
        if expected_size is None:
            try:
                metadata = entry.path.lstat()
            except OSError as error:
                raise InstallerError(
                    f"Não foi possível medir o arquivo materializado {entry.path}."
                ) from error
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise InstallerError(f"Arquivo materializado ausente ou inseguro: {entry.path}")
            expected_size = metadata.st_size
        if (
            type(expected_size) is not int
            or not 0 <= expected_size <= MAX_MANAGED_FILE_SIZE
        ):
            raise InstallerError(
                f"Tamanho inválido do arquivo materializado {entry.path}."
            )
        return {
            "path": self._relative(entry.path),
            "expected_hash": entry.expected_hash,
            "expected_size": expected_size,
            "origin": entry.origin,
            "created_by_session": entry.created_by_session,
            "existed": entry.existed,
            "modified_during_session": False,
            "type": "materialized-content",
            "sensitive": False,
            "device": identity[0],
            "inode": identity[1],
            "state": state,
        }

    def record_materialized_intent(self, entry: MaterializedFile) -> None:
        entries = self.data["materialized_files"]
        assert isinstance(entries, list)
        entries.append(self._materialized_record(entry, state="pending"))
        self._write()

    def record_materialized(self, entry: MaterializedFile) -> None:
        entries = self.data["materialized_files"]
        assert isinstance(entries, list)
        recorded = self._materialized_record(entry, state="ready")
        relative = recorded["path"]
        for index, existing in enumerate(entries):
            if (
                isinstance(existing, dict)
                and existing.get("path") == relative
                and existing.get("state") == "pending"
            ):
                entries[index] = recorded
                break
        else:
            entries.append(recorded)
        self._write()

    def record_directory(self, entry: MaterializedDirectory) -> None:
        directories = self.data["created_directories"]
        assert isinstance(directories, list)
        relative = self._relative(entry.path)
        if not any(
            item == relative
            or isinstance(item, dict) and item.get("path") == relative
            for item in directories
        ):
            directories.append({
                "path": relative,
                "device": entry.identity[0],
                "inode": entry.identity[1],
            })
            self._write()

    def mark_modified(self, relative: str) -> None:
        entries = self.data["materialized_files"]
        assert isinstance(entries, list)
        for entry in entries:
            if isinstance(entry, dict) and entry.get("path") == relative:
                entry["modified_during_session"] = True
        self._write()


def journal_path(target: Path, relative: object) -> Path | None:
    if not isinstance(relative, str):
        return None
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        return None
    candidate = target.joinpath(*pure.parts)
    try:
        candidate.resolve(strict=False).relative_to(target.resolve())
    except ValueError:
        return None
    return candidate


def load_session_journal(path: Path) -> dict[str, object]:
    def approved_legacy(payload: bytes) -> bool:
        try:
            candidate = json.loads(payload.decode("utf-8"))
        except (UnicodeError, ValueError, json.JSONDecodeError):
            return False
        return (
            isinstance(candidate, dict)
            and candidate.get("format") == 1
            and "private_filesystem" not in candidate
        )

    try:
        payload, migrated_legacy_acl = (
            private_fs.read_private_file_with_legacy_windows_migration(
                path,
                maximum_size=_MAX_SESSION_JOURNAL_BYTES,
                approve_legacy=approved_legacy,
            )
        )
        data = json.loads(payload.decode("utf-8"))
        required = {
            "format", "project", "session_id", "created_at", "status",
            "processes", "temporary_files", "materialized_files", "created_directories",
        }
        optional = {
            "controller", "recovery_actions", "recovered_at",
            "background", "background_log", "private_filesystem",
        }
        if (
            not isinstance(data, dict)
            or set(data) < required
            or set(data) - required - optional
            or data.get("format") != 1
            or data.get("project") != "x86qw"
            or (
                data.get("private_filesystem") is not None
                and (
                    type(data.get("private_filesystem")) is not int
                    or data.get("private_filesystem") != 1
                )
            )
            or not isinstance(data.get("session_id"), str)
            or not data.get("session_id")
            or not isinstance(data.get("created_at"), str)
            or data.get("status") not in {"starting", "running", "stopping", "interrupted", "clean"}
            or not all(isinstance(data.get(field), list) for field in (
                "processes", "temporary_files", "materialized_files", "created_directories",
            ))
        ):
            raise ValueError("identidade inválida")
        data.setdefault("controller", None)
        data.setdefault("background", False)
        data.setdefault("background_log", None)
        if (
            not isinstance(data["background"], bool)
            or data["background_log"] is not None
            and not isinstance(data["background_log"], str)
        ):
            raise ValueError("execução em segundo plano inválida")
        for entry in data["temporary_files"]:
            if isinstance(entry, dict):
                entry.setdefault("type", "temporary-config")
                entry.setdefault("sensitive", True)
                entry.setdefault("state", "ready")
                if entry.get("sensitive") is True:
                    entry.pop("expected_hash", None)
                    entry.pop("expected_size", None)
        for entry in data["materialized_files"]:
            if isinstance(entry, dict):
                entry.setdefault("type", "materialized-content")
                entry.setdefault("sensitive", False)
                entry.setdefault("state", "ready")
        controller = data.get("controller")
        if controller is not None and (
            not isinstance(controller, dict)
            or set(controller) != {"pid", "creation_token", "executable", "command"}
            or type(controller.get("pid")) is not int
            or not all(
                isinstance(controller.get(field), str) and controller.get(field)
                for field in ("creation_token", "executable", "command")
            )
        ):
            raise ValueError("controlador inválido")
        for entry in data["processes"]:
            if isinstance(entry, dict):
                entry.setdefault("parameters", {})
                entry.setdefault("runtime_executable", entry.get("executable"))
                entry.setdefault("runtime_pid", entry.get("pid"))
                entry.setdefault("state", "ready")
            if (
                not isinstance(entry, dict)
                or type(entry.get("pid")) is not int
                or not isinstance(entry.get("label"), str)
                or not isinstance(entry.get("parameters"), dict)
                or not (
                    entry.get("runtime_executable") is None
                    or isinstance(entry.get("runtime_executable"), str)
                )
                or entry.get("state") not in {"pending", "ready"}
                or entry.get("state") == "pending"
                and (
                    not isinstance(entry.get("runtime_executable"), str)
                    or not entry.get("runtime_executable")
                    or entry.get("runtime_pid") is not None
                )
                or entry.get("state") == "ready"
                and (
                    type(entry.get("runtime_pid")) is not int
                    or entry.get("runtime_pid") <= 0
                )
                or not all(
                    isinstance(name, str) and isinstance(value, str)
                    for name, value in entry.get("parameters", {}).items()
                )
            ):
                raise ValueError("processo inválido")
        for collection in ("temporary_files", "materialized_files"):
            for entry in data[collection]:
                expected_size = entry.get("expected_size") if isinstance(entry, dict) else None
                if (
                    not isinstance(entry, dict)
                    or not isinstance(entry.get("path"), str)
                    or not isinstance(entry.get("created_by_session"), bool)
                    or not isinstance(entry.get("sensitive"), bool)
                    or entry.get("state") not in {"pending", "ready"}
                    or (
                        ("device" in entry) != ("inode" in entry)
                        or "device" in entry
                        and (
                            type(entry.get("device")) is not int
                            or type(entry.get("inode")) is not int
                        )
                    )
                    or expected_size is not None
                    and (
                        type(expected_size) is not int
                        or not 0 <= expected_size <= MAX_MANAGED_FILE_SIZE
                    )
                ):
                    raise ValueError("arquivo inválido")
        if not all(
            isinstance(entry, str)
            or isinstance(entry, dict)
            and set(entry) == {"path", "device", "inode"}
            and isinstance(entry.get("path"), str)
            and type(entry.get("device")) is int
            and type(entry.get("inode")) is int
            for entry in data["created_directories"]
        ):
            raise ValueError("diretório inválido")
        if migrated_legacy_acl or (
            os.name == "nt" and "private_filesystem" not in data
        ):
            data[_LEGACY_ACL_MIGRATED] = True
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise InstallerError(
            f"Journal de sessão inválido preservado para inspeção: {path.parent}"
        ) from error
    return data


def journal_controller_probe(data: dict[str, object]) -> session_control.ProcessProbe | None:
    controller = data.get("controller")
    if controller is None:
        return None
    assert isinstance(controller, dict)
    return session_control.probe_expected_process(
        int(controller["pid"]),
        str(controller["creation_token"]),
        str(controller["executable"]),
    )


def journal_process_probe(entry: object) -> session_control.ProcessProbe:
    if not isinstance(entry, dict) or type(entry.get("pid")) is not int:
        return session_control.ProcessProbe(
            "inconclusive", detail="registro de processo incompleto",
        )
    pid = int(entry["pid"])
    token = entry.get("creation_token")
    executable = entry.get("executable")
    if not isinstance(token, str) or not token or not isinstance(executable, str) or not executable:
        probe = session_control.process_identity(pid)
        if probe.status == "dead":
            return probe
        return session_control.ProcessProbe(
            "inconclusive", probe.identity,
            "journal legado não possui token de criação e executável",
        )
    return session_control.probe_expected_process(pid, token, executable)


def legacy_clean_journal_is_inert(data: dict[str, object]) -> bool:
    if data.get(_LEGACY_ACL_MIGRATED) is not True or data.get("status") != "clean":
        return False
    controller_probe = journal_controller_probe(data)
    if controller_probe is not None and controller_probe.status not in {
        "dead", "identity_mismatch",
    }:
        return False
    processes = data.get("processes")
    if not isinstance(processes, list):
        return False
    return all(
        journal_process_probe(entry).status in {"dead", "identity_mismatch"}
        for entry in processes
    )


def _remove_incomplete_initial_session(directory: Path, sessions: Path) -> bool:
    """Remove only an unambiguous pre-journal crash residue."""

    if _SESSION_DIRECTORY_NAME.fullmatch(directory.name) is None:
        return False
    try:
        metadata = directory.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            return False
        directory_identity = persistent_path_identity(directory, directory=True)
        entries = list(directory.iterdir())
    except OSError:
        return False
    if len(entries) == 1:
        staging = entries[0]
        if _INITIAL_JOURNAL_STAGING_NAME.fullmatch(staging.name) is None:
            return False
        try:
            private_fs.validate_private_file(staging)
            staging_metadata = staging.lstat()
            if staging_metadata.st_size != 0:
                return False
            staging_identity = persistent_path_identity(
                staging, directory=False,
            )
            private_fs.unlink_private_file(
                staging, expected_identity=staging_identity,
            )
        except OSError:
            return False
    elif entries:
        return False
    return cleanup_materialized_directory(MaterializedDirectory(
        directory, sessions, directory_identity,
    ))


def session_journal_paths(target: Path, session_id: str | None = None) -> list[Path]:
    sessions = target / ".x86qw" / "sessions"
    if not lexists(sessions):
        return []
    if sessions.is_symlink() or not sessions.is_dir():
        raise InstallerError(f"Diretório de sessões ausente ou inseguro: {sessions}")
    try:
        private_fs.migrate_legacy_private_directory(sessions)
    except OSError as error:
        raise InstallerError(
            f"Diretório de sessões sem privacidade comprovada: {sessions}"
        ) from error
    directories = [sessions / session_id] if session_id is not None else sorted(sessions.iterdir())
    paths: list[Path] = []
    for directory in directories:
        if not directory.is_dir() or directory.is_symlink():
            continue
        try:
            private_fs.migrate_legacy_private_directory(directory)
        except OSError as error:
            raise InstallerError(
                f"Diretório de sessão sem privacidade comprovada: {directory}"
            ) from error
        journal = directory / "session.json"
        if lexists(journal):
            paths.append(journal)
            continue
        if _remove_incomplete_initial_session(directory, sessions):
            continue
        raise InstallerError(
            f"Journal de sessão inválido preservado para inspeção: {directory}"
        )
    return paths


def assert_recovery_processes_confirmable(
    target: Path,
    session_id: str | None = None,
) -> None:
    for path in session_journal_paths(target, session_id):
        data = load_session_journal(path)
        if (
            data.get(_LEGACY_ACL_MIGRATED) is True
            and not legacy_clean_journal_is_inert(data)
        ):
            raise InstallerError(
                "Journal legado do Windows foi protegido, mas seu conteúdo histórico "
                "não pode autorizar encerramento ou remoção automática. "
                f"Inspecione a sessão preservada em {path.parent} antes de removê-la manualmente."
            )
        if data.get("status") == "clean":
            continue
        processes = data.get("processes", [])
        assert isinstance(processes, list)
        controller_probe = journal_controller_probe(data)
        if controller_probe is not None and controller_probe.status == "alive":
            controller = data["controller"]
            assert isinstance(controller, dict)
            raise InstallerError(
                "O controlador registrado no journal continua ativo; recuperação bloqueada.\n"
                f"Sessão: {data['session_id']}\nControlador: PID {controller['pid']}"
            )
        if controller_probe is not None and controller_probe.status == "inconclusive":
            raise InstallerError(
                "Não foi possível confirmar o controlador do journal; lock, processos e arquivos "
                f"foram preservados em {path.parent}."
            )
        for entry in processes:
            probe = journal_process_probe(entry)
            if probe.status == "inconclusive":
                label = entry.get("label", "processo") if isinstance(entry, dict) else "processo"
                raise InstallerError(
                    f"Não foi possível confirmar a identidade de {label} na sessão abandonada; "
                    f"lock, journal e arquivos foram preservados em {path.parent}."
                )
            if controller_probe is None and probe.status == "alive":
                raise InstallerError(
                    "Journal legado possui processo vivo e não prova que seu controlador terminou; "
                    f"processos e arquivos foram preservados em {path.parent}."
                )


def process_still_matches(entry: dict[str, object]) -> bool:
    probe = journal_process_probe(entry)
    if probe.status == "alive":
        return True
    if probe.status != "dead" or os.name == "nt":
        return False
    process_group = entry.get("process_group")
    return (
        type(process_group) is int
        and process_group == entry.get("pid")
        and process_group > 1
        and posix_process_group_status(process_group) == "alive"
    )


def signal_recorded_process(entry: dict[str, object], *, force: bool) -> None:
    pid = int(entry["pid"])
    if os.name == "nt":
        session_control.terminate_windows_process(pid, 1 if force else 0)
        return
    process_group = entry.get("process_group")
    selected_signal = signal.SIGKILL if force else signal.SIGTERM
    if type(process_group) is int and process_group == pid and process_group > 1:
        os.killpg(process_group, selected_signal)
    else:
        os.kill(pid, selected_signal)


def terminate_recorded_process(entry: dict[str, object], timeout: float = 4.0) -> str:
    probe = journal_process_probe(entry)
    if probe.status == "dead":
        process_group = entry.get("process_group")
        if (
            os.name == "nt"
            or type(process_group) is not int
            or process_group != entry.get("pid")
            or process_group <= 1
        ):
            return "already_dead"
        group_status = posix_process_group_status(process_group)
        if group_status == "dead":
            return "already_dead"
        if group_status == "inconclusive":
            raise InstallerError(
                f"O líder PID {entry['pid']} terminou, mas seu grupo ficou inconclusivo."
            )
    if probe.status == "identity_mismatch":
        return "identity_mismatch"
    if probe.status not in {"alive", "dead"}:
        raise InstallerError("A identidade de um processo órfão ficou inconclusiva.")
    try:
        signal_recorded_process(entry, force=False)
    except ProcessLookupError:
        return "already_dead"
    except OSError as error:
        raise InstallerError(f"Não foi possível encerrar o processo órfão PID {entry['pid']}.") from error
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_still_matches(entry):
            return "terminated"
        time.sleep(0.05)
    try:
        signal_recorded_process(entry, force=True)
    except ProcessLookupError:
        return "terminated"
    except OSError as error:
        raise InstallerError(f"Não foi possível forçar o processo órfão PID {entry['pid']}.") from error
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not process_still_matches(entry):
            return "killed"
        time.sleep(0.05)
    raise InstallerError(f"O processo órfão PID {entry['pid']} não encerrou; estado preservado.")


def write_session_journal(path: Path, data: dict[str, object], prefix: str = ".recovery-") -> None:
    del prefix
    serialized = {
        key: value for key, value in data.items()
        if key != _LEGACY_ACL_MIGRATED
    }
    try:
        atomic_write_json(path, serialized, private=True)
        private_fs.validate_private_file(path)
    except AtomicWriteError as error:
        raise InstallerError("O journal privado de recuperação não pôde ser gravado.") from error
    except OSError as error:
        raise InstallerError("O journal privado de recuperação não pôde ser validado.") from error


def reconcile_journal(
    target: Path,
    path: Path,
    *,
    reporter: RecoveryReporter,
) -> None:
    try:
        data = load_session_journal(path)
    except InstallerError:
        reporter.warning(f"Journal de sessão inválido preservado para inspeção: {path.parent}")
        return
    if (
        data.get(_LEGACY_ACL_MIGRATED) is True
        and not legacy_clean_journal_is_inert(data)
    ):
        raise InstallerError(
            "Journal legado do Windows não possui autoridade para autorizar recuperação; "
            f"estado preservado em {path.parent}."
        )
    if data.get("status") == "clean":
        return
    controller_probe = journal_controller_probe(data)
    if controller_probe is not None and controller_probe.status == "alive":
        raise InstallerError(
            f"O controlador da sessão {data['session_id']} continua ativo; recuperação bloqueada."
        )
    if controller_probe is not None and controller_probe.status == "inconclusive":
        raise InstallerError(
            f"O controlador da sessão {data['session_id']} está inconclusivo; estado preservado."
        )
    data["status"] = "interrupted"
    actions = data.setdefault("recovery_actions", [])
    if not isinstance(actions, list):
        raise InstallerError(f"Ações de recuperação inválidas no journal: {path.parent}")
    processes = data.get("processes", [])
    assert isinstance(processes, list)
    if controller_probe is None:
        for entry in processes:
            legacy_probe = journal_process_probe(entry)
            if legacy_probe.status in {"alive", "inconclusive"}:
                raise InstallerError(
                    "Journal legado não prova que seu controlador terminou; "
                    f"processos e arquivos foram preservados em {path.parent}."
                )
    for entry in reversed(processes):
        probe = journal_process_probe(entry)
        if probe.status == "inconclusive":
            raise InstallerError(
                f"Processo possivelmente ativo não pôde ser confirmado; journal preservado: {path.parent}"
            )
        if not isinstance(entry, dict):
            continue
        result = terminate_recorded_process(entry)
        actions.append({
            "at": datetime.now(timezone.utc).isoformat(),
            "label": entry.get("label", "processo"),
            "pid": entry.get("pid"),
            "result": result,
        })
    for collection in ("temporary_files", "materialized_files"):
        entries = data.get(collection, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("created_by_session"):
                continue
            candidate = journal_path(target, entry.get("path"))
            expected = entry.get("expected_hash")
            expected_size = entry.get("expected_size")
            identity = (
                (entry.get("device"), entry.get("inode"))
                if type(entry.get("device")) is int
                and type(entry.get("inode")) is int
                else None
            )
            sensitive = entry.get("sensitive") is True
            if candidate is None or not lexists(candidate):
                continue
            if sensitive:
                if cleanup_sensitive_temporary(candidate, identity):
                    continue
                entry["modified_during_session"] = True
                reporter.warning(
                    "Temporário sensível substituído foi preservado: "
                    f"{candidate}"
                )
                raise InstallerError(
                    "Temporário sensível sem identidade original confirmável foi "
                    f"preservado: {candidate}"
                )
            elif collection == "materialized_files":
                if (
                    isinstance(expected, str)
                    and type(expected_size) is int
                    and identity is not None
                ):
                    materialized_entry = MaterializedFile(
                        candidate,
                        expected,
                        str(entry.get("origin", "journal de sessão")),
                        True,
                        bool(entry.get("existed")),
                        target,
                        identity,
                        expected_size,
                    )
                    if cleanup_materialized_file(materialized_entry):
                        continue
                entry["modified_during_session"] = True
                reporter.warning(f"Arquivo de sessão alterado foi preservado: {candidate}")
            elif (
                isinstance(expected, str)
                and type(expected_size) is int
                and identity is not None
            ):
                temporary_entry = MaterializedFile(
                    candidate,
                    expected,
                    str(entry.get("origin", "temporário de sessão")),
                    True,
                    False,
                    target,
                    identity,
                    expected_size,
                )
                if not cleanup_materialized_file(temporary_entry):
                    entry["modified_during_session"] = True
                    reporter.warning(f"Arquivo de sessão alterado foi preservado: {candidate}")
            else:
                entry["modified_during_session"] = True
                reporter.warning(f"Arquivo de sessão alterado foi preservado: {candidate}")
    directories = data.get("created_directories", [])
    if isinstance(directories, list):
        for recorded in reversed(directories):
            relative = recorded.get("path") if isinstance(recorded, dict) else recorded
            candidate = journal_path(target, relative)
            if candidate is None or not lexists(candidate):
                continue
            identity = (
                (recorded.get("device"), recorded.get("inode"))
                if isinstance(recorded, dict)
                and type(recorded.get("device")) is int
                and type(recorded.get("inode")) is int
                else None
            )
            if identity is not None and cleanup_materialized_directory(
                MaterializedDirectory(candidate, target, identity),
            ):
                continue
            reporter.warning(
                "Diretório de sessão sem identidade confirmável foi preservado: "
                f"{candidate}"
            )
    data["status"] = "clean"
    data["recovered_at"] = datetime.now(timezone.utc).isoformat()
    write_session_journal(path, data)


def recover_sessions(target: Path, *, reporter: RecoveryReporter) -> None:
    """Recover every abandoned session after one full confirmability preflight."""

    assert_recovery_processes_confirmable(target)
    for path in session_journal_paths(target):
        reconcile_journal(target, path, reporter=reporter)


__all__ = (
    "MaterializedDirectory",
    "MaterializedFile",
    "RecoveryReporter",
    "SessionJournal",
    "activate_background_log",
    "assert_recovery_processes_confirmable",
    "journal_controller_probe",
    "journal_path",
    "journal_process_probe",
    "legacy_clean_journal_is_inert",
    "load_session_journal",
    "process_still_matches",
    "publish_stop_request",
    "reconcile_journal",
    "recover_sessions",
    "session_journal_paths",
    "signal_recorded_process",
    "terminate_recorded_process",
    "unlink_stop_request",
    "write_session_journal",
)
