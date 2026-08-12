#!/usr/bin/env python3
"""Legacy native smoke entrypoint kept for compatibility.

Format-2 plans are delegated to ``native_macos_harness``; the historical
format-1 helpers remain available only for compatibility tests and older
local plans. The release workflow always uses the canonical format-2 path.

The harness is intentionally small: the candidate supplies literal commands
and the assertions they must materialize as files.  The harness never uses a
shell, downloads bytes, accepts a missing plan, or invents a passed report.
Each assertion is bound to a post-process artifact and the final handoff is
validated through the same closed contract used by release promotion.
"""

from __future__ import annotations

import argparse
from functools import wraps
import hashlib
import json
import os
import platform as host_platform
import re
import secrets
import signal
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools.native_release_smoke import normalize_native_smoke  # noqa: E402
from maintenance.tools.release_candidate import CandidateError, verify_candidate  # noqa: E402
from x86qw_runtime.contracts.native_evidence import (  # noqa: E402
    CASE_ASSERTIONS,
    CANONICAL_CASES,
    NATIVE_EVIDENCE_FORMAT,
    NativeEvidenceError,
    validate_cases,
    validate_environment,
    validate_hardware,
)


class NativeM3Error(RuntimeError):
    """The native host, candidate plan, or observed result is not acceptable."""


M3_PATTERN = re.compile(r"^Apple M3(?:\s.*)?$")
PLAN_FIELDS = frozenset({"format", "project", "platform", "candidate", "cases"})
PLAN_CASE_FIELDS = frozenset({"name", "command", "assertions", "artifacts", "timeout_seconds"})
PLAN_ARTIFACT_FIELDS = frozenset({"path", "kind", "assertion"})
CONTROL_CHARS = ("\x00", "\r", "\n", ";", "&", "|", "`", "$", ">", "<")
FORBIDDEN_WORDS = ("mock", "fake", "stub", "fixture", "dry-run")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _identity(candidate: Path) -> dict[str, str]:
    manifest = candidate / "candidate.json"
    try:
        verified = verify_candidate(candidate)
    except (CandidateError, OSError) as error:
        raise NativeM3Error(f"candidato não pôde ser verificado: {error}") from error
    return {
        "version": str(verified["version"]),
        "commit": str(verified["commit"]),
        "manifest_sha256": _digest(manifest)[1],
    }


