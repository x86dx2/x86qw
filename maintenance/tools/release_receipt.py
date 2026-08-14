"""Create and verify the durable public receipt for one promoted candidate."""

from __future__ import annotations

import argparse
import hashlib
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

from maintenance.tools.release_candidate import (  # noqa: E402
    BOUND_METADATA_NAMES,
    CandidateError,
    verify_candidate,
)
from maintenance.tools.release_trust import canonical_json_bytes  # noqa: E402


PROJECT = "x86qw"
FORMAT = "x86qw-release-receipt-v1"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_JSON_BYTES = 2 * 1024 * 1024
PUBLIC_METADATA_NAMES = (
    "candidate.json",
    *BOUND_METADATA_NAMES,
    "release-evidence.json",
    "evidence-root.json",
)
INTERNAL_CANDIDATE_PREFIXES = ("runtime/native-smoke/",)
SECTIONS = ("promotion", "publication", "tuf", "deployment")
SECTION_FIELDS = {
    "promotion": frozenset({
        "workflow", "run_id", "run_attempt", "candidate_run_id",
        "candidate_artifact_id", "candidate_artifact_name", "candidate_artifact_digest",
        "native_evidence_run_id", "native_evidence_artifact_id",
        "native_evidence_artifact_name",
    }),
    "publication": frozenset({
        "repository", "tag", "github_release", "gitlab_project", "gitlab_asset",
    }),
    "tuf": frozenset({"workflow", "run_id", "artifact_id", "artifact_name"}),
    "deployment": frozenset({"endpoint", "verification"}),
}


class ReleaseReceiptError(RuntimeError):
    """The durable release receipt is missing, unsafe, or inconsistent."""


def _no_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ReleaseReceiptError(f"JSON contém chave duplicada: {key}")
        value[key] = item
    return value


def _read_json(path: Path, *, label: str) -> dict[str, object]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ReleaseReceiptError(f"{label} ausente ou inseguro: {path}")
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise ReleaseReceiptError(f"não foi possível ler {label}: {path}") from error
    if not payload or len(payload) > MAX_JSON_BYTES:
        raise ReleaseReceiptError(f"{label} ausente ou excede o limite")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_no_duplicate_pairs)
    except ReleaseReceiptError:
        raise
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseReceiptError(f"{label} não é JSON UTF-8 válido") from error
    if not isinstance(value, dict):
        raise ReleaseReceiptError(f"{label} precisa ser um objeto JSON")
    return value


def _regular_bytes(path: Path, *, label: str) -> bytes:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ReleaseReceiptError(f"{label} ausente ou inseguro: {path}")
    try:
        return path.read_bytes()
    except OSError as error:
        raise ReleaseReceiptError(f"não foi possível ler {label}: {path}") from error


def _identity(path: Path, *, label: str) -> dict[str, object]:
    payload = _regular_bytes(path, label=label)
    return {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _validate_asset(value: object, *, label: str) -> dict[str, object]:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"size", "sha256"}
        or type(value.get("size")) is not int
        or value.get("size", -1) < 0
        or not isinstance(value.get("sha256"), str)
        or HEX64.fullmatch(str(value["sha256"])) is None
    ):
        raise ReleaseReceiptError(f"identidade de asset inválida: {label}")
    return {"size": value["size"], "sha256": value["sha256"]}


def _validate_coordinates(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, Mapping) or set(value) != set(SECTIONS):
        raise ReleaseReceiptError("coordenadas do recibo possuem seções inválidas")
    result: dict[str, dict[str, str]] = {}
    secret_words = ("token", "secret", "password", "private", "credential", "key")
    for section in SECTIONS:
        raw = value.get(section)
        expected = SECTION_FIELDS[section]
        if not isinstance(raw, Mapping) or set(raw) != set(expected):
            raise ReleaseReceiptError(f"coordenadas inválidas na seção {section}")
        normalized: dict[str, str] = {}
        for key, item in raw.items():
            if not isinstance(key, str) or any(word in key.casefold() for word in secret_words):
                raise ReleaseReceiptError(f"campo sensível nas coordenadas: {section}.{key}")
            if not isinstance(item, str) or not item or len(item) > 1024 or any(
                ord(char) < 0x20 for char in item
            ):
                raise ReleaseReceiptError(f"valor inválido nas coordenadas: {section}.{key}")
            normalized[key] = item
        result[section] = normalized
    return result


