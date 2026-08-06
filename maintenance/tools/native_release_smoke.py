"""Normalize an externally executed native smoke handoff.

The release workflow never treats a configurable environment variable as proof
that a runtime ran.  A protected native workflow must publish one handoff per
announced platform; this read-only boundary checks its exact candidate binding,
canonical smoke cases, successful exit codes, and redaction before the release
evidence producer consumes it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from pathlib import PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools.release_candidate import CandidateError, verify_candidate
from x86qw_runtime.contracts.native_evidence import (
    CANONICAL_CASES,
    NATIVE_EVIDENCE_FORMAT,
    NativeEvidenceError,
    validate_cases,
    validate_environment,
)
from x86qw_runtime.io.managed_files import read_regular_file_beneath


PROJECT = "x86qw"
FORMAT = NATIVE_EVIDENCE_FORMAT
PLATFORM_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
HANDOFF_FIELDS = frozenset({
    "format", "project", "status", "platform", "completed_at", "candidate",
    "environment", "runtime_executed", "cases", "secrets",
})
SMOKE_REPORT_FIELDS = frozenset({
    "format", "status", "platform", "completed_at", "candidate", "environment", "cases", "secrets", "runtime_executed",
})
MAX_NATIVE_ARTIFACT_SIZE = 64 * 1024 * 1024
MAX_NATIVE_ARTIFACT_TOTAL_SIZE = 256 * 1024 * 1024


def _no_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise CandidateError(f"JSON contém chave duplicada: {key}")
        value[key] = item
    return value


def _read_json(path: Path, *, label: str) -> dict[str, object]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise CandidateError(f"{label} ausente ou inseguro: {path}")
    try:
        if path.stat().st_size > 1024 * 1024:
            raise CandidateError(f"{label} excede o limite de 1 MiB")
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_pairs)
    except CandidateError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CandidateError(f"{label} inválido: {path}") from error
    if not isinstance(value, dict):
        raise CandidateError(f"{label} precisa ser objeto: {path}")
    return value


def _manifest_digest(candidate: Path) -> str:
    manifest = Path(candidate) / "candidate.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise CandidateError(f"manifest do candidato ausente ou inseguro: {manifest}")
    try:
        return hashlib.sha256(manifest.read_bytes()).hexdigest()
    except OSError as error:
        raise CandidateError(f"não foi possível ler o manifest do candidato: {manifest}") from error


def _identity(manifest: dict[str, object], manifest_digest: str) -> dict[str, object]:
    return {
        "version": manifest["version"],
        "commit": manifest["commit"],
        "manifest_sha256": manifest_digest,
    }


def _validate_artifact_files(cases: object, *, root: Path) -> None:
    """Bind every declared evidence digest to bytes beside the handoff."""

    if not isinstance(cases, list):
        raise CandidateError("casos nativos inválidos para verificar artefatos")
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise CandidateError(f"raiz de artefatos ausente ou insegura: {root}")
    seen: set[str] = set()
    total_size = 0
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("artifacts"), list):
            raise CandidateError("caso nativo sem artefatos verificáveis")
        for artifact in case["artifacts"]:
            if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
                raise CandidateError("artefato nativo sem caminho verificável")
            raw_path = artifact["path"]
            relative = PurePosixPath(raw_path)
            normalized = relative.as_posix()
            if (
                normalized != raw_path
                or relative.is_absolute()
                or not relative.parts
                or any(part in {"", ".", ".."} for part in relative.parts)
                or "\\" in raw_path
            ):
                raise CandidateError(f"caminho de evidência inseguro: {raw_path}")
            if normalized in seen:
                raise CandidateError(f"artefato nativo duplicado: {relative.as_posix()}")
            seen.add(normalized)
            size = artifact.get("size")
            if type(size) is not int or size < 0 or size > MAX_NATIVE_ARTIFACT_SIZE:
                raise CandidateError(f"tamanho de evidência fora do limite: {normalized}")
            total_size += size
            if total_size > MAX_NATIVE_ARTIFACT_TOTAL_SIZE:
                raise CandidateError("artefatos nativos excedem o limite total")
            try:
                read_regular_file_beneath(
                    root,
                    relative,
                    expected_size=size,
                    expected_hash=artifact.get("sha256"),
                    max_size=MAX_NATIVE_ARTIFACT_SIZE,
                )
            except OSError as error:
                raise CandidateError(f"não foi possível validar evidência: {normalized}") from error


def _write(path: Path, value: object) -> None:
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise CandidateError(f"destino de handoff normalizado já existe: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def normalize_native_smoke(
    *,
    candidate: Path,
    platform: str,
    handoff: Path,
) -> dict[str, object]:
    """Validate one external native run and emit the smoke-report shape."""

    if not isinstance(platform, str) or PLATFORM_NAME.fullmatch(platform) is None:
        raise CandidateError(f"plataforma nativa inválida: {platform!r}")
    manifest = verify_candidate(Path(candidate), allow_pending_evidence=True)
    identity = _identity(manifest, _manifest_digest(Path(candidate)))
    value = _read_json(Path(handoff), label="handoff nativo")
    if set(value) != HANDOFF_FIELDS:
        raise CandidateError("handoff nativo contém campos desconhecidos ou ausentes")
    if (
        type(value.get("format")) is not int
        or value.get("format") != FORMAT
        or value.get("project") != PROJECT
        or value.get("status") != "passed"
        or value.get("platform") != platform
        or not isinstance(value.get("completed_at"), str)
        or UTC_TIMESTAMP.fullmatch(value["completed_at"]) is None
        or value.get("candidate") != identity
        or value.get("runtime_executed") is not True
        or value.get("secrets") != "redacted"
    ):
        raise CandidateError("handoff nativo não corresponde ao candidato/plataforma")
    try:
        environment = validate_environment(value.get("environment"), platform=platform)
        cases = validate_cases(value.get("cases"))
        _validate_artifact_files(value.get("cases"), root=Path(handoff).parent)
    except NativeEvidenceError as error:
        raise CandidateError(str(error)) from error
    report = {
        "format": FORMAT,
        "status": "passed",
        "platform": platform,
        "completed_at": value["completed_at"],
        "candidate": identity,
        "environment": environment,
        "cases": list(cases),
        "secrets": "redacted",
        "runtime_executed": True,
    }
    if set(report) != SMOKE_REPORT_FIELDS:
        raise CandidateError("relatório de smoke normalizado possui campos inválidos")
    return report


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="valida handoff de smoke nativo executado externamente")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        report = normalize_native_smoke(
            candidate=options.candidate,
            platform=options.platform,
            handoff=options.handoff,
        )
        _write(options.output, report)
    except (CandidateError, OSError) as error:
        print(f"[ERRO] {error}")
        return 1
    print(f"[OK] Handoff nativo validado em {options.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
