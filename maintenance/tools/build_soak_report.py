"""Build the exact protected RC soak handoff report from workflow inputs."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path


PROJECT = "x86qw"
FORMAT = 2
PLATFORM = "macos-arm64"
MAX_EVIDENCE_B64_CHARS = 512 * 1024
MAX_EVIDENCE_BYTES = 128 * 1024
MAX_HARDWARE_CHARS = 256
POSITIVE_NUMBER = re.compile(r"^[1-9][0-9]{0,8}$")
GATE_NAMES = frozenset({
    "p0_p1_clear",
    "tuf_healthy",
    "public_install",
    "gameplay",
    "hosting",
})


class SoakReportBuildError(ValueError):
    """The protected soak inputs cannot produce a safe report."""


def _no_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SoakReportBuildError(f"JSON contém chave duplicada: {key}")
        result[key] = value
    return result


def _decode_evidence(encoded: str) -> dict[str, object]:
    if not isinstance(encoded, str) or not encoded or len(encoded) > MAX_EVIDENCE_B64_CHARS:
        raise SoakReportBuildError("observation_evidence_b64 vazio ou acima do limite")
    try:
        payload = base64.b64decode(encoded, validate=True)
        if not payload or len(payload) > MAX_EVIDENCE_BYTES:
            raise SoakReportBuildError("evidence payload vazio ou acima do limite")
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_no_duplicate_pairs,
        )
    except SoakReportBuildError:
        raise
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise SoakReportBuildError(
            "observation_evidence_b64 precisa ser JSON UTF-8 base64 válido"
        ) from error
    if not isinstance(value, dict) or not value:
        raise SoakReportBuildError("evidence por data precisa ser um objeto não vazio")
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        raise SoakReportBuildError("evidence por data precisa conter strings")
    return value


def _dates(value: str) -> list[str]:
    dates = [item.strip() for item in value.split(",") if item.strip()]
    if not dates:
        raise SoakReportBuildError("observed_dates não pode ser vazio")
    if len(dates) != len(set(dates)):
        raise SoakReportBuildError("observed_dates não pode conter duplicatas")
    return dates


def _hardware(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_HARDWARE_CHARS
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise SoakReportBuildError("hardware do soak inválido")
    return value


def _issue_number(value: str) -> int:
    if not isinstance(value, str) or POSITIVE_NUMBER.fullmatch(value) is None:
        raise SoakReportBuildError("número do issue inválido")
    return int(value)


def build_report(
    *,
    candidate_commit: str,
    candidate_version: str,
    candidate_sha256: str,
    bundle_sha256: str,
    started_at: str,
    completed_at: str,
    issue_number: str,
    issue_state: str,
    issue_url: str,
    observed_dates: str,
    hardware: str,
    observation_evidence_b64: str,
    gates: Mapping[str, bool],
) -> dict[str, object]:
    """Build a report; the companion verifier remains the acceptance gate."""

    dates = _dates(observed_dates)
    evidence_by_date = _decode_evidence(observation_evidence_b64)
    if set(evidence_by_date) != set(dates):
        raise SoakReportBuildError(
            "evidence por data precisa corresponder exatamente a observed_dates"
        )
    if set(gates) != GATE_NAMES or any(type(value) is not bool for value in gates.values()):
        raise SoakReportBuildError("gates do soak possuem campos ou valores inválidos")
    hardware = _hardware(hardware)
    return {
        "format": FORMAT,
        "project": PROJECT,
        "status": "passed",
        "environment": {
            "platform": PLATFORM,
            "hardware": hardware,
        },
        "candidate": {
            "commit": candidate_commit,
            "version": candidate_version,
            "candidate_json_sha256": candidate_sha256,
            "bundle_sha256": bundle_sha256,
        },
        "period": {
            "started_at": started_at,
            "completed_at": completed_at,
            "minimum_days": 7,
        },
        "issue": {
            "number": _issue_number(issue_number),
            "state": issue_state,
            "url": issue_url,
        },
        "gates": dict(gates),
        "observations": [
            {
                "date": date,
                "status": "green",
                "evidence": evidence_by_date[date],
            }
            for date in dates
        ],
    }


def _environment_value(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if value is None:
        raise SoakReportBuildError(f"input ausente: {name}")
    return value


def _environment_gate(environment: Mapping[str, str], name: str) -> bool:
    value = _environment_value(environment, name).casefold()
    if value not in {"true", "false"}:
        raise SoakReportBuildError(f"input booleano inválido: {name}")
    return value == "true"


def build_from_environment(environment: Mapping[str, str] | None = None) -> dict[str, object]:
    """Build from the names exported by ``rc-soak.yml``."""

    source = os.environ if environment is None else environment
    return build_report(
        candidate_commit=_environment_value(source, "CANDIDATE_COMMIT"),
        candidate_version=_environment_value(source, "CANDIDATE_VERSION"),
        candidate_sha256=_environment_value(source, "CANDIDATE_SHA256"),
        bundle_sha256=_environment_value(source, "BUNDLE_SHA256"),
        started_at=_environment_value(source, "STARTED_AT"),
        completed_at=_environment_value(source, "COMPLETED_AT"),
        issue_number=_environment_value(source, "ISSUE_NUMBER"),
        issue_state=_environment_value(source, "ISSUE_STATE"),
        issue_url=_environment_value(source, "ISSUE_URL"),
        observed_dates=_environment_value(source, "OBSERVED_DATES"),
        hardware=_environment_value(source, "HARDWARE"),
        observation_evidence_b64=_environment_value(source, "OBSERVATION_EVIDENCE_B64"),
        gates={name: _environment_gate(source, name.upper()) for name in GATE_NAMES},
    )


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        output = Path(options.output)
        if output.exists() or output.is_symlink():
            raise SoakReportBuildError(f"destino do relatório já existe: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        report = build_from_environment()
        output.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, SoakReportBuildError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(f"[OK] Relatório de soak construído: {output}")
    return 0


__all__ = ["FORMAT", "PLATFORM", "SoakReportBuildError", "build_from_environment", "build_report", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
