"""Leitura mínima de diretórios PAK usados pelo gameplay."""

from __future__ import annotations

import os
import re
import stat
import struct
import unicodedata
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator

from x86qw_runtime.io.archive import ArchiveLimits, DEFAULT_ARCHIVE_LIMITS


class PakError(ValueError):
    """O PAK não satisfaz o contrato estrutural mínimo."""


_WINDOWS_FORBIDDEN_CHARACTERS = frozenset('<>"|?*')
_WINDOWS_RESERVED_NAMES = frozenset({
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CLOCK$",
    "CONIN$",
    "CONOUT$",
    *(f"COM{suffix}" for suffix in (*range(1, 10), "¹", "²", "³")),
    *(f"LPT{suffix}" for suffix in (*range(1, 10), "¹", "²", "³")),
})
_WINDOWS_REPARSE_POINT_ATTRIBUTE = 0x0400


def _validated_member_name(
    name: str,
    limits: ArchiveLimits,
) -> tuple[str, str]:
    if not isinstance(name, str) or not name:
        raise PakError("Nome de membro PAK vazio.")
    if len(name.encode("utf-16-le", "surrogatepass")) // 2 > limits.max_path_utf16_units:
        raise PakError("Nome de membro PAK excede o limite portátil.")
    if (
        name.startswith("/")
        or "\\" in name
        or ":" in name
        or any(unicodedata.category(character).startswith("C") for character in name)
    ):
        raise PakError(f"Nome de membro PAK não é POSIX portátil: {name!r}")
    parts = tuple(name.split("/"))
    if (
        len(parts) > limits.max_depth
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise PakError(f"Nome de membro PAK possui componente inseguro: {name!r}")
    for part in parts:
        if any(character in _WINDOWS_FORBIDDEN_CHARACTERS for character in part):
            raise PakError(f"Nome de membro PAK não é portátil no Windows: {name!r}")
        if part.endswith((".", " ")):
            raise PakError(f"Nome de membro PAK termina em ponto ou espaço: {name!r}")
        reserved_stem = part.split(".", 1)[0].rstrip(". ").upper()
        if reserved_stem in _WINDOWS_RESERVED_NAMES:
            raise PakError(f"Nome de membro PAK é reservado no Windows: {name!r}")
    posix = PurePosixPath(*parts)
    if posix.is_absolute() or posix.drive or posix.root or posix.as_posix() != name:
        raise PakError(f"Nome de membro PAK não é POSIX canônico: {name!r}")
    semantic = "/".join(
        unicodedata.normalize("NFC", part).casefold() for part in parts
    )
    return name, semantic


def _source_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        stat.S_IFMT(int(metadata.st_mode)),
        int(metadata.st_size),
        int(getattr(metadata, "st_mtime_ns", metadata.st_mtime * 1_000_000_000)),
    )


