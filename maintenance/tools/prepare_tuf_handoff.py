#!/usr/bin/env python3
"""Decode a bounded, signed TUF handoff bundle into a private workspace.

The bundle is supplied by a protected workflow input as base64-encoded gzip
tar.  This boundary only decodes and safely materializes regular files below
``metadata/`` and ``targets/``; ``publish_tuf_metadata.py`` remains the
authority that authenticates the signatures against the exact candidate
catalog.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import gzip
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


MAX_BUNDLE_B64_CHARS = 65_535
MAX_COMPRESSED_BYTES = 512 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_FILES = 128
MAX_FILE_BYTES = 2 * 1024 * 1024
ALLOWED_ROOTS = frozenset({"metadata", "targets"})


class TufHandoffError(RuntimeError):
    """The external TUF handoff is malformed or unsafe to materialize."""


def _decode_archive(encoded: str) -> bytes:
    if not isinstance(encoded, str) or not encoded:
        raise TufHandoffError("bundle TUF está ausente")
    if len(encoded) > MAX_BUNDLE_B64_CHARS:
        raise TufHandoffError("bundle TUF excede o limite de entrada")
    try:
        compressed = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as error:
        raise TufHandoffError("bundle TUF não é base64 válido") from error
    if not compressed or len(compressed) > MAX_COMPRESSED_BYTES:
        raise TufHandoffError("bundle TUF excede o limite comprimido")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as stream:
            archive = stream.read(MAX_ARCHIVE_BYTES + 1)
    except (OSError, EOFError) as error:
        raise TufHandoffError("bundle TUF não é gzip válido") from error
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise TufHandoffError("bundle TUF excede o limite descomprimido")
    return archive


def _member_path(name: str) -> PurePosixPath:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        raise TufHandoffError("bundle TUF contém caminho inseguro")
    if name.endswith("/"):
        name = name[:-1]
    if not name or "//" in name:
        raise TufHandoffError("bundle TUF contém caminho inseguro")
    raw_parts = name.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise TufHandoffError("bundle TUF contém caminho inseguro")
    path = PurePosixPath(name)
    if path.is_absolute():
        raise TufHandoffError("bundle TUF contém caminho inseguro")
    if not path.parts or path.parts[0] not in ALLOWED_ROOTS:
        raise TufHandoffError("bundle TUF contém caminho fora de metadata/targets")
    return path


def _write_member(
    root: Path, archive: tarfile.TarFile, member: tarfile.TarInfo, names: set[str],
) -> int:
    relative = _member_path(member.name)
    normalized = relative.as_posix()
    if normalized in names:
        raise TufHandoffError(f"bundle TUF contém caminho duplicado: {normalized}")
    names.add(normalized)
    if member.isdir():
        (root / relative.as_posix()).mkdir(mode=0o700, parents=True, exist_ok=True)
        return 0
    if not member.isreg():
        raise TufHandoffError(f"bundle TUF contém symlink ou tipo especial: {normalized}")
    if len(relative.parts) == 1:
        raise TufHandoffError(f"arquivo TUF está fora de metadata/targets: {normalized}")
    if member.size < 0 or member.size > MAX_FILE_BYTES:
        raise TufHandoffError(f"arquivo TUF excede o limite: {normalized}")
    target = root / relative.as_posix()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    source = archive.extractfile(member)
    if source is None:
        raise TufHandoffError(f"arquivo TUF não pôde ser lido: {normalized}")
    try:
        payload = source.read(MAX_FILE_BYTES + 1)
    finally:
        source.close()
    if len(payload) != member.size or len(payload) > MAX_FILE_BYTES:
        raise TufHandoffError(f"tamanho inválido no arquivo TUF: {normalized}")
    with target.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(target, 0o644)
    return 1


def _extract(archive_bytes: bytes, output: Path) -> dict[str, object]:
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise TufHandoffError(f"destino TUF já existe: {output}")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    names: set[str] = set()
    file_count = 0
    try:
        try:
            with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
                members = archive.getmembers()
                if len(members) > MAX_FILES * 2:
                    raise TufHandoffError("bundle TUF contém arquivos demais")
                for member in members:
                    if len(names) >= MAX_FILES:
                        raise TufHandoffError("bundle TUF contém arquivos demais")
                    file_count += _write_member(temporary, archive, member, names)
        except TufHandoffError:
            raise
        except (OSError, tarfile.TarError) as error:
            raise TufHandoffError("bundle TUF não é tar válido") from error
        if file_count == 0 or not any(name.startswith("metadata/") for name in names):
            raise TufHandoffError("bundle TUF não contém metadata regular")
        if not any(name.startswith("targets/") for name in names):
            raise TufHandoffError("bundle TUF não contém targets regulares")
        os.chmod(temporary, 0o700)
        os.replace(temporary, output)
        return {
            "format": 1,
            "project": "x86qw",
            "status": "prepared",
            "file_count": file_count,
        }
    except BaseException:
        if temporary.exists() and not temporary.is_symlink():
            shutil.rmtree(temporary)
        raise


def prepare_tuf_handoff(*, encoded: str, output: Path) -> dict[str, object]:
    """Decode and safely materialize one external TUF handoff."""

    return _extract(_decode_archive(encoded), Path(output))


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-env", default="TUF_METADATA_BUNDLE_B64")
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args(arguments)
    encoded = os.environ.get(options.bundle_env, "")
    try:
        result = prepare_tuf_handoff(encoded=encoded, output=options.output)
    except (OSError, TufHandoffError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
