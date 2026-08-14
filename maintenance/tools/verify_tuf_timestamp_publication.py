#!/usr/bin/env python3
"""Verify the durable receipt emitted after a protected TUF timestamp deploy."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Mapping


PROJECT = "x86qw"
MAX_JSON_BYTES = 2 * 1024 * 1024
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
POSITIVE = re.compile(r"^[1-9][0-9]{0,19}$")
ARTIFACT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
CANONICAL_WORKFLOW = ".github/workflows/tuf-timestamp-publish.yml"


class TimestampPublicationVerificationError(RuntimeError):
    """The timestamp publication receipt is missing or inconsistent."""


def _regular_file(path: Path, label: str, maximum: int = MAX_JSON_BYTES) -> bytes:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise TimestampPublicationVerificationError(f"{label} ausente ou inseguro: {path}")
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise TimestampPublicationVerificationError(f"{label} não pôde ser lido: {path}") from error
    if not payload or len(payload) > maximum:
        raise TimestampPublicationVerificationError(f"{label} excede o limite: {path}")
    return payload


def _json(path: Path, label: str) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise TimestampPublicationVerificationError(f"{label} contém chave duplicada: {key}")
            value[key] = item
        return value

    try:
        value = json.loads(
            _regular_file(path, label).decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except TimestampPublicationVerificationError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise TimestampPublicationVerificationError(f"{label} não é JSON válido") from error
    if not isinstance(value, dict):
        raise TimestampPublicationVerificationError(f"{label} precisa ser um objeto JSON")
    return value


def _identity(path: Path, label: str) -> dict[str, object]:
    payload = _regular_file(path, label)
    return {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TimestampPublicationVerificationError(f"{label} inválido")
    return value


def _text(value: object, label: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or any(
        ord(char) < 0x20 for char in value
    ):
        raise TimestampPublicationVerificationError(f"{label} inválido")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise TimestampPublicationVerificationError(f"{label} inválido")
    return value


def _coordinate_section(
    value: object,
    *,
    label: str,
    fields: set[str],
    commit_field: str,
    artifact_prefix: str,
    candidate_commit: str,
) -> Mapping[str, object]:
    section = _mapping(value, label)
    if set(section) != fields:
        raise TimestampPublicationVerificationError(f"{label} possui campos inválidos")
    _text(section[commit_field], f"{label}.{commit_field}", HEX40)
    run_id = _text(section["run_id"], f"{label}.run_id", POSITIVE)
    artifact_id = _text(section["artifact_id"], f"{label}.artifact_id", POSITIVE)
    artifact_name = _text(section["artifact_name"], f"{label}.artifact_name", ARTIFACT)
    if not artifact_name.startswith(f"{artifact_prefix}{candidate_commit}-"):
        raise TimestampPublicationVerificationError(f"{label}.artifact_name não pertence ao candidato")
    del run_id, artifact_id
    return section


def _require_status(value: object, *, label: str, status: str) -> Mapping[str, object]:
    section = _mapping(value, label)
    if section.get("format") != 1 or section.get("project") != PROJECT or section.get("status") != status:
        raise TimestampPublicationVerificationError(f"{label} não possui status {status}")
    return section


def _reject_secret_keys(value: object, path: str = "receipt") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).casefold()
            if any(word in lowered for word in ("token", "secret", "password", "private_key")):
                raise TimestampPublicationVerificationError(f"{path}.{key} não pode aparecer no recibo")
            _reject_secret_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_keys(item, f"{path}[{index}]")


def verify_timestamp_publication_receipt(
    *,
    receipt: Mapping[str, object],
    candidate: Path,
    renewal_report: Path,
    verified_renewal: Path,
    public_tuf: Path,
    public_bootstraps: Path,
    public_product: Path,
) -> dict[str, object]:
    """Validate the receipt and all JSON files it claims to summarize."""

    expected_fields = {
        "format", "project", "status", "published", "changed_files", "checked_at",
        "release_code_commit", "candidate", "source_tuf", "renewal", "publication",
        "public_verification",
    }
    if set(receipt) != expected_fields:
        raise TimestampPublicationVerificationError("recibo possui campos inválidos")
    _reject_secret_keys(receipt)
    if (
        receipt.get("format") != 1
        or receipt.get("project") != PROJECT
        or receipt.get("status") != "timestamp-published"
        or receipt.get("published") is not True
        or receipt.get("changed_files") != ["metadata/timestamp.json"]
    ):
        raise TimestampPublicationVerificationError("recibo não representa uma publicação timestamp-only")
    checked_at = _text(receipt.get("checked_at"), "receipt.checked_at")
    try:
        parsed_checked_at = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise TimestampPublicationVerificationError("receipt.checked_at inválido") from error
    if parsed_checked_at.tzinfo is None:
        raise TimestampPublicationVerificationError("receipt.checked_at precisa conter timezone")

    release_code_commit = _text(receipt.get("release_code_commit"), "receipt.release_code_commit", HEX40)
    candidate_section = _mapping(receipt.get("candidate"), "candidate")
    candidate_fields = {
        "commit", "run_id", "artifact_id", "artifact_name", "candidate_json_sha256", "catalog_sha256",
    }
    if set(candidate_section) != candidate_fields:
        raise TimestampPublicationVerificationError("candidate possui campos inválidos")
    candidate_commit = _text(candidate_section["commit"], "candidate.commit", HEX40)
    _text(candidate_section["candidate_json_sha256"], "candidate.candidate_json_sha256", HEX64)
    _text(candidate_section["catalog_sha256"], "candidate.catalog_sha256", HEX64)
    _coordinate_section(
        candidate_section,
        label="candidate",
        fields=candidate_fields,
        commit_field="commit",
        artifact_prefix="candidate-",
        candidate_commit=candidate_commit,
    )

    source_section = _coordinate_section(
        receipt.get("source_tuf"),
        label="source_tuf",
        fields={"workflow_commit", "run_id", "artifact_id", "artifact_name"},
        commit_field="workflow_commit",
        artifact_prefix="tuf-metadata-",
        candidate_commit=candidate_commit,
    )
    renewal_section = _mapping(receipt.get("renewal"), "renewal")
    renewal_fields = {
        "workflow_commit", "run_id", "artifact_id", "artifact_name", "report_sha256",
        "key_id", "report", "verified",
    }
    if set(renewal_section) != renewal_fields:
        raise TimestampPublicationVerificationError("renewal possui campos inválidos")
    renewal_commit = _text(renewal_section["workflow_commit"], "renewal.workflow_commit", HEX40)
    if renewal_commit != release_code_commit:
        raise TimestampPublicationVerificationError("renewal não pertence à revisão de publicação")
    _coordinate_section(
        renewal_section,
        label="renewal",
        fields=renewal_fields,
        commit_field="workflow_commit",
        artifact_prefix="tuf-timestamp-renewal-",
        candidate_commit=candidate_commit,
    )
    key_id = _text(renewal_section["key_id"], "renewal.key_id", HEX64)
    _text(renewal_section["report_sha256"], "renewal.report_sha256", HEX64)
    renewal_report_value = _require_status(
        renewal_section["report"], label="renewal.report", status="timestamp-renewed",
    )
    if renewal_report_value.get("published") is not False or renewal_report_value.get("changed_files") != ["metadata/timestamp.json"]:
        raise TimestampPublicationVerificationError("renewal.report não é um handoff unpublished timestamp-only")
    if renewal_report_value.get("key_id") != key_id:
        raise TimestampPublicationVerificationError("renewal.report diverge do key ID")
    verified_value = _require_status(
        renewal_section["verified"], label="renewal.verified", status="verified-timestamp-renewal",
    )
    if verified_value.get("published") is not False or verified_value.get("changed_files") != ["metadata/timestamp.json"]:
        raise TimestampPublicationVerificationError("renewal.verified não comprova timestamp-only")

    publication = _mapping(receipt.get("publication"), "publication")
    if set(publication) != {"workflow", "run_id", "run_attempt"}:
        raise TimestampPublicationVerificationError("publication possui campos inválidos")
    if publication.get("workflow") != CANONICAL_WORKFLOW:
        raise TimestampPublicationVerificationError("publication.workflow inválido")
    _text(publication["run_id"], "publication.run_id", POSITIVE)
    _text(publication["run_attempt"], "publication.run_attempt", POSITIVE)

    public = _mapping(receipt.get("public_verification"), "public_verification")
    if set(public) != {"tuf", "bootstraps", "product"}:
        raise TimestampPublicationVerificationError("public_verification possui campos inválidos")
    _require_status(public["tuf"], label="public_verification.tuf", status="verified-public-tuf")
    _require_status(public["bootstraps"], label="public_verification.bootstraps", status="verified-public-bootstraps")
    _require_status(public["product"], label="public_verification.product", status="verified-public-product")

    candidate_path = Path(candidate)
    actual_candidate = _identity(candidate_path / "candidate.json", "candidate.json")
    actual_catalog = _identity(candidate_path / "catalog.json", "catálogo candidato")
    if actual_candidate["sha256"] != candidate_section["candidate_json_sha256"]:
        raise TimestampPublicationVerificationError("candidate.json diverge do recibo")
    if actual_catalog["sha256"] != candidate_section["catalog_sha256"]:
        raise TimestampPublicationVerificationError("catálogo candidato diverge do recibo")

    actual_renewal_report = _json(Path(renewal_report), "renewal report")
    if actual_renewal_report != renewal_report_value:
        raise TimestampPublicationVerificationError("renewal report diverge do recibo")
    if hashlib.sha256(_regular_file(Path(renewal_report), "renewal report")).hexdigest() != renewal_section["report_sha256"]:
        raise TimestampPublicationVerificationError("digest do renewal report diverge do recibo")
    actual_verified = _json(Path(verified_renewal), "renewal verification")
    if actual_verified != verified_value:
        raise TimestampPublicationVerificationError("renewal verification diverge do recibo")
    for path, label, expected in (
        (public_tuf, "public TUF verification", public["tuf"]),
        (public_bootstraps, "public bootstrap verification", public["bootstraps"]),
        (public_product, "public product verification", public["product"]),
    ):
        if _json(Path(path), label) != expected:
            raise TimestampPublicationVerificationError(f"{label} diverge do recibo")

    return {
        "format": 1,
        "project": PROJECT,
        "status": "verified-timestamp-publication",
        "published": True,
        "changed_files": ["metadata/timestamp.json"],
        "candidate_json_sha256": actual_candidate["sha256"],
        "catalog_sha256": actual_catalog["sha256"],
        "timestamp_key_id": key_id,
        "source_workflow_commit": source_section["workflow_commit"],
    }


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--renewal-report", type=Path, required=True)
    parser.add_argument("--verified-renewal", type=Path, required=True)
    parser.add_argument("--public-tuf", type=Path, required=True)
    parser.add_argument("--public-bootstraps", type=Path, required=True)
    parser.add_argument("--public-product", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        result = verify_timestamp_publication_receipt(
            receipt=_json(options.receipt, "receipt"),
            candidate=options.candidate,
            renewal_report=options.renewal_report,
            verified_renewal=options.verified_renewal,
            public_tuf=options.public_tuf,
            public_bootstraps=options.public_bootstraps,
            public_product=options.public_product,
        )
    except (OSError, TimestampPublicationVerificationError, ValueError) as error:
        print(f"[ERRO] Verificação do recibo timestamp falhou: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
