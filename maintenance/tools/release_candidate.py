#!/usr/bin/env python3
"""Prepare one immutable candidate; keep 1.0 promotion behind real M3 evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


PROJECT = "x86qw"
FORMAT = 1
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
BOUND_METADATA = (
    "checksums.txt",
    "ownership.json",
    "sbom.spdx.json",
    "provenance.json",
    "mirrors.json",
)
RESERVED = frozenset((*BOUND_METADATA, "candidate.json", "release-evidence.json"))
M3_IMPORT_ERROR = None


class CandidateError(RuntimeError):
    """Candidate bytes or release evidence are absent, unsafe, or inconsistent."""


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _regular(path: Path, label: str) -> Path:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise CandidateError(f"{label} ausente ou inseguro: {path}")
    return path


def _sha256(path: Path) -> tuple[int, str]:
    _regular(path, "arquivo do candidato")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _safe_name(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise CandidateError(f"caminho de artefato inválido: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CandidateError(f"caminho de artefato inválido: {value!r}")
    canonical = path.as_posix()
    if canonical in RESERVED:
        raise CandidateError(f"nome reservado no candidato: {canonical}")
    return canonical


def _files(root: Path, *, include_metadata: bool = True) -> list[Path]:
    result: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise CandidateError(f"candidato não aceita symlink: {path}")
        if path.is_file():
            relative = PurePosixPath(*path.relative_to(root).parts).as_posix()
            if include_metadata or relative not in RESERVED:
                result.append(path)
        elif not path.is_dir():
            raise CandidateError(f"candidato contém tipo especial: {path}")
    return result


def _identity_digest(manifest: dict[str, object]) -> str:
    identity = dict(manifest)
    identity["candidate_sha256"] = None
    return hashlib.sha256(_json_bytes(identity)).hexdigest()


def _load_json(path: Path, label: str) -> dict[str, object]:
    _regular(path, label)

    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise CandidateError(f"{label} contém chave duplicada: {key}")
            value[key] = item
        return value

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CandidateError(f"{label} inválido: {path}") from error
    if not isinstance(value, dict):
        raise CandidateError(f"{label} precisa ser objeto JSON")
    return value


def _copy_inputs(source: Path, staging: Path) -> None:
    if source.is_symlink() or not source.is_dir():
        raise CandidateError(f"fonte do candidato ausente ou insegura: {source}")
    portable: set[str] = set()
    for path in _files(source):
        relative = PurePosixPath(*path.relative_to(source).parts).as_posix()
        name = _safe_name(relative)
        key = unicodedata.normalize("NFC", name).casefold()
        if key in portable:
            raise CandidateError(f"colisão de caminho portátil no candidato: {name}")
        portable.add(key)
        destination = staging.joinpath(*PurePosixPath(name).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination, follow_symlinks=False)
        os.chmod(destination, 0o644)


def _artifact_manifest(root: Path) -> dict[str, dict[str, object]]:
    artifacts: dict[str, dict[str, object]] = {}
    for path in _files(root, include_metadata=False):
        name = _safe_name(PurePosixPath(*path.relative_to(root).parts).as_posix())
        size, digest = _sha256(path)
        artifacts[name] = {"size": size, "sha256": digest}
    if not artifacts:
        raise CandidateError("candidato sem artefatos")
    return artifacts


def _ownership(artifacts: dict[str, dict[str, object]]) -> dict[str, object]:
    """Record only facts known at this boundary; never infer ownership by path."""

    return {
        "format": FORMAT,
        "project": PROJECT,
        "artifacts": [
            {
                "path": name,
                "size": facts["size"],
                "sha256": facts["sha256"],
                "ownership": "unclassified",
                "source": f"candidate-input:{name}",
                "license_concluded": "NOASSERTION",
                "copyright_text": "NOASSERTION",
            }
            for name, facts in sorted(artifacts.items())
        ],
    }


def _sbom(ownership: dict[str, object], *, version: str, generated_at: str, commit: str) -> dict[str, object]:
    files = []
    for index, entry in enumerate(ownership["artifacts"], start=1):  # type: ignore[index]
        files.append({
            "SPDXID": f"SPDXRef-file-{index}",
            "fileName": entry["path"],
            "checksums": [{"algorithm": "SHA256", "checksumValue": entry["sha256"]}],
            "licenseConcluded": entry["license_concluded"],
            "copyrightText": entry["copyright_text"],
        })
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"{PROJECT}-{version}",
        "documentNamespace": f"https://x86qw.x86.com.br/release/{version}/{commit}",
        "creationInfo": {"created": generated_at, "creators": ["Tool: x86QW release-candidate"]},
        "files": files,
    }


def prepare_candidate(
    *,
    source: Path,
    output: Path,
    version: str,
    commit: str,
    generated_at: str | None = None,
) -> dict[str, object]:
    if not isinstance(version, str) or SEMVER.fullmatch(version) is None:
        raise CandidateError(f"versão SemVer inválida: {version!r}")
    if not isinstance(commit, str) or HEX40.fullmatch(commit) is None:
        raise CandidateError("commit precisa ser SHA-1 hexadecimal completo")
    if generated_at is None:
        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if UTC_TIMESTAMP.fullmatch(generated_at) is None:
        raise CandidateError("generated_at precisa ser timestamp UTC canônico")
    source = Path(source)
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise CandidateError(f"destino do candidato já existe: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        _copy_inputs(source, staging)
        artifacts = _artifact_manifest(staging)
        checksums = "".join(
            f"{facts['sha256']}  {name}\n" for name, facts in sorted(artifacts.items())
        ).encode("utf-8")
        ownership = _ownership(artifacts)
        provenance = {
            "format": FORMAT,
            "project": PROJECT,
            "version": version,
            "subject": {
                "commit": commit,
                "source": f"https://github.com/x86dx2/x86qw/tree/{commit}",
            },
            "builder": {"id": "x86qw/release-candidate", "rebuild": False},
            "materials": [
                {"path": name, **facts} for name, facts in sorted(artifacts.items())
            ],
        }
        mirrors = {
            "format": FORMAT,
            "project": PROJECT,
            "version": version,
            "status": "unpublished",
            "artifacts": [
                {"path": name, **facts, "urls": []}
                for name, facts in sorted(artifacts.items())
            ],
        }
        _write(staging / "checksums.txt", checksums)
        _write(staging / "ownership.json", _json_bytes(ownership))
        _write(staging / "sbom.spdx.json", _json_bytes(_sbom(
            ownership, version=version, generated_at=generated_at, commit=commit,
        )))
        _write(staging / "provenance.json", _json_bytes(provenance))
        _write(staging / "mirrors.json", _json_bytes(mirrors))
        metadata = {
            name: dict(zip(("size", "sha256"), _sha256(staging / name), strict=True))
            for name in BOUND_METADATA
        }
        manifest: dict[str, object] = {
            "format": FORMAT,
            "project": PROJECT,
            "version": version,
            "commit": commit,
            "generated_at": generated_at,
            "artifacts": artifacts,
            "artifact_count": len(artifacts),
            "metadata": metadata,
            "candidate_sha256": None,
        }
        manifest["candidate_sha256"] = _identity_digest(manifest)
        # candidate.json is deliberately the last candidate metadata written.
        _write(staging / "candidate.json", _json_bytes(manifest))
        try:
            shutil.copytree(staging, output, dirs_exist_ok=False)
        except FileExistsError as error:
            raise CandidateError(f"destino do candidato já existe: {output}") from error
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _validate_ownership(value: dict[str, object], artifacts: dict[str, dict[str, object]]) -> None:
    if set(value) != {"format", "project", "artifacts"} or value.get("format") != FORMAT or value.get("project") != PROJECT:
        raise CandidateError("ownership inválido")
    entries = value.get("artifacts")
    if not isinstance(entries, list) or len(entries) != len(artifacts):
        raise CandidateError("ownership não cobre exatamente os artefatos")
    normalized = {}
    required = {"path", "size", "sha256", "ownership", "source", "license_concluded", "copyright_text"}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != required:
            raise CandidateError("entrada de ownership inválida")
        name = _safe_name(entry.get("path"))
        if (
            entry.get("ownership") != "unclassified"
            or entry.get("source") != f"candidate-input:{name}"
            or entry.get("license_concluded") != "NOASSERTION"
            or entry.get("copyright_text") != "NOASSERTION"
        ):
            raise CandidateError("ownership infere fonte ou licença não demonstrada")
        normalized[name] = {"size": entry.get("size"), "sha256": entry.get("sha256")}
    if normalized != artifacts:
        raise CandidateError("ownership diverge dos bytes do candidato")


def verify_candidate(candidate: Path) -> dict[str, object]:
    candidate = Path(candidate)
    if candidate.is_symlink() or not candidate.is_dir():
        raise CandidateError(f"candidato ausente ou inseguro: {candidate}")
    manifest = _load_json(candidate / "candidate.json", "manifest")
    required = {
        "format", "project", "version", "commit", "generated_at", "artifacts",
        "artifact_count", "metadata", "candidate_sha256",
    }
    if set(manifest) != required or manifest.get("format") != FORMAT or manifest.get("project") != PROJECT:
        raise CandidateError("manifest contém campos desconhecidos ou ausentes")
    version = manifest.get("version")
    commit = manifest.get("commit")
    generated_at = manifest.get("generated_at")
    if not isinstance(version, str) or SEMVER.fullmatch(version) is None or not isinstance(commit, str) or HEX40.fullmatch(commit) is None:
        raise CandidateError("identidade do candidato inválida")
    if not isinstance(generated_at, str) or UTC_TIMESTAMP.fullmatch(generated_at) is None:
        raise CandidateError("timestamp do candidato inválido")
    digest = manifest.get("candidate_sha256")
    if not isinstance(digest, str) or HEX64.fullmatch(digest) is None or digest != _identity_digest(manifest):
        raise CandidateError("candidate_sha256 diverge")
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, dict) or not raw_artifacts:
        raise CandidateError("candidato sem artefatos")
    artifacts: dict[str, dict[str, object]] = {}
    portable: set[str] = set()
    for raw_name, facts in raw_artifacts.items():
        name = _safe_name(raw_name)
        key = unicodedata.normalize("NFC", name).casefold()
        if key in portable:
            raise CandidateError(f"colisão de caminho portátil: {name}")
        portable.add(key)
        if (
            not isinstance(facts, dict) or set(facts) != {"size", "sha256"}
            or type(facts.get("size")) is not int or facts["size"] < 0
            or not isinstance(facts.get("sha256"), str) or HEX64.fullmatch(facts["sha256"]) is None
        ):
            raise CandidateError(f"identidade de artefato inválida: {name}")
        actual = _sha256(candidate.joinpath(*PurePosixPath(name).parts))
        if actual != (facts["size"], facts["sha256"]):
            raise CandidateError(f"artefato diverge: {name}")
        artifacts[name] = facts
    if manifest.get("artifact_count") != len(artifacts):
        raise CandidateError("contagem de artefatos diverge")
    actual_names = {
        PurePosixPath(*path.relative_to(candidate).parts).as_posix()
        for path in _files(candidate, include_metadata=False)
    }
    if actual_names != set(artifacts):
        raise CandidateError("candidato contém artefato não registrado")
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict) or set(metadata) != set(BOUND_METADATA):
        raise CandidateError("manifest sem metadata vinculada completa")
    for name in BOUND_METADATA:
        expected = metadata[name]
        if not isinstance(expected, dict) or set(expected) != {"size", "sha256"}:
            raise CandidateError(f"metadata inválida: {name}")
        if _sha256(candidate / name) != (expected.get("size"), expected.get("sha256")):
            raise CandidateError(f"metadata diverge: {name}")
    checksum_lines = (candidate / "checksums.txt").read_text(encoding="utf-8").splitlines()
    expected_lines = [f"{facts['sha256']}  {name}" for name, facts in sorted(artifacts.items())]
    if checksum_lines != expected_lines:
        raise CandidateError("checksums.txt diverge")
    _validate_ownership(_load_json(candidate / "ownership.json", "ownership"), artifacts)
    provenance = _load_json(candidate / "provenance.json", "provenance")
    if provenance.get("subject") != {
        "commit": commit,
        "source": f"https://github.com/x86dx2/x86qw/tree/{commit}",
    } or provenance.get("materials") != [
        {"path": name, **facts} for name, facts in sorted(artifacts.items())
    ]:
        raise CandidateError("proveniência diverge do candidato")
    mirrors = _load_json(candidate / "mirrors.json", "mirrors")
    if mirrors.get("status") != "unpublished" or mirrors.get("artifacts") != [
        {"path": name, **facts, "urls": []} for name, facts in sorted(artifacts.items())
    ]:
        raise CandidateError("mirrors de rehearsal inferem publicação inexistente")
    return manifest


def _copy_verified(candidate: Path, destination: Path) -> dict[str, object]:
    manifest = verify_candidate(candidate)
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise CandidateError(f"destino já existe: {destination}")
    try:
        shutil.copytree(candidate, destination, symlinks=False, dirs_exist_ok=False)
    except FileExistsError as error:
        raise CandidateError(f"destino já existe: {destination}") from error
    try:
        if verify_candidate(destination) != manifest:
            raise CandidateError("cópia do candidato diverge")
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return manifest


def rehearse_candidate(candidate: Path, destination: Path) -> dict[str, object]:
    """Exercise immutable transport locally without claiming M3 or release readiness."""

    return _copy_verified(Path(candidate), Path(destination))


def _verify_m3(candidate: Path, trust_root: Path) -> None:
    evidence = candidate / "release-evidence.json"
    if evidence.is_symlink() or not evidence.is_file():
        raise CandidateError("promoção 1.0 exige evidência M3 canônica e assinada")
    _regular(Path(trust_root), "root de trust M3")
    try:
        from maintenance.tools.native_release_evidence import validate_signed_evidence_coverage
        from x86qw_runtime.contracts.native_evidence import REQUIRED_NATIVE_PLATFORMS
        from x86qw_runtime.trust import TrustError, verify_release_evidence
    except ImportError as error:
        raise CandidateError("verificador M3 ainda não está disponível; promoção bloqueada") from error
    try:
        validate_signed_evidence_coverage(
            candidate=candidate,
            evidence=evidence,
            expected_platforms=REQUIRED_NATIVE_PLATFORMS,
        )
        manifest = verify_candidate(candidate)
        verify_release_evidence(
            Path(trust_root).read_bytes(),
            evidence.read_bytes(),
            expected_identity={
                "version": manifest["version"],
                "commit": manifest["commit"],
                "manifest_sha256": _sha256(candidate / "candidate.json")[1],
            },
        )
    except (CandidateError, OSError, TrustError) as error:
        raise CandidateError(f"evidência M3 rejeitada: {error}") from error


def promote_candidate(candidate: Path, destination: Path, *, trust_root: Path) -> dict[str, object]:
    """Promote only after PR G/M3 validates exact candidate runtime evidence."""

    candidate = Path(candidate)
    verify_candidate(candidate)
    _verify_m3(candidate, Path(trust_root))
    destination = Path(destination)
    manifest = _copy_verified(candidate, destination)
    try:
        # Reauthenticate the bytes actually copied, closing evidence mutation
        # races between the source gate and the promotion snapshot.
        _verify_m3(destination, Path(trust_root))
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--version", required=True)
    prepare.add_argument("--commit", required=True)
    prepare.add_argument("--generated-at")
    verify = commands.add_parser("verify")
    verify.add_argument("candidate", type=Path)
    rehearse = commands.add_parser("rehearse")
    rehearse.add_argument("candidate", type=Path)
    rehearse.add_argument("destination", type=Path)
    promote = commands.add_parser("promote")
    promote.add_argument("candidate", type=Path)
    promote.add_argument("destination", type=Path)
    promote.add_argument("--trust-root", type=Path, required=True)
    return parser


def main(arguments: list[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    try:
        if options.command == "prepare":
            prepare_candidate(
                source=options.source,
                output=options.output,
                version=options.version,
                commit=options.commit,
                generated_at=options.generated_at,
            )
        elif options.command == "verify":
            verify_candidate(options.candidate)
        elif options.command == "rehearse":
            rehearse_candidate(options.candidate, options.destination)
        else:
            promote_candidate(options.candidate, options.destination, trust_root=options.trust_root)
    except CandidateError as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(f"[OK] candidate {options.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
