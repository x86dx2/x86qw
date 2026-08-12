"""Prepare and assemble the signed release-evidence handoff.

This tool never handles a private key.  The native runner produces unsigned
platform records; an external custodian signs the canonical body and returns a
small public signature envelope.  This module binds both to the exact
candidate and emits the aggregate consumed by the promotion gate.
"""

from __future__ import annotations

import argparse
import hashlib
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

from maintenance.tools import native_release_evidence
from maintenance.tools.release_candidate import CandidateError, verify_candidate
from maintenance.tools.release_trust import canonical_json_bytes, verify_release_evidence
from x86qw_runtime.contracts.native_evidence import REQUIRED_NATIVE_PLATFORMS


PROJECT = "x86qw"
FORMAT = 1
BODY_FIELDS = frozenset({
    "format", "project", "version", "commit", "status", "candidate", "platforms",
})
SIGNATURE_ENVELOPE_FIELDS = frozenset({
    "format", "project", "candidate", "body_sha256", "signatures",
})
SIGNATURE_FIELDS = frozenset({"keyid", "sig"})
HEX64 = re.compile(r"^[0-9a-f]{64}$")
BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_JSON_BYTES = 4 * 1024 * 1024


class EvidenceAssemblyError(RuntimeError):
    """The signing handoff is missing, unsafe, or bound to another candidate."""


def _no_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceAssemblyError(f"JSON contém chave duplicada: {key}")
        result[key] = value
    return result


def _read_json(path: Path, *, label: str) -> dict[str, object]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise EvidenceAssemblyError(f"{label} ausente ou inseguro: {path}")
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise EvidenceAssemblyError(f"{label} excede o limite de 4 MiB")
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_no_duplicate_pairs,
        )
    except EvidenceAssemblyError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvidenceAssemblyError(f"{label} inválido: {path}") from error
    if not isinstance(value, dict):
        raise EvidenceAssemblyError(f"{label} precisa ser um objeto JSON")
    return value


def _manifest_identity(candidate: Path) -> dict[str, str]:
    try:
        manifest = verify_candidate(Path(candidate), allow_pending_evidence=True)
        manifest_path = Path(candidate) / "candidate.json"
        manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    except (CandidateError, OSError) as error:
        raise EvidenceAssemblyError(f"candidato não pôde ser validado: {error}") from error
    return {
        "version": str(manifest["version"]),
        "commit": str(manifest["commit"]),
        "manifest_sha256": manifest_sha256,
    }


def _safe_output(candidate: Path, output: Path, *, label: str) -> Path:
    candidate = Path(candidate).absolute()
    output = Path(output).absolute()
    if output == candidate or candidate in output.parents:
        raise EvidenceAssemblyError(f"{label} não pode modificar o candidato imutável")
    if output.exists() or output.is_symlink():
        raise EvidenceAssemblyError(f"destino de {label} já existe: {output}")
    if output.parent.is_symlink() or not output.parent.is_dir():
        raise EvidenceAssemblyError(f"diretório de saída ausente ou inseguro: {output.parent}")
    return output


def _write_exclusive(path: Path, payload: bytes) -> None:
    path = Path(path)
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
            raise EvidenceAssemblyError(f"destino de evidência já existe: {path}") from error
    finally:
        temporary.unlink(missing_ok=True)


def _expected_records(candidate: Path, records_dir: Path) -> tuple[dict[str, object], ...]:
    expected = tuple(sorted(REQUIRED_NATIVE_PLATFORMS, key=str.casefold))
    try:
        return native_release_evidence.validate_native_evidence(
            candidate=Path(candidate),
            evidence_dir=Path(records_dir),
            expected_platforms=expected,
        )
    except (CandidateError, OSError) as error:
        raise EvidenceAssemblyError(f"registros nativos inválidos: {error}") from error


def _validate_body(
    body: Mapping[str, object],
    *,
    identity: dict[str, str],
    records: tuple[dict[str, object], ...],
) -> dict[str, object]:
    if set(body) != BODY_FIELDS:
        raise EvidenceAssemblyError("corpo da evidência possui campos desconhecidos ou ausentes")
    if (
        body.get("format") != FORMAT
        or body.get("project") != PROJECT
        or body.get("version") != identity["version"]
        or body.get("commit") != identity["commit"]
        or body.get("status") != "complete"
        or body.get("candidate") != identity
    ):
        raise EvidenceAssemblyError("corpo da evidência diverge do candidato exato")
    expected_platforms = {record["platform"]: record for record in records}
    if body.get("platforms") != expected_platforms:
        raise EvidenceAssemblyError("corpo da evidência diverge dos registros nativos")
    return dict(body)


def _canonical_body(path: Path, *, identity: dict[str, str], records: tuple[dict[str, object], ...]) -> tuple[dict[str, object], bytes]:
    value = _read_json(path, label="corpo da evidência")
    body = _validate_body(value, identity=identity, records=records)
    payload = canonical_json_bytes(body)
    try:
        actual = Path(path).read_bytes()
    except OSError as error:
        raise EvidenceAssemblyError(f"não foi possível ler o corpo da evidência: {path}") from error
    if actual != payload:
        raise EvidenceAssemblyError("corpo da evidência não está em JSON canônico")
    return body, payload


