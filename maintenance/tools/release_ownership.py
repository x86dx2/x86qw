"""Validate explicit ownership facts used by release SBOMs.

This module is deliberately independent from archive extraction.  Builders know
which bytes they produced and which source each byte came from; the release
candidate only binds those declarations to the final artifact hashes.  No
license conclusion is inferred from a filename or directory.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Iterable


PROJECT = "x86qw"
FORMAT = 1
PROJECT_COPYRIGHT = "Copyright (c) 2026 x86dx2"
LICENSES = frozenset({"MIT", "NOASSERTION"})
OWNERSHIPS = frozenset({"project", "upstream", "mixed"})
KINDS = frozenset({"file", "archive", "metadata", "registered-game-data"})
BASES = frozenset({
    "project-source",
    "project-override",
    "generated-project-metadata",
    "build-output",
    "upstream-release",
    "registered-game-data",
    "composed-archive",
    "unclassified-candidate-input",
})
HEX64 = re.compile(r"^[0-9a-f]{64}$")
IMMUTABLE_LICENSE_URL = re.compile(
    r"^https://github\.com/x86dx2/x86qw/blob/([A-Za-z0-9][A-Za-z0-9._-]*)/LICENSE$"
)
MAX_BYTES = 512 * 1024 * 1024
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 100_000
MAX_DEPTH = 8
MAX_TEXT = 1024


class OwnershipError(ValueError):
    """An ownership fragment is malformed, ambiguous, or inconsistent."""


def _json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise OwnershipError(f"ownership JSON contém chave duplicada: {key}")
        result[key] = value
    return result


def canonical_bytes(document: dict[str, object]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _safe_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise OwnershipError(f"{label} inválido")
    if "\\" in value or ":" in value or any(ord(ch) < 0x20 for ch in value):
        raise OwnershipError(f"{label} não é um caminho POSIX seguro: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise OwnershipError(f"{label} não é um caminho POSIX seguro: {value!r}")
    if len(path.parts) > MAX_DEPTH or path.as_posix() != value:
        raise OwnershipError(f"{label} excede o limite de profundidade ou não é canônico")
    return value


def _text(value: object, label: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
        raise OwnershipError(f"{label} inválido")
    if any(ord(ch) < 0x20 for ch in value):
        raise OwnershipError(f"{label} contém controle")
    return value


def _hash(value: object, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise OwnershipError(f"{label} precisa ser SHA-256 hexadecimal minúsculo")
    return value


def _size(value: object, label: str) -> int:
    if type(value) is not int or value < 0 or value > MAX_BYTES:
        raise OwnershipError(f"{label} excede o limite permitido")
    return value


def _validate_license_url(value: object, *, ownership: str) -> str | None:
    if ownership != "project":
        if value is not None:
            raise OwnershipError("ownership upstream/mixed não pode carregar URL de licença do projeto")
        return None
    if not isinstance(value, str) or IMMUTABLE_LICENSE_URL.fullmatch(value) is None:
        raise OwnershipError("ownership project exige URL imutável da licença MIT")
    reference = IMMUTABLE_LICENSE_URL.fullmatch(value).group(1)  # type: ignore[union-attr]
    if reference.casefold() in {"main", "master", "head", "latest"}:
        raise OwnershipError("URL de licença não pode apontar para referência mutável")
    return value


def _validate_entry(raw: object, *, depth: int, seen: set[str]) -> dict[str, object]:
    if depth > MAX_DEPTH:
        raise OwnershipError("ownership excede a profundidade máxima")
    if not isinstance(raw, dict):
        raise OwnershipError("entrada de ownership inválida")
    required = {
        "path", "size", "sha256", "kind", "ownership", "ownership_basis",
        "source", "license_concluded", "license_url", "copyright_text", "members",
    }
    if set(raw) != required:
        raise OwnershipError("entrada de ownership contém campos desconhecidos ou ausentes")
    path = _safe_path(raw.get("path"), "path")
    semantic_path = unicodedata.normalize("NFC", path).casefold()
    if semantic_path in seen:
        raise OwnershipError(f"ownership contém membro duplicado: {path}")
    seen.add(semantic_path)
    size = _size(raw.get("size"), f"size de {path}")
    digest = _hash(raw.get("sha256"), f"sha256 de {path}")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise OwnershipError(f"kind inválido em {path}")
    ownership = raw.get("ownership")
    if ownership not in OWNERSHIPS:
        raise OwnershipError(f"ownership inválido em {path}")
    basis = raw.get("ownership_basis")
    if basis not in BASES:
        raise OwnershipError(f"ownership_basis inválido em {path}")
    source = _text(raw.get("source"), f"source de {path}")
    license_concluded = raw.get("license_concluded")
    if license_concluded not in LICENSES:
        raise OwnershipError(f"license_concluded inválido em {path}")
    copyright_text = _text(raw.get("copyright_text"), f"copyright_text de {path}")
    members = raw.get("members")
    if not isinstance(members, list) or len(members) > MAX_ENTRIES:
        raise OwnershipError(f"members inválido em {path}")
    child_entries: list[dict[str, object]] = []
    child_paths: set[str] = set()
    for child in members:
        if not isinstance(child, dict):
            raise OwnershipError(f"member inválido em {path}")
        # Members use paths relative to their containing artifact, and can
        # therefore be checked in a fresh namespace at each archive layer.
        child_entry = _validate_entry(child, depth=depth + 1, seen=child_paths)
        child_entries.append(child_entry)
    expected_ownership = ownership
    if child_entries:
        child_ownerships = {str(child["ownership"]) for child in child_entries}
        if child_ownerships == {"project"}:
            expected_ownership = "project"
        elif child_ownerships == {"upstream"}:
            expected_ownership = "upstream"
        else:
            expected_ownership = "mixed"
        if ownership != expected_ownership:
            raise OwnershipError(
                f"ownership de {path} não corresponde aos membros: {ownership} != {expected_ownership}"
            )
    if ownership == "project":
        if license_concluded != "MIT" or copyright_text != PROJECT_COPYRIGHT:
            raise OwnershipError(f"ownership project inválido em {path}")
    else:
        if license_concluded != "NOASSERTION" or copyright_text != "NOASSERTION":
            raise OwnershipError(f"ownership {ownership} precisa ser NOASSERTION em {path}")
    license_url = _validate_license_url(raw.get("license_url"), ownership=ownership)
    return {
        "path": path,
        "size": size,
        "sha256": digest,
        "kind": kind,
        "ownership": ownership,
        "ownership_basis": basis,
        "source": source,
        "license_concluded": license_concluded,
        "license_url": license_url,
        "copyright_text": copyright_text,
        "members": sorted(child_entries, key=lambda item: str(item["path"])),
    }


def validate_document(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict) or set(raw) != {"format", "project", "artifacts"}:
        raise OwnershipError("ownership document contém campos desconhecidos ou ausentes")
    if raw.get("format") != FORMAT or raw.get("project") != PROJECT:
        raise OwnershipError("identidade do ownership document inválida")
    artifacts = raw.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts or len(artifacts) > MAX_ENTRIES:
        raise OwnershipError("ownership document não possui artifacts válidos")
    entries: list[dict[str, object]] = []
    paths: set[str] = set()
    semantic_paths: set[str] = set()
    for artifact in artifacts:
        entry = _validate_entry(artifact, depth=0, seen=set())
        path = str(entry["path"])
        semantic_path = unicodedata.normalize("NFC", path).casefold()
        if path in paths or semantic_path in semantic_paths:
            raise OwnershipError(f"ownership document contém artifact duplicado: {path}")
        paths.add(path)
        semantic_paths.add(semantic_path)
        entries.append(entry)
    return {"format": FORMAT, "project": PROJECT, "artifacts": sorted(entries, key=lambda item: str(item["path"]))}


def load_fragment(path: Path) -> dict[str, object]:
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise OwnershipError(f"fragmento de ownership ausente ou inseguro: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_json_pairs)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OwnershipError(f"fragmento de ownership inválido: {path}") from error
    return validate_document(raw)


def merge_documents(documents: Iterable[dict[str, object]]) -> dict[str, object]:
    combined: dict[str, dict[str, object]] = {}
    count = 0
    for document in documents:
        normalized = validate_document(document)
        for entry in normalized["artifacts"]:  # type: ignore[union-attr]
            count += 1
            path = str(entry["path"])
            previous = combined.get(path)
            if previous is not None and previous != entry:
                raise OwnershipError(f"fragmentos de ownership divergem para {path}")
            combined[path] = entry
    if not combined or count > MAX_ENTRIES:
        raise OwnershipError("nenhum artifact de ownership foi declarado")
    return validate_document({"format": FORMAT, "project": PROJECT, "artifacts": list(combined.values())})


def default_document(artifacts: dict[str, dict[str, object]]) -> dict[str, object]:
    """Create a fail-closed document when a legacy API caller supplies no facts."""

    entries = []
    for path, metadata in sorted(artifacts.items()):
        entries.append({
            "path": path,
            "size": metadata["size"],
            "sha256": metadata["sha256"],
            "kind": "file",
            "ownership": "upstream",
            "ownership_basis": "unclassified-candidate-input",
            "source": "unclassified-candidate-input",
            "license_concluded": "NOASSERTION",
            "license_url": None,
            "copyright_text": "NOASSERTION",
            "members": [],
        })
    return validate_document({"format": FORMAT, "project": PROJECT, "artifacts": entries})


def flatten_entries(document: dict[str, object]) -> dict[str, dict[str, object]]:
    normalized = validate_document(document)
    flattened: dict[str, dict[str, object]] = {}

    def visit(entry: dict[str, object], prefix: str) -> None:
        if prefix in flattened:
            raise OwnershipError(f"ownership contém caminho SPDX duplicado: {prefix}")
        flattened[prefix] = entry
        for child in entry["members"]:  # type: ignore[union-attr]
            child_path = str(child["path"])
            visit(child, f"{prefix}::{child_path}")

    for entry in normalized["artifacts"]:  # type: ignore[union-attr]
        visit(entry, str(entry["path"]))
    return flattened


def write_document(path: Path, document: dict[str, object]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(validate_document(document))
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "BASES", "FORMAT", "LICENSES", "OWNERSHIPS", "PROJECT", "PROJECT_COPYRIGHT",
    "OwnershipError", "canonical_bytes", "default_document", "flatten_entries",
    "load_fragment", "merge_documents", "validate_document", "write_document",
]
