"""Bind native evidence bytes to one immutable release candidate.

The binding is deliberately unsigned.  It is the transport/approval
checkpoint between the protected native runner and the publisher: it proves
that the evidence files reviewed by the approval job are the exact files
validated against the candidate manifest.  Cryptographic signing, when
required by the release policy, remains a separate custody ceremony.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools.native_release_evidence import validate_native_evidence
from maintenance.tools.release_candidate import CandidateError, verify_candidate
from x86qw_runtime.contracts.native_evidence import REQUIRED_NATIVE_PLATFORMS


PROJECT = "x86qw"
FORMAT = 1
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
DECIMAL = re.compile(r"^[1-9][0-9]{0,19}$")
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
BINDING_FIELDS = frozenset({
    "format", "project", "generated_at", "candidate", "records_root",
    "platforms", "files", "source", "binding_sha256",
})
IDENTITY_FIELDS = frozenset({"version", "commit", "manifest_sha256"})
SOURCE_FIELDS = frozenset({"workflow", "run_id", "run_attempt", "artifact_name"})
MAX_BINDING_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024


class EvidenceBindingError(CandidateError):
    """The native evidence handoff is incomplete or changed in transit."""


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _no_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceBindingError(f"JSON contém chave duplicada: {key}")
        result[key] = value
    return result


def _read_json(path: Path, *, label: str) -> dict[str, object]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise EvidenceBindingError(f"{label} ausente ou inseguro: {path}")
    try:
        if path.stat().st_size > MAX_BINDING_BYTES:
            raise EvidenceBindingError(f"{label} excede o limite permitido")
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_no_duplicate_pairs,
        )
    except EvidenceBindingError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvidenceBindingError(f"{label} inválido: {path}") from error
    if not isinstance(value, dict):
        raise EvidenceBindingError(f"{label} precisa ser um objeto")
    return value


def _relative(root: Path, path: Path, *, label: str) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise EvidenceBindingError(f"{label} está fora da raiz: {path}") from error
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise EvidenceBindingError(f"{label} não é um caminho relativo canônico")
    value = PurePosixPath(*relative.parts).as_posix()
    if value != "/".join(relative.parts) or "\\" in value:
        raise EvidenceBindingError(f"{label} possui caminho não portável: {value}")
    return value


def _safe_root(path: Path, *, label: str) -> Path:
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        raise EvidenceBindingError(f"{label} ausente ou insegura: {path}")
    return path


def _walk_regular_files(root: Path) -> list[Path]:
    root = _safe_root(root, label="raiz de evidência")
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise EvidenceBindingError(f"evidência contém symlink: {path}")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise EvidenceBindingError(f"evidência contém tipo especial: {path}")
    return files


def _digest(path: Path) -> tuple[int, str]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise EvidenceBindingError(f"arquivo de evidência ausente ou inseguro: {path}")
    try:
        metadata = path.stat()
        if metadata.st_size > MAX_FILE_BYTES:
            raise EvidenceBindingError(f"arquivo de evidência excede o limite: {path}")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()
    except EvidenceBindingError:
        raise
    except OSError as error:
        raise EvidenceBindingError(f"não foi possível ler evidência: {path}") from error


def _file_map(root: Path, *, excluded: str | None = None) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for path in _walk_regular_files(root):
        relative = _relative(root, path, label="arquivo de evidência")
        if relative == excluded:
            continue
        size, digest = _digest(path)
        result[relative] = {"size": size, "sha256": digest}
    if not result:
        raise EvidenceBindingError("não há arquivos de evidência para vincular")
    return result


def _manifest_identity(candidate: Path) -> dict[str, str]:
    try:
        manifest = verify_candidate(Path(candidate), allow_pending_evidence=True)
        manifest_path = Path(candidate) / "candidate.json"
        manifest_digest = _digest(manifest_path)[1]
    except (CandidateError, OSError, EvidenceBindingError) as error:
        raise EvidenceBindingError(f"candidato não pôde ser validado: {error}") from error
    return {
        "version": str(manifest["version"]),
        "commit": str(manifest["commit"]),
        "manifest_sha256": manifest_digest,
    }


def _validate_identity(value: object, *, expected: dict[str, str]) -> None:
    if not isinstance(value, dict) or set(value) != IDENTITY_FIELDS:
        raise EvidenceBindingError("identidade do candidato possui campos inválidos")
    if (
        not isinstance(value.get("version"), str)
        or not isinstance(value.get("commit"), str)
        or not HEX40.fullmatch(value["commit"])
        or not isinstance(value.get("manifest_sha256"), str)
        or not HEX64.fullmatch(value["manifest_sha256"])
        or value != expected
    ):
        raise EvidenceBindingError("binding diverge do manifest do candidato")


def _validate_source(value: object) -> None:
    if not isinstance(value, dict) or set(value) != SOURCE_FIELDS:
        raise EvidenceBindingError("proveniência do handoff possui campos inválidos")
    for field in ("workflow", "artifact_name"):
        item = value.get(field)
        if not isinstance(item, str) or not item or len(item) > 512 or any(ord(char) < 0x20 for char in item):
            raise EvidenceBindingError(f"source.{field} inválido")
    for field in ("run_id", "run_attempt"):
        if not isinstance(value.get(field), str) or DECIMAL.fullmatch(value[field]) is None:
            raise EvidenceBindingError(f"source.{field} inválido")


def _binding_digest(value: dict[str, object]) -> str:
    identity = dict(value)
    identity["binding_sha256"] = None
    return hashlib.sha256(_canonical_bytes(identity)).hexdigest()


def _record_paths(records_root: Path, artifact_root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in _walk_regular_files(records_root):
        if path.suffix.casefold() != ".json":
            raise EvidenceBindingError(f"registro nativo não é JSON: {path}")
        report = _read_json(path, label="registro nativo")
        platform = report.get("platform")
        if not isinstance(platform, str) or not platform:
            raise EvidenceBindingError(f"registro nativo sem plataforma: {path}")
        if platform in result:
            raise EvidenceBindingError(f"registro nativo duplicado: {platform}")
        result[platform] = _relative(artifact_root, path, label="registro nativo")
    return result


def _validate_file_map(
    value: object,
    *,
    artifact_root: Path,
    binding_relative: str,
) -> dict[str, dict[str, object]]:
    if not isinstance(value, dict) or not value:
        raise EvidenceBindingError("binding sem mapa de arquivos")
    declared: dict[str, dict[str, object]] = {}
    for raw_path, metadata in value.items():
        if not isinstance(raw_path, str):
            raise EvidenceBindingError("binding contém caminho de arquivo inválido")
        relative = PurePosixPath(raw_path)
        canonical = relative.as_posix()
        if (
            canonical != raw_path
            or relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or "\\" in raw_path
            or raw_path == binding_relative
        ):
            raise EvidenceBindingError(f"caminho declarado inválido no binding: {raw_path}")
        if (
            not isinstance(metadata, dict)
            or set(metadata) != {"size", "sha256"}
            or type(metadata.get("size")) is not int
            or metadata["size"] < 0
            or not isinstance(metadata.get("sha256"), str)
            or HEX64.fullmatch(metadata["sha256"]) is None
        ):
            raise EvidenceBindingError(f"digest inválido no binding: {raw_path}")
        path = artifact_root.joinpath(*relative.parts)
        size, digest = _digest(path)
        if size != metadata["size"] or digest != metadata["sha256"]:
            raise EvidenceBindingError(f"arquivo de evidência diverge: {raw_path}")
        declared[raw_path] = {"size": metadata["size"], "sha256": metadata["sha256"]}
    actual = _file_map(artifact_root, excluded=binding_relative)
    if actual != declared:
        missing = sorted(set(actual) - set(declared))
        extra = sorted(set(declared) - set(actual))
        details = []
        if missing:
            details.append("faltam " + ", ".join(missing))
        if extra:
            details.append("extras " + ", ".join(extra))
        raise EvidenceBindingError("mapa de arquivos não corresponde ao handoff (" + "; ".join(details) + ")")
    return declared


def create_binding(
    *,
    candidate: Path,
    records_dir: Path,
    artifact_root: Path,
    output: Path,
    source_workflow: str,
    source_run_id: str,
    source_run_attempt: str,
    source_artifact: str,
    generated_at: str | None = None,
) -> dict[str, object]:
    candidate = Path(candidate)
    artifact_root = _safe_root(Path(artifact_root), label="raiz de artefatos")
    records_dir = _safe_root(Path(records_dir), label="diretório de registros")
    output = Path(output)
    binding_relative = _relative(artifact_root, output, label="binding")
    if output.exists() or output.is_symlink():
        raise EvidenceBindingError(f"destino do binding já existe: {output}")
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if not isinstance(generated_at, str) or UTC_TIMESTAMP.fullmatch(generated_at) is None:
        raise EvidenceBindingError("generated_at precisa ser timestamp UTC canônico")
    _validate_source({
        "workflow": source_workflow,
        "run_id": source_run_id,
        "run_attempt": source_run_attempt,
        "artifact_name": source_artifact,
    })
    identity = _manifest_identity(candidate)
    expected_platforms = tuple(sorted(REQUIRED_NATIVE_PLATFORMS, key=str.casefold))
    try:
        validate_native_evidence(
            candidate=candidate,
            evidence_dir=records_dir,
            expected_platforms=expected_platforms,
            artifact_root=artifact_root,
        )
    except (CandidateError, OSError) as error:
        raise EvidenceBindingError(f"cobertura nativa inválida: {error}") from error
    record_paths = _record_paths(records_dir, artifact_root)
    if set(record_paths) != set(expected_platforms):
        raise EvidenceBindingError("registros nativos não cobrem exatamente o conjunto obrigatório")
    document: dict[str, object] = {
        "format": FORMAT,
        "project": PROJECT,
        "generated_at": generated_at,
        "candidate": identity,
        "records_root": _relative(artifact_root, records_dir, label="raiz de registros"),
        "platforms": {
            platform: {"path": record_paths[platform]}
            for platform in expected_platforms
        },
        "files": _file_map(artifact_root, excluded=binding_relative),
        "source": {
            "workflow": source_workflow,
            "run_id": source_run_id,
            "run_attempt": source_run_attempt,
            "artifact_name": source_artifact,
        },
        "binding_sha256": None,
    }
    document["binding_sha256"] = _binding_digest(document)
    _write_new(output, _canonical_bytes(document))
    return document


def verify_binding(
    *,
    candidate: Path,
    records_dir: Path,
    artifact_root: Path,
    binding: Path,
    expected_platforms: tuple[str, ...] | None = None,
) -> dict[str, object]:
    candidate = Path(candidate)
    artifact_root = _safe_root(Path(artifact_root), label="raiz de artefatos")
    records_dir = _safe_root(Path(records_dir), label="diretório de registros")
    binding = Path(binding)
    binding_relative = _relative(artifact_root, binding, label="binding")
    document = _read_json(binding, label="binding de evidência")
    if set(document) != BINDING_FIELDS:
        raise EvidenceBindingError("binding contém campos desconhecidos ou ausentes")
    if document.get("format") != FORMAT or document.get("project") != PROJECT:
        raise EvidenceBindingError("identidade do binding inválida")
    generated_at = document.get("generated_at")
    if not isinstance(generated_at, str) or UTC_TIMESTAMP.fullmatch(generated_at) is None:
        raise EvidenceBindingError("generated_at do binding inválido")
    identity = _manifest_identity(candidate)
    _validate_identity(document.get("candidate"), expected=identity)
    _validate_source(document.get("source"))
    binding_sha256 = document.get("binding_sha256")
    if not isinstance(binding_sha256, str) or HEX64.fullmatch(binding_sha256) is None:
        raise EvidenceBindingError("binding_sha256 inválido")
    if binding_sha256 != _binding_digest(document):
        raise EvidenceBindingError("binding_sha256 diverge do documento")
    records_root = document.get("records_root")
    expected_records_root = _relative(artifact_root, records_dir, label="raiz de registros")
    if records_root != expected_records_root:
        raise EvidenceBindingError("records_root diverge do diretório validado")
    expected = tuple(sorted(expected_platforms or REQUIRED_NATIVE_PLATFORMS, key=str.casefold))
    try:
        validate_native_evidence(
            candidate=candidate,
            evidence_dir=records_dir,
            expected_platforms=expected,
            artifact_root=artifact_root,
        )
    except (CandidateError, OSError) as error:
        raise EvidenceBindingError(f"cobertura nativa inválida: {error}") from error
    platforms = document.get("platforms")
    if not isinstance(platforms, dict) or set(platforms) != set(expected):
        raise EvidenceBindingError("binding não cobre exatamente as plataformas esperadas")
    actual_record_paths = _record_paths(records_dir, artifact_root)
    for platform in expected:
        entry = platforms.get(platform)
        if not isinstance(entry, dict) or set(entry) != {"path"} or entry.get("path") != actual_record_paths.get(platform):
            raise EvidenceBindingError(f"binding diverge do registro nativo: {platform}")
    _validate_file_map(
        document.get("files"),
        artifact_root=artifact_root,
        binding_relative=binding_relative,
    )
    return document


def _write_new(path: Path, payload: bytes) -> None:
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise EvidenceBindingError(f"destino do binding já existe: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="vincula evidência nativa ao candidato exato")
    commands = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--candidate", type=Path, required=True)
    common.add_argument("--records-dir", type=Path, required=True)
    common.add_argument("--artifact-root", type=Path, required=True)
    create = commands.add_parser("create", parents=[common])
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--source-workflow", required=True)
    create.add_argument("--source-run-id", required=True)
    create.add_argument("--source-run-attempt", required=True)
    create.add_argument("--source-artifact", required=True)
    create.add_argument("--generated-at")
    verify = commands.add_parser("verify", parents=[common])
    verify.add_argument("--binding", type=Path, required=True)
    verify.add_argument("--expected-platform", action="append", dest="expected_platforms")
    options = parser.parse_args(arguments)
    try:
        if options.command == "create":
            create_binding(
                candidate=options.candidate,
                records_dir=options.records_dir,
                artifact_root=options.artifact_root,
                output=options.output,
                source_workflow=options.source_workflow,
                source_run_id=options.source_run_id,
                source_run_attempt=options.source_run_attempt,
                source_artifact=options.source_artifact,
                generated_at=options.generated_at,
            )
        else:
            verify_binding(
                candidate=options.candidate,
                records_dir=options.records_dir,
                artifact_root=options.artifact_root,
                binding=options.binding,
                expected_platforms=tuple(options.expected_platforms) if options.expected_platforms else None,
            )
    except (CandidateError, OSError) as error:
        print(f"[ERRO] {error}")
        return 1
    print(f"[OK] Binding de evidência {options.command} validado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
