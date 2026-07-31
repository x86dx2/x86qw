#!/usr/bin/env python3
"""Validate materialized Git LFS objects and reject large direct binaries."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAX_DIRECT_BINARY = 1024 * 1024
LFS_POINTER = b"version https://git-lfs.github.com/spec/v1\n"


def git(*arguments: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", *arguments], cwd=ROOT, input=stdin, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout


def tracked_files() -> list[str]:
    return [
        value.decode("utf-8", "surrogateescape")
        for value in git("ls-files", "-z").split(b"\0")
        if value
    ]


def lfs_attributes(paths: list[str]) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for offset in range(0, len(paths), 100):
        batch = paths[offset:offset + 100]
        payload = b"\0".join(path.encode("utf-8", "surrogateescape") for path in batch) + b"\0"
        fields = git("check-attr", "-z", "--stdin", "filter", stdin=payload).split(b"\0")
        for index in range(0, len(fields) - 2, 3):
            path = fields[index].decode("utf-8", "surrogateescape")
            result[path] = fields[index + 2] == b"lfs"
    return result


def looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            sample = source.read(8192)
    except OSError:
        return False
    return b"\0" in sample


def main() -> int:
    paths = tracked_files()
    attributes = lfs_attributes(paths)
    errors: list[str] = []
    lfs_count = 0
    for relative in paths:
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            continue
        is_lfs = attributes.get(relative, False)
        if is_lfs:
            lfs_count += 1
            try:
                with path.open("rb") as source:
                    if source.read(len(LFS_POINTER)) == LFS_POINTER:
                        errors.append(f"objeto LFS não materializado: {relative}")
            except OSError as error:
                errors.append(f"objeto LFS ilegível: {relative}: {error}")
            continue
        try:
            size = path.stat().st_size
        except OSError as error:
            errors.append(f"arquivo rastreado ilegível: {relative}: {error}")
            continue
        if size > MAX_DIRECT_BINARY and looks_binary(path):
            errors.append(
                f"binário grande fora do Git LFS ({size} bytes): {relative}"
            )
    if errors:
        for error in errors:
            print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(f"[OK] Git LFS: {lfs_count} objetos materializados; nenhum binário grande direto.")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    raise SystemExit(main())
