#!/usr/bin/env python3
"""Materialize repository LFS objects from an immutable GitHub raw commit.

GitHub Actions may have no usable Git LFS quota even though the public commit
bytes remain available.  This CI-only bridge keeps the LFS object layout and
the normal ``git lfs checkout`` contract while verifying every object against
the SHA-256 and size recorded by its committed pointer.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from x86qw_runtime.io.downloader import (  # noqa: E402
    MAX_ARTIFACT_BYTES,
    PinnedArtifact,
    download,
)


LFS_POINTER_VERSION = "version https://git-lfs.github.com/spec/v1"
LFS_POINTER_HEADER = (LFS_POINTER_VERSION + "\n").encode("ascii")
MAX_POINTER_BYTES = 4096
HEX64 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
FetchObject = Callable[[str, Path, int, str], None]


def parse_pointer(payload: bytes) -> tuple[str, int]:
    """Return the SHA-256 object id and size from one canonical LFS pointer."""

    if len(payload) > MAX_POINTER_BYTES:
        raise ValueError("ponteiro LFS excede o limite estrutural")
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("ponteiro LFS não é ASCII") from error
    if len(lines) != 3 or lines[0] != LFS_POINTER_VERSION:
        raise ValueError("ponteiro LFS não canônico")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        name, separator, value = line.partition(" ")
        if separator != " " or name in fields or not value:
            raise ValueError("ponteiro LFS malformado")
        fields[name] = value
    oid = fields.get("oid", "")
    if not oid.startswith("sha256:") or not HEX64.fullmatch(oid[7:]):
        raise ValueError("ponteiro LFS sem SHA-256 canônico")
    size_text = fields.get("size", "")
    if not size_text.isdecimal() or (len(size_text) > 1 and size_text[0] == "0"):
        raise ValueError("ponteiro LFS sem tamanho decimal canônico")
    size = int(size_text)
    if size > MAX_ARTIFACT_BYTES:
        raise ValueError("objeto LFS excede o limite de download")
    if set(fields) != {"oid", "size"}:
        raise ValueError("ponteiro LFS contém campos inesperados")
    return oid[7:], size


def iter_pointers(root: Path) -> Iterator[tuple[str, str, int]]:
    """Yield relative paths and identities for pointer files in ``dist``."""

    distribution = root / "dist"
    if not distribution.is_dir() or distribution.is_symlink():
        raise ValueError("a árvore dist não existe ou é um symlink")
    for path in sorted(distribution.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as source:
            prefix = source.read(len(LFS_POINTER_HEADER))
            if prefix != LFS_POINTER_HEADER:
                continue
            payload = prefix + source.read(MAX_POINTER_BYTES + 1 - len(prefix))
        oid, size = parse_pointer(payload)
        yield path.relative_to(root).as_posix(), oid, size


def object_path(root: Path, oid: str) -> Path:
    return root / ".git/lfs/objects" / oid[:2] / oid[2:4] / oid


def raw_url(repository: str, commit: str, relative: str) -> str:
    if not REPOSITORY.fullmatch(repository):
        raise ValueError("repositório GitHub inválido")
    if not COMMIT.fullmatch(commit):
        raise ValueError("commit candidato inválido")
    return f"https://raw.githubusercontent.com/{repository}/{commit}/{quote(relative, safe='/')}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_object(path: Path, expected_size: int, expected_sha256: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"objeto LFS ausente ou não regular: {path}")
    if path.stat().st_size != expected_size or _sha256(path) != expected_sha256:
        raise ValueError(f"objeto LFS divergente: {path}")


def fetch_object(url: str, destination: Path, expected_size: int, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    download(PinnedArtifact(
        url=url,
        destination=destination,
        expected_size=expected_size,
        expected_sha256=expected_sha256,
        maximum_size=MAX_ARTIFACT_BYTES,
        deadline_seconds=1800,
        headers={"User-Agent": "x86qw-ci-lfs/1"},
        label="objeto LFS do commit candidato",
    ))


def materialize(
    root: Path,
    repository: str,
    commit: str,
    *,
    fetch: FetchObject = fetch_object,
) -> int:
    """Populate the Git LFS object store and return the number of new objects."""

    pointers = list(iter_pointers(root))
    if not pointers:
        raise ValueError("nenhum ponteiro LFS foi encontrado em dist")
    created = 0
    for relative, oid, size in pointers:
        destination = object_path(root, oid)
        if destination.exists() or destination.is_symlink():
            _verify_object(destination, size, oid)
            continue
        fetch(raw_url(repository, commit, relative), destination, size, oid)
        _verify_object(destination, size, oid)
        created += 1
    return created


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    options = parser.parse_args(arguments)
    created = materialize(options.root.resolve(), options.repository, options.commit)
    print(f"[OK] LFS: {created} objetos novos verificados no cache.")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    raise SystemExit(main())