def _public_assets(candidate: Path, manifest: Mapping[str, object], evidence_root: Path) -> dict[str, dict[str, object]]:
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, Mapping):
        raise ReleaseReceiptError("candidato sem lista de artefatos")
    assets: dict[str, dict[str, object]] = {}
    seen: set[str] = set()
    for raw_name in sorted(raw_artifacts, key=str.casefold):
        if (
            not isinstance(raw_name, str)
            or not raw_name.casefold().endswith(".zip")
            or raw_name.startswith(INTERNAL_CANDIDATE_PREFIXES)
        ):
            continue
        name = PurePosixPath(raw_name).name
        key = name.casefold()
        if key in seen:
            raise ReleaseReceiptError(f"basename público duplicado: {name}")
        seen.add(key)
        assets[name] = _identity(candidate.joinpath(*PurePosixPath(raw_name).parts), label=name)
    if not assets:
        raise ReleaseReceiptError("candidato não possui ZIP público")
    for name in PUBLIC_METADATA_NAMES:
        path = candidate / name
        if name == "evidence-root.json":
            path = Path(evidence_root)
        if name.casefold() in seen:
            raise ReleaseReceiptError(f"metadata colide com asset: {name}")
        seen.add(name.casefold())
        assets[name] = _identity(path, label=name)
    return assets


def _evidence_summary(candidate: Path, evidence_identity: Mapping[str, object]) -> dict[str, object]:
    evidence = _read_json(candidate / "release-evidence.json", label="release-evidence.json")
    if (
        evidence.get("format") != 1
        or evidence.get("project") != PROJECT
        or evidence.get("status") != "complete"
        or not isinstance(evidence.get("platforms"), Mapping)
        or not isinstance(evidence.get("signatures"), list)
    ):
        raise ReleaseReceiptError("release-evidence.json não é um agregado completo")
    signatures = evidence["signatures"]
    keyids: list[str] = []
    for item in signatures:
        if not isinstance(item, Mapping) or not isinstance(item.get("keyid"), str):
            raise ReleaseReceiptError("release-evidence.json possui assinatura inválida")
        keyid = str(item["keyid"])
        if HEX64.fullmatch(keyid) is None or keyid in keyids:
            raise ReleaseReceiptError("release-evidence.json possui key ID inválido ou duplicado")
        keyids.append(keyid)
    platforms: dict[str, dict[str, object]] = {}
    raw_platforms = evidence["platforms"]
    assert isinstance(raw_platforms, Mapping)
    for name, raw_report in sorted(raw_platforms.items(), key=lambda item: str(item[0]).casefold()):
        if not isinstance(name, str) or not isinstance(raw_report, Mapping):
            raise ReleaseReceiptError("plataforma ausente no resumo de evidência")
        raw_cases = raw_report.get("cases")
        if not isinstance(raw_cases, list):
            raise ReleaseReceiptError(f"casos ausentes na evidência: {name}")
        case_names: list[str] = []
        for raw_case in raw_cases:
            if not isinstance(raw_case, Mapping) or not isinstance(raw_case.get("name"), str):
                raise ReleaseReceiptError(f"caso inválido na evidência: {name}")
            case_names.append(str(raw_case["name"]))
        hardware = raw_report.get("hardware")
        if hardware is not None and not isinstance(hardware, Mapping):
            raise ReleaseReceiptError(f"hardware inválido na evidência: {name}")
        platforms[name] = {
            "hardware": dict(hardware) if isinstance(hardware, Mapping) else None,
            "case_names": case_names,
        }
    return {
        "asset": dict(evidence_identity),
        "signature_keyids": keyids,
        "platforms": platforms,
    }


def build_receipt(
    *,
    candidate: Path,
    evidence_root: Path,
    coordinates: Mapping[str, object],
) -> dict[str, object]:
    """Build a receipt from the exact candidate and public handoff coordinates."""

    candidate = Path(candidate)
    evidence_root = Path(evidence_root)
    try:
        manifest = verify_candidate(candidate, trust_root=evidence_root)
    except (CandidateError, OSError) as error:
        raise ReleaseReceiptError(f"candidato não pôde ser autenticado: {error}") from error
    normalized_coordinates = _validate_coordinates(coordinates)
    root_identity = _identity(evidence_root, label="root M3")
    assets = _public_assets(candidate, manifest, evidence_root)
    manifest_path = candidate / "candidate.json"
    manifest_identity = _identity(manifest_path, label="candidate.json")
    version = manifest.get("version")
    commit = manifest.get("commit")
    if not isinstance(version, str) or not isinstance(commit, str) or HEX40.fullmatch(commit) is None:
        raise ReleaseReceiptError("identidade do candidato inválida")
    receipt: dict[str, object] = {
        "format": FORMAT,
        "project": PROJECT,
        "status": "authorized",
        "candidate": {
            "version": version,
            "commit": commit,
            "candidate_json": manifest_identity,
        },
        "assets": assets,
        "evidence": {
            "root": assets["evidence-root.json"],
            **_evidence_summary(candidate, assets["release-evidence.json"]),
        },
        **normalized_coordinates,
    }
    return receipt


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
            raise ReleaseReceiptError(f"destino já existe: {path}") from error
    finally:
        temporary.unlink(missing_ok=True)


