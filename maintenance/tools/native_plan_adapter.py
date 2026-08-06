"""Generate a native plan only from an explicit candidate-owned entrypoint contract."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools.native_handoff import (
    CANONICAL_CASES,
    ENTRYPOINT_CONTRACT_FIELDS,
    NATIVE_CASE_PROTOCOL,
    PLAN_FORMAT,
    PLATFORM,
    PROJECT,
    NativeHandoffError,
    candidate_artifact,
    candidate_identity,
    read_json,
    validate_plan,
)


SHA256 = re.compile(r"^[0-9a-f]{64}$")
class PlanNotRun(NativeHandoffError):
    """The candidate has no explicit native entrypoint capability."""


def _relative(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise NativeHandoffError(f"{label} inválido")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise NativeHandoffError(f"{label} inseguro: {value!r}")
    return path.as_posix()


def _preflight_output(candidate: Path, output: Path) -> None:
    candidate = Path(candidate).absolute()
    output = Path(output).absolute()
    if output == candidate or candidate in output.parents:
        raise NativeHandoffError("plano precisa ficar fora do candidato imutável")
    if output.exists() or output.is_symlink():
        raise NativeHandoffError(f"destino de plano já existe: {output}")
    if output.parent.is_symlink() or not output.parent.is_dir():
        raise NativeHandoffError(f"diretório de plano ausente ou inseguro: {output.parent}")


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
            raise NativeHandoffError(f"destino de plano já existe: {path}") from error
    finally:
        temporary.unlink(missing_ok=True)


def generate_native_plan(
    *,
    candidate: Path,
    expected_candidate_sha256: str,
    entrypoint_contract: str,
    output: Path,
) -> dict[str, object]:
    """Emit deterministic format-2 plan, or not-run when capability is absent."""

    candidate = Path(candidate)
    output = Path(output)
    contract_name = _relative(entrypoint_contract, label="entrypoint contract")
    if candidate.is_symlink() or not candidate.is_dir() or not (candidate / "candidate.json").is_file():
        raise PlanNotRun("candidato exato não está disponível")
    identity = candidate_identity(candidate)
    if (
        not isinstance(expected_candidate_sha256, str)
        or SHA256.fullmatch(expected_candidate_sha256) is None
        or expected_candidate_sha256 != identity["manifest_sha256"]
    ):
        raise NativeHandoffError("expected candidate-sha256 diverge do candidate.json exato")
    manifest = read_json(candidate / "candidate.json", label="manifest do candidato")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise NativeHandoffError("manifest do candidato não possui artifacts")
    contract_path = candidate.joinpath(*PurePosixPath(contract_name).parts)
    if contract_name not in artifacts and not contract_path.exists():
        raise PlanNotRun("candidato não declara contrato de entrypoint; plano permanece not-run")
    if contract_name not in artifacts:
        raise NativeHandoffError("contrato de entrypoint existe, mas não está registrado no candidato")
    contract_path, _contract_size, contract_digest = candidate_artifact(candidate, contract_name)
    contract = read_json(contract_path, label="contrato de entrypoint")
    if set(contract) != ENTRYPOINT_CONTRACT_FIELDS:
        raise NativeHandoffError("contrato de entrypoint possui campos desconhecidos ou ausentes")
    if (
        contract.get("format") != 1
        or contract.get("project") != PROJECT
        or contract.get("platform") != PLATFORM
        or contract.get("protocol") != NATIVE_CASE_PROTOCOL
    ):
        raise NativeHandoffError("contrato de entrypoint possui identidade/protocolo inválido")
    entrypoint_name = _relative(
        contract.get("entrypoint_artifact"), label="entrypoint_artifact",
    )
    if entrypoint_name not in artifacts:
        raise NativeHandoffError("entrypoint não está registrado no candidato")
    _entrypoint_path, entrypoint_size, entrypoint_digest = candidate_artifact(
        candidate, entrypoint_name,
    )
    _preflight_output(candidate, output)
    plan = {
        "format": PLAN_FORMAT,
        "project": PROJECT,
        "platform": PLATFORM,
        "candidate": identity,
        "entrypoint": {
            "contract_artifact": contract_name,
            "contract_sha256": contract_digest,
            "artifact": entrypoint_name,
            "size": entrypoint_size,
            "sha256": entrypoint_digest,
        },
        "cases": [
            {
                "name": name,
                "arguments": ["--candidate-root", "{candidate}", "--case", name],
                "timeout_seconds": 300,
            }
            for name in CANONICAL_CASES
        ],
    }
    validate_plan(plan, candidate=candidate)
    _write_exclusive(output, plan)
    return plan


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--expected-candidate-sha256", required=True)
    parser.add_argument("--entrypoint-contract", required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        generate_native_plan(
            candidate=options.candidate,
            expected_candidate_sha256=options.expected_candidate_sha256,
            entrypoint_contract=options.entrypoint_contract,
            output=options.output,
        )
    except PlanNotRun as error:
        print(f"[NOT-RUN] {error}")
        return 2
    except (NativeHandoffError, OSError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(f"[OK] plano nativo candidato-owned: {options.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
