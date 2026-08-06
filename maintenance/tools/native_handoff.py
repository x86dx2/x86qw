"""Closed contracts for a native macOS arm64 smoke handoff.

This module validates identity and evidence; it does not promote support or
turn a portable test result into native evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath


PROJECT = "x86qw"
FORMAT = 1
PLAN_FORMAT = 2
PLATFORM = "macOS-ARM64"
MAX_JSON_BYTES = 1024 * 1024
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")

CANONICAL_CASES = (
    "install-clean-space-unicode",
    "install-existing-space-unicode",
    "client-stable-window-map-exit",
    "client-nightly-window-map-exit",
    "game-ktx",
    "game-final-arena",
    "game-pro-x",
    "game-team-fortress",
    "game-td2",
    "mvdsv-mvd",
    "qtv-stream",
    "qwfwd-forward",
    "lifecycle-update",
    "lifecycle-upgrade",
    "lifecycle-verify",
    "lifecycle-repair",
    "lifecycle-cleanup",
    "lifecycle-uninstall",
)

IDENTITY_FIELDS = frozenset({"version", "commit", "manifest_sha256"})
PLAN_FIELDS = frozenset({
    "format", "project", "platform", "candidate", "entrypoint", "cases",
})
PLAN_CASE_FIELDS = frozenset({"name", "arguments", "timeout_seconds"})
ENTRYPOINT_FIELDS = frozenset({
    "contract_artifact", "contract_sha256", "artifact", "size", "sha256",
})
ENTRYPOINT_CONTRACT_FIELDS = frozenset({
    "format", "project", "platform", "protocol", "entrypoint_artifact",
})
NATIVE_CASE_PROTOCOL = "x86qw-native-case-v1"
RUNTIME_FIELDS = frozenset({"path", "size", "sha256"})
HANDOFF_FIELDS = frozenset({
    "format", "project", "status", "platform", "candidate", "environment",
    "runtime_executed", "cases", "reason",
})
RESULT_FIELDS = frozenset({
    "name", "status", "exit_code", "duration_ms", "candidate_artifact",
    "candidate_artifact_sha256", "runtime", "stdout", "stdout_sha256", "stderr",
    "stderr_sha256",
})


class NativeHandoffError(RuntimeError):
    """A candidate, execution plan, or native handoff is not trustworthy."""


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise NativeHandoffError(f"JSON contém chave duplicada: {key}")
        result[key] = value
    return result


def read_json(path: Path, *, label: str) -> dict[str, object]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise NativeHandoffError(f"{label} ausente ou inseguro: {path}")
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise NativeHandoffError(f"{label} excede 1 MiB")
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicates)
    except NativeHandoffError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise NativeHandoffError(f"{label} inválido: {path}") from error
    if not isinstance(value, dict):
        raise NativeHandoffError(f"{label} precisa ser objeto")
    return value


def _digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
    except OSError as error:
        raise NativeHandoffError(f"não foi possível ler bytes: {path}") from error
    return size, digest.hexdigest()


def _relative_path(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise NativeHandoffError(f"{label} inválido")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise NativeHandoffError(f"{label} inseguro: {value!r}")
    return path.as_posix()


def _candidate_root(candidate: Path) -> Path:
    root = Path(candidate).absolute()
    if root.is_symlink() or not root.is_dir():
        raise NativeHandoffError(f"candidato ausente ou inseguro: {root}")
    return root


def candidate_identity(candidate: Path) -> dict[str, str]:
    root = _candidate_root(candidate)
    manifest_path = root / "candidate.json"
    manifest = read_json(manifest_path, label="manifest do candidato")
    if manifest.get("project") != PROJECT:
        raise NativeHandoffError("manifest não pertence ao projeto x86qw")
    version = manifest.get("version")
    commit = manifest.get("commit")
    artifacts = manifest.get("artifacts")
    if not isinstance(version, str) or not version or len(version) > 128:
        raise NativeHandoffError("manifest possui versão inválida")
    if not isinstance(commit, str) or SHA40.fullmatch(commit) is None:
        raise NativeHandoffError("manifest possui commit inválido")
    if not isinstance(artifacts, Mapping) or not artifacts:
        raise NativeHandoffError("manifest não declara artifacts")
    return {
        "version": version,
        "commit": commit,
        "manifest_sha256": _digest(manifest_path)[1],
    }


def candidate_artifact(candidate: Path, name: str) -> tuple[Path, int, str]:
    root = _candidate_root(candidate)
    manifest = read_json(root / "candidate.json", label="manifest do candidato")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping) or name not in artifacts:
        raise NativeHandoffError(f"artifact não pertence ao candidato exato: {name}")
    entry = artifacts[name]
    if not isinstance(entry, Mapping) or set(entry) != {"size", "sha256"}:
        raise NativeHandoffError(f"identidade de artifact inválida: {name}")
    expected_size = entry.get("size")
    expected_digest = entry.get("sha256")
    if (
        type(expected_size) is not int
        or expected_size < 0
        or not isinstance(expected_digest, str)
        or SHA256.fullmatch(expected_digest) is None
    ):
        raise NativeHandoffError(f"identidade de artifact inválida: {name}")
    relative = _relative_path(name, label="caminho de artifact")
    path = root.joinpath(*PurePosixPath(relative).parts)
    current = path
    while current != root:
        if current.is_symlink():
            raise NativeHandoffError(f"artifact usa symlink: {name}")
        current = current.parent
    if not path.is_file():
        raise NativeHandoffError(f"bytes do artifact ausentes: {name}")
    size, digest = _digest(path)
    if size != expected_size or digest != expected_digest:
        raise NativeHandoffError(f"bytes do artifact divergem do candidato exato: {name}")
    return path, size, digest


def validate_runtime(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != RUNTIME_FIELDS:
        raise NativeHandoffError("identidade do runtime exato é inválida")
    raw_path = value.get("path")
    size = value.get("size")
    digest = value.get("sha256")
    if (
        not isinstance(raw_path, str)
        or not raw_path
        or "\x00" in raw_path
        or type(size) is not int
        or size < 0
        or not isinstance(digest, str)
        or SHA256.fullmatch(digest) is None
    ):
        raise NativeHandoffError("identidade do runtime exato é inválida")
    path = Path(raw_path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise NativeHandoffError(f"runtime exato ausente ou inseguro: {raw_path}")
    actual_size, actual_digest = _digest(path)
    if actual_size != size or actual_digest != digest:
        raise NativeHandoffError(f"bytes do runtime exato divergem: {raw_path}")
    return {"path": str(path), "size": size, "sha256": digest}


def validate_plan(value: object, *, candidate: Path) -> list[dict[str, object]]:
    if not isinstance(value, Mapping):
        raise NativeHandoffError("plano nativo precisa ser objeto")
    if value.get("format") != PLAN_FORMAT:
        raise NativeHandoffError("plano nativo usa formato legado ou inválido")
    if set(value) != PLAN_FIELDS:
        raise NativeHandoffError("plano nativo possui campos desconhecidos ou ausentes")
    if (
        value.get("format") != PLAN_FORMAT
        or value.get("project") != PROJECT
        or value.get("platform") != PLATFORM
    ):
        raise NativeHandoffError("plano nativo possui formato/projeto/plataforma inválido")
    expected_identity = candidate_identity(candidate)
    identity = value.get("candidate")
    if not isinstance(identity, Mapping) or set(identity) != IDENTITY_FIELDS or dict(identity) != expected_identity:
        raise NativeHandoffError("plano não corresponde ao candidato exato")
    entrypoint = value.get("entrypoint")
    if not isinstance(entrypoint, Mapping) or set(entrypoint) != ENTRYPOINT_FIELDS:
        raise NativeHandoffError("plano nativo não possui entrypoint fechado")
    contract_name = _relative_path(
        entrypoint.get("contract_artifact"), label="entrypoint.contract_artifact",
    )
    contract_path, _contract_size, contract_digest = candidate_artifact(
        candidate, contract_name,
    )
    artifact = _relative_path(entrypoint.get("artifact"), label="entrypoint.artifact")
    artifact_path, artifact_size, artifact_digest = candidate_artifact(candidate, artifact)
    contract = read_json(contract_path, label="contrato de entrypoint")
    if (
        set(contract) != ENTRYPOINT_CONTRACT_FIELDS
        or contract.get("format") != 1
        or contract.get("project") != PROJECT
        or contract.get("platform") != PLATFORM
        or contract.get("protocol") != NATIVE_CASE_PROTOCOL
        or contract.get("entrypoint_artifact") != artifact
    ):
        raise NativeHandoffError("contrato de entrypoint não autoriza o protocolo fechado")
    if (
        entrypoint.get("contract_sha256") != contract_digest
        or entrypoint.get("size") != artifact_size
        or entrypoint.get("sha256") != artifact_digest
    ):
        raise NativeHandoffError("entrypoint diverge dos bytes do candidato exato")
    cases = value.get("cases")
    if not isinstance(cases, list) or len(cases) != len(CANONICAL_CASES):
        raise NativeHandoffError("plano não contém o lifecycle completo")
    validated: list[dict[str, object]] = []
    for expected_name, raw_case in zip(CANONICAL_CASES, cases, strict=True):
        if not isinstance(raw_case, Mapping) or set(raw_case) != PLAN_CASE_FIELDS:
            raise NativeHandoffError(f"caso inválido: {expected_name}")
        if raw_case.get("name") != expected_name:
            raise NativeHandoffError(f"caso ausente ou fora de ordem: {expected_name}")
        arguments = raw_case.get("arguments")
        if arguments != ["--candidate-root", "{candidate}", "--case", expected_name]:
            raise NativeHandoffError(f"argumentos fora do protocolo fechado: {expected_name}")
        timeout = raw_case.get("timeout_seconds")
        if type(timeout) is not int or not 1 <= timeout <= 900:
            raise NativeHandoffError(f"timeout inválido: {expected_name}")
        validated.append({
            "name": expected_name,
            "candidate_artifact": artifact,
            "candidate_artifact_path": artifact_path,
            "candidate_artifact_sha256": artifact_digest,
            "runtime_size": artifact_size,
            "arguments": list(arguments),
            "timeout_seconds": timeout,
        })
    return validated


def validate_evidence_file(path: Path, *, candidate: Path) -> dict[str, object]:
    value = read_json(path, label="handoff de evidência")
    if set(value) != HANDOFF_FIELDS:
        raise NativeHandoffError("handoff possui campos desconhecidos ou ausentes")
    if value.get("status") != "passed" or value.get("runtime_executed") is not True:
        raise NativeHandoffError("handoff not-run/failed não é evidência nativa")
    if value.get("format") != FORMAT or value.get("project") != PROJECT or value.get("platform") != PLATFORM:
        raise NativeHandoffError("handoff possui formato/projeto/plataforma inválido")
    expected_identity = candidate_identity(candidate)
    identity = value.get("candidate")
    if not isinstance(identity, Mapping) or set(identity) != IDENTITY_FIELDS or dict(identity) != expected_identity:
        raise NativeHandoffError("handoff não corresponde ao candidato exato")
    environment = value.get("environment")
    if environment != {"system": "Darwin", "machine": "arm64"}:
        raise NativeHandoffError("handoff não corresponde a macOS arm64 nativo")
    if value.get("reason") is not None:
        raise NativeHandoffError("handoff aprovado não pode conter razão de not-run")
    cases = value.get("cases")
    if not isinstance(cases, list) or len(cases) != len(CANONICAL_CASES):
        raise NativeHandoffError("handoff não contém o lifecycle completo")
    evidence_root = Path(path).parent
    for expected_name, case in zip(CANONICAL_CASES, cases, strict=True):
        if not isinstance(case, Mapping) or set(case) != RESULT_FIELDS:
            raise NativeHandoffError(f"resultado inválido: {expected_name}")
        if case.get("name") != expected_name or case.get("status") != "passed" or case.get("exit_code") != 0:
            raise NativeHandoffError(f"caso nativo não aprovado: {expected_name}")
        artifact = _relative_path(case.get("candidate_artifact"), label="candidate_artifact")
        _artifact_path, _artifact_size, artifact_digest = candidate_artifact(candidate, artifact)
        if case.get("candidate_artifact_sha256") != artifact_digest:
            raise NativeHandoffError(f"resultado diverge dos bytes do candidato: {expected_name}")
        duration = case.get("duration_ms")
        if type(duration) is not int or duration < 0:
            raise NativeHandoffError(f"duração inválida: {expected_name}")
        runtime = case.get("runtime")
        validated_runtime = validate_runtime(runtime)
        if not isinstance(runtime, Mapping) or dict(runtime) != validated_runtime:
            raise NativeHandoffError(f"runtime exato diverge do handoff: {expected_name}")
        for stream_name in ("stdout", "stderr"):
            relative = _relative_path(case.get(stream_name), label=f"{stream_name} do caso")
            stream = evidence_root.joinpath(*PurePosixPath(relative).parts)
            if stream.is_symlink() or not stream.is_file():
                raise NativeHandoffError(f"evidência {stream_name} ausente: {expected_name}")
            expected_digest = case.get(f"{stream_name}_sha256")
            if not isinstance(expected_digest, str) or _digest(stream)[1] != expected_digest:
                raise NativeHandoffError(f"evidência {stream_name} diverge: {expected_name}")
    return dict(value)