def _m3_environment() -> dict[str, object]:
    if host_platform.system() != "Darwin" or host_platform.machine().casefold() != "arm64":
        raise NativeM3Error("o harness nativo exige macOS arm64")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise NativeM3Error("o harness nativo exige usuário padrão não elevado")
    try:
        result = subprocess.run(
            ["/usr/sbin/system_profiler", "SPHardwareDataType", "-json"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError) as error:
        raise NativeM3Error(f"não foi possível observar o hardware M3: {error}") from error
    records = payload.get("SPHardwareDataType") if isinstance(payload, dict) else None
    record = records[0] if isinstance(records, list) and len(records) == 1 else None
    if not isinstance(record, dict):
        raise NativeM3Error("system_profiler não retornou um registro de hardware único")
    chip = record.get("chip") or record.get("chip_type")
    model = record.get("machine_model") or record.get("machine_name")
    if (
        not isinstance(chip, str) or M3_PATTERN.fullmatch(chip.strip()) is None
        or not isinstance(model, str) or not model.strip()
    ):
        raise NativeM3Error("o host não foi confirmado como Apple M3")
    return {
        "os": "macOS",
        "architecture": "arm64",
        "standard_user": True,
        "elevated": False,
        "distro": None,
        "distro_version": None,
        "glibc_version": None,
        "chip": chip.strip(),
        "model": model.strip(),
    }


def _read_json(path: Path, label: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise NativeM3Error(f"{label} ausente ou inseguro: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise NativeM3Error(f"{label} inválido: {path}") from error
    if not isinstance(value, dict):
        raise NativeM3Error(f"{label} precisa ser objeto")
    return value


def _relative(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or any(char in value for char in CONTROL_CHARS):
        raise NativeM3Error(f"{label} possui texto inseguro")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise NativeM3Error(f"{label} não é relativo e canônico")
    return path


def _safe_under(root: Path, relative: PurePosixPath, label: str) -> Path:
    path = root.joinpath(*relative.parts)
    current = path
    while current != root:
        if current.is_symlink():
            raise NativeM3Error(f"{label} atravessa symlink: {relative}")
        current = current.parent
    return path


def _expand_command(command: list[str], *, candidate: Path, scratch: Path, output: Path, case: str) -> list[str]:
    rendered: list[str] = []
    for item in command:
        if not isinstance(item, str) or not item or any(char in item for char in CONTROL_CHARS):
            raise NativeM3Error(f"comando inseguro no caso {case}")
        rendered.append(item.format(
            candidate=str(candidate), scratch=str(scratch), output=str(output), case=case,
        ))
    if any(word in " ".join(rendered).casefold() for word in FORBIDDEN_WORDS):
        raise NativeM3Error(f"comando de mock/dry-run no caso {case}")
    if not rendered:
        raise NativeM3Error(f"comando ausente no caso {case}")
    executable = Path(rendered[0])
    if executable.is_absolute():
        if executable != Path(sys.executable) and not str(executable).startswith("/usr/bin/"):
            raise NativeM3Error(f"executável não permitido no caso {case}: {executable}")
    else:
        executable = _safe_under(candidate, _relative(rendered[0], "executável"), "executável")
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise NativeM3Error(f"executável do candidato ausente no caso {case}: {executable}")
    return rendered


def _plan(plan_path: Path, identity: dict[str, str]) -> list[dict[str, object]]:
    value = _read_json(Path(plan_path), "plano nativo")
    if set(value) != PLAN_FIELDS or value.get("format") != 1 or value.get("project") != "x86qw" or value.get("platform") != "macOS-ARM64":
        raise NativeM3Error("plano nativo possui contrato inválido")
    if value.get("candidate") != identity:
        raise NativeM3Error("plano nativo diverge do candidato")
    raw_cases = value.get("cases")
    if not isinstance(raw_cases, list) or len(raw_cases) != len(CANONICAL_CASES):
        raise NativeM3Error("plano nativo não contém todos os casos canônicos")
    cases: list[dict[str, object]] = []
    for expected, raw in zip(CANONICAL_CASES, raw_cases, strict=True):
        if not isinstance(raw, dict) or set(raw) != PLAN_CASE_FIELDS or raw.get("name") != expected:
            raise NativeM3Error(f"plano nativo inválido: {expected}")
        assertions = raw.get("assertions")
        if not isinstance(assertions, list) or set(assertions) != CASE_ASSERTIONS[expected]:
            raise NativeM3Error(f"assertions incompletas no plano: {expected}")
        artifacts = raw.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise NativeM3Error(f"artefatos ausentes no plano: {expected}")
        seen_assertions: set[str] = set()
        checked_artifacts: list[dict[str, str]] = []
        for artifact in artifacts:
            if not isinstance(artifact, dict) or set(artifact) != PLAN_ARTIFACT_FIELDS:
                raise NativeM3Error(f"artefato de plano inválido: {expected}")
            relative = _relative(artifact.get("path"), f"artefato.{expected}.path")
            kind = artifact.get("kind")
            assertion = artifact.get("assertion")
            if not isinstance(kind, str) or not kind or not isinstance(assertion, str) or assertion not in CASE_ASSERTIONS[expected]:
                raise NativeM3Error(f"artefato não liga assertion: {expected}")
            if relative.as_posix() in {item["path"] for item in checked_artifacts}:
                raise NativeM3Error(f"artefato duplicado no plano: {expected}")
            seen_assertions.add(assertion)
            checked_artifacts.append({"path": relative.as_posix(), "kind": kind, "assertion": assertion})
        if seen_assertions != set(CASE_ASSERTIONS[expected]):
            raise NativeM3Error(f"cada assertion precisa de artefato observado: {expected}")
        if sum(item["kind"] == "case-attestation" for item in checked_artifacts) != 1:
            raise NativeM3Error(f"cada caso precisa de uma atestação da execução: {expected}")
        timeout = raw.get("timeout_seconds")
        if type(timeout) is not int or not 1 <= timeout <= 900:
            raise NativeM3Error(f"timeout inválido no plano: {expected}")
        command = raw.get("command")
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            raise NativeM3Error(f"comando inválido no plano: {expected}")
        cases.append({
            "name": expected,
            "command": list(command),
            "assertions": sorted(assertions),
            "artifacts": checked_artifacts,
            "timeout_seconds": timeout,
        })
    return cases


def _observed_artifacts(
    case: dict[str, object], output: Path, *, nonce: str, started_ns: int,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for raw in case["artifacts"]:  # type: ignore[index]
        assert isinstance(raw, dict)
        relative = _relative(raw["path"], "artefato observado")
        path = _safe_under(output, relative, "artefato observado")
        if path.is_symlink() or not path.is_file():
            raise NativeM3Error(f"assertion sem artefato produzido: {relative}")
        try:
            metadata = path.stat()
        except OSError as error:
            raise NativeM3Error(f"não foi possível observar artefato: {relative}") from error
        if metadata.st_mtime_ns < started_ns:
            raise NativeM3Error(f"artefato não foi produzido durante o caso: {relative}")
        if raw["kind"] == "case-attestation":
            try:
                payload = path.read_bytes()
            except OSError as error:
                raise NativeM3Error(f"atestação não pôde ser lida: {relative}") from error
            if nonce.encode("ascii") not in payload:
                raise NativeM3Error(f"atestação não está ligada ao processo do caso: {relative}")
        size, digest = _digest(path)
        result.append({"path": relative.as_posix(), "kind": raw["kind"], "size": size, "sha256": digest})
    return result


def _terminate_process_group(process: subprocess.Popen[object], *, force: bool = False) -> None:
    """Stop the native case leader and its descendants on the POSIX runner."""

    if not hasattr(os, "killpg"):
        raise NativeM3Error("o harness nativo exige suporte a grupos de processos POSIX")
    try:
        os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        # The leader and its group are already gone; this is the only safe
        # no-op. Permission and other OS failures remain visible.
        return
    except OSError as error:
        raise NativeM3Error(f"não foi possível encerrar o grupo do caso nativo: {error}") from error


def _cleanup_run_scratch(scratch_root: Path) -> None:
    """Remove only the private shared scratch tree owned by this run."""

    scratch_root = Path(scratch_root).absolute()
    if scratch_root.is_symlink():
        raise NativeM3Error("scratch compartilhado usa symlink")
    if not scratch_root.exists():
        return
    if not scratch_root.is_dir():
        raise NativeM3Error("scratch compartilhado não é diretório")
    shutil.rmtree(scratch_root)


def _cleanup_legacy_native_scratch(function: Any) -> Any:
    """Clean scratch created by the legacy run without touching old output."""

    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Path:
        output_dir = Path(kwargs["output_dir"]).absolute()
        owns_output = not output_dir.exists() and not output_dir.is_symlink()
        try:
            return function(*args, **kwargs)
        finally:
            if owns_output:
                _cleanup_run_scratch(output_dir / "scratch")

    return wrapped


@_cleanup_legacy_native_scratch
def run_native(*, candidate: Path, plan_path: Path, output_dir: Path) -> Path:
    candidate = Path(candidate).resolve()
    if candidate.is_symlink() or not candidate.is_dir():
        raise NativeM3Error(f"candidato ausente ou inseguro: {candidate}")
    environment = _m3_environment()
    identity = _identity(candidate)
    cases = _plan(plan_path, identity)
    output_dir = Path(output_dir).absolute()
    if output_dir.exists() or output_dir.is_symlink():
        raise NativeM3Error(f"saída nativa já existe: {output_dir}")
    output_dir.mkdir(parents=True, mode=0o700)
    scratch = output_dir / "scratch"
    scratch.mkdir(mode=0o700)
    results: list[dict[str, object]] = []
    process_environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(output_dir),
        "TMPDIR": str(scratch),
        "X86QW_CANDIDATE_ROOT": str(candidate),
        "X86QW_NATIVE_SCRATCH_ROOT": str(scratch),
    }
    for index, case in enumerate(cases, start=1):
        name = str(case["name"])
        command = _expand_command(
            list(case["command"]), candidate=candidate, scratch=scratch, output=output_dir, case=name,
        )
        nonce = secrets.token_urlsafe(24)
        case_environment = dict(process_environment)
        case_environment["X86QW_NATIVE_CASE_NONCE"] = nonce
        stdout_path = output_dir / f"{index:02d}-{name}.stdout.log"
        stderr_path = output_dir / f"{index:02d}-{name}.stderr.log"
        started_at = _now()
        started = time.monotonic()
        started_ns = time.time_ns()
        with stdout_path.open("x", encoding="utf-8") as stdout, stderr_path.open("x", encoding="utf-8") as stderr:
            process = subprocess.Popen(
                command, cwd=candidate, env=case_environment, stdin=subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, shell=False, start_new_session=True,
            )
            try:
                exit_code = process.wait(timeout=int(case["timeout_seconds"]))
            except subprocess.TimeoutExpired as error:
                _terminate_process_group(process)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    _terminate_process_group(process, force=True)
                    process.wait(timeout=10)
                raise NativeM3Error(f"caso nativo excedeu timeout: {name}") from error
        if exit_code != 0:
            raise NativeM3Error(f"caso nativo falhou: {name} (exit={exit_code})")
        artifacts = _observed_artifacts(case, output_dir, nonce=nonce, started_ns=started_ns)
        results.append({
            "name": name, "command": command, "status": "passed", "exit_code": 0,
            "started_at": started_at, "duration_ms": int((time.monotonic() - started) * 1000),
            "assertions": list(case["assertions"]), "artifacts": artifacts,
        })
    try:
        validated_cases = validate_cases(results)
        validated_environment = validate_environment(
            {key: value for key, value in environment.items() if key not in {"chip", "model"}},
            platform="macOS-ARM64",
        )
        validated_hardware = validate_hardware(
            {key: environment[key] for key in ("chip", "model")},
            platform="macOS-ARM64",
        )
    except NativeEvidenceError as error:
        raise NativeM3Error(f"resultado nativo não satisfaz o contrato: {error}") from error
    handoff = {
        "format": NATIVE_EVIDENCE_FORMAT,
        "project": "x86qw",
        "status": "passed",
        "platform": "macOS-ARM64",
        "completed_at": _now(),
        "candidate": identity,
        "environment": validated_environment,
        "hardware": validated_hardware,
        "runtime_executed": True,
        "cases": list(validated_cases),
        "secrets": "redacted",
    }
    handoff_path = output_dir / "handoff.json"
    handoff_path.write_text(json.dumps(handoff, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return handoff_path


def validate_handoff(*, candidate: Path, handoff: Path) -> None:
    try:
        normalize_native_smoke(candidate=Path(candidate), platform="macOS-ARM64", handoff=Path(handoff))
    except (CandidateError, OSError) as error:
        raise NativeM3Error(f"handoff M3 inválido: {error}") from error


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--candidate", type=Path, required=True)
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    check = commands.add_parser("validate")
    check.add_argument("--candidate", type=Path, required=True)
    check.add_argument("--handoff", type=Path, required=True)
    options = parser.parse_args(arguments)
    if options.command == "run":
        try:
            plan_marker = _read_json(options.plan, "plano nativo").get("format")
        except NativeM3Error:
            plan_marker = None
        if plan_marker == 2:
            # Keep the historical command name usable while ensuring current
            # candidates execute through the canonical format-2 harness.
            from maintenance.tools import native_macos_harness

            return native_macos_harness.main([
                "run",
                "--candidate", str(options.candidate),
                "--plan", str(options.plan),
                "--output-dir", str(options.output_dir),
            ])
    try:
        if options.command == "run":
            path = run_native(
                candidate=options.candidate,
                plan_path=options.plan,
                output_dir=options.output_dir,
            )
            validate_handoff(candidate=options.candidate, handoff=path)
            print(f"[OK] evidência M3 real: {path}")
        else:
            validate_handoff(candidate=options.candidate, handoff=options.handoff)
            print(f"[OK] handoff M3 validado: {options.handoff}")
    except (NativeM3Error, OSError, subprocess.SubprocessError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
