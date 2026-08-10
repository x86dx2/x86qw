"""Same-filesystem quarantine for reversible destructive mutations."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from x86qw_runtime.errors import InstallerError

from . import private_fs


class QuarantineError(InstallerError):
    """A destructive path could not be quarantined or reconciled safely."""


@dataclass(frozen=True)
class QuarantineToken:
    destination: Path
    quarantine: Path
    previous: Path
    identity: tuple[int, ...]
    device: int


def lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _identity(path: Path) -> tuple[int, ...]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise QuarantineError(f"Caminho de quarantine indisponível: {path}") from error
    identity = (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(stat.S_IFMT(metadata.st_mode)),
    )
    if os.name != "nt" or not (
        stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)
    ):
        return identity
    from . import managed_files

    native_identity = managed_files.persistent_path_identity(
        path, directory=stat.S_ISDIR(metadata.st_mode),
    )
    if _identity_from_metadata(path.lstat()) != identity:
        raise QuarantineError(f"Caminho de quarantine mudou: {path}")
    return identity + native_identity


def _identity_from_metadata(metadata: os.stat_result) -> tuple[int, int, int]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(stat.S_IFMT(metadata.st_mode)),
    )


def _move_no_replace(source: Path, destination: Path) -> None:
    """Move one node atomically while preserving an existing destination."""

    from . import managed_files

    if os.name == "nt":
        api = managed_files._get_windows_file_api()
        if api is None:
            raise QuarantineError("Rename exclusivo indisponível no Windows")
        api.move_no_replace(source, destination)
        return
    api = managed_files._get_posix_rename_api()
    if api is None:
        raise QuarantineError("Rename exclusivo indisponível neste sistema")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    source_parent = os.open(source.parent, flags)
    try:
        destination_parent = os.open(destination.parent, flags)
        try:
            api.move_no_replace(
                source_parent, source.name,
                destination_parent, destination.name,
            )
        finally:
            os.close(destination_parent)
    finally:
        os.close(source_parent)


def observe_quarantine_target(path: Path) -> tuple[object, ...]:
    """Capture a stable leaf identity for transaction revalidation."""

    if not lexists(path):
        return ("absent",)
    metadata = path.lstat()
    parent_metadata = path.parent.lstat()
    if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(parent_metadata.st_mode):
        raise QuarantineError(f"Diretório pai inseguro para quarantine: {path.parent}")
    if int(metadata.st_dev) != int(parent_metadata.st_dev):
        raise QuarantineError(f"Quarantine recusou um mountpoint dedicado: {path}")
    _validate_tree(path, int(metadata.st_dev))
    return (
        "present",
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(stat.S_IFMT(metadata.st_mode)),
        int(metadata.st_size),
        int(getattr(metadata, "st_mtime_ns", metadata.st_mtime * 1_000_000_000)),
    )


def _validate_tree(path: Path, device: int) -> None:
    metadata = path.lstat()
    if int(metadata.st_dev) != device:
        raise QuarantineError(f"Quarantine recusou atravessar filesystem: {path}")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        return
    try:
        with os.scandir(path) as entries:
            children = tuple(Path(entry.path) for entry in entries)
    except OSError as error:
        raise QuarantineError(f"Quarantine não pôde inspecionar: {path}") from error
    for child in children:
        _validate_tree(child, device)


def _unlink_leaf(path: Path, expected_identity: tuple[int, ...]) -> None:
    """Remove only the leaf identity observed by the recursive plan."""

    from . import managed_files

    if stat.S_ISLNK(expected_identity[2]):
        if managed_files.unlink_identity_bound_symlink(
            path, expected_identity,
        ):
            return
        raise QuarantineError(f"Nó de quarantine mudou: {path}")
    if not stat.S_ISREG(expected_identity[2]):
        raise QuarantineError(
            f"Nó não regular preservado no quarantine: {path}"
        )
    if managed_files.remove_identity_bound_path(
        path, expected_identity, directory=False,
    ):
        return
    raise QuarantineError(f"Nó de quarantine mudou: {path}")


def _remove_tree(
    path: Path,
    device: int,
    *,
    expected_identity: tuple[int, ...] | None = None,
) -> None:
    metadata = path.lstat()
    if int(metadata.st_dev) != device:
        raise QuarantineError(f"Finalização recusou atravessar filesystem: {path}")
    current_identity = _identity(path)
    if expected_identity is not None and current_identity != expected_identity:
        raise QuarantineError(f"Backup de quarantine mudou: {path}")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        if _identity(path) != current_identity:
            raise QuarantineError(f"Nó de quarantine mudou: {path}")
        _unlink_leaf(path, current_identity)
        return
    with os.scandir(path) as entries:
        children = tuple(
            (
                Path(entry.path),
                _identity(Path(entry.path)),
            )
            for entry in entries
        )
    for child, child_identity in children:
        _remove_tree(
            child,
            device,
            expected_identity=child_identity,
        )
    from . import managed_files

    if not managed_files.remove_identity_bound_path(
        path, current_identity, directory=True,
    ):
        raise QuarantineError(f"Diretório de quarantine mudou: {path}")


def apply_quarantine_removal(
    destination: Path,
    *,
    expected_observation: tuple[object, ...] | None = None,
) -> QuarantineToken:
    """Atomically move one existing node into a private sibling quarantine."""

    destination = Path(destination)
    current_observation = observe_quarantine_target(destination)
    if (
        expected_observation is not None
        and current_observation != expected_observation
    ):
        raise QuarantineError(
            f"Caminho mudou após o plano de quarantine: {destination}"
        )
    if not lexists(destination):
        raise QuarantineError(f"Caminho de purge não existe: {destination}")
    parent = destination.parent
    try:
        parent_metadata = parent.lstat()
    except OSError as error:
        raise QuarantineError(f"Diretório pai inválido para quarantine: {parent}") from error
    if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(parent_metadata.st_mode):
        raise QuarantineError(f"Diretório pai inseguro para quarantine: {parent}")
    identity = _identity(destination)
    if identity[0] != int(parent_metadata.st_dev):
        raise QuarantineError(
            f"Quarantine recusou um mountpoint dedicado: {destination}"
        )
    _validate_tree(destination, identity[0])
    try:
        root = private_fs.private_mkdtemp(
            directory=parent,
            prefix=f".x86qw-{destination.name}-quarantine.",
        )
    except OSError as error:
        raise QuarantineError(f"Não foi possível criar quarantine ao lado de {destination}") from error
    previous = root / "node"
    token = QuarantineToken(destination, root, previous, identity, identity[0])
    try:
        destination.replace(previous)
        if _identity(previous) != identity:
            raise QuarantineError(f"Caminho mudou durante o quarantine: {destination}")
        return token
    except BaseException as error:
        try:
            if lexists(previous) and not lexists(destination):
                _move_no_replace(previous, destination)
            if root.is_dir() and not any(root.iterdir()):
                root.rmdir()
        except BaseException as rollback_error:
            raise QuarantineError(
                f"Quarantine falhou e a restauração ficou incompleta: {root}"
            ) from rollback_error
        if isinstance(error, QuarantineError):
            raise
        raise QuarantineError(f"Não foi possível recolher {destination}") from error


def rollback_quarantine(token: QuarantineToken) -> None:
    """Restore a quarantined node without overwriting concurrent data."""

    if not isinstance(token, QuarantineToken):
        raise TypeError("token must be QuarantineToken")
    if lexists(token.destination):
        raise QuarantineError(
            f"Destino ocupado foi preservado durante rollback: {token.destination}"
        )
    if not lexists(token.previous) or _identity(token.previous) != token.identity:
        raise QuarantineError(f"Backup de quarantine mudou: {token.previous}")
    try:
        _move_no_replace(token.previous, token.destination)
    except FileExistsError as error:
        raise QuarantineError(
            f"Destino ocupado foi preservado durante rollback: {token.destination}"
        ) from error
    except OSError as error:
        raise QuarantineError(
            f"Rollback de quarantine falhou sem substituir o destino: {token.destination}"
        ) from error
    try:
        token.quarantine.rmdir()
    except OSError as error:
        raise QuarantineError(
            f"Quarantine restaurado, mas o diretório residual foi preservado: {token.quarantine}"
        ) from error


def finalize_quarantine(token: QuarantineToken) -> None:
    """Irreversibly discard one validated quarantine after logical commit."""

    if not isinstance(token, QuarantineToken):
        raise TypeError("token must be QuarantineToken")
    if not lexists(token.previous):
        if token.quarantine.is_dir() and not any(token.quarantine.iterdir()):
            token.quarantine.rmdir()
        return
    if _identity(token.previous) != token.identity:
        raise QuarantineError(f"Backup de quarantine mudou: {token.previous}")
    _validate_tree(token.previous, token.device)
    try:
        _remove_tree(
            token.previous,
            token.device,
            expected_identity=token.identity,
        )
        token.quarantine.rmdir()
    except OSError as error:
        raise QuarantineError(
            f"Finalização do quarantine ficou incompleta: {token.quarantine}"
        ) from error
