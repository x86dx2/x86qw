"""Create an unsigned, redacted pending aggregate from one valid M3 handoff."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools.native_handoff import (
    CANONICAL_CASES,
    FORMAT,
    PLATFORM,
    PROJECT,
    NativeHandoffError,
    OBSERVATION_CASES,
    candidate_identity,
    read_json,
    validate_case_observations,
    validate_evidence_file,
)


SHA256 = re.compile(r"^[0-9a-f]{64}$")
M3_CHIP = re.compile(r"^Apple M3(?:\s.*)?$")
AGGREGATE_FIELDS = frozenset({
    "format", "project", "status", "signed", "promotable", "candidate",
    "platforms", "redaction",
})
PLATFORM_FIELDS = frozenset({"status", "runtime_executed", "environment", "cases"})
CASE_FIELDS = frozenset({
    "name", "status", "exit_code", "duration_ms", "candidate_artifact",
    "candidate_artifact_size", "candidate_artifact_sha256", "entrypoint", "runtime",
    "receipt", "stdout_sha256", "stderr_sha256",
})
RUNTIME_FIELDS = frozenset({"size", "sha256"})
ENTRYPOINT_FIELDS = frozenset({"artifact", "size", "sha256"})
REDACTED_RECEIPT_FIELDS = frozenset({"case", "artifact", "execution", "state"})
RECEIPT_ARTIFACT_FIELDS = frozenset({"name", "size", "sha256"})
RECEIPT_EXECUTION_FIELDS = frozenset({"status", "exit_code"})
RECEIPT_STATE_FIELDS = frozenset({"before", "after"})
REDACTION = {
    "commands": "removed",
    "environment_variables": "removed",
    "log_content": "digests-only",
    "paths": "removed",
}


class EvidenceNotRun(NativeHandoffError):
    """Exact native candidate evidence was not available, so nothing was written."""


def _preflight_inputs(candidate: Path, handoff: Path) -> None:
    candidate = Path(candidate)
    handoff = Path(handoff)
    if (
        candidate.is_symlink()
        or not candidate.is_dir()
        or not (candidate / "candidate.json").is_file()
    ):
        raise EvidenceNotRun("candidato exato não está disponível; agregado permanece not-run")
    if not handoff.exists():
        raise EvidenceNotRun("handoff nativo não está disponível; agregado permanece not-run")
    if handoff.is_symlink() or not handoff.is_file():
        raise NativeHandoffError(f"handoff nativo inseguro: {handoff}")
    value = read_json(handoff, label="handoff nativo")
    if value.get("status") == "not-run" and value.get("runtime_executed") is False:
        raise EvidenceNotRun("handoff nativo está not-run; agregado não foi criado")


def _preflight_output(candidate: Path, output: Path) -> None:
    candidate = Path(candidate).absolute()
    output = Path(output).absolute()
    if output.name.casefold() == "release-evidence.json":
        raise NativeHandoffError("complemento não pode criar release-evidence.json")
    if output == candidate or candidate in output.parents:
        raise NativeHandoffError("agregado pending precisa ficar fora do candidato imutável")
    if output.exists() or output.is_symlink():
        raise NativeHandoffError(f"destino de agregado já existe: {output}")
    parent = output.parent
    if parent.is_symlink() or not parent.is_dir():
        raise NativeHandoffError(f"diretório de saída ausente ou inseguro: {parent}")


def _project_case(case: Mapping[str, object]) -> dict[str, object]:
    runtime = case["runtime"]
    entrypoint = case["entrypoint"]
    receipt = case.get("_receipt_data")
    if not isinstance(runtime, Mapping) or not isinstance(entrypoint, Mapping) or not isinstance(receipt, Mapping):
        raise NativeHandoffError("handoff validado perdeu a identidade do runtime")
    projected = {
        "name": case["name"],
        "status": case["status"],
        "exit_code": case["exit_code"],
        "duration_ms": case["duration_ms"],
        "candidate_artifact": case["candidate_artifact"],
        "candidate_artifact_size": case["candidate_artifact_size"],
        "candidate_artifact_sha256": case["candidate_artifact_sha256"],
        "entrypoint": dict(entrypoint),
        "runtime": {"size": runtime["size"], "sha256": runtime["sha256"]},
        "receipt": {
            "case": receipt["case"],
            "artifact": dict(receipt["artifact"]),
            "execution": dict(receipt["execution"]),
            "state": dict(receipt["state"]),
        },
        "stdout_sha256": case["stdout_sha256"],
        "stderr_sha256": case["stderr_sha256"],
    }
    if receipt["case"] in OBSERVATION_CASES:
        observations = receipt.get("observations")
        if observations is None:
            raise NativeHandoffError(f"recibo de serviço sem observações: {receipt['case']}")
        projected["receipt"]["observations"] = validate_case_observations(
            str(receipt["case"]), observations,
        )
    return projected


def _validate_aggregate(value: object, *, identity: dict[str, str]) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != AGGREGATE_FIELDS:
        raise NativeHandoffError("agregado pending possui campos inválidos")
    if (
        value.get("format") != FORMAT
        or value.get("project") != PROJECT
        or value.get("status") != "pending"
        or value.get("signed") is not False
        or value.get("promotable") is not False
        or value.get("candidate") != identity
        or value.get("redaction") != REDACTION
    ):
        raise NativeHandoffError("agregado pending viola o contrato não promovível")
    platforms = value.get("platforms")
    if not isinstance(platforms, Mapping) or set(platforms) != {PLATFORM}:
        raise NativeHandoffError("agregado pending não contém exatamente macOS-ARM64")
    platform = platforms[PLATFORM]
    if not isinstance(platform, Mapping) or set(platform) != PLATFORM_FIELDS:
        raise NativeHandoffError("registro macOS-ARM64 possui campos inválidos")
    if (
        platform.get("status") != "passed"
        or platform.get("runtime_executed") is not True
    ):
        raise NativeHandoffError("registro macOS-ARM64 não prova execução nativa")
    environment = platform.get("environment")
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
        raise NativeHandoffError("registro macOS-ARM64 não prova Apple M3 observável")
    cases = platform.get("cases")
    if not isinstance(cases, list) or len(cases) != len(CANONICAL_CASES):
        raise NativeHandoffError("agregado pending não contém o lifecycle completo")
    for expected_name, case in zip(CANONICAL_CASES, cases, strict=True):
        if not isinstance(case, Mapping) or set(case) != CASE_FIELDS:
            raise NativeHandoffError(f"caso redigido inválido: {expected_name}")
        if (
            case.get("name") != expected_name
            or case.get("status") != "passed"
            or case.get("exit_code") != 0
        ):
            raise NativeHandoffError(f"caso redigido não aprovado: {expected_name}")
        duration = case.get("duration_ms")
        runtime = case.get("runtime")
        entrypoint = case.get("entrypoint")
        receipt = case.get("receipt")
        if type(duration) is not int or duration < 0:
            raise NativeHandoffError(f"duração redigida inválida: {expected_name}")
        if not isinstance(runtime, Mapping) or set(runtime) != RUNTIME_FIELDS:
            raise NativeHandoffError(f"runtime redigido inválido: {expected_name}")
        if not isinstance(entrypoint, Mapping) or set(entrypoint) != ENTRYPOINT_FIELDS:
            raise NativeHandoffError(f"entrypoint redigido inválido: {expected_name}")
        if not isinstance(receipt, Mapping):
            raise NativeHandoffError(f"recibo redigido inválido: {expected_name}")
        receipt_fields = set(receipt)
        allowed_receipt_fields = set(REDACTED_RECEIPT_FIELDS)
        if expected_name in OBSERVATION_CASES:
            allowed_receipt_fields.add("observations")
        if receipt_fields != allowed_receipt_fields:
            raise NativeHandoffError(f"recibo redigido inválido: {expected_name}")
        if type(runtime.get("size")) is not int or runtime["size"] < 0:
            raise NativeHandoffError(f"runtime redigido inválido: {expected_name}")
        if (
            not isinstance(case.get("candidate_artifact"), str)
            or type(case.get("candidate_artifact_size")) is not int
            or case["candidate_artifact_size"] < 0
        ):
            raise NativeHandoffError(f"artifact redigido inválido: {expected_name}")
        if (
            not isinstance(entrypoint.get("artifact"), str)
            or type(entrypoint.get("size")) is not int
            or entrypoint["size"] < 0
        ):
            raise NativeHandoffError(f"entrypoint redigido inválido: {expected_name}")
        receipt_artifact = receipt.get("artifact")
        execution = receipt.get("execution")
        state = receipt.get("state")
        if (
            not isinstance(receipt_artifact, Mapping)
            or set(receipt_artifact) != RECEIPT_ARTIFACT_FIELDS
            or not isinstance(execution, Mapping)
            or set(execution) != RECEIPT_EXECUTION_FIELDS
            or not isinstance(state, Mapping)
            or set(state) != RECEIPT_STATE_FIELDS
            or receipt.get("case") != expected_name
            or receipt_artifact != {
                "name": case["candidate_artifact"],
                "size": case["candidate_artifact_size"],
                "sha256": case["candidate_artifact_sha256"],
            }
            or execution != {"status": "passed", "exit_code": 0}
        ):
            raise NativeHandoffError(f"recibo redigido não prova o caso: {expected_name}")
        expected_before = "clean" if expected_name == CANONICAL_CASES[0] else "installed"
        expected_after = "uninstalled" if expected_name == CANONICAL_CASES[-1] else "installed"
        if state != {"before": expected_before, "after": expected_after}:
            raise NativeHandoffError(f"estado redigido inválido: {expected_name}")
        if expected_name in OBSERVATION_CASES:
            validate_case_observations(expected_name, receipt.get("observations"))
        for field in (
            "candidate_artifact_sha256", "stdout_sha256", "stderr_sha256",
        ):
            digest = case.get(field)
            if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
                raise NativeHandoffError(f"digest redigido inválido: {expected_name}.{field}")
        runtime_digest = runtime.get("sha256")
        if not isinstance(runtime_digest, str) or SHA256.fullmatch(runtime_digest) is None:
            raise NativeHandoffError(f"digest de runtime inválido: {expected_name}")
        for field in ("entrypoint",):
            digest = entrypoint.get("sha256")
            if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
                raise NativeHandoffError(f"digest redigido inválido: {expected_name}.{field}")
    return dict(value)


def _write_exclusive(path: Path, value: object) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise NativeHandoffError(f"destino de agregado já existe: {path}") from error
    finally:
        temporary.unlink(missing_ok=True)


def aggregate_pending_evidence(
    *,
    candidate: Path,
    handoff: Path,
    expected_candidate_sha256: str,
    output: Path,
) -> dict[str, object]:
    """Validate, redact, and write an unsigned non-promotable M3 aggregate."""

    candidate = Path(candidate)
    handoff = Path(handoff)
    output = Path(output)
    _preflight_inputs(candidate, handoff)
    _preflight_output(candidate, output)
    identity = candidate_identity(candidate)
    if (
        not isinstance(expected_candidate_sha256, str)
        or SHA256.fullmatch(expected_candidate_sha256) is None
        or expected_candidate_sha256 != identity["manifest_sha256"]
    ):
        raise NativeHandoffError("expected candidate-sha256 diverge do candidate.json exato")
    validated = validate_evidence_file(handoff, candidate=candidate)
    cases = validated["cases"]
    environment = validated["environment"]
    assert isinstance(cases, list)
    if not isinstance(environment, Mapping):
        raise NativeHandoffError("handoff validado perdeu o ambiente nativo")
    aggregate = {
        "format": FORMAT,
        "project": PROJECT,
        "status": "pending",
        "signed": False,
        "promotable": False,
        "candidate": identity,
        "platforms": {
            PLATFORM: {
                "status": "passed",
                "runtime_executed": True,
                "environment": dict(environment),
                "cases": [_project_case(case) for case in cases],
            }
        },
        "redaction": REDACTION,
    }
    result = _validate_aggregate(aggregate, identity=identity)
    _write_exclusive(output, result)
    return result


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--candidate", type=Path, required=True)
    aggregate.add_argument("--handoff", type=Path, required=True)
    aggregate.add_argument("--expected-candidate-sha256", required=True)
    aggregate.add_argument("--output", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        aggregate_pending_evidence(
            candidate=options.candidate,
            handoff=options.handoff,
            expected_candidate_sha256=options.expected_candidate_sha256,
            output=options.output,
        )
    except EvidenceNotRun as error:
        print(f"[NOT-RUN] {error}")
        return 2
    except (NativeHandoffError, OSError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(f"[OK] agregado pending redigido: {options.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
