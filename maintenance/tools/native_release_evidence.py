"""Write native-release evidence only after an explicitly executed smoke."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools.release_candidate import CandidateError, verify_candidate
from maintenance.tools.native_release_smoke import _validate_artifact_files
from x86qw_runtime.contracts.native_evidence import (
    NATIVE_EVIDENCE_FORMAT,
    NativeEvidenceError,
    validate_cases,
    validate_environment,
    validate_hardware,
)


PLATFORM_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
KEY_ID = re.compile(r"^[0-9a-f]{64}$")
SIGNATURE = re.compile(r"^[A-Za-z0-9_-]+$")
SMOKE_REPORT_FIELDS = frozenset({
    "format", "status", "platform", "completed_at", "candidate", "environment", "cases", "secrets", "runtime_executed",
})
NATIVE_EVIDENCE_FIELDS = frozenset({
    "format", "project", "status", "platform", "recorded_at", "candidate",
    "environment", "runtime_executed", "cases", "secrets", "signature",
})
SIGNED_EVIDENCE_BASE_FIELDS = frozenset({
    "format", "project", "version", "commit", "status", "candidate", "platforms",
})


def _fields_for_platform(base: frozenset[str], platform: str) -> frozenset[str]:
    return base | {"hardware"} if platform == "macOS-ARM64" else base


def _no_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise CandidateError(f"JSON contém chave duplicada: {key}")
        value[key] = item
    return value


def _write(path: Path, value: object) -> None:
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


def _manifest_digest(candidate: Path) -> str:
    manifest = Path(candidate) / "candidate.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise CandidateError(f"manifest do candidato ausente ou inseguro: {manifest}")
    try:
        return hashlib.sha256(manifest.read_bytes()).hexdigest()
    except OSError as error:
        raise CandidateError(f"não foi possível ler o manifest do candidato: {manifest}") from error


def _load_smoke_report(
    path: Path,
    *,
    platform: str,
    manifest: dict[str, object],
    manifest_digest: str,
    artifact_root: Path | None = None,
) -> dict[str, object]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise CandidateError(f"relatório nativo ausente ou inseguro: {path}")
    try:
        if path.stat().st_size > 1024 * 1024:
            raise CandidateError("relatório nativo excede o limite de 1 MiB")
        report = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CandidateError("relatório nativo inválido") from error
    if (
        not isinstance(report, dict)
        or set(report) != _fields_for_platform(SMOKE_REPORT_FIELDS, platform)
        or type(report.get("format")) is not int
        or report.get("format") != NATIVE_EVIDENCE_FORMAT
        or report.get("status") != "passed"
        or report.get("platform") != platform
        or not isinstance(report.get("completed_at"), str)
        or UTC_TIMESTAMP.fullmatch(report["completed_at"]) is None
        or report.get("secrets") != "redacted"
        or report.get("runtime_executed") is not True
        or report.get("candidate") != {
            "version": manifest["version"],
            "commit": manifest["commit"],
            "manifest_sha256": manifest_digest,
        }
    ):
        # Candidate identity is checked explicitly below; keeping the shape
        # closed here prevents a free-form environment variable from becoming
        # release evidence.
        raise CandidateError("relatório nativo não corresponde ao candidato/plataforma")
    candidate = report.get("candidate")
    if not isinstance(candidate, dict) or set(candidate) != {"version", "commit", "manifest_sha256"}:
        raise CandidateError("identidade ausente no relatório nativo")
    if (
        candidate.get("version") != manifest["version"]
        or candidate.get("commit") != manifest["commit"]
        or candidate.get("manifest_sha256") != manifest_digest
    ):
        raise CandidateError("identidade do relatório nativo diverge do candidato")
    try:
        validate_environment(report.get("environment"), platform=platform)
        validate_hardware(report.get("hardware"), platform=platform)
        validate_cases(report.get("cases"))
        _validate_artifact_files(
            report.get("cases"),
            root=Path(artifact_root) if artifact_root is not None else path.parent,
        )
    except NativeEvidenceError as error:
        raise CandidateError(str(error)) from error
    return report


def _load_json_report(path: Path, *, label: str) -> dict[str, object]:
    """Read one bounded JSON report without following links or special files."""

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


def _identity(manifest: dict[str, object], manifest_digest: str) -> dict[str, object]:
    return {
        "version": manifest["version"],
        "commit": manifest["commit"],
        "manifest_sha256": manifest_digest,
    }


def _redacted_cases(value: object) -> list[dict[str, object]]:
    """Keep case proof while removing runner-specific command paths."""

    if not isinstance(value, list):
        raise CandidateError("casos nativos inválidos para registro redigido")
    result: list[dict[str, object]] = []
    for raw_case in value:
        if not isinstance(raw_case, dict) or not isinstance(raw_case.get("name"), str):
            raise CandidateError("caso nativo inválido para registro redigido")
        case = dict(raw_case)
        case["command"] = ["x86qw-native-case-v1", raw_case["name"]]
        result.append(case)
    return result


def _expected_platforms(values: Iterable[str]) -> tuple[str, ...]:
    raw = tuple(values)
    if any(not isinstance(value, str) for value in raw):
        raise CandidateError("a cobertura nativa esperada contém plataforma inválida")
    expected = tuple(sorted(raw, key=lambda value: value.casefold()))
    if not expected or len(set(expected)) != len(expected):
        raise CandidateError("a cobertura nativa esperada precisa conter plataformas únicas")
    if any(not isinstance(value, str) or PLATFORM_NAME.fullmatch(value) is None for value in expected):
        raise CandidateError("a cobertura nativa esperada contém plataforma inválida")
    return expected


def _validate_native_record(
    report: dict[str, object],
    *,
    platform: str,
    identity: dict[str, object],
    require_unsigned: bool,
) -> None:
    if set(report) != _fields_for_platform(NATIVE_EVIDENCE_FIELDS, platform):
        raise CandidateError(f"evidência nativa da plataforma {platform} possui campos inválidos")
    if (
        type(report.get("format")) is not int
        or report.get("format") != NATIVE_EVIDENCE_FORMAT
        or report.get("project") != "x86qw"
        or report.get("status") != "complete"
        or report.get("platform") != platform
        or not isinstance(report.get("recorded_at"), str)
        or UTC_TIMESTAMP.fullmatch(report["recorded_at"]) is None
        or report.get("candidate") != identity
        or report.get("runtime_executed") is not True
        or report.get("secrets") != "redacted"
    ):
        raise CandidateError(f"evidência nativa da plataforma {platform} não corresponde ao candidato")
    try:
        validate_environment(report.get("environment"), platform=platform)
        validate_hardware(report.get("hardware"), platform=platform)
        validate_cases(report.get("cases"))
    except NativeEvidenceError as error:
        raise CandidateError(str(error)) from error
    if require_unsigned and report.get("signature") is not None:
        raise CandidateError(f"evidência nativa da plataforma {platform} não pode trazer assinatura local")


def _has_signature(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"keyid", "sig"}
        and isinstance(value.get("keyid"), str)
        and KEY_ID.fullmatch(value["keyid"]) is not None
        and isinstance(value.get("sig"), str)
        and SIGNATURE.fullmatch(value["sig"]) is not None
    )


def _has_signatures(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(_has_signature(item) for item in value)


def validate_native_evidence(
    *,
    candidate: Path,
    evidence_dir: Path,
    expected_platforms: Iterable[str],
    artifact_root: Path | None = None,
) -> tuple[dict[str, object], ...]:
    """Validate exact native coverage for one immutable candidate.

    The result is sorted by platform so callers can feed a stable set into a
    protected signing ceremony.  The function only reads the candidate and
    reports; it never merges or promotes either tree.
    """

    expected = _expected_platforms(expected_platforms)
    manifest = verify_candidate(Path(candidate), allow_pending_evidence=True)
    manifest_digest = _manifest_digest(Path(candidate))
    identity = _identity(manifest, manifest_digest)
    evidence_dir = Path(evidence_dir)
    if evidence_dir.is_symlink() or not evidence_dir.is_dir():
        raise CandidateError(f"diretório de evidência nativa ausente ou inseguro: {evidence_dir}")
    paths = []
    for path in sorted(evidence_dir.rglob("*")):
        if path.is_symlink():
            raise CandidateError(f"diretório de evidência nativa contém symlink: {path}")
        if path.is_file():
            if path.suffix.casefold() != ".json":
                raise CandidateError(f"diretório de evidência nativa contém arquivo não-JSON: {path}")
            paths.append(path)
        elif not path.is_dir():
            raise CandidateError(f"diretório de evidência nativa contém tipo especial: {path}")
    if len(paths) != len(expected):
        raise CandidateError(
            f"cobertura nativa exige exatamente {len(expected)} relatórios, recebeu {len(paths)}"
        )
    seen: dict[str, dict[str, object]] = {}
    for path in paths:
        report = _load_json_report(path, label="evidência nativa")
        platform = report.get("platform")
        if not isinstance(platform, str) or PLATFORM_NAME.fullmatch(platform) is None:
            raise CandidateError(f"evidência nativa contém plataforma inválida: {platform!r}")
        if platform not in expected:
            raise CandidateError(f"evidência nativa contém plataforma não anunciada: {platform}")
        if platform in seen:
            raise CandidateError(f"evidência nativa duplicada para a plataforma: {platform}")
        _validate_native_record(
            report,
            platform=platform,
            identity=identity,
            require_unsigned=True,
        )
        if artifact_root is not None:
            try:
                _validate_artifact_files(report.get("cases"), root=Path(artifact_root))
            except (CandidateError, OSError) as error:
                raise CandidateError(
                    f"artefatos da evidência nativa não correspondem ao relatório: {platform}"
                ) from error
        seen[platform] = report
    if set(seen) != set(expected):
        missing = sorted(set(expected) - set(seen))
        raise CandidateError(f"cobertura nativa incompleta; faltam: {missing}")
    return tuple(seen[platform] for platform in expected)


def validate_signed_evidence_coverage(
    *,
    candidate: Path,
    evidence: Path,
    expected_platforms: Iterable[str],
    unsigned_evidence_dir: Path | None = None,
) -> dict[str, object]:
    """Validate signed aggregate identity/coverage before cryptographic gate."""

    expected = _expected_platforms(expected_platforms)
    manifest = verify_candidate(Path(candidate), allow_pending_evidence=True)
    manifest_digest = _manifest_digest(Path(candidate))
    identity = _identity(manifest, manifest_digest)
    value = _load_json_report(Path(evidence), label="agregado assinado")
    value_fields = set(value)
    if value_fields == SIGNED_EVIDENCE_BASE_FIELDS | {"signature"}:
        signed = _has_signature(value.get("signature"))
    elif value_fields == SIGNED_EVIDENCE_BASE_FIELDS | {"signatures"}:
        signed = _has_signatures(value.get("signatures"))
    else:
        signed = False
    if not signed:
        raise CandidateError("agregado assinado possui campos desconhecidos ou ausentes")
    if (
        type(value.get("format")) is not int
        or value.get("format") != 1
        or value.get("project") != "x86qw"
        or value.get("version") != manifest["version"]
        or value.get("commit") != manifest["commit"]
        or value.get("status") != "complete"
        or value.get("candidate") != identity
    ):
        raise CandidateError("agregado assinado não corresponde ao candidato exato")
    platforms = value.get("platforms")
    if not isinstance(platforms, dict) or set(platforms) != set(expected):
        raise CandidateError("agregado assinado não cobre exatamente as plataformas anunciadas")
    for platform in expected:
        report = platforms[platform]
        if not isinstance(report, dict):
            raise CandidateError(f"agregado assinado possui evidência inválida: {platform}")
        # The release-candidate verifier performs the complete per-platform
        # schema and signature check after this file is copied into the
        # candidate.  Here we bind coverage and identity before that mutation.
        try:
            _validate_native_record(
                report,
                platform=platform,
                identity=identity,
                require_unsigned=True,
            )
        except CandidateError as error:
            raise CandidateError(f"agregado assinado diverge da identidade: {platform}") from error
    if unsigned_evidence_dir is not None:
        local_records = validate_native_evidence(
            candidate=candidate,
            evidence_dir=unsigned_evidence_dir,
            expected_platforms=expected,
        )
        local_by_platform = {record["platform"]: record for record in local_records}
        for platform in expected:
            if platforms[platform] != local_by_platform[platform]:
                raise CandidateError(
                    f"agregado assinado diverge do relatório coletado: {platform}"
                )
    return value


def write_native_evidence(
    *,
    candidate: Path,
    platform: str,
    output: Path,
    report: Path,
    artifact_root: Path | None = None,
    recorded_at: str | None = None,
) -> dict[str, object]:
    if not isinstance(platform, str) or PLATFORM_NAME.fullmatch(platform) is None:
        raise CandidateError(f"plataforma nativa inválida: {platform!r}")
    # The candidate is intentionally pending while native platform evidence is
    # collected.  The normalized handoff carries the explicit runtime-executed
    # attestation; this path cannot be used by promote.
    manifest = verify_candidate(candidate, allow_pending_evidence=True)
    candidate_digest = _manifest_digest(Path(candidate))
    smoke = _load_smoke_report(
        Path(report), platform=platform, manifest=manifest, manifest_digest=candidate_digest,
        artifact_root=artifact_root,
    )
    reported_candidate = smoke["candidate"]
    assert isinstance(reported_candidate, dict)
    if reported_candidate.get("manifest_sha256") != candidate_digest:
        raise CandidateError("relatório nativo usa manifest diferente do candidato")
    if recorded_at is None:
        # The handoff timestamp is the time the native process actually
        # finished.  Preserve it instead of assigning a new timestamp on the
        # verification runner, which could hide stale evidence.
        recorded_at = str(smoke["completed_at"])
    if not isinstance(recorded_at, str) or UTC_TIMESTAMP.fullmatch(recorded_at) is None:
        raise CandidateError("recorded_at precisa ser timestamp UTC canônico")
    evidence = {
        "format": NATIVE_EVIDENCE_FORMAT,
        "project": "x86qw",
        "status": "complete",
        "platform": platform,
        "recorded_at": recorded_at,
        "candidate": _identity(manifest, candidate_digest),
        "environment": smoke["environment"],
        "runtime_executed": True,
        "cases": _redacted_cases(smoke["cases"]),
        "secrets": "redacted",
        "signature": None,
    }
    if "hardware" in smoke:
        evidence["hardware"] = smoke["hardware"]
    _validate_native_record(
        evidence,
        platform=platform,
        identity=_identity(manifest, candidate_digest),
        require_unsigned=True,
    )
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise CandidateError(f"destino de evidência nativa já existe: {output}")
    _write(output, evidence)
    return evidence


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="registra evidência nativa do candidato exato")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--platform")
    parser.add_argument("--report", type=Path, help="relatório produzido pelo smoke nativo")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--artifact-root",
        type=Path,
        help="diretório que contém os artefatos declarados pelo relatório de smoke",
    )
    parser.add_argument("--recorded-at", help="timestamp UTC canônico para evidência determinística")
    parser.add_argument("--validate-evidence-dir", type=Path)
    parser.add_argument("--validate-signed-evidence", type=Path)
    parser.add_argument(
        "--compare-evidence-dir",
        type=Path,
        help="diretório de relatórios não assinados que o agregado deve reproduzir exatamente",
    )
    parser.add_argument(
        "--expected-platform",
        action="append",
        dest="expected_platforms",
        help="plataforma anunciada; repetir para exigir cobertura exata",
    )
    options = parser.parse_args(arguments)
    try:
        validation_mode = options.validate_evidence_dir is not None or options.validate_signed_evidence is not None
        expected = options.expected_platforms or []
        if validation_mode:
            if (
                options.platform is not None
                or options.report is not None
                or options.output is not None
                or options.recorded_at is not None
            ):
                raise CandidateError("opções de validação não podem ser combinadas com produção de evidência")
            if options.validate_evidence_dir is not None:
                validate_native_evidence(
                    candidate=options.candidate,
                    evidence_dir=options.validate_evidence_dir,
                    expected_platforms=expected,
                    artifact_root=options.artifact_root,
                )
            if options.validate_signed_evidence is not None:
                if options.compare_evidence_dir is not None and options.validate_evidence_dir is None:
                    raise CandidateError("--compare-evidence-dir exige validação da cobertura local")
                validate_signed_evidence_coverage(
                    candidate=options.candidate,
                    evidence=options.validate_signed_evidence,
                    expected_platforms=expected,
                    unsigned_evidence_dir=options.compare_evidence_dir,
                )
            print(f"[OK] Cobertura nativa exata validada para: {', '.join(_expected_platforms(expected))}")
            return 0
        if options.platform is None or options.report is None or options.output is None:
            raise CandidateError("produção exige --platform, --report e --output")
        write_native_evidence(
            candidate=options.candidate,
            platform=options.platform,
            output=options.output,
            report=options.report,
            artifact_root=options.artifact_root,
            recorded_at=options.recorded_at,
        )
    except (CandidateError, OSError) as error:
        print(f"[ERRO] {error}")
        return 1
    print(f"[OK] Evidência nativa registrada em {options.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
