"""Reversible, identity-bound mutations for small personal configuration files."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from x86qw_runtime.errors import InstallerError
from x86qw_runtime.io.atomic import AtomicWriteError, atomic_create_bytes
from x86qw_runtime.io.atomic import sync_directory
from x86qw_runtime.io.managed_files import (
    MAX_MANAGED_FILE_SIZE,
    MaterializedDirectory,
    MaterializedFile,
    cleanup_materialized_directory,
    cleanup_materialized_file,
    persistent_path_identity,
    unlink_identity_bound_regular,
)
from x86qw_runtime.io.metadata import MetadataFileError, read_bounded_regular_file
from x86qw_runtime.io.paths import lexists
from x86qw_runtime.io.quarantine import (
    QuarantineError,
    QuarantineToken,
    apply_quarantine_removal,
)
from x86qw_runtime.transaction import MutationStep


@dataclass(frozen=True)
class PersonalFileSnapshot:
    root: Path
    root_identity: tuple[int, int]
    path: Path
    payload: bytes | None
    mode: int | None
    identity: tuple[int, int] | None
    digest: str | None
    parent_topology: tuple[tuple[Path, tuple[int, int] | None], ...]

    @property
    def exists(self) -> bool:
        return self.payload is not None

    @property
    def missing_parents(self) -> tuple[Path, ...]:
        return tuple(path for path, identity in self.parent_topology if identity is None)


@dataclass(frozen=True)
class PersonalFileMutation:
    before: PersonalFileSnapshot
    installed: MaterializedFile | None
    quarantine: QuarantineToken | None
    created_directories: tuple[MaterializedDirectory, ...]


def _path_topology(
    root: Path, path: Path,
) -> tuple[tuple[Path, tuple[int, int] | None], ...]:
    root = Path(root)
    path = Path(path)
    try:
        root_metadata = root.lstat()
    except OSError as error:
        raise InstallerError(f"Raiz de configuração pessoal inválida: {root}") from error
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise InstallerError(f"Raiz de configuração pessoal inválida: {root}")
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise InstallerError(f"Arquivo pessoal fora da instalação: {path}") from error
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise InstallerError(f"Arquivo pessoal fora da instalação: {path}")
    current = root
    topology: list[tuple[Path, tuple[int, int] | None]] = []
    for part in relative.parts[:-1]:
        current /= part
        if (topology and topology[-1][1] is None) or not lexists(current):
            topology.append((current, None))
            continue
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise InstallerError(f"Diretório de configuração pessoal inválido: {current}")
        topology.append((current, (int(metadata.st_dev), int(metadata.st_ino))))
    return tuple(topology)


def _root_identity(root: Path) -> tuple[int, int]:
    try:
        return persistent_path_identity(root, directory=True)
    except OSError as error:
        raise InstallerError(f"Raiz de configuração pessoal inválida: {root}") from error


def observe_personal_file(root: Path, path: Path) -> PersonalFileSnapshot:
    """Capture stable bytes and identity without following links."""

    root, path = Path(root), Path(path)
    parent_topology = _path_topology(root, path)
    root_identity = _root_identity(root)
    if not lexists(path):
        if _root_identity(root) != root_identity:
            raise InstallerError(f"Raiz de configuração pessoal mudou: {root}")
        return PersonalFileSnapshot(
            root, root_identity, path, None, None, None, None, parent_topology,
        )
    try:
        identity = persistent_path_identity(path, directory=False)
        payload = read_bounded_regular_file(path, maximum_size=MAX_MANAGED_FILE_SIZE)
        if persistent_path_identity(path, directory=False) != identity:
            raise OSError("arquivo pessoal mudou durante a leitura")
        mode = stat.S_IMODE(path.lstat().st_mode)
        if _root_identity(root) != root_identity:
            raise OSError("raiz de configuração pessoal mudou durante a leitura")
    except (MetadataFileError, OSError) as error:
        raise InstallerError(f"Arquivo de configuração pessoal inválido: {path}") from error
    return PersonalFileSnapshot(
        root,
        root_identity,
        path,
        payload,
        mode,
        identity,
        hashlib.sha256(payload).hexdigest(),
        parent_topology,
    )


def rollback_personal_file(token: PersonalFileMutation) -> None:
    """Restore old bytes only while the published file remains unchanged."""

    if token.installed is not None:
        if not lexists(token.installed.path) or not cleanup_materialized_file(token.installed):
            raise InstallerError(
                f"Arquivo pessoal alterado foi preservado: {token.installed.path}"
            )
    if token.quarantine is not None:
        _restore_quarantined_file(token.quarantine)
    for directory in reversed(token.created_directories):
        if not cleanup_materialized_directory(directory):
            raise InstallerError(
                f"Diretório pessoal alterado foi preservado: {directory.path}"
            )


def finalize_personal_file(token: PersonalFileMutation) -> None:
    """Discard the original only after the surrounding transaction commits."""

    quarantine = token.quarantine
    if quarantine is None:
        return
    if not lexists(quarantine.previous):
        if quarantine.quarantine.is_dir() and not any(quarantine.quarantine.iterdir()):
            quarantine.quarantine.rmdir()
            sync_directory(quarantine.quarantine.parent)
            return
        raise QuarantineError(f"Backup de quarantine mudou: {quarantine.previous}")
    if token.before.payload is None or token.before.digest is None:
        raise QuarantineError(f"Backup de quarantine sem identidade: {quarantine.previous}")
    original = MaterializedFile(
        quarantine.previous,
        token.before.digest,
        "x86qw:personal-config-original",
        True,
        True,
        quarantine.quarantine,
        token.before.identity,
        len(token.before.payload),
    )
    if not cleanup_materialized_file(original):
        raise QuarantineError(f"Backup de quarantine mudou: {quarantine.previous}")
    try:
        quarantine.quarantine.rmdir()
        sync_directory(quarantine.quarantine.parent)
    except OSError as error:
        raise QuarantineError(
            f"Finalização do quarantine ficou incompleta: {quarantine.quarantine}"
        ) from error


def _quarantine_identity(path: Path) -> tuple[int, int, int]:
    metadata = path.lstat()
    return int(metadata.st_dev), int(metadata.st_ino), int(stat.S_IFMT(metadata.st_mode))


def _restore_quarantined_file(token: QuarantineToken) -> None:
    """Restore one regular file with an atomic no-replace publication."""

    if lexists(token.destination):
        raise QuarantineError(
            f"Destino ocupado foi preservado durante rollback: {token.destination}"
        )
    if (
        not lexists(token.previous)
        or _quarantine_identity(token.previous) != token.identity
        or not stat.S_ISREG(token.identity[2])
    ):
        raise QuarantineError(f"Backup de quarantine mudou: {token.previous}")
    try:
        os.link(token.previous, token.destination, follow_symlinks=False)
    except FileExistsError as error:
        raise QuarantineError(
            f"Destino ocupado foi preservado durante rollback: {token.destination}"
        ) from error
    except OSError as error:
        raise QuarantineError(
            f"Original preservado no quarantine: {token.previous}"
        ) from error
    try:
        if _quarantine_identity(token.destination) != token.identity:
            raise QuarantineError(
                f"Identidade restaurada divergiu: {token.destination}"
            )
        if not unlink_identity_bound_regular(token.previous, token.identity[:2]):
            raise QuarantineError(f"Backup de quarantine mudou: {token.previous}")
        token.quarantine.rmdir()
        sync_directory(token.destination.parent)
    except BaseException:
        # Both names retain the same original inode if cleanup is inconclusive.
        raise


def apply_personal_file(
    snapshot: PersonalFileSnapshot,
    payload: bytes | None,
) -> PersonalFileMutation:
    """Replace or remove exactly the observed file and return its inverse."""

    if payload is not None and not isinstance(payload, bytes):
        raise TypeError("payload must be bytes or None")
    if observe_personal_file(snapshot.root, snapshot.path) != snapshot:
        raise InstallerError(f"Arquivo pessoal mudou antes da alteração: {snapshot.path}")
    created_directories: list[MaterializedDirectory] = []
    quarantine: QuarantineToken | None = None
    try:
        for directory in snapshot.missing_parents:
            directory.mkdir(mode=0o755)
            created_directories.append(MaterializedDirectory(
                directory,
                snapshot.root,
                persistent_path_identity(directory, directory=True),
            ))
        if snapshot.exists:
            quarantine = apply_quarantine_removal(snapshot.path)
            quarantined_payload = read_bounded_regular_file(
                quarantine.previous,
                maximum_size=MAX_MANAGED_FILE_SIZE,
            )
            if (
                quarantine.identity[:2] != snapshot.identity
                or quarantined_payload != snapshot.payload
                or persistent_path_identity(
                    quarantine.previous, directory=False,
                ) != snapshot.identity
            ):
                raise InstallerError(
                    f"Arquivo pessoal mudou antes da alteração: {snapshot.path}"
                )
    except BaseException:
        if quarantine is not None:
            _restore_quarantined_file(quarantine)
        for directory in reversed(created_directories):
            cleanup_materialized_directory(directory)
        raise
    if payload is None:
        return PersonalFileMutation(
            snapshot, None, quarantine, tuple(created_directories),
        )
    digest = hashlib.sha256(payload).hexdigest()
    try:
        result = atomic_create_bytes(snapshot.path, payload, mode=0o644)
        installed_snapshot = observe_personal_file(snapshot.root, snapshot.path)
        assert installed_snapshot.identity is not None
        installed = MaterializedFile(
            snapshot.path,
            digest,
            "x86qw:personal-config-transaction",
            True,
            result.replaced,
            snapshot.root,
            installed_snapshot.identity,
            len(payload),
        )
    except BaseException as error:
        rollback_error: BaseException | None = None
        if quarantine is not None:
            try:
                _restore_quarantined_file(quarantine)
            except BaseException as failure:
                rollback_error = failure
        for directory in reversed(created_directories):
            cleanup_materialized_directory(directory)
        if rollback_error is not None:
            raise InstallerError(
                f"Arquivo concorrente preservado; original retido em {quarantine.quarantine}"
            ) from rollback_error
        if isinstance(error, AtomicWriteError):
            raise InstallerError(
                f"Falha ao publicar configuração pessoal: {snapshot.path}"
            ) from error
        raise
    return PersonalFileMutation(
        snapshot, installed, quarantine, tuple(created_directories),
    )


def personal_file_step(
    root: Path,
    path: Path,
    payload: bytes | None,
    *,
    key: str,
    description: str,
) -> MutationStep:
    """Bind one personal-file mutation to an immutable observation."""

    snapshot = observe_personal_file(root, path)
    return MutationStep(
        key=key,
        description=description,
        observe=lambda: observe_personal_file(root, path),
        apply=lambda: apply_personal_file(snapshot, payload),
        rollback=rollback_personal_file,
        finalize=finalize_personal_file,
    )