def write_durable_assets(
    *,
    candidate: Path,
    evidence_root: Path,
    coordinates: Mapping[str, object],
) -> dict[str, object]:
    """Copy the public M3 root and write one receipt without changing candidate.json."""

    candidate = Path(candidate)
    root_destination = candidate / "evidence-root.json"
    receipt_destination = candidate / "release-receipt.json"
    if root_destination.exists() or root_destination.is_symlink() or receipt_destination.exists() or receipt_destination.is_symlink():
        raise ReleaseReceiptError("assets duráveis já existem; overwrite recusado")
    receipt = build_receipt(
        candidate=candidate,
        evidence_root=evidence_root,
        coordinates=coordinates,
    )
    root_bytes = _regular_bytes(Path(evidence_root), label="root M3")
    created: list[Path] = []
    try:
        _write_exclusive(root_destination, root_bytes)
        created.append(root_destination)
        _write_exclusive(receipt_destination, canonical_json_bytes(receipt))
        created.append(receipt_destination)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return receipt


def validate_durable_assets(
    candidate: Path,
    *,
    trust_root: Path | None = None,
) -> dict[str, object]:
    """Verify the public root, receipt, and every receipt-bound candidate asset."""

    candidate = Path(candidate)
    root = candidate / "evidence-root.json"
    receipt_path = candidate / "release-receipt.json"
    root_bytes = _regular_bytes(root, label="evidence-root.json")
    document = _read_json(receipt_path, label="release-receipt.json")
    if canonical_json_bytes(document) != receipt_path.read_bytes():
        raise ReleaseReceiptError("release-receipt.json não está em JSON canônico")
    effective_root = Path(trust_root) if trust_root is not None else root
    try:
        verify_candidate(candidate, trust_root=effective_root)
    except (CandidateError, OSError) as error:
        raise ReleaseReceiptError(f"candidato com evidência não pôde ser autenticado: {error}") from error
    if document.get("format") != FORMAT or document.get("project") != PROJECT or document.get("status") != "authorized":
        raise ReleaseReceiptError("identidade do recibo inválida")
    coordinates = {section: document.get(section) for section in SECTIONS}
    expected = build_receipt(candidate=candidate, evidence_root=root, coordinates=coordinates)
    if expected != document:
        raise ReleaseReceiptError("release-receipt.json diverge dos bytes públicos")
    expected_root = document.get("evidence", {}).get("root") if isinstance(document.get("evidence"), Mapping) else None
    actual_root = {"size": len(root_bytes), "sha256": hashlib.sha256(root_bytes).hexdigest()}
    if expected_root != actual_root:
        raise ReleaseReceiptError("evidence-root.json diverge do recibo")
    if trust_root is not None:
        trusted_root_bytes = _regular_bytes(Path(trust_root), label="root M3 externa")
        if root_bytes != trusted_root_bytes:
            raise ReleaseReceiptError(
                "evidence-root.json diverge da root externa usada pelo gate"
            )
    return document


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    write = commands.add_parser("write", help="materializar evidence-root.json e release-receipt.json")
    write.add_argument("--candidate", type=Path, required=True)
    write.add_argument("--evidence-root", type=Path, required=True)
    write.add_argument("--coordinates", type=Path, required=True)
    verify = commands.add_parser("verify", help="verificar assets duráveis do candidato")
    verify.add_argument("--candidate", type=Path, required=True)
    verify.add_argument("--trust-root", type=Path)
    options = parser.parse_args(arguments)
    try:
        if options.command == "write":
            coordinates = _read_json(options.coordinates, label="coordenadas do recibo")
            write_durable_assets(
                candidate=options.candidate,
                evidence_root=options.evidence_root,
                coordinates=coordinates,
            )
        else:
            validate_durable_assets(options.candidate, trust_root=options.trust_root)
    except (ReleaseReceiptError, OSError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(f"[OK] Assets duráveis {options.command} concluídos sem overwrite.")
    return 0


__all__ = [
    "FORMAT",
    "ReleaseReceiptError",
    "build_receipt",
    "main",
    "validate_durable_assets",
    "write_durable_assets",
]


if __name__ == "__main__":
    raise SystemExit(main())
