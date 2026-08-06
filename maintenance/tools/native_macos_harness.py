"""Execute the complete x86QW candidate smoke on a real macOS arm64 host."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as host_platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools.native_handoff import (
    FORMAT,
    PLATFORM,
    PROJECT,
    NativeHandoffError,
    candidate_identity,
    read_json,
    validate_evidence_file,
    validate_plan,
    validate_runtime,
)


def select_platform(*, system: str, machine: str, candidate_available: bool) -> tuple[str, str | None, str]:
    """Return execute only for the live platform this PR can truthfully prove."""

    if not candidate_available:
        return "not-run", None, "candidato exato não foi fornecido"
    if system != "Darwin" or machine.casefold() != "arm64":
        return "not-run", None, f"host {system}/{machine} não é macOS arm64"
    return "execute", PLATFORM, "candidato explícito disponível em macOS arm64"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def execute_cases(*, candidate: Path, plan: dict[str, object], output_dir: Path) -> list[dict[str, object]]:
    """Run real processes for every canonical case without claiming native evidence."""

    candidate = Path(candidate).absolute()
    cases = validate_plan(plan, candidate=candidate)
    output_dir = Path(output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise NativeHandoffError(f"destino de logs já existe: {output_dir}") from error
    environment = os.environ.copy()
    identity = candidate_identity(candidate)
    environment.update({
        "X86QW_CANDIDATE_ROOT": str(candidate),
        "X86QW_CANDIDATE_COMMIT": identity["commit"],
        "X86QW_CANDIDATE_MANIFEST_SHA256": identity["manifest_sha256"],
    })
    results: list[dict[str, object]] = []
    for index, case in enumerate(cases, start=1):
        name = str(case["name"])
        stdout_name = f"{index:02d}-{name}.stdout.log"
        stderr_name = f"{index:02d}-{name}.stderr.log"
        stdout_path = output_dir / stdout_name
        stderr_path = output_dir / stderr_name
        artifact_path = str(case["candidate_artifact_path"])
        runtime = validate_runtime(case["runtime"])
        command = [
            artifact_path if part == "{candidate}/" + str(case["candidate_artifact"]) else part
            for part in case["command"]
        ]
        started = time.monotonic()
        timed_out = False
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            process = subprocess.Popen(
                command,
                cwd=candidate,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                shell=False,
            )
            try:
                exit_code = process.wait(timeout=int(case["timeout_seconds"]))
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                exit_code = process.wait()
        validate_runtime(runtime)
        result = {
            "name": name,
            "status": "failed" if timed_out or exit_code != 0 else "passed",
            "exit_code": exit_code,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "candidate_artifact": case["candidate_artifact"],
            "candidate_artifact_sha256": case["candidate_artifact_sha256"],
            "runtime": runtime,
            "stdout": stdout_name,
            "stdout_sha256": _sha256(stdout_path),
            "stderr": stderr_name,
            "stderr_sha256": _sha256(stderr_path),
        }
        results.append(result)
        if result["status"] != "passed":
            break
    return results


def _write_json(path: Path, value: object) -> None:
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def preview_handoff(*, system: str, machine: str, reason: str) -> dict[str, object]:
    return {
        "format": FORMAT,
        "project": PROJECT,
        "status": "not-run",
        "platform": None,
        "candidate": None,
        "environment": {"system": system, "machine": machine},
        "runtime_executed": False,
        "cases": [],
        "reason": reason,
    }


def run_native(*, candidate: Path, plan: dict[str, object], output_dir: Path) -> dict[str, object]:
    """Emit a passed handoff only after real Darwin/arm64 process execution."""

    system = host_platform.system()
    machine = host_platform.machine()
    candidate = Path(candidate)
    mode, selected, reason = select_platform(
        system=system,
        machine=machine,
        candidate_available=(candidate / "candidate.json").is_file(),
    )
    output_dir = Path(output_dir)
    if mode != "execute":
        output_dir.mkdir(parents=True, exist_ok=False)
        handoff = preview_handoff(system=system, machine=machine, reason=reason)
        _write_json(output_dir / "handoff.json", handoff)
        return handoff
    results = execute_cases(candidate=candidate, plan=plan, output_dir=output_dir)
    passed = len(results) == len(plan["cases"]) and all(case["status"] == "passed" for case in results)
    handoff = {
        "format": FORMAT,
        "project": PROJECT,
        "status": "passed" if passed else "failed",
        "platform": selected,
        "candidate": candidate_identity(candidate),
        "environment": {"system": system, "machine": machine.casefold()},
        "runtime_executed": True,
        "cases": results,
        "reason": None if passed else "um ou mais casos nativos falharam",
    }
    _write_json(output_dir / "handoff.json", handoff)
    return handoff


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="executor real de smoke macOS M3/arm64")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--candidate", type=Path, required=True)
    run_parser.add_argument("--plan", type=Path, required=True)
    run_parser.add_argument("--output-dir", type=Path, required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--candidate", type=Path, required=True)
    validate_parser.add_argument("--handoff", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        if options.command == "validate":
            validate_evidence_file(options.handoff, candidate=options.candidate)
            print(f"[OK] handoff nativo válido: {options.handoff}")
            return 0
        mode = select_platform(
            system=host_platform.system(),
            machine=host_platform.machine(),
            candidate_available=(options.candidate / "candidate.json").is_file(),
        )[0]
        plan = {} if mode != "execute" else read_json(options.plan, label="plano nativo")
        handoff = run_native(candidate=options.candidate, plan=plan, output_dir=options.output_dir)
        if handoff["status"] != "passed":
            print(f"[NOT-RUN] {handoff['reason']}")
            return 2
        print(f"[OK] smoke macOS arm64 concluído: {options.output_dir / 'handoff.json'}")
        return 0
    except (NativeHandoffError, OSError, subprocess.SubprocessError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
