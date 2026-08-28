#!/usr/bin/env python3
"""Validate structured workflow-dispatch handoffs before release jobs start."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any


SHA1 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
RUN_ID = re.compile(r"^[1-9][0-9]{0,19}$")
ARTIFACT_ID = re.compile(r"^[1-9][0-9]{0,19}$")
ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
WORKFLOW = re.compile(r"^\.github/workflows/[A-Za-z0-9._/-]+\.ya?ml$")
VERSION = re.compile(r"^1\.0\.(?:0|[1-9][0-9]*)(?:-rc\.[0-9]+)?$")
MAX_JSON_BYTES = 32 * 1024
MAX_VALUE_LENGTH = 512

GROUP_KEYS = {
    "candidate_handoff": frozenset({"run_id", "artifact_id", "artifact_name", "candidate_sha256"}),
    "native_evidence_handoff": frozenset({"run_id", "artifact_id", "artifact_name"}),
    "tuf_metadata_handoff": frozenset({"run_id", "artifact_id", "artifact_name", "workflow"}),
    "public_acceptance_handoff": frozenset({
        "commit",
        "run_id",
        "artifact_id",
        "artifact_name",
        "version",
        "receipt_sha256",
        "bundle_sha256",
        "catalog_sha256",
    }),
    "tuf_operation_handoff": frozenset({
        "run_id",
        "artifact_id",
        "artifact_name",
        "report_sha256",
        "operator",
        "custody_host",
        "sla_hours",
    }),
    "soak_handoff": frozenset({
        "commit",
        "version",
        "candidate_json_sha256",
        "bundle_sha256",
        "run_id",
        "artifact_id",
        "artifact_name",
        "report_sha256",
        "issue_number",
    }),
}


class ReleaseInputError(ValueError):
    """A workflow-dispatch input is malformed or incomplete."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseInputError("JSON contém chave duplicada: " + key)
        result[key] = value
    return result


def parse_handoff(name: str, raw: str) -> dict[str, str]:
    if not isinstance(raw, str):
        raise ReleaseInputError(f"{name} não é uma string JSON")
    if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
        raise ReleaseInputError(f"{name} excede o limite de tamanho")
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ReleaseInputError) as error:
        raise ReleaseInputError(f"{name} não é JSON estrito válido: {error}") from error
    if not isinstance(value, dict):
        raise ReleaseInputError(f"{name} precisa ser um objeto JSON")
    unknown = sorted(set(value) - GROUP_KEYS[name])
    if unknown:
        raise ReleaseInputError(f"{name} contém chaves desconhecidas: {', '.join(unknown)}")
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ReleaseInputError(f"{name}.{key} precisa ser string")
        if not item or len(item) > MAX_VALUE_LENGTH or any(ord(char) < 32 for char in item):
            raise ReleaseInputError(f"{name}.{key} possui valor vazio, longo ou de controle")
    return value


def _require(group: dict[str, str], name: str, fields: frozenset[str]) -> None:
    missing = sorted(fields - set(group))
    if missing:
        raise ReleaseInputError(f"{name} incompleto; faltam: {', '.join(missing)}")


def _require_match(group: dict[str, str], field: str, pattern: re.Pattern[str], label: str) -> None:
    value = group.get(field, "")
    if pattern.fullmatch(value) is None:
        raise ReleaseInputError(f"{label} inválido")


