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
    identity: tuple[int, int, int]
    device: int


def lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _identity(path: Path) -> tuple[int, int, int]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise QuarantineError(f"Caminho de quarantine indisponível: {path}") from error
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(stat.S_IFMT(metadata.st_mode)),
    )


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


def _remove_tree(path: Path, device: int) -> None:
    metadata = path.lstat()
    if int(metadata.st_dev) != device:
        raise QuarantineError(f"Finalização recusou atravessar filesystem: {path}")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        path.unlink()
        return
    with os.scandir(path) as entries:
        children = tuple(Path(entry.path) for entry in entries)
    for child in children:
        _remove_tree(child, device)
    path.rmdir()


def apply_quarantine_removal(destination: Path) -> QuarantineToken:
    """Atomically move one existing node into a private sibling quarantine."""

    destination = Path(destination)
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
                previous.replace(destination)
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
    token.previous.replace(token.destination)
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
        _remove_tree(token.previous, token.device)
        token.quarantine.rmdir()
    except OSError as error:
        raise QuarantineError(
            f"Finalização do quarantine ficou incompleta: {token.quarantine}"
        ) from error
