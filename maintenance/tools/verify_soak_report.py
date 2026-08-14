"""Verify the accountable soak handoff for the final x86QW promotion gate."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


PROJECT = "x86qw"
FORMAT = 2
PLATFORM = "macos-arm64"
MAX_JSON_BYTES = 512 * 1024
MAX_HARDWARE_CHARS = 256
MAX_EVIDENCE_URL_CHARS = 2048
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
RC_VERSION = re.compile(r"^1\.0\.0-rc\.[0-9]+$")
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
OBSERVATION_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SAFE_HARDWARE = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")
GATE_NAMES = frozenset({
    "p0_p1_clear",
    "tuf_healthy",
    "public_install",
    "gameplay",
    "hosting",
})


class SoakReportError(RuntimeError):
    """The soak report is missing, unsafe, or does not prove the gate."""


def _no_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SoakReportError(f"JSON contém chave duplicada: {key}")
        result[key] = value
    return result


def _read_report(path: Path) -> dict[str, object]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise SoakReportError(f"relatório de soak ausente ou inseguro: {path}")
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise SoakReportError("não foi possível ler o relatório de soak") from error
    if not payload or len(payload) > MAX_JSON_BYTES:
        raise SoakReportError("relatório de soak vazio ou acima do limite")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_no_duplicate_pairs)
    except SoakReportError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SoakReportError("relatório de soak não é JSON UTF-8 válido") from error
    if not isinstance(value, dict):
        raise SoakReportError("relatório de soak precisa ser um objeto")
    return value


def _parse_utc(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or UTC_TIMESTAMP.fullmatch(value) is None:
        raise SoakReportError(f"timestamp UTC inválido: {label}")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise SoakReportError(f"timestamp UTC inválido: {label}") from error
    if parsed.tzinfo != timezone.utc:
        raise SoakReportError(f"timestamp UTC inválido: {label}")
    return parsed


def _parse_date(value: object, *, label: str) -> date:
    if not isinstance(value, str) or OBSERVATION_DATE.fullmatch(value) is None:
        raise SoakReportError(f"data de observação inválida: {label}")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise SoakReportError(f"data de observação inválida: {label}") from error


def _require_sha(value: object, *, label: str, length: int) -> str:
    pattern = SHA40 if length == 40 else SHA64
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise SoakReportError(f"SHA-256/commit inválido: {label}")
    return value


def _require_exact_mapping(value: object, fields: frozenset[str], *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise SoakReportError(f"campos inválidos no relatório de soak: {label}")
    return value


def verify_report(
    path: Path,
    *,
    expected_commit: str,
    expected_version: str,
    expected_candidate_json_sha256: str,
    expected_bundle_sha256: str,
    expected_issue_number: int,
    minimum_days: int = 7,
) -> dict[str, object]:
    """Validate a completed soak report against the exact RC identity."""

    if SHA40.fullmatch(expected_commit) is None:
        raise SoakReportError("commit esperado inválido")
    if RC_VERSION.fullmatch(expected_version) is None:
        raise SoakReportError("versão RC esperada inválida")
    _require_sha(expected_candidate_json_sha256, label="candidate.json esperado", length=64)
    _require_sha(expected_bundle_sha256, label="bundle esperado", length=64)
    if isinstance(expected_issue_number, bool) or not isinstance(expected_issue_number, int) or expected_issue_number <= 0:
        raise SoakReportError("número do issue esperado inválido")
    if isinstance(minimum_days, bool) or not isinstance(minimum_days, int) or minimum_days < 1 or minimum_days > 366:
        raise SoakReportError("duração mínima do soak inválida")

    report = _read_report(path)
    _require_exact_mapping(
        report,
        frozenset({
            "format",
            "project",
            "status",
            "environment",
            "candidate",
            "period",
            "issue",
            "gates",
            "observations",
        }),
        label="raiz",
    )
    if report["format"] != FORMAT or report["project"] != PROJECT or report["status"] != "passed":
        raise SoakReportError("identidade/status do relatório de soak inválido")

    environment = _require_exact_mapping(
        report["environment"],
        frozenset({"platform", "hardware"}),
        label="environment",
    )
    if environment["platform"] != PLATFORM:
        raise SoakReportError("plataforma do soak não é macOS arm64/M3")
    hardware = environment["hardware"]
    if (
        not isinstance(hardware, str)
        or len(hardware) > MAX_HARDWARE_CHARS
        or SAFE_HARDWARE.fullmatch(hardware) is None
    ):
        raise SoakReportError("hardware do soak inválido")

    candidate = _require_exact_mapping(
        report["candidate"],
        frozenset({"commit", "version", "candidate_json_sha256", "bundle_sha256"}),
        label="candidate",
    )
    if (
        candidate["commit"] != expected_commit
        or candidate["version"] != expected_version
        or candidate["candidate_json_sha256"] != expected_candidate_json_sha256
        or candidate["bundle_sha256"] != expected_bundle_sha256
    ):
        raise SoakReportError("identidade do candidato no soak diverge dos inputs protegidos")
    _require_sha(candidate["commit"], label="candidate.commit", length=40)
    if not isinstance(candidate["version"], str) or RC_VERSION.fullmatch(candidate["version"]) is None:
        raise SoakReportError("versão RC no soak inválida")
    _require_sha(candidate["candidate_json_sha256"], label="candidate.candidate_json_sha256", length=64)
    _require_sha(candidate["bundle_sha256"], label="candidate.bundle_sha256", length=64)

    period = _require_exact_mapping(
        report["period"],
        frozenset({"started_at", "completed_at", "minimum_days"}),
        label="period",
    )
    started = _parse_utc(period["started_at"], label="started_at")
    completed = _parse_utc(period["completed_at"], label="completed_at")
    if completed < started:
        raise SoakReportError("período de soak termina antes de começar")
    if completed > datetime.now(timezone.utc):
        raise SoakReportError("período de soak termina no futuro")
    if period["minimum_days"] != minimum_days:
        raise SoakReportError("duração mínima declarada diverge do gate")
    if completed - started < timedelta(days=minimum_days):
        raise SoakReportError("duração do soak abaixo do mínimo")

    issue = _require_exact_mapping(
        report["issue"],
        frozenset({"number", "state", "url"}),
        label="issue",
    )
    if issue["number"] != expected_issue_number or issue["state"] != "closed":
        raise SoakReportError("issue canônico do soak não está fechado")
    if issue["url"] != f"https://github.com/x86dx2/x86qw/issues/{expected_issue_number}":
        raise SoakReportError("URL do issue canônico do soak inválida")

    gates = _require_exact_mapping(report["gates"], GATE_NAMES, label="gates")
    if any(value is not True for value in gates.values()):
        labels = {"tuf_healthy": "TUF", "p0_p1_clear": "P0/P1"}
        failed = ", ".join(
            labels.get(key, key)
            for key, value in sorted(gates.items())
            if value is not True
        )
        raise SoakReportError(f"gate operacional do soak não está verde: {failed}")

    raw_observations = report["observations"]
    if not isinstance(raw_observations, list):
        raise SoakReportError("observations do soak precisa ser uma lista")
    observations: dict[date, str] = {}
    for index, raw in enumerate(raw_observations):
        observation = _require_exact_mapping(
            raw,
            frozenset({"date", "status", "evidence"}),
            label=f"observations[{index}]",
        )
        observed_date = _parse_date(observation["date"], label=f"observations[{index}].date")
        if observation["status"] != "green":
            raise SoakReportError(f"observação de soak não está verde: {observed_date}")
        evidence = observation["evidence"]
        if (
            not isinstance(evidence, str)
            or len(evidence) > MAX_EVIDENCE_URL_CHARS
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in evidence)
        ):
            raise SoakReportError(f"referência de evidência inválida: {observed_date}")
        try:
            evidence_url = urlsplit(evidence)
            hostname = evidence_url.hostname
        except ValueError as error:
            raise SoakReportError(f"referência de evidência inválida: {observed_date}") from error
        if (
            evidence_url.scheme != "https"
            or not evidence_url.netloc
            or not hostname
            or evidence_url.username is not None
            or evidence_url.password is not None
        ):
            raise SoakReportError(f"referência de evidência precisa ser HTTPS: {observed_date}")
        if observed_date in observations:
            raise SoakReportError(f"data de observação duplicada: {observed_date}")
        observations[observed_date] = "green"
    expected_dates = {
        started.date() + timedelta(days=offset)
        for offset in range((completed.date() - started.date()).days + 1)
    }
    observed_dates = set(observations)
    if not expected_dates.issubset(observed_dates):
        missing = ", ".join(str(item) for item in sorted(expected_dates - observed_dates))
        raise SoakReportError(f"observações diárias ausentes no soak: {missing}")
    if any(item < started.date() or item > completed.date() for item in observations):
        raise SoakReportError("observação de soak fora da janela declarada")
    return report


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-candidate-json-sha256", required=True)
    parser.add_argument("--expected-bundle-sha256", required=True)
    parser.add_argument("--expected-issue-number", type=int, required=True)
    parser.add_argument("--minimum-days", type=int, default=7)
    options = parser.parse_args(arguments)
    try:
        verify_report(
            options.report,
            expected_commit=options.expected_commit,
            expected_version=options.expected_version,
            expected_candidate_json_sha256=options.expected_candidate_json_sha256,
            expected_bundle_sha256=options.expected_bundle_sha256,
            expected_issue_number=options.expected_issue_number,
            minimum_days=options.minimum_days,
        )
    except SoakReportError as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(f"[OK] Soak validado: {options.expected_version} / {options.minimum_days} dias")
    return 0


__all__ = ["FORMAT", "PROJECT", "SoakReportError", "verify_report"]


if __name__ == "__main__":
    raise SystemExit(main())
