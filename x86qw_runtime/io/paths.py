"""Filesystem path primitives shared by installed x86QW entrypoints."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from x86qw_runtime.errors import InstallerError


def lexists(path: Path) -> bool:
    """Return true for any directory entry, including a dangling symlink."""

    return os.path.lexists(path)


def remove_path(path: Path, root_device: int | None = None) -> None:
    """Delete without following symlinks or crossing filesystem boundaries."""

    if not lexists(path):
        return
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        path.unlink()
        return
    device = info.st_dev if root_device is None else root_device
    if info.st_dev != device:
        raise InstallerError(f"refusing to cross filesystem boundary: {path}")
    with os.scandir(path) as entries:
        for entry in entries:
            child = Path(entry.path)
            child_info = child.lstat()
            if not stat.S_ISLNK(child_info.st_mode) and child_info.st_dev != device:
                raise InstallerError(f"refusing to cross filesystem boundary: {child}")
            remove_path(child, device)
    path.rmdir()


__all__ = ("lexists", "remove_path")
