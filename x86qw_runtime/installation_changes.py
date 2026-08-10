"""Compare an installed tree with the recorded installation baseline."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping


@dataclass(frozen=True)
class ManagedInstallationFile:
    component: str
    sha256: str


@dataclass(frozen=True)
class InstallationChange:
    status: str
    path: str
    component: str | None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise ValueError(f"invalid installation-relative path: {value}")
    return path.as_posix()


def _is_ignored(path: str, ignored_paths: tuple[str, ...]) -> bool:
    return any(path == ignored or path.startswith(ignored + "/") for ignored in ignored_paths)


def inspect_installation_changes(
    target: Path,
    managed: Mapping[str, ManagedInstallationFile],
    *,
    ignored_paths: Iterable[str] = (),
) -> tuple[InstallationChange, ...]:
    """Return added, modified and deleted paths relative to one installation."""

    target = Path(target)
    normalized_managed = {
        _relative_path(path): record for path, record in managed.items()
    }
    normalized_ignored = tuple(
        sorted({_relative_path(path).rstrip("/") for path in ignored_paths})
    )
    changes: list[InstallationChange] = []

    for relative, record in normalized_managed.items():
        path = target.joinpath(*PurePosixPath(relative).parts)
        if not path.is_file() or path.is_symlink():
            changes.append(InstallationChange("D", relative, record.component))
        elif _file_sha256(path) != record.sha256:
            changes.append(InstallationChange("M", relative, record.component))

    for path in target.rglob("*"):
        relative = path.relative_to(target).as_posix()
        if (
            relative in normalized_managed
            or _is_ignored(relative, normalized_ignored)
            or (not path.is_file() and not path.is_symlink())
        ):
            continue
        changes.append(InstallationChange("A", relative, None))

    return tuple(sorted(changes, key=lambda change: change.path))


def _escape_gitignore_path(path: str) -> str:
    return re.sub(r"([\\*?\[\]#! ])", r"\\\1", _relative_path(path))


def render_installation_gitignore(
    managed_paths: Iterable[str],
    *,
    ignored_paths: Iterable[str] = (),
) -> str:
    """Render exact ignore rules without hiding unmanaged sibling files."""

    paths = sorted({
        *(_escape_gitignore_path(path.rstrip("/")) for path in ignored_paths),
        *(_escape_gitignore_path(path) for path in managed_paths),
    })
    lines = [
        "# Gerado pelo x86QW a partir dos inventories instalados.",
        "# Arquivos novos continuam visíveis; use `x86qw changes` para M/D/A.",
        *[f"/{path}" for path in paths],
        "",
    ]
    return "\n".join(lines)
