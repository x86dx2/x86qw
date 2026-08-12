"""Private ownership journals for ephemeral local gameplay configurations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from x86qw_runtime.errors import InstallerError
from x86qw_runtime.io import private_fs
from x86qw_runtime.io.atomic import (
    AtomicWriteError,
    atomic_create_bytes,
    atomic_write_bytes,
    sync_directory,
)
from x86qw_runtime.io.managed_files import (
    MaterializedFile,
    cleanup_materialized_file,
    file_sha256,
    persistent_path_identity,
    remove_persistent_identity_bound_path,
)
from x86qw_runtime.io.paths import lexists
from x86qw_runtime.platform.processes import (
    ProcessIdentity,
    process_identity,
    probe_expected_process,
)


_CONFIG_NAME = re.compile(r"x86qw-ktx-session-([0-9a-f]{24})\.cfg\Z")
_STAGING_NAME = re.compile(r"\.x86qw-ktx-session-([0-9a-f]{24})\.stage\Z")
_MAX_JOURNAL_SIZE = 16 * 1024
_MAX_CONFIG_SIZE = 1024 * 1024


@dataclass(frozen=True)
class RuntimeConfigOwnership:
    journal: Path
    journal_identity: tuple[int, int]
    config: Path
    staging: Path
    config_identity: tuple[int, int] | None
    sha256: str
    size: int
    state: str


@dataclass(frozen=True)
class RuntimeConfigRecovery:
    removed: tuple[Path, ...]
    preserved: tuple[Path, ...]


def _journal_directory(target: Path) -> Path:
    return target / ".x86qw" / "gameplay" / "ktx-runtime-configs"


def _relative_under(target: Path, path: Path) -> str:
    try:
        relative = path.relative_to(target)
    except ValueError as error:
        raise InstallerError(f"Caminho KTX fora da instalação: {path}") from error
    return PurePosixPath(*relative.parts).as_posix()


def _validate_config_relative(relative: PurePosixPath) -> str:
    if (
        relative.is_absolute()
        or relative.parent != PurePosixPath("qw")
        or not _CONFIG_NAME.fullmatch(relative.name)
    ):
        raise ValueError("caminho público inválido")
    return relative.name


def _validate_staging_relative(relative: PurePosixPath) -> str:
    expected_parent = PurePosixPath(".x86qw/gameplay/ktx-runtime-configs")
    if (
        relative.is_absolute()
        or relative.parent != expected_parent
        or not _STAGING_NAME.fullmatch(relative.name)
    ):
        raise ValueError("caminho de staging inválido")
    return relative.name


def _journal_document(
    target: Path,
    config: Path,
    staging: Path,
    identity: tuple[int, int] | None,
    digest: str,
    size: int,
    state: str,
) -> dict[str, object]:
    controller = process_identity(os.getpid())
    if controller.status != "alive" or controller.identity is None:
        raise InstallerError("Não foi possível registrar o controlador da configuração KTX.")
    return _journal_document_for_controller(
        target, config, staging, identity, digest, size, state, controller.identity,
    )


def _journal_document_for_controller(
    target: Path,
    config: Path,
    staging: Path,
    identity: tuple[int, int] | None,
    digest: str,
    size: int,
    state: str,
    controller: ProcessIdentity,
) -> dict[str, object]:
    """Build a journal document bound to a verified process identity."""

    return {
        "format": 1,
        "project": "x86qw",
        "type": "ktx-runtime-config",
        "state": state,
        "controller": {
            "pid": controller.pid,
            "creation_token": controller.creation_token,
            "executable": controller.executable,
        },
        "config": {
            "path": _relative_under(target, config),
            "staging_path": _relative_under(target, staging),
            "device": identity[0] if identity is not None else None,
            "inode": identity[1] if identity is not None else None,
            "sha256": digest,
            "size": size,
        },
    }


def _unlink_journal(ownership: RuntimeConfigOwnership) -> None:
    private_fs.unlink_private_file(
        ownership.journal, expected_identity=ownership.journal_identity,
    )


def _unlink_staging(target: Path, ownership: RuntimeConfigOwnership) -> bool:
    if ownership.config_identity is None:
        return not lexists(ownership.staging)
    return remove_persistent_identity_bound_path(
        ownership.staging,
        ownership.config_identity,
        directory=False,
    )


def _cleanup_public_config(target: Path, ownership: RuntimeConfigOwnership) -> bool:
    if ownership.config_identity is None:
        return not lexists(ownership.config)
    return cleanup_materialized_file(MaterializedFile(
        path=ownership.config,
        expected_hash=ownership.sha256,
        origin="x86qw:ktx-runtime-config",
        created_by_session=True,
        existed=False,
        root=target,
        identity=ownership.config_identity,
        expected_size=ownership.size,
    ))


def _discard_owned_paths(
    target: Path, ownership: RuntimeConfigOwnership,
) -> tuple[bool, tuple[Path, ...]]:
    preserved: list[Path] = []
    if not _unlink_staging(target, ownership) and lexists(ownership.staging):
        preserved.append(ownership.staging)
    removed = _cleanup_public_config(target, ownership)
    if not removed and lexists(ownership.config):
        preserved.append(ownership.config)
    return removed, tuple(preserved)


def _verified_intent_staging(
    ownership: RuntimeConfigOwnership,
) -> RuntimeConfigOwnership:
    if lexists(ownership.config):
        raise InstallerError(
            "Intenção KTX possui um caminho público não comprovado; journal e "
            f"arquivo foram preservados para inspeção: {ownership.journal}"
        )
    try:
        metadata = ownership.staging.lstat()
        valid = (
            not stat.S_ISLNK(metadata.st_mode)
            and stat.S_ISREG(metadata.st_mode)
            and metadata.st_size == ownership.size
            and file_sha256(
                ownership.staging, expected_size=ownership.size,
            ) == ownership.sha256
        )
    except OSError:
        valid = False
    if not valid:
        raise InstallerError(
            "Staging KTX parcial ou divergente foi preservado com seu journal. "
            f"Inspecione {ownership.staging}; depois remova-o ou mova-o e execute repair."
        )
    return RuntimeConfigOwnership(
        ownership.journal,
        ownership.journal_identity,
        ownership.config,
        ownership.staging,
        persistent_path_identity(ownership.staging, directory=False),
        ownership.sha256,
        ownership.size,
        ownership.state,
    )


def create_runtime_config(target: Path, payload: bytes) -> RuntimeConfigOwnership:
    """Journal a private inode before publishing its public ezQuake name."""

    if not isinstance(payload, bytes) or len(payload) > _MAX_CONFIG_SIZE:
        raise InstallerError("Payload da configuração KTX inválido.")
    target = Path(target)
    directory = target / "qw"
    if directory.is_symlink() or not directory.is_dir():
        raise InstallerError(f"Diretório de configuração do ezQuake inválido: {directory}")
    recover_runtime_configs(target)
    journal_directory = _journal_directory(target)
    try:
        private_fs.ensure_private_directories(journal_directory, stop=target)
    except OSError as error:
        raise InstallerError("Não foi possível criar o journal privado do gameplay.") from error

    token = os.urandom(12).hex()
    staging = journal_directory / f".x86qw-ktx-session-{token}.stage"
    config = directory / f"x86qw-ktx-session-{token}.cfg"
    journal = journal_directory / f"x86qw-ktx-session-{token}.json"
    digest = hashlib.sha256(payload).hexdigest()
    intent = _journal_document(
        target, config, staging, None, digest, len(payload), "intent",
    )
    intent_payload = (
        json.dumps(intent, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode()
    try:
        atomic_create_bytes(journal, intent_payload, mode=0o600)
        private_fs.validate_private_file(journal)
    except AtomicWriteError as error:
        if error.committed_identity is not None:
            try:
                private_fs.unlink_private_file(
                    journal, expected_identity=error.committed_identity,
                )
            except OSError as cleanup_error:
                raise InstallerError(
                    f"Intenção KTX comprometida preservada para inspeção: {journal}"
                ) from cleanup_error
        raise InstallerError("Não foi possível registrar a intenção KTX.") from error
    except OSError as error:
        raise InstallerError("Não foi possível registrar a intenção KTX.") from error

    identity: tuple[int, int] | None = None
    ownership: RuntimeConfigOwnership | None = None
    try:
        atomic_create_bytes(staging, payload, mode=0o600)
        metadata = staging.lstat()
        identity = persistent_path_identity(staging, directory=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size != len(payload)
            or file_sha256(staging, expected_size=len(payload)) != digest
        ):
            raise OSError("conteúdo divergente no staging KTX")
        ready = _journal_document(
            target, config, staging, identity, digest, len(payload), "ready",
        )
        ready_payload = (
            json.dumps(ready, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode()
        atomic_write_bytes(journal, ready_payload, mode=0o600)
        private_fs.validate_private_file(journal)
        ownership = RuntimeConfigOwnership(
            journal,
            persistent_path_identity(journal, directory=False),
            config,
            staging,
            identity,
            digest,
            len(payload),
            "ready",
        )
        os.link(staging, config, follow_symlinks=False)
        sync_directory(directory)
        _unlink_staging(target, ownership)
        return ownership
    except (AtomicWriteError, OSError, ValueError) as error:
        if ownership is not None:
            _removed, preserved = _discard_owned_paths(target, ownership)
            if not preserved:
                try:
                    _unlink_journal(ownership)
                except OSError:
                    pass
        elif identity is not None:
            try:
                private_fs.unlink_private_file(staging, expected_identity=identity)
            except OSError:
                pass
        raise InstallerError(
            f"Não foi possível publicar a configuração KTX: {error}"
        ) from error


def _read_ownership(
    target: Path, journal: Path,
) -> tuple[RuntimeConfigOwnership, dict[str, object]]:
    try:
        payload = private_fs.read_private_file(journal, maximum_size=_MAX_JOURNAL_SIZE)
        document = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise InstallerError(
            f"Journal KTX inválido preservado para inspeção: {journal}"
        ) from error
    if not isinstance(document, dict) or set(document) != {
        "format", "project", "type", "state", "controller", "config",
    }:
        raise InstallerError(f"Journal KTX inválido preservado para inspeção: {journal}")
    controller = document.get("controller")
    config_data = document.get("config")
    if (
        document.get("format") != 1
        or document.get("project") != "x86qw"
        or document.get("type") != "ktx-runtime-config"
        or document.get("state") not in {"intent", "pending", "ready"}
        or not isinstance(controller, dict)
        or set(controller) != {"pid", "creation_token", "executable"}
        or type(controller.get("pid")) is not int
        or not isinstance(controller.get("creation_token"), str)
        or not controller.get("creation_token")
        or not isinstance(controller.get("executable"), str)
        or not controller.get("executable")
        or not isinstance(config_data, dict)
        or set(config_data) != {
            "path", "staging_path", "device", "inode", "sha256", "size",
        }
        or not isinstance(config_data.get("path"), str)
        or not isinstance(config_data.get("staging_path"), str)
        or config_data.get("device") is not None
        and type(config_data.get("device")) is not int
        or config_data.get("inode") is not None
        and type(config_data.get("inode")) is not int
        or type(config_data.get("size")) is not int
        or not isinstance(config_data.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", config_data.get("sha256", ""))
        or not 0 <= config_data.get("size", -1) <= _MAX_CONFIG_SIZE
    ):
        raise InstallerError(f"Journal KTX inválido preservado para inspeção: {journal}")
    try:
        config_relative = PurePosixPath(config_data["path"])
        staging_relative = PurePosixPath(config_data["staging_path"])
        config_name = _validate_config_relative(config_relative)
        staging_name = _validate_staging_relative(staging_relative)
    except (TypeError, ValueError) as error:
        raise InstallerError(
            f"Journal KTX inválido preservado para inspeção: {journal}"
        ) from error
    if (
        _CONFIG_NAME.fullmatch(config_name).group(1)
        != _STAGING_NAME.fullmatch(staging_name).group(1)
    ):
        raise InstallerError(f"Journal KTX inválido preservado para inspeção: {journal}")
    state = str(document["state"])
    device = config_data["device"]
    inode = config_data["inode"]
    if state == "intent":
        if device is not None or inode is not None:
            raise InstallerError(f"Journal KTX inválido preservado para inspeção: {journal}")
        identity = None
    else:
        if type(device) is not int or type(inode) is not int:
            raise InstallerError(f"Journal KTX inválido preservado para inspeção: {journal}")
        identity = int(device), int(inode)
    ownership = RuntimeConfigOwnership(
        journal,
        persistent_path_identity(journal, directory=False),
        target.joinpath(*config_relative.parts),
        target.joinpath(*staging_relative.parts),
        identity,
        str(config_data["sha256"]),
        int(config_data["size"]),
        state,
    )
    return ownership, controller


def transfer_runtime_config_controller(
    target: Path,
    ownership: RuntimeConfigOwnership,
    process: object,
) -> RuntimeConfigOwnership:
    """Transfer recovery ownership from the launcher to its child process.

    The launcher exits immediately after opening ezQuake.  Binding the journal
    to the returned process (the POSIX guardian on Unix, or ezQuake directly
    on Windows) prevents a later invocation from treating a still-running
    client configuration as abandoned.
    """

    pid = getattr(process, "pid", None)
    if type(pid) is not int or pid <= 1:
        raise InstallerError("O processo ezQuake não expôs um PID válido para o journal KTX.")
    probe = process_identity(pid)
    if probe.status != "alive" or probe.identity is None:
        detail = f": {probe.detail}" if probe.detail else "."
        raise InstallerError(
            "A identidade do processo ezQuake não pôde ser confirmada" + detail
        )
    target = Path(target)
    try:
        current_identity = persistent_path_identity(ownership.journal, directory=False)
    except OSError as error:
        raise InstallerError(
            f"O journal KTX desapareceu antes da transferência: {ownership.journal}"
        ) from error
    if current_identity != ownership.journal_identity:
        raise InstallerError(
            "O journal KTX mudou de identidade durante a transferência; estado preservado."
        )
    document = _journal_document_for_controller(
        target,
        ownership.config,
        ownership.staging,
        ownership.config_identity,
        ownership.sha256,
        ownership.size,
        ownership.state,
        probe.identity,
    )
    payload = (json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n").encode()
    try:
        atomic_write_bytes(ownership.journal, payload, mode=0o600)
        private_fs.validate_private_file(ownership.journal)
        new_identity = persistent_path_identity(ownership.journal, directory=False)
    except (AtomicWriteError, OSError) as error:
        raise InstallerError(
            f"Não foi possível transferir a ownership do journal KTX: {error}"
        ) from error
    return RuntimeConfigOwnership(
        ownership.journal,
        new_identity,
        ownership.config,
        ownership.staging,
        ownership.config_identity,
        ownership.sha256,
        ownership.size,
        ownership.state,
    )


def recover_runtime_configs(target: Path) -> RuntimeConfigRecovery:
    """Reconcile dead gameplay controllers and preserve every uncertain object."""

    target = Path(target)
    directory = _journal_directory(target)
    if not lexists(directory):
        return RuntimeConfigRecovery((), ())
    try:
        private_fs.validate_private_directory(directory)
    except OSError as error:
        raise InstallerError(f"Diretório de journals KTX inseguro: {directory}") from error
    removed: list[Path] = []
    preserved: list[Path] = []
    for journal in sorted(directory.glob("x86qw-ktx-session-*.json")):
        ownership, controller = _read_ownership(target, journal)
        probe = probe_expected_process(
            int(controller["pid"]),
            str(controller["creation_token"]),
            str(controller["executable"]),
        )
        if probe.status == "alive":
            continue
        if probe.status == "inconclusive":
            raise InstallerError(
                "Controlador da configuração KTX não pôde ser confirmado; "
                f"preservado: {ownership.config}"
            )
        if ownership.config_identity is None and lexists(ownership.staging):
            ownership = _verified_intent_staging(ownership)
        elif ownership.config_identity is None and lexists(ownership.config):
            raise InstallerError(
                f"Intenção KTX inconclusiva foi preservada para inspeção: {journal}"
            )
        config_removed, uncertain = _discard_owned_paths(target, ownership)
        if config_removed:
            removed.append(ownership.config)
        if uncertain:
            preserved.extend(uncertain)
            raise InstallerError(
                "Objetos KTX divergentes e seu journal foram preservados para "
                f"inspeção: {', '.join(str(path) for path in uncertain)}"
            )
        try:
            _unlink_journal(ownership)
        except OSError as error:
            raise InstallerError(f"Journal KTX não pôde ser removido: {journal}") from error
    return RuntimeConfigRecovery(tuple(removed), tuple(preserved))


def release_runtime_config(target: Path, ownership: RuntimeConfigOwnership) -> bool:
    """Release one current config, preserving an object that changed identity/content."""

    removed, preserved = _discard_owned_paths(Path(target), ownership)
    if preserved:
        return False
    try:
        _unlink_journal(ownership)
    except OSError:
        return False
    return removed
