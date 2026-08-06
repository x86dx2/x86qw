"""Execute the complete x86QW candidate smoke on a real macOS arm64 host."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as host_platform
import stat
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


def select_platform(
    *,
    system: str,
    machine: str,
    candidate_available: bool,
    plan_available: bool = True,
) -> tuple[str, str | None, str]:
    """Return execute only for the live platform this PR can truthfully prove."""

    if not candidate_available:
        return "not-run", None, "candidato exato não foi fornecido"
    if system != "Darwin" or machine.casefold() != "arm64":
        return "not-run", None, f"host {system}/{machine} não é macOS arm64"
    if not plan_available:
        return "not-run", None, "plano nativo validável não foi fornecido"
    return "execute", PLATFORM, "candidato explícito disponível em macOS arm64"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _stage_entrypoint(
    *, source: Path, destination: Path, expected_size: int, expected_digest: str,
) -> dict[str, object]:
    """Copy candidate-owned bytes into an executable, private staging file."""

    source_descriptor = -1
    destination_descriptor = -1
    created = False
    try:
        source_descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(source_descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise NativeHandoffError(f"entrypoint não é arquivo regular: {source}")
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        created = True
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(source_descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            pending = memoryview(chunk)
            while pending:
                written = os.write(destination_descriptor, pending)
                if written <= 0:
                    raise OSError("escrita incompleta durante staging do entrypoint")
                pending = pending[written:]
        after = os.fstat(source_descriptor)
        unchanged = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) == (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        if not unchanged or size != expected_size or digest.hexdigest() != expected_digest:
            raise NativeHandoffError("bytes do entrypoint divergiram durante staging")
        os.fsync(destination_descriptor)
        os.fchmod(destination_descriptor, 0o700)
    except OSError as error:
        raise NativeHandoffError(f"não foi possível preparar entrypoint privado: {source}") from error
    finally:
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if created and (
            not destination.exists()
            or destination.stat().st_size != expected_size
            or _sha256(destination) != expected_digest
        ):
            destination.unlink(missing_ok=True)
    runtime = {
        "path": str(destination.absolute()),
        "size": expected_size,
        "sha256": expected_digest,
    }
    return validate_runtime(runtime)


def execute_cases(*, candidate: Path, plan: dict[str, object], output_dir: Path) -> list[dict[str, object]]:
    """Run the candidate-owned entrypoint for every canonical case."""

    candidate = Path(candidate).absolute()
    cases = validate_plan(plan, candidate=candidate)
    initial_identity = candidate_identity(candidate)
    output_dir = Path(output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise NativeHandoffError(f"destino de logs já existe: {output_dir}") from error
    output_dir.chmod(0o700)
    runtime_dir = output_dir / ".runtime"
    runtime_dir.mkdir(mode=0o700)
    first_case = cases[0]
    runtime = _stage_entrypoint(
        source=Path(first_case["candidate_artifact_path"]),
        destination=runtime_dir / "x86qw-native-smoke",
        expected_size=int(first_case["runtime_size"]),
        expected_digest=str(first_case["candidate_artifact_sha256"]),
    )
    environment = {
        "HOME": str(output_dir.absolute()),
        "TMPDIR": str(output_dir.absolute()),
        "X86QW_CANDIDATE_ROOT": str(candidate),
        "X86QW_CANDIDATE_COMMIT": initial_identity["commit"],
        "X86QW_CANDIDATE_MANIFEST_SHA256": initial_identity["manifest_sha256"],
    }
    results: list[dict[str, object]] = []
    for index, case in enumerate(cases, start=1):
        name = str(case["name"])
        stdout_name = f"{index:02d}-{name}.stdout.log"
        stderr_name = f"{index:02d}-{name}.stderr.log"
        stdout_path = output_dir / stdout_name
        stderr_path = output_dir / stderr_name
        validate_runtime(runtime)
        arguments = [
            str(candidate) if part == "{candidate}" else str(part)
            for part in case["arguments"]
        ]
        command = [str(runtime["path"]), *arguments]
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
            "runtime": dict(runtime),
            "stdout": stdout_name,
            "stdout_sha256": _sha256(stdout_path),
            "stderr": stderr_name,
            "stderr_sha256": _sha256(stderr_path),
        }
        results.append(result)
        if result["status"] != "passed":
            break
    validate_plan(plan, candidate=candidate)
    if candidate_identity(candidate) != initial_identity:
        raise NativeHandoffError("candidato exato divergiu durante execução")
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
        plan_available=bool(plan),
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
            plan_available=options.plan.is_file(),
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
