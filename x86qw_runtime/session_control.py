"""Cross-platform exclusive operation lock for one x86QW installation."""

from __future__ import annotations

import json
import os
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path

from x86qw_runtime.io import private_fs
from x86qw_runtime.platform.locking import (
    SessionControlError,
    installation_acquisition_mutex as _installation_acquisition_mutex,
    windows_acquisition_mutex as _windows_acquisition_mutex,
    windows_acquisition_mutex_name as _windows_acquisition_mutex_name,
)
from x86qw_runtime.platform.processes import (
    ProcessIdentity,
    ProcessProbe,
    _windows_kernel32,
    probe_expected_process,
    process_identity,
    terminate_windows_process,
)


SERVICE_COMMANDS = frozenset({"host", "proxy", "qtv"})
MAINTENANCE_COMMANDS = frozenset({
    "install", "components", "presets", "update", "upgrade", "repair",
    "cleanup", "uninstall", "purge",
})
LOCK_COMMANDS = SERVICE_COMMANDS | MAINTENANCE_COMMANDS
_LEGACY_ACL_MIGRATED = "_x86qw_legacy_acl_migrated"


def lexists(path: Path) -> bool:
    return os.path.lexists(path)


def new_session_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{secrets.token_hex(6)}"


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


def _private_file_identity(path: Path) -> tuple[int, int]:
    private_fs.validate_private_file(path)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise SessionControlError(f"Lock de operação mudou de tipo: {path}")
    return int(metadata.st_dev), int(metadata.st_ino)


def _read_lock_owner_with_identity(
    path: Path,
) -> tuple[dict[str, object], tuple[int, int]]:
    before = _private_file_identity(path)
    owner = read_lock_owner(path)
    identity = _private_file_identity(path)
    if identity != before:
        raise SessionControlError(
            f"Lock de operação mudou de identidade durante a leitura: {path}"
        )
    return owner, identity


def _move_private_lock_no_replace(
    source: Path, destination: Path, expected_identity: tuple[int, int],
) -> None:
    """Move one exact lock without replacing a concurrent owner."""

    os.link(source, destination)
    try:
        if _private_file_identity(destination) != expected_identity:
            raise SessionControlError(
                f"Lock movido mudou de identidade: {destination}"
            )
        private_fs.unlink_private_file(
            source, expected_identity=expected_identity,
        )
    except BaseException:
        try:
            private_fs.unlink_private_file(
                destination, expected_identity=expected_identity,
            )
        except OSError:
            pass
        raise


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
        active_identity: tuple[int, int] | None = None,
        reclaimed_identity: tuple[int, int] | None = None,
        created_directories: tuple[Path, ...] = (),
        control_lease: object | None = None,
    ) -> None:
        self.target = target
        self.path = path
        self.owner = owner
        self.session_id = str(owner["session_id"])
        self.reclaimed_path = reclaimed_path
        self.active_identity = active_identity
        self.reclaimed_identity = reclaimed_identity
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
        reclaimed_identity: tuple[int, int] | None = None
        while True:
            try:
                descriptor = private_fs.create_private_file(path)
            except FileExistsError:
                existing, existing_identity = _read_lock_owner_with_identity(path)
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
                    _move_private_lock_no_replace(
                        path, candidate, existing_identity,
                    )
                except FileNotFoundError:
                    continue
                candidate_identity = _private_file_identity(candidate)
                if candidate_identity != existing_identity:
                    raise SessionControlError(
                        "O lock mudou de identidade durante a recuperação; "
                        f"{candidate} foi preservado para inspeção."
                    )
                reclaimed_path = candidate
                reclaimed_identity = candidate_identity
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
                if (
                    reclaimed_path is not None
                    and reclaimed_identity is not None
                    and lexists(reclaimed_path)
                ):
                    try:
                        _move_private_lock_no_replace(
                            reclaimed_path, path, reclaimed_identity,
                        )
                        reclaimed_path = None
                        reclaimed_identity = None
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
                active_identity=created_identity,
                reclaimed_identity=reclaimed_identity,
                created_directories=tuple(created),
                control_lease=control_lease,
            )

    def confirm_recovery(self) -> None:
        if self.reclaimed_path is not None and lexists(self.reclaimed_path):
            if self.reclaimed_identity is None:
                raise SessionControlError(
                    "A identidade do lock recuperado não está disponível."
                )
            private_fs.unlink_private_file(
                self.reclaimed_path,
                expected_identity=self.reclaimed_identity,
            )
        self.reclaimed_path = None
        self.reclaimed_identity = None

    def release(self, *, restore_reclaimed: bool = False) -> None:
        try:
            if lexists(self.path):
                current, _current_identity = _read_lock_owner_with_identity(self.path)
                if current.get("session_id") == self.session_id:
                    if self.active_identity is None:
                        raise SessionControlError(
                            "A identidade do lock ativo não está disponível."
                        )
                    private_fs.unlink_private_file(
                        self.path, expected_identity=self.active_identity,
                    )
            if self.reclaimed_path is not None and lexists(self.reclaimed_path):
                if self.reclaimed_identity is None:
                    raise SessionControlError(
                        "A identidade do lock recuperado não está disponível."
                    )
                if restore_reclaimed:
                    _move_private_lock_no_replace(
                        self.reclaimed_path, self.path, self.reclaimed_identity,
                    )
                else:
                    private_fs.unlink_private_file(
                        self.reclaimed_path,
                        expected_identity=self.reclaimed_identity,
                    )
            self.reclaimed_path = None
            self.reclaimed_identity = None
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