def prepare_body(*, candidate: Path, records_dir: Path, output: Path) -> dict[str, object]:
    """Write the exact unsigned body that the external signer must sign."""

    output = _safe_output(candidate, output, label="corpo")
    identity = _manifest_identity(Path(candidate))
    records = _expected_records(Path(candidate), Path(records_dir))
    body = {
        "format": FORMAT,
        "project": PROJECT,
        "version": identity["version"],
        "commit": identity["commit"],
        "status": "complete",
        "candidate": identity,
        "platforms": {record["platform"]: record for record in records},
    }
    _validate_body(body, identity=identity, records=records)
    _write_exclusive(output, canonical_json_bytes(body))
    return body


def _read_signatures(path: Path, *, identity: dict[str, str], body_payload: bytes) -> list[dict[str, str]]:
    value = _read_json(path, label="envelope de assinaturas")
    if set(value) != SIGNATURE_ENVELOPE_FIELDS:
        raise EvidenceAssemblyError("envelope de assinaturas possui campos desconhecidos ou ausentes")
    if (
        value.get("format") != FORMAT
        or value.get("project") != PROJECT
        or value.get("candidate") != identity
        or value.get("body_sha256") != hashlib.sha256(body_payload).hexdigest()
    ):
        raise EvidenceAssemblyError("envelope de assinaturas não corresponde ao corpo exato")
    raw = value.get("signatures")
    if not isinstance(raw, list) or not raw:
        raise EvidenceAssemblyError("envelope de assinaturas está vazio")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping) or set(item) != SIGNATURE_FIELDS:
            raise EvidenceAssemblyError(f"assinatura #{index} contém campos inválidos")
        keyid = item.get("keyid")
        signature = item.get("sig")
        if (
            not isinstance(keyid, str)
            or HEX64.fullmatch(keyid) is None
            or keyid in seen
            or not isinstance(signature, str)
            or BASE64URL.fullmatch(signature) is None
        ):
            raise EvidenceAssemblyError(f"assinatura #{index} inválida ou duplicada")
        seen.add(keyid)
        result.append({"keyid": keyid, "sig": signature})
    return result


def assemble(
    *,
    candidate: Path,
    records_dir: Path,
    body: Path,
    signatures: Path,
    output: Path,
    trust_root: Path | None = None,
) -> dict[str, object]:
    """Attach only external public signatures and optionally verify their root."""

    output = _safe_output(candidate, output, label="evidência assinada")
    identity = _manifest_identity(Path(candidate))
    records = _expected_records(Path(candidate), Path(records_dir))
    body_value, body_payload = _canonical_body(
        Path(body), identity=identity, records=records,
    )
    signature_values = _read_signatures(
        Path(signatures), identity=identity, body_payload=body_payload,
    )
    aggregate = {**body_value, "signatures": signature_values}
    temporary_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}-", dir=output.parent,
    )
    temporary = Path(temporary_name)
    payload = canonical_json_bytes(aggregate)
    try:
        with os.fdopen(temporary_descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        try:
            native_release_evidence.validate_signed_evidence_coverage(
                candidate=Path(candidate),
                evidence=temporary,
                expected_platforms=tuple(sorted(REQUIRED_NATIVE_PLATFORMS, key=str.casefold)),
                unsigned_evidence_dir=Path(records_dir),
            )
            if trust_root is not None:
                try:
                    root_path = Path(trust_root)
                    if root_path.is_symlink() or not root_path.is_file():
                        raise EvidenceAssemblyError("root de trust ausente ou inseguro")
                    if root_path.stat().st_size > MAX_JSON_BYTES:
                        raise EvidenceAssemblyError("root de trust excede o limite de 4 MiB")
                    root_bytes = root_path.read_bytes()
                    verify_release_evidence(
                        root_bytes,
                        payload,
                        expected_identity=identity,
                    )
                except EvidenceAssemblyError:
                    raise
                except (OSError, ValueError) as error:
                    raise EvidenceAssemblyError(
                        f"assinaturas não foram autenticadas pelo root fornecido: {error}"
                    ) from error
            try:
                os.link(temporary, output)
            except FileExistsError as error:
                raise EvidenceAssemblyError(
                    f"destino de evidência assinada já existe: {output}"
                ) from error
        except CandidateError as error:
            raise EvidenceAssemblyError(f"agregado assinado inválido: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)
    return aggregate


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="gerar o corpo canônico não assinado")
    prepare.add_argument("--candidate", type=Path, required=True)
    prepare.add_argument("--records-dir", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    assemble_parser = commands.add_parser("assemble", help="anexar assinaturas externas sem chave privada")
    assemble_parser.add_argument("--candidate", type=Path, required=True)
    assemble_parser.add_argument("--records-dir", type=Path, required=True)
    assemble_parser.add_argument("--body", type=Path, required=True)
    assemble_parser.add_argument("--signatures", type=Path, required=True)
    assemble_parser.add_argument("--output", type=Path, required=True)
    assemble_parser.add_argument("--trust-root", type=Path)
    options = parser.parse_args(arguments)
    try:
        if options.command == "prepare":
            prepare_body(
                candidate=options.candidate,
                records_dir=options.records_dir,
                output=options.output,
            )
        else:
            if options.trust_root is None:
                raise EvidenceAssemblyError(
                    "assemble exige --trust-root para não produzir evidência não autenticada"
                )
            assemble(
                candidate=options.candidate,
                records_dir=options.records_dir,
                body=options.body,
                signatures=options.signatures,
                output=options.output,
                trust_root=options.trust_root,
            )
    except (EvidenceAssemblyError, OSError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(f"[OK] Evidência {options.command} concluída sem chave privada.")
    return 0


__all__ = ["EvidenceAssemblyError", "assemble", "canonical_json_bytes", "main", "prepare_body"]


if __name__ == "__main__":
    raise SystemExit(main())
