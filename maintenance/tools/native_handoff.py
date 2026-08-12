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
M3_CHIP = re.compile(r"^Apple M3(?:\s.*)?$")

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
    "candidate_artifact_size", "candidate_artifact_sha256", "entrypoint", "runtime",
    "receipt", "receipt_sha256", "stdout", "stdout_sha256", "stderr", "stderr_sha256",
})
ENTRYPOINT_RESULT_FIELDS = frozenset({"artifact", "size", "sha256"})
RECEIPT_FIELDS = frozenset({
    "format", "project", "protocol", "case", "artifact", "execution", "state",
})
RECEIPT_OPTIONAL_FIELDS = frozenset({"observations"})
RECEIPT_ARTIFACT_FIELDS = frozenset({"name", "size", "sha256"})
RECEIPT_EXECUTION_FIELDS = frozenset({"status", "exit_code"})
RECEIPT_STATE_FIELDS = frozenset({"before", "after"})
NATIVE_STATES = frozenset({"clean", "installed", "uninstalled"})
SERVICE_CASES = frozenset({"mvdsv-mvd", "qtv-stream", "qwfwd-forward"})
OBSERVATION_CASES = SERVICE_CASES | {"install-clean-space-unicode"}
CLIENT_CASES = frozenset({
    "client-stable-window-map-exit", "client-nightly-window-map-exit",
    "game-ktx", "game-final-arena", "game-pro-x", "game-team-fortress", "game-td2",
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
        if arguments != [
            "--candidate-root", "{candidate}", "--case", expected_name,
            "--scratch-root", "{scratch}", "--receipt", "{receipt}",
        ]:
            raise NativeHandoffError(f"argumentos fora do protocolo fechado: {expected_name}")
        timeout = raw_case.get("timeout_seconds")
        if type(timeout) is not int or not 1 <= timeout <= 900:
            raise NativeHandoffError(f"timeout inválido: {expected_name}")
        validated.append({
            "name": expected_name,
            "entrypoint_artifact": artifact,
            "entrypoint_artifact_path": artifact_path,
            "entrypoint_artifact_size": artifact_size,
            "entrypoint_artifact_sha256": artifact_digest,
            "arguments": list(arguments),
            "timeout_seconds": timeout,
        })
    return validated


def _expected_state(case: str) -> tuple[str, str]:
    if case == CANONICAL_CASES[0]:
        return "clean", "installed"
    if case == CANONICAL_CASES[1]:
        return "installed", "installed"
    if case == CANONICAL_CASES[-1]:
        return "installed", "uninstalled"
    return "installed", "installed"


def validate_case_observations(expected_case: str, value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise NativeHandoffError(f"observações nativas inválidas: {expected_case}")
    if expected_case == "install-clean-space-unicode":
        required = {
            "launcher", "commands", "help_lists_changes", "help_lists_migrate",
            "version_matches", "changes_executed", "migrate_dry_run_executed",
            "termination", "process_exit_code",
        }
        if set(value) != required or value.get("launcher") not in {"x86qw.sh", "x86qw.cmd"}:
            raise NativeHandoffError(f"observações nativas incompletas: {expected_case}")
        commands = value.get("commands")
        expected_commands = ["help", "version", "changes", "migrate"]
        if (
            not isinstance(commands, list)
            or len(commands) != len(expected_commands)
            or any(
                not isinstance(item, Mapping)
                or set(item) != {"name", "exit_code"}
                or item.get("name") != name
                or item.get("exit_code") != 0
                for item, name in zip(commands, expected_commands, strict=True)
            )
            or any(type(value.get(field)) is not bool or value[field] is not True for field in (
                "help_lists_changes", "help_lists_migrate", "version_matches",
                "changes_executed", "migrate_dry_run_executed",
            ))
        ):
            raise NativeHandoffError(f"observações nativas inválidas: {expected_case}")
    elif expected_case == "mvdsv-mvd":
        required = {
            "service", "server_ready", "map", "gamecode_log", "mvd_valid", "mvd_size",
            "mvd_sha256", "termination", "process_exit_code",
        }
        if set(value) != required or value.get("service") != "mvdsv":
            raise NativeHandoffError(f"observações nativas incompletas: {expected_case}")
        if (
            value.get("server_ready") is not True
            or value.get("map") != "dm6"
            or not isinstance(value.get("gamecode_log"), str)
            or not value["gamecode_log"]
            or value.get("mvd_valid") is not True
            or type(value.get("mvd_size")) is not int
            or value["mvd_size"] < 64
            or not isinstance(value.get("mvd_sha256"), str)
            or SHA256.fullmatch(value["mvd_sha256"]) is None
        ):
            raise NativeHandoffError(f"observações nativas inválidas: {expected_case}")
    elif expected_case == "qtv-stream":
        required = {
            "service", "http_ready", "http_status", "upstream_map", "stream_readable",
            "stream_header", "stream_bytes", "termination", "process_exit_code",
        }
        if set(value) != required or value.get("service") != "qtv":
            raise NativeHandoffError(f"observações nativas incompletas: {expected_case}")
        if (
            value.get("http_ready") is not True
            or value.get("http_status") != 200
            or value.get("upstream_map") != "dm6"
            or value.get("stream_readable") is not True
            or not isinstance(value.get("stream_header"), str)
            or "QTVSV" not in value["stream_header"]
            or type(value.get("stream_bytes")) is not int
            or value["stream_bytes"] <= 64
        ):
            raise NativeHandoffError(f"observações nativas inválidas: {expected_case}")
    elif expected_case == "qwfwd-forward":
        required = {
            "service", "udp_forwarded", "response_returned", "termination", "process_exit_code",
        }
        if set(value) != required or value.get("service") != "qwfwd":
            raise NativeHandoffError(f"observações nativas incompletas: {expected_case}")
        if value.get("udp_forwarded") is not True or value.get("response_returned") is not True:
            raise NativeHandoffError(f"observações nativas inválidas: {expected_case}")
    elif expected_case in CLIENT_CASES:
        required = {
            "window_title", "map", "gamecode_log", "content", "termination", "process_exit_code",
        }
        if set(value) != required:
            raise NativeHandoffError(f"observações nativas incompletas: {expected_case}")
        if (
            not isinstance(value["window_title"], str)
            or not value["window_title"]
            or len(value["window_title"]) > 512
            or not isinstance(value["map"], str)
            or not value["map"]
            or not isinstance(value["gamecode_log"], (str, type(None)))
            or not isinstance(value["content"], Mapping)
        ):
            raise NativeHandoffError(f"observações nativas inválidas: {expected_case}")
        content = value["content"]
        if set(content) != {"gamedir", "map", "map_source", "gamecode_package"}:
            raise NativeHandoffError(f"conteúdo nativo inválido: {expected_case}")
        if (
            not isinstance(content["gamedir"], str)
            or not isinstance(content["map"], str)
            or not isinstance(content["map_source"], str)
            or not isinstance(content["gamecode_package"], (str, type(None)))
        ):
            raise NativeHandoffError(f"conteúdo nativo inválido: {expected_case}")
    else:
        raise NativeHandoffError(f"observações nativas não suportadas: {expected_case}")
    if value.get("termination") != "controlled" or type(value.get("process_exit_code")) is not int:
        raise NativeHandoffError(f"observações nativas inválidas: {expected_case}")
    return dict(value)


def validate_case_receipt(
    path: Path,
    *,
    candidate: Path,
    expected_case: str,
    require_passed: bool = True,
    require_native_observations: bool = False,
) -> dict[str, object]:
    """Validate the candidate-owned receipt for one executed case."""

    value = read_json(path, label="recibo nativo")
    if (
        not RECEIPT_FIELDS.issubset(value)
        or set(value) - RECEIPT_FIELDS - RECEIPT_OPTIONAL_FIELDS
    ):
        raise NativeHandoffError("recibo nativo possui campos desconhecidos ou ausentes")
    if (
        value.get("format") != FORMAT
        or value.get("project") != PROJECT
        or value.get("protocol") != NATIVE_CASE_PROTOCOL
        or value.get("case") != expected_case
    ):
        raise NativeHandoffError(f"recibo nativo não corresponde ao caso: {expected_case}")
    artifact = value.get("artifact")
    if not isinstance(artifact, Mapping) or set(artifact) != RECEIPT_ARTIFACT_FIELDS:
        raise NativeHandoffError(f"recibo sem identidade do artefato: {expected_case}")
    name = _relative_path(artifact.get("name"), label="recibo.artifact.name")
    artifact_path, artifact_size, artifact_digest = candidate_artifact(candidate, name)
    if (
        artifact.get("size") != artifact_size
        or artifact.get("sha256") != artifact_digest
    ):
        raise NativeHandoffError(f"recibo diverge dos bytes do candidato: {expected_case}")
    execution = value.get("execution")
    if not isinstance(execution, Mapping) or set(execution) != RECEIPT_EXECUTION_FIELDS:
        raise NativeHandoffError(f"recibo sem resultado de execução: {expected_case}")
    status = execution.get("status")
    exit_code = execution.get("exit_code")
    if status not in {"passed", "failed"} or type(exit_code) is not int:
        raise NativeHandoffError(f"resultado de execução inválido: {expected_case}")
    if require_passed and (status != "passed" or exit_code != 0):
        raise NativeHandoffError(f"caso nativo não aprovado no recibo: {expected_case}")
    state = value.get("state")
    if not isinstance(state, Mapping) or set(state) != RECEIPT_STATE_FIELDS:
        raise NativeHandoffError(f"recibo sem pré-condição de lifecycle: {expected_case}")
    expected_before, expected_after = _expected_state(expected_case)
    if (
        state.get("before") != expected_before
        or state.get("after") != expected_after
        or state.get("before") not in NATIVE_STATES
        or state.get("after") not in NATIVE_STATES
    ):
        raise NativeHandoffError(f"pré-condição de lifecycle inválida: {expected_case}")
    result = {
        "format": FORMAT,
        "project": PROJECT,
        "protocol": NATIVE_CASE_PROTOCOL,
        "case": expected_case,
        "artifact": {
            "name": name,
            "size": artifact_size,
            "sha256": artifact_digest,
        },
        "execution": {"status": status, "exit_code": exit_code},
        "state": {"before": state["before"], "after": state["after"]},
    }
    observations = value.get("observations")
    if observations is None:
        if require_native_observations and expected_case in OBSERVATION_CASES:
            raise NativeHandoffError(f"observações nativas ausentes: {expected_case}")
    else:
        result["observations"] = validate_case_observations(expected_case, observations)
    return result


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
    if (
        not isinstance(environment, Mapping)
        or set(environment) != {"system", "machine", "chip", "model"}
        or environment.get("system") != "Darwin"
        or environment.get("machine") != "arm64"
        or not isinstance(environment.get("chip"), str)
        or M3_CHIP.fullmatch(environment["chip"]) is None
        or not isinstance(environment.get("model"), str)
        or not environment["model"]
    ):
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
        _artifact_path, artifact_size, artifact_digest = candidate_artifact(candidate, artifact)
        if (
            case.get("candidate_artifact_size") != artifact_size
            or case.get("candidate_artifact_sha256") != artifact_digest
        ):
            raise NativeHandoffError(f"resultado diverge dos bytes do candidato: {expected_name}")
        entrypoint = case.get("entrypoint")
        if not isinstance(entrypoint, Mapping) or set(entrypoint) != ENTRYPOINT_RESULT_FIELDS:
            raise NativeHandoffError(f"entrypoint do caso inválido: {expected_name}")
        entrypoint_name = _relative_path(entrypoint.get("artifact"), label="entrypoint.artifact")
        _entrypoint_path, entrypoint_size, entrypoint_digest = candidate_artifact(
            candidate, entrypoint_name,
        )
        if (
            entrypoint.get("size") != entrypoint_size
            or entrypoint.get("sha256") != entrypoint_digest
        ):
            raise NativeHandoffError(f"entrypoint do caso diverge: {expected_name}")
        duration = case.get("duration_ms")
        if type(duration) is not int or duration < 0:
            raise NativeHandoffError(f"duração inválida: {expected_name}")
        runtime = case.get("runtime")
        validated_runtime = validate_runtime(runtime)
        if not isinstance(runtime, Mapping) or dict(runtime) != validated_runtime:
            raise NativeHandoffError(f"runtime exato diverge do handoff: {expected_name}")
        runtime_path = Path(validated_runtime["path"])
        candidate_root = _candidate_root(candidate)
        if runtime_path == candidate_root or candidate_root in runtime_path.parents:
            raise NativeHandoffError(f"runtime do caso está dentro do candidato: {expected_name}")
        if (
            validated_runtime["size"] != entrypoint_size
            or validated_runtime["sha256"] != entrypoint_digest
        ):
            raise NativeHandoffError(f"runtime não está vinculado ao entrypoint: {expected_name}")
        receipt_relative = _relative_path(case.get("receipt"), label="recibo do caso")
        receipt_path = evidence_root.joinpath(*PurePosixPath(receipt_relative).parts)
        if receipt_path.is_symlink() or not receipt_path.is_file():
            raise NativeHandoffError(f"recibo ausente: {expected_name}")
        receipt_digest = case.get("receipt_sha256")
        if not isinstance(receipt_digest, str) or _digest(receipt_path)[1] != receipt_digest:
            raise NativeHandoffError(f"recibo diverge: {expected_name}")
        receipt = validate_case_receipt(
            receipt_path,
            candidate=candidate,
            expected_case=expected_name,
            require_native_observations=True,
        )
        if (
            receipt["artifact"]["name"] != artifact
            or receipt["artifact"]["size"] != artifact_size
            or receipt["artifact"]["sha256"] != artifact_digest
        ):
            raise NativeHandoffError(f"recibo não corresponde ao resultado: {expected_name}")
        case["_receipt_data"] = receipt
        for stream_name in ("stdout", "stderr"):
            relative = _relative_path(case.get(stream_name), label=f"{stream_name} do caso")
            stream = evidence_root.joinpath(*PurePosixPath(relative).parts)
            if stream.is_symlink() or not stream.is_file():
                raise NativeHandoffError(f"evidência {stream_name} ausente: {expected_name}")
            expected_digest = case.get(f"{stream_name}_sha256")
            if not isinstance(expected_digest, str) or _digest(stream)[1] != expected_digest:
                raise NativeHandoffError(f"evidência {stream_name} diverge: {expected_name}")
    return dict(value)