def validate_release_inputs(
    *,
    mode: str,
    release_audience: str,
    candidate_commit: str,
    candidate_version: str,
    public_acceptance_scope: str,
    handoffs: dict[str, str],
) -> dict[str, object]:
    if mode not in {"rehearsal", "promote-rc", "promote-1.0"}:
        raise ReleaseInputError("mode inválido")
    if release_audience not in {"owner-only", "external-public"}:
        raise ReleaseInputError("release_audience inválido")
    if public_acceptance_scope not in {"single-user", "external-users"}:
        raise ReleaseInputError("public_acceptance_scope inválido")
    if SHA1.fullmatch(candidate_commit) is None:
        raise ReleaseInputError("candidate_commit inválido")
    if VERSION.fullmatch(candidate_version) is None:
        raise ReleaseInputError("candidate_version inválido")
    parsed = {name: parse_handoff(name, raw) for name, raw in handoffs.items()}
    if set(parsed) != set(GROUP_KEYS):
        raise ReleaseInputError("conjunto de handoffs incompleto")

    if mode != "rehearsal":
        _require(parsed["candidate_handoff"], "candidate_handoff", GROUP_KEYS["candidate_handoff"])
        _require(parsed["native_evidence_handoff"], "native_evidence_handoff", GROUP_KEYS["native_evidence_handoff"])
        _require(parsed["tuf_metadata_handoff"], "tuf_metadata_handoff", GROUP_KEYS["tuf_metadata_handoff"])

        candidate = parsed["candidate_handoff"]
        _require_match(candidate, "run_id", RUN_ID, "candidate_handoff.run_id")
        _require_match(candidate, "artifact_id", ARTIFACT_ID, "candidate_handoff.artifact_id")
        _require_match(candidate, "artifact_name", re.compile(r"^candidate-[0-9a-f]{40}-[0-9]+-[0-9]+$"), "candidate_handoff.artifact_name")
        _require_match(candidate, "candidate_sha256", HEX64, "candidate_handoff.candidate_sha256")

        native = parsed["native_evidence_handoff"]
        _require_match(native, "run_id", RUN_ID, "native_evidence_handoff.run_id")
        _require_match(native, "artifact_id", ARTIFACT_ID, "native_evidence_handoff.artifact_id")
        _require_match(native, "artifact_name", re.compile(r"^native-m3-signed-[0-9a-f]{40}-[0-9]+-[0-9]+$"), "native_evidence_handoff.artifact_name")

        tuf = parsed["tuf_metadata_handoff"]
        _require_match(tuf, "run_id", RUN_ID, "tuf_metadata_handoff.run_id")
        _require_match(tuf, "artifact_id", ARTIFACT_ID, "tuf_metadata_handoff.artifact_id")
        _require_match(tuf, "artifact_name", ARTIFACT_NAME, "tuf_metadata_handoff.artifact_name")
        _require_match(tuf, "workflow", WORKFLOW, "tuf_metadata_handoff.workflow")

    if mode == "promote-1.0":
        _require(parsed["public_acceptance_handoff"], "public_acceptance_handoff", GROUP_KEYS["public_acceptance_handoff"])
        acceptance = parsed["public_acceptance_handoff"]
        _require_match(acceptance, "commit", SHA1, "public_acceptance_handoff.commit")
        _require_match(acceptance, "run_id", RUN_ID, "public_acceptance_handoff.run_id")
        _require_match(acceptance, "artifact_id", ARTIFACT_ID, "public_acceptance_handoff.artifact_id")
        _require_match(acceptance, "artifact_name", re.compile(r"^public-acceptance-[A-Za-z0-9._-]{1,180}$"), "public_acceptance_handoff.artifact_name")
        _require_match(acceptance, "version", re.compile(r"^1\.0\.0-rc\.[0-9]+$"), "public_acceptance_handoff.version")
        for field in ("receipt_sha256", "bundle_sha256", "catalog_sha256"):
            _require_match(acceptance, field, HEX64, f"public_acceptance_handoff.{field}")
        expected_scope = "single-user" if release_audience == "owner-only" else "external-users"
        if public_acceptance_scope != expected_scope:
            raise ReleaseInputError("escopo de aceitação não corresponde ao público da release")

        if release_audience == "external-public":
            _require(parsed["tuf_operation_handoff"], "tuf_operation_handoff", GROUP_KEYS["tuf_operation_handoff"])
            operation = parsed["tuf_operation_handoff"]
            _require_match(operation, "run_id", RUN_ID, "tuf_operation_handoff.run_id")
            _require_match(operation, "artifact_id", ARTIFACT_ID, "tuf_operation_handoff.artifact_id")
            _require_match(operation, "artifact_name", re.compile(r"^tuf-operation-[0-9a-f]{40}-[0-9]+-[0-9]+$"), "tuf_operation_handoff.artifact_name")
            _require_match(operation, "report_sha256", HEX64, "tuf_operation_handoff.report_sha256")
            _require_match(operation, "sla_hours", re.compile(r"^[1-9][0-9]{0,3}$"), "tuf_operation_handoff.sla_hours")
            if int(operation["sla_hours"]) > 8760:
                raise ReleaseInputError("tuf_operation_handoff.sla_hours excede 8760")
            for field in ("operator", "custody_host"):
                if not operation[field].strip():
                    raise ReleaseInputError(f"tuf_operation_handoff.{field} vazio")
            _require(parsed["soak_handoff"], "soak_handoff", GROUP_KEYS["soak_handoff"])
            soak = parsed["soak_handoff"]
            _require_match(soak, "commit", SHA1, "soak_handoff.commit")
            _require_match(soak, "version", re.compile(r"^1\.0\.0-rc\.[0-9]+$"), "soak_handoff.version")
            for field in ("candidate_json_sha256", "bundle_sha256", "report_sha256"):
                _require_match(soak, field, HEX64, f"soak_handoff.{field}")
            _require_match(soak, "run_id", RUN_ID, "soak_handoff.run_id")
            _require_match(soak, "artifact_id", ARTIFACT_ID, "soak_handoff.artifact_id")
            _require_match(soak, "artifact_name", re.compile(r"^rc-soak-[0-9a-f]{40}-[0-9]+-[0-9]+$"), "soak_handoff.artifact_name")
            _require_match(soak, "issue_number", re.compile(r"^[1-9][0-9]{0,8}$"), "soak_handoff.issue_number")

    return {
        "status": "validated-release-inputs",
        "mode": mode,
        "release_audience": release_audience,
        "candidate_version": candidate_version,
        "required_handoffs": sorted(
            name for name, value in parsed.items() if value and (
                mode != "rehearsal" and name in {"candidate_handoff", "native_evidence_handoff", "tuf_metadata_handoff"}
                or mode == "promote-1.0" and name == "public_acceptance_handoff"
                or mode == "promote-1.0" and release_audience == "external-public" and name in {"tuf_operation_handoff", "soak_handoff"}
            )
        ),
    }


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", default=os.environ.get("RELEASE_MODE", ""))
    parser.add_argument("--release-audience", default=os.environ.get("RELEASE_AUDIENCE", ""))
    parser.add_argument("--candidate-commit", default=os.environ.get("CANDIDATE_COMMIT", ""))
    parser.add_argument("--candidate-version", default=os.environ.get("CANDIDATE_VERSION", ""))
    parser.add_argument("--public-acceptance-scope", default=os.environ.get("PUBLIC_ACCEPTANCE_SCOPE", ""))
    for name in GROUP_KEYS:
        parser.add_argument(f"--{name.replace('_', '-')}", dest=name, default=os.environ.get(name.upper(), "{}"))
    options = parser.parse_args(arguments)
    try:
        result = validate_release_inputs(
            mode=options.mode,
            release_audience=options.release_audience,
            candidate_commit=options.candidate_commit,
            candidate_version=options.candidate_version,
            public_acceptance_scope=options.public_acceptance_scope,
            handoffs={name: getattr(options, name) for name in GROUP_KEYS},
        )
    except ReleaseInputError as error:
        print(f"[ERRO] Inputs de release inválidos: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