@contextmanager
def _open_regular_package(package: Path) -> Iterator[BinaryIO]:
    try:
        before = package.lstat()
    except OSError as error:
        raise PakError(f"Fonte PAK indisponível: {package}") from error
    attributes = int(getattr(before, "st_file_attributes", 0))
    if (
        stat.S_ISLNK(before.st_mode)
        or attributes & _WINDOWS_REPARSE_POINT_ATTRIBUTE
        or not stat.S_ISREG(before.st_mode)
    ):
        raise PakError(f"Fonte PAK deve ser arquivo regular sem symlink: {package}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(package, flags)
    except OSError as error:
        raise PakError(f"Fonte PAK não pôde ser aberta com segurança: {package}") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _source_identity(opened) != _source_identity(before)
        ):
            raise PakError(f"Fonte PAK mudou durante a abertura: {package}")
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            yield stream
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _entries(
    archive: BinaryIO,
    package: Path,
    limits: ArchiveLimits,
) -> tuple[tuple[str, int, int], ...]:
    size = os.fstat(archive.fileno()).st_size
    if size > limits.max_source_size:
        raise PakError(f"PAK excede o limite permitido: {package}")
    header = archive.read(12)
    if len(header) != 12 or header[:4] != b"PACK":
        raise PakError(f"PAK inválido: {package}")
    directory_offset, directory_size = struct.unpack("<II", header[4:])
    if (
        directory_offset < 12
        or directory_size % 64
        or directory_size > limits.max_metadata_size
        or directory_size // 64 > limits.max_members
        or directory_offset + directory_size > size
    ):
        raise PakError(f"Diretório PAK inválido: {package}")
    archive.seek(directory_offset)
    directory = archive.read(directory_size)
    if len(directory) != directory_size:
        raise PakError(f"Diretório PAK truncado: {package}")
    entries: list[tuple[str, int, int]] = []
    exact_names: set[str] = set()
    semantic_names: dict[str, str] = {}
    regions: list[tuple[int, int, str]] = []
    total_size = 0
    for offset in range(0, len(directory), 64):
        name_field = directory[offset:offset + 56]
        raw_name = name_field.split(b"\0", 1)[0]
        try:
            decoded_name = raw_name.decode("utf-8")
            name, semantic = _validated_member_name(decoded_name, limits)
        except (PakError, UnicodeDecodeError) as error:
            raise PakError(f"Nome de membro PAK inválido em {package}") from error
        if name in exact_names:
            raise PakError(f"colisão exata de membro PAK em {package}: {name}")
        exact_names.add(name)
        prior_name = semantic_names.get(semantic)
        if prior_name is not None:
            raise PakError(
                f"colisão casefold/NFC de membro PAK em {package}: "
                f"{prior_name}, {name}"
            )
        semantic_names[semantic] = name
        data_offset, data_size = struct.unpack_from("<II", directory, offset + 56)
        total_size += data_size
        data_end = data_offset + data_size
        if (
            data_offset < 12
            or data_size > limits.max_member_size
            or total_size > limits.max_total_size
            or data_end > size
            or (
                data_size > 0
                and data_offset < directory_offset + directory_size
                and data_end > directory_offset
            )
        ):
            raise PakError(f"Membro PAK inválido em {package}: {name}")
        if data_size:
            regions.append((data_offset, data_end, name))
        entries.append((name, data_offset, data_size))
    regions.sort()
    for previous, current in zip(regions, regions[1:]):
        if current[0] < previous[1]:
            raise PakError(
                f"Regiões de membros PAK sobrepostas em {package}: "
                f"{previous[2]}, {current[2]}"
            )
    return tuple(entries)


def list_bsp_names(
    package: Path,
    *,
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
) -> set[str]:
    """List direct ``maps/*.bsp`` members from a Quake PAK."""

    maps: set[str] = set()
    with _open_regular_package(package) as archive:
        for member, _data_offset, _data_size in _entries(archive, package, limits):
            path = PurePosixPath(member)
            if (
                len(path.parts) != 2
                or path.parts[0].lower() != "maps"
                or path.suffix.lower() != ".bsp"
            ):
                continue
            name = path.stem
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
                maps.add(name)
    return maps


def read_member(
    package: Path,
    member_name: str,
    *,
    limits: ArchiveLimits = DEFAULT_ARCHIVE_LIMITS,
) -> bytes:
    """Read one case-insensitive exact member from a Quake PAK."""

    try:
        _requested, requested_semantic = _validated_member_name(member_name, limits)
    except PakError as error:
        raise PakError(f"Nome de membro PAK solicitado inválido: {member_name!r}") from error
    with _open_regular_package(package) as archive:
        for name, data_offset, data_size in _entries(archive, package, limits):
            _canonical, semantic = _validated_member_name(name, limits)
            if semantic != requested_semantic:
                continue
            archive.seek(data_offset)
            payload = archive.read(data_size)
            if len(payload) != data_size:
                raise PakError(f"Membro PAK truncado em {package}: {name}")
            return payload
    raise PakError(f"Gamecode {member_name} não encontrado em {package}.")
