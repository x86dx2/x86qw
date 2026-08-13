"""Prepare and verify one immutable release candidate without rebuilding it."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from x86qw_runtime.trust import TrustError, verify_release_evidence
from x86qw_runtime.contracts.native_evidence import (
    NATIVE_EVIDENCE_FORMAT,
    NativeEvidenceError,
    REQUIRED_NATIVE_PLATFORMS,
    validate_cases,
    validate_environment,
    validate_hardware,
)
from maintenance.tools import release_ownership


PROJECT = "x86qw"
FORMAT = 1
# ``candidate.json`` gained the closed metadata identity map in format 2.
# Other candidate-side documents retain their format-1 contracts.
CANDIDATE_FORMAT = 2
SPDX_VERSION = "SPDX-2.3"
SPDX_CREATOR = "Tool: x86QW release-candidate"
SPDX_LICENSES = frozenset({"MIT", "NOASSERTION"})
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SIGNATURE_TEXT = re.compile(r"^[A-Za-z0-9._:-]+$")
BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")
PLATFORM_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
METADATA_NAMES = frozenset({
    "candidate.json",
    "checksums.txt",
    "ownership.json",
    "sbom.spdx.json",
    "provenance.json",
    "release-evidence.json",
})
# These metadata files are produced once from the immutable build inputs.  The
# native evidence is intentionally excluded: this checkout does not execute
# native smokes and keeps that document as an optional compatibility artifact.
BOUND_METADATA_NAMES = (
    "checksums.txt",
    "ownership.json",
    "sbom.spdx.json",
    "provenance.json",
)
# Compatibility name used by the publisher boundary.
BOUND_METADATA = BOUND_METADATA_NAMES
FORBIDDEN_CANDIDATE_PREFIXES = (
    "site/public/api/v1/trust/",
)
EVIDENCE_FIELDS = frozenset({
    "format", "project", "version", "commit", "status", "candidate", "platforms",
})
MANIFEST_FIELDS = frozenset({
    "format",
    "project",
    "version",
    "commit",
    "generated_at",
    "artifacts",
    "artifact_count",
    "candidate_sha256",
    "checksums",
    "metadata",
})


class CandidateError(RuntimeError):
    """A candidate is invalid, incomplete, or would overwrite an artifact."""


def _atomic_publish_no_replace(source: Path, destination: Path) -> None:
    """Atomically publish a directory without replacing a concurrent path.

    A normal POSIX ``rename`` replaces an existing directory, which is not a
    safe release primitive.  Use the native no-replace operation on each
    supported platform and fail closed when the filesystem cannot provide it.
    The source and destination are created under the same parent, so the
    operation remains atomic within one filesystem.
    """

    source = Path(source)
    destination = Path(destination)
    if os.name == "nt":
        # Windows ``MoveFileEx`` (which backs os.rename) refuses an existing
        # destination when MOVEFILE_REPLACE_EXISTING is not requested.
        os.rename(source, destination)
        return

    at_fdcwd = getattr(os, "AT_FDCWD", -100)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        function = getattr(libc, "renameatx_np", None)
        if function is None:
            raise CandidateError("o macOS não oferece renameatx_np para publicação sem overwrite")
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        function.restype = ctypes.c_int
        # Darwin's RENAME_EXCL refuses an existing destination atomically.
        result = function(at_fdcwd, source_bytes, at_fdcwd, destination_bytes, 0x00000004)
    else:
        libc = ctypes.CDLL(None, use_errno=True)
        function = getattr(libc, "renameat2", None)
        if function is not None:
            function.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            function.restype = ctypes.c_int
            result = function(at_fdcwd, source_bytes, at_fdcwd, destination_bytes, 1)
        else:
            syscall = getattr(libc, "syscall", None)
            syscall_numbers = {
                "x86_64": 316,
                "amd64": 316,
                "aarch64": 276,
                "arm64": 276,
                "i386": 353,
                "i686": 353,
                "armv7l": 382,
                "riscv64": 276,
            }
            number = syscall_numbers.get(os.uname().machine.casefold()) if hasattr(os, "uname") else None
            if syscall is None or number is None:
                raise CandidateError("o sistema não oferece publicação atômica sem overwrite")
            syscall.argtypes = [
                ctypes.c_long,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            syscall.restype = ctypes.c_long
            result = syscall(number, at_fdcwd, source_bytes, at_fdcwd, destination_bytes, 1)

    if result != 0:
        error_number = ctypes.get_errno()
        if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(error_number, os.strerror(error_number), destination)
        raise OSError(error_number, os.strerror(error_number), destination)


def _regular_file(path: Path, *, label: str) -> Path:
    """Return a regular file path, refusing links and special files."""

    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise CandidateError(f"{label} ausente ou inseguro: {path}")
    return path


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _no_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise CandidateError(f"JSON contém chave duplicada: {key}")
        value[key] = item
    return value


def _write_bytes(path: Path, payload: bytes) -> None:
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


def _sha256(path: Path) -> tuple[int, str]:
    _regular_file(path, label="arquivo do candidato")
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
    except OSError as error:
        raise CandidateError(f"não foi possível ler o artefato do candidato: {path}") from error
    return size, digest.hexdigest()


def _relative_file(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise CandidateError(f"arquivo fora do candidato: {path}") from error
    value = PurePosixPath(*relative.parts).as_posix()
    if not value or value in METADATA_NAMES or value.startswith("../") or "\\" in value:
        raise CandidateError(f"caminho de artefato inválido: {value!r}")
    return value


def _validate_artifact_name(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise CandidateError(f"caminho de artefato inválido: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CandidateError(f"caminho de artefato inválido: {value!r}")
    if value in METADATA_NAMES:
        raise CandidateError(f"nome reservado de metadata no candidato: {value!r}")
    return path.as_posix()


def _validate_candidate_payload_path(value: str) -> str:
    """Reject release-generated metadata that is supplied only at publish time."""

    for prefix in FORBIDDEN_CANDIDATE_PREFIXES:
        if value.startswith(prefix):
            raise CandidateError(
                "metadata TUF pública não pode entrar no candidato antes do metadata-last: "
                + value
            )
    return value


def _portable_name_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _validate_identity(version: str, commit: str) -> None:
    if not isinstance(version, str) or SEMVER.fullmatch(version) is None:
        raise CandidateError(f"versão SemVer inválida: {version!r}")
    if not isinstance(commit, str) or HEX40.fullmatch(commit) is None:
        raise CandidateError("commit precisa ser um SHA-1 hexadecimal completo")


def _artifact_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise CandidateError(f"candidato não aceita symlink: {path}")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise CandidateError(f"candidato contém tipo especial: {path}")
    return files


def _copy_source(source: Path, destination: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        raise CandidateError(f"fonte de artefatos inválida: {source}")
    destination.chmod(0o700)
    for path in _artifact_files(source):
        relative = _relative_file(path, source)
        target = destination.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target, follow_symlinks=False)
        os.chmod(target, 0o644)


def _manifest_for(root: Path, *, version: str, commit: str, generated_at: str) -> tuple[dict[str, object], str]:
    artifacts: dict[str, dict[str, object]] = {}
    portable_names: set[str] = set()
    checksum_lines: list[str] = []
    for path in _artifact_files(root):
        relative = _validate_candidate_payload_path(_relative_file(path, root))
        portable_name = _portable_name_key(relative)
        if portable_name in portable_names:
            raise CandidateError(f"colisão de caminho portátil no candidato: {relative}")
        portable_names.add(portable_name)
        size, digest = _sha256(path)
        artifacts[relative] = {"size": size, "sha256": digest}
        checksum_lines.append(f"{digest}  {relative}")
    manifest: dict[str, object] = {
        "format": CANDIDATE_FORMAT,
        "project": PROJECT,
        "version": version,
        "commit": commit,
        "generated_at": generated_at,
        "artifacts": artifacts,
        "artifact_count": len(artifacts),
        "candidate_sha256": None,
        "checksums": "checksums.txt",
        "metadata": {},
    }
    # ``candidate_sha256`` deliberately hashes the canonical manifest with its
    # own field set to null.  This avoids a self-referential digest while still
    # binding every other manifest byte before the final raw-byte digest used by
    # release evidence is calculated.
    manifest["candidate_sha256"] = hashlib.sha256(_json_bytes(manifest)).hexdigest()
    return manifest, "\n".join(checksum_lines) + ("\n" if checksum_lines else "")


def _metadata_identity(root: Path) -> dict[str, dict[str, object]]:
    """Hash the immutable metadata that is covered by candidate.json."""

    result: dict[str, dict[str, object]] = {}
    for name in BOUND_METADATA_NAMES:
        size, digest = _sha256(root / name)
        result[name] = {"size": size, "sha256": digest}
    return result


def _candidate_digest(manifest: dict[str, object]) -> str:
    """Hash a manifest with its self-referential digest cleared."""

    identity = dict(manifest)
    identity["candidate_sha256"] = None
    return hashlib.sha256(_json_bytes(identity)).hexdigest()


def _load_ownership_fragments(
    fragments: object,
    *,
    artifacts: dict[str, dict[str, object]],
) -> dict[str, object]:
    """Load builder-bound facts and bind their outer nodes to exact bytes."""

    documents: list[dict[str, object]] = []
    if fragments is not None:
        if not isinstance(fragments, (list, tuple)):
            raise CandidateError("ownership fragments precisam ser uma lista")
        for fragment in fragments:
            try:
                if isinstance(fragment, (str, Path)):
                    documents.append(release_ownership.load_fragment(Path(fragment)))
                elif isinstance(fragment, dict):
                    documents.append(release_ownership.validate_document(fragment))
                else:
                    raise CandidateError("fragmento de ownership possui tipo inválido")
            except release_ownership.OwnershipError as error:
                raise CandidateError(f"fragmento de ownership inválido: {error}") from error
    try:
        ownership = (
            release_ownership.merge_documents(documents)
            if documents
            else release_ownership.default_document(artifacts)
        )
    except release_ownership.OwnershipError as error:
        raise CandidateError(f"ownership do candidato inválido: {error}") from error
    declared = {
        str(entry["path"]): entry
        for entry in ownership["artifacts"]  # type: ignore[union-attr]
    }
    actual = set(artifacts)
    if set(declared) != actual:
        missing = sorted(actual - set(declared))
        extra = sorted(set(declared) - actual)
        details = []
        if missing:
            details.append(f"faltam: {', '.join(missing)}")
        if extra:
            details.append(f"extras: {', '.join(extra)}")
        raise CandidateError("ownership não cobre exatamente o candidato (" + "; ".join(details) + ")")
    for name, entry in declared.items():
        expected = artifacts[name]
        if entry["size"] != expected["size"] or entry["sha256"] != expected["sha256"]:
            raise CandidateError(f"ownership diverge do artefato do candidato: {name}")
    return ownership


def prepare_candidate(
    *,
    source: Path,
    output: Path,
    version: str,
    commit: str,
    generated_at: str | None = None,
    ownership_fragments: list[Path | str | dict[str, object]] | None = None,
) -> dict[str, object]:
    """Copy already-built artifacts once and emit auditable metadata."""

    _validate_identity(version, commit)
    source = Path(source)
    output = Path(output)
    if output.exists() or output.is_symlink():
        raise CandidateError(f"destino do candidato já existe: {output}")
    if generated_at is None:
        raise CandidateError(
            "generated_at é obrigatório e deve ser ligado ao commit candidato"
        )
    if not isinstance(generated_at, str) or UTC_TIMESTAMP.fullmatch(generated_at) is None:
        raise CandidateError("generated_at precisa ser timestamp UTC canônico")
    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=parent))
    try:
        _copy_source(source, staging)
        manifest, checksums = _manifest_for(staging, version=version, commit=commit, generated_at=generated_at)
        _write_bytes(staging / "checksums.txt", checksums.encode("utf-8"))
        ownership = _load_ownership_fragments(
            ownership_fragments,
            artifacts=manifest["artifacts"],  # type: ignore[arg-type]
        )
        _write_bytes(staging / "ownership.json", release_ownership.canonical_bytes(ownership))
        sbom_files = []
        for index, (name, entry) in enumerate(
            sorted(release_ownership.flatten_entries(ownership).items()), start=1,
        ):
            sbom_files.append({
                "SPDXID": f"SPDXRef-file-{index}",
                "fileName": name,
                "checksums": [{"algorithm": "SHA256", "checksumValue": entry["sha256"]}],
                "licenseConcluded": entry["license_concluded"],
                "copyrightText": entry["copyright_text"],
            })
        _write_bytes(staging / "sbom.spdx.json", _json_bytes({
            "spdxVersion": SPDX_VERSION,
            "dataLicense": "CC0-1.0",
            "SPDXID": "SPDXRef-DOCUMENT",
            "name": f"{PROJECT}-{version}",
            "documentNamespace": f"https://x86qw.x86.com.br/release/{version}/{commit}",
            "creationInfo": {
                "created": generated_at,
                "creators": [SPDX_CREATOR],
            },
            "files": sbom_files,
        }))
        _write_bytes(staging / "provenance.json", _json_bytes({
            "format": 1,
            "project": PROJECT,
            "version": version,
            "subject": {"commit": commit, "source": "immutable-checkout"},
            "builder": {"id": "x86qw/release-candidate", "rebuild": False},
            "materials": [{"path": name, **metadata} for name, metadata in sorted(manifest["artifacts"].items())],
        }))
        # The three deterministic metadata documents are independent of the
        # candidate manifest digest.  Hash them before writing candidate.json
        # so the signed manifest identity covers their exact bytes as well as
        # the built payloads.
        manifest["metadata"] = _metadata_identity(staging)
        manifest["candidate_sha256"] = _candidate_digest(manifest)
        _write_bytes(staging / "candidate.json", _json_bytes(manifest))
        try:
            # The destination is claimed with mkdir semantics; a directory
            # created after the preflight is never replaced by this prepare.
            shutil.copytree(staging, output, dirs_exist_ok=False)
        except FileExistsError as error:
            raise CandidateError(
                f"destino do candidato já existe ou foi criado concorrentemente: {output}"
            ) from error
        return manifest
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _load_json(path: Path) -> dict[str, object]:
    _regular_file(path, label="metadata do candidato")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_pairs)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CandidateError(f"metadata do candidato inválida: {path}") from error
    if not isinstance(value, dict):
        raise CandidateError(f"metadata do candidato precisa ser objeto: {path}")
    return value


def _manifest_sha256(candidate: Path) -> str:
    """Hash the exact candidate manifest bytes used by native evidence."""

    return _sha256(Path(candidate) / "candidate.json")[1]


def _validate_signature(value: object, *, label: str = "assinatura") -> None:
    """Validate the shape of an offline signature without pretending to verify it.

    The signing ceremony owns the private key and cryptographic verification is
    performed by the trust metadata gate.  This boundary still rejects empty,
    free-form or malformed signature records before promotion.
    """

    if not isinstance(value, dict):
        raise CandidateError(f"{label} ausente ou inválida")
    if set(value) != {"keyid", "sig"}:
        raise CandidateError(f"{label} contém campos desconhecidos ou ausentes")
    keyid = value.get("keyid")
    signature = value.get("sig")
    if (
        not isinstance(keyid, str)
        or HEX64.fullmatch(keyid) is None
        or not isinstance(signature, str)
        or BASE64URL.fullmatch(signature) is None
    ):
        raise CandidateError(f"{label} não possui keyid/sig válidos")


def _validate_evidence_signatures(evidence: dict[str, object]) -> None:
    """Accept canonical threshold signatures or explicit legacy singular form."""

    fields = set(evidence)
    if fields == EVIDENCE_FIELDS | {"signature"}:
        _validate_signature(evidence.get("signature"), label="assinatura da evidência")
        return
    if fields == EVIDENCE_FIELDS | {"signatures"}:
        signatures = evidence.get("signatures")
        if not isinstance(signatures, list) or not signatures:
            raise CandidateError("assinaturas da evidência ausentes ou inválidas")
        for index, signature in enumerate(signatures):
            _validate_signature(signature, label=f"assinatura da evidência #{index + 1}")
        return
    raise CandidateError("evidência contém campos desconhecidos ou ausentes")


def _validate_sbom(
    sbom: dict[str, object],
    *,
    ownership: dict[str, object],
    version: str,
    commit: str,
    generated_at: str,
) -> None:
    if set(sbom) != {
        "spdxVersion", "dataLicense", "SPDXID", "name", "documentNamespace",
        "creationInfo", "files",
    }:
        raise CandidateError("SBOM contém campos desconhecidos ou ausentes")
    if type(sbom.get("spdxVersion")) is not str or sbom.get("spdxVersion") != SPDX_VERSION:
        raise CandidateError("SBOM SPDX inválido")
    if (
        sbom.get("SPDXID") != "SPDXRef-DOCUMENT"
        or sbom.get("dataLicense") != "CC0-1.0"
        or sbom.get("name") != f"{PROJECT}-{version}"
        or sbom.get("documentNamespace") != f"https://x86qw.x86.com.br/release/{version}/{commit}"
    ):
        raise CandidateError("SBOM não corresponde à identidade do candidato")
    creation = sbom.get("creationInfo")
    if (
        not isinstance(creation, dict)
        or set(creation) != {"created", "creators"}
        or creation.get("created") != generated_at
        or not isinstance(creation.get("creators"), list)
        or creation.get("creators") != [SPDX_CREATOR]
    ):
        raise CandidateError("SBOM possui creationInfo SPDX inválido")
    files = sbom.get("files")
    if not isinstance(files, list):
        raise CandidateError("SBOM sem lista de arquivos")
    ownership_entries = release_ownership.flatten_entries(ownership)
    seen: set[str] = set()
    seen_ids: set[str] = {"SPDXRef-DOCUMENT"}
    for item in files:
        if not isinstance(item, dict):
            raise CandidateError("SBOM contém arquivo inválido")
        if set(item) != {
            "SPDXID", "fileName", "checksums", "licenseConcluded", "copyrightText",
        }:
            raise CandidateError("SBOM contém campos de arquivo desconhecidos ou ausentes")
        spdx_id = item.get("SPDXID")
        if (
            not isinstance(spdx_id, str)
            or not re.fullmatch(r"SPDXRef-[A-Za-z0-9.-]+", spdx_id)
            or spdx_id in seen_ids
        ):
            raise CandidateError("SBOM contém SPDXID de arquivo inválido ou duplicado")
        seen_ids.add(spdx_id)
        name = item.get("fileName")
        if not isinstance(name, str) or name not in ownership_entries:
            raise CandidateError("SBOM contém caminho de arquivo inválido ou não declarado")
        if name in seen:
            raise CandidateError("SBOM não corresponde aos artefatos do candidato")
        seen.add(name)
        checksums = item.get("checksums")
        if not isinstance(checksums, list) or len(checksums) != 1:
            raise CandidateError(f"SBOM sem checksum único para {name}")
        checksum = checksums[0]
        if (
            not isinstance(checksum, dict)
            or set(checksum) != {"algorithm", "checksumValue"}
            or checksum.get("algorithm") != "SHA256"
        ):
            raise CandidateError(f"SBOM usa algoritmo inválido para {name}")
        entry = ownership_entries[name]
        if checksum.get("checksumValue") != entry.get("sha256"):
            raise CandidateError(f"SBOM diverge do artefato {name}")
        license_concluded = item.get("licenseConcluded")
        copyright_text = item.get("copyrightText")
        expected_license = entry.get("license_concluded")
        expected_copyright = entry.get("copyright_text")
        if (
            not isinstance(license_concluded, str)
            or license_concluded not in SPDX_LICENSES
            or license_concluded != expected_license
            or not isinstance(copyright_text, str)
            or copyright_text != expected_copyright
        ):
            raise CandidateError(f"SBOM possui licença/copyright incompatível para {name}")
    if seen != set(ownership_entries):
        raise CandidateError("SBOM não lista exatamente o ownership declarado")


def _validate_provenance(
    provenance: dict[str, object],
    *,
    artifacts: dict[str, dict[str, object]],
    version: str,
    commit: str,
) -> None:
    if set(provenance) != {"format", "project", "version", "subject", "builder", "materials"}:
        raise CandidateError("proveniência contém campos desconhecidos ou ausentes")
    if (
        type(provenance.get("format")) is not int
        or provenance.get("format") != FORMAT
        or provenance.get("project") != PROJECT
        or provenance.get("version") != version
    ):
        raise CandidateError("proveniência não corresponde à identidade do candidato")
    subject = provenance.get("subject")
    if (
        not isinstance(subject, dict)
        or set(subject) != {"commit", "source"}
        or subject.get("commit") != commit
        or subject.get("source") != "immutable-checkout"
    ):
        raise CandidateError("proveniência não corresponde ao commit do candidato")
    builder = provenance.get("builder")
    if (
        not isinstance(builder, dict)
        or set(builder) != {"id", "rebuild"}
        or builder.get("id") != "x86qw/release-candidate"
        or builder.get("rebuild") is not False
    ):
        raise CandidateError("proveniência de builder inválida")
    materials = provenance.get("materials")
    if not isinstance(materials, list):
        raise CandidateError("proveniência sem materiais")
    seen: set[str] = set()
    for material in materials:
        if not isinstance(material, dict):
            raise CandidateError("material de proveniência inválido")
        if set(material) != {"path", "size", "sha256"}:
            raise CandidateError("material de proveniência contém campos desconhecidos ou ausentes")
        name = material.get("path")
        if not isinstance(name, str) or name != _validate_artifact_name(name):
            raise CandidateError("material de proveniência possui caminho inválido")
        if name in seen or name not in artifacts:
            raise CandidateError("proveniência não corresponde aos artefatos do candidato")
        seen.add(name)
        expected = artifacts[name]
        if material.get("size") != expected.get("size") or material.get("sha256") != expected.get("sha256"):
            raise CandidateError(f"proveniência diverge do artefato {name}")
    if seen != set(artifacts):
        raise CandidateError("proveniência não lista exatamente os artefatos do candidato")


def _validate_platforms(
    platforms: object,
    *,
    identity: dict[str, str],
) -> None:
    if not isinstance(platforms, dict) or not platforms:
        raise CandidateError("evidência completa sem plataformas")
    if set(platforms) != set(REQUIRED_NATIVE_PLATFORMS):
        missing = sorted(set(REQUIRED_NATIVE_PLATFORMS) - set(platforms))
        extra = sorted(set(platforms) - set(REQUIRED_NATIVE_PLATFORMS))
        details: list[str] = []
        if missing:
            details.append(f"faltam: {', '.join(missing)}")
        if extra:
            details.append(f"não exigidas: {', '.join(extra)}")
        raise CandidateError(
            "evidência completa não cobre exatamente as plataformas nativas "
            + " (" + "; ".join(details) + ")"
        )
    for platform_name, report in platforms.items():
        if not isinstance(platform_name, str) or PLATFORM_NAME.fullmatch(platform_name) is None:
            raise CandidateError("evidência possui plataforma inválida")
        if not isinstance(report, dict):
            raise CandidateError(f"evidência da plataforma {platform_name} inválida")
        expected_fields = {
            "format", "project", "status", "platform", "recorded_at", "candidate",
            "environment", "runtime_executed", "cases", "secrets", "signature",
        }
        if platform_name == "macOS-ARM64":
            expected_fields.add("hardware")
        if set(report) != expected_fields:
            raise CandidateError(f"evidência da plataforma {platform_name} possui campos inválidos")
        if (
            type(report.get("format")) is not int
            or report.get("format") != NATIVE_EVIDENCE_FORMAT
            or report.get("project") != PROJECT
            or report.get("status") != "complete"
            or report.get("platform") != platform_name
            or not isinstance(report.get("recorded_at"), str)
            or UTC_TIMESTAMP.fullmatch(report["recorded_at"]) is None
            or report.get("candidate") != identity
            or report.get("runtime_executed") is not True
            or report.get("secrets") != "redacted"
        ):
            raise CandidateError(f"evidência da plataforma {platform_name} não corresponde ao candidato")
        try:
            validate_environment(report.get("environment"), platform=platform_name)
            validate_hardware(report.get("hardware"), platform=platform_name)
            validate_cases(report.get("cases"))
        except NativeEvidenceError as error:
            raise CandidateError(str(error)) from error
        if report.get("signature") is not None:
            raise CandidateError(
                f"evidência da plataforma {platform_name} não pode ter assinatura individual"
            )


def _validate_release_evidence(
    evidence: dict[str, object],
    *,
    version: str,
    commit: str,
    manifest_sha256: str,
    allow_pending_evidence: bool,
) -> None:
    if (
        type(evidence.get("format")) is not int
        or evidence.get("format") != FORMAT
        or evidence.get("project") != PROJECT
    ):
        raise CandidateError("identidade da evidência inválida")
    status = evidence.get("status")
    if status == "pending" and allow_pending_evidence:
        # This is only for the native evidence producer while it is collecting
        # platform reports.  It can never reach promote_candidate.
        if set(evidence) != EVIDENCE_FIELDS | {"signature"}:
            raise CandidateError("evidência pendente possui campos inválidos")
        if evidence.get("version") != version or evidence.get("commit") != commit:
            raise CandidateError("evidência pendente diverge do candidato")
        if evidence.get("candidate") != "candidate.json" or evidence.get("platforms") != {}:
            raise CandidateError("identidade da evidência pendente inválida")
        if evidence.get("signature") is not None:
            raise CandidateError("evidência pendente não pode ter assinatura")
        return
    if status != "complete":
        raise CandidateError("evidência precisa estar complete para validação/promoção")
    _validate_evidence_signatures(evidence)
    if evidence.get("version") != version or evidence.get("commit") != commit:
        raise CandidateError("evidência diverge da identidade do candidato")
    identity = {
        "version": version,
        "commit": commit,
        "manifest_sha256": manifest_sha256,
    }
    if evidence.get("candidate") != identity:
        raise CandidateError("evidência não está vinculada ao manifest do candidato")
    _validate_platforms(evidence.get("platforms"), identity=identity)


def verify_candidate(
    candidate: Path,
    *,
    allow_pending_evidence: bool = False,
    trust_root: Path | None = None,
) -> dict[str, object]:
    """Verify one candidate and all metadata bound to its exact bytes.

    ``allow_pending_evidence`` is a narrow compatibility escape hatch for the
    native evidence producer while it is collecting platform reports.  It is
    intentionally not exposed by :func:`promote_candidate`.
    """

    candidate = Path(candidate)
    if candidate.is_symlink() or not candidate.is_dir():
        raise CandidateError(f"candidato ausente ou inseguro: {candidate}")
    manifest = _load_json(candidate / "candidate.json")
    if set(manifest) != MANIFEST_FIELDS:
        raise CandidateError("manifest contém campos desconhecidos ou ausentes")
    if (
        type(manifest.get("format")) is not int
        or manifest.get("format") != CANDIDATE_FORMAT
        or manifest.get("project") != PROJECT
    ):
        raise CandidateError("identidade do candidato inválida")
    version = manifest.get("version")
    commit = manifest.get("commit")
    if not isinstance(version, str) or not isinstance(commit, str):
        raise CandidateError("identidade do candidato incompleta")
    _validate_identity(version, commit)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise CandidateError("candidato sem artefatos")
    if manifest.get("checksums") != "checksums.txt":
        raise CandidateError("manifest aponta para checksums inválido")
    generated_at = manifest.get("generated_at")
    if not isinstance(generated_at, str) or UTC_TIMESTAMP.fullmatch(generated_at) is None:
        raise CandidateError("manifest sem generated_at UTC canônico")
    candidate_sha256 = manifest.get("candidate_sha256")
    if not isinstance(candidate_sha256, str) or HEX64.fullmatch(candidate_sha256) is None:
        raise CandidateError("candidate_sha256 do manifest inválido")
    expected_candidate_sha256 = _candidate_digest(manifest)
    if candidate_sha256 != expected_candidate_sha256:
        raise CandidateError("candidate_sha256 do manifest diverge")
    seen: set[str] = set()
    portable_names: set[str] = set()
    normalized_artifacts: dict[str, dict[str, object]] = {}
    for raw_name, expected in artifacts.items():
        name = _validate_artifact_name(raw_name)
        _validate_candidate_payload_path(name)
        if raw_name != name:
            raise CandidateError(f"caminho de artefato não canônico: {raw_name!r}")
        portable_name = _portable_name_key(name)
        if portable_name in portable_names:
            raise CandidateError(f"colisão de caminho portátil no candidato: {name}")
        portable_names.add(portable_name)
        path = candidate.joinpath(*PurePosixPath(name).parts)
        if path.is_symlink() or not path.is_file():
            raise CandidateError(f"artefato ausente ou inseguro: {name}")
        if (
            not isinstance(expected, dict)
            or set(expected) != {"size", "sha256"}
            or type(expected.get("size")) is not int
            or expected.get("size", -1) < 0
            or not isinstance(expected.get("sha256"), str)
            or HEX64.fullmatch(expected["sha256"]) is None
        ):
            raise CandidateError(f"metadados inválidos para artefato: {name}")
        size, digest = _sha256(path)
        if size != expected["size"] or digest != expected["sha256"]:
            raise CandidateError(f"artefato do candidato diverge: {name}")
        seen.add(name)
        normalized_artifacts[name] = expected
    if type(manifest.get("artifact_count")) is not int or manifest.get("artifact_count") != len(artifacts):
        raise CandidateError("contagem de artefatos do candidato diverge")
    actual: set[str] = set()
    for path in _artifact_files(candidate):
        relative = PurePosixPath(*path.relative_to(candidate).parts).as_posix()
        if relative not in METADATA_NAMES:
            actual.add(relative)
    if actual != seen:
        raise CandidateError(f"arquivos não registrados no candidato: {sorted(actual ^ seen)}")
    checksums_path = candidate / "checksums.txt"
    try:
        checksum_lines = checksums_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise CandidateError("checksums.txt do candidato está ausente ou inválido") from error
    declared: dict[str, str] = {}
    for line in checksum_lines:
        parts = line.split("  ", 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise CandidateError("checksums.txt contém uma linha inválida")
        name = _validate_artifact_name(parts[1])
        if name in declared:
            raise CandidateError("checksums.txt contém um artefato duplicado")
        declared[name] = parts[0]
    expected_checksums = {
        name: metadata["sha256"]
        for name, metadata in normalized_artifacts.items()
    }
    if declared != expected_checksums:
        raise CandidateError("checksums.txt diverge do manifest do candidato")
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict) or set(metadata) != set(BOUND_METADATA_NAMES):
        raise CandidateError("manifest sem metadata imutável completa")
    for name in BOUND_METADATA_NAMES:
        expected = metadata.get(name)
        if (
            not isinstance(expected, dict)
            or set(expected) != {"size", "sha256"}
            or type(expected.get("size")) is not int
            or expected.get("size", -1) < 0
            or not isinstance(expected.get("sha256"), str)
            or HEX64.fullmatch(expected["sha256"]) is None
        ):
            raise CandidateError(f"metadados vinculados inválidos: {name}")
        path = candidate / name
        size, digest = _sha256(path)
        if size != expected["size"] or digest != expected["sha256"]:
            raise CandidateError(f"metadata imutável diverge do manifest: {name}")
    ownership_raw = _load_json(candidate / "ownership.json")
    ownership = _load_ownership_fragments(
        [ownership_raw],
        artifacts=normalized_artifacts,
    )
    sbom = _load_json(candidate / "sbom.spdx.json")
    _validate_sbom(
        sbom,
        ownership=ownership,
        version=version,
        commit=commit,
        generated_at=generated_at,
    )
    provenance = _load_json(candidate / "provenance.json")
    _validate_provenance(
        provenance,
        artifacts=normalized_artifacts,
        version=version,
        commit=commit,
    )
    evidence_path = candidate / "release-evidence.json"
    if evidence_path.exists() or evidence_path.is_symlink():
        evidence = _load_json(evidence_path)
        _validate_release_evidence(
            evidence,
            version=version,
            commit=commit,
            manifest_sha256=_manifest_sha256(candidate),
            allow_pending_evidence=allow_pending_evidence,
        )
    if trust_root is not None:
        root_path = _regular_file(Path(trust_root), label="root de trust")
        if not evidence_path.is_file() or evidence_path.is_symlink():
            raise CandidateError("trust_root exige release-evidence.json explícito")
        try:
            root_bytes = root_path.read_bytes()
            evidence_bytes = (candidate / "release-evidence.json").read_bytes()
            verify_release_evidence(
                root_bytes,
                evidence_bytes,
                expected_identity={
                    "version": version,
                    "commit": commit,
                    "manifest_sha256": _manifest_sha256(candidate),
                },
            )
        except (OSError, TrustError) as error:
            raise CandidateError(
                f"assinatura da evidência não foi autenticada pelo root fornecido: {error}"
            ) from error
    return manifest


def promote_candidate(
    candidate: Path,
    destination: Path,
    *,
    trust_root: Path | None = None,
) -> dict[str, object]:
    """Promote a verified candidate without replacing a destination.

    Without a trust root, local promotion authenticates the immutable candidate
    identity and its byte-bound metadata only. A trust root remains an opt-in
    compatibility path when an explicit signed native-evidence document is
    supplied. Verification is performed before any destination path is created.
    The destination root is then claimed with an atomic ``mkdir``; copying
    happens only inside that owned root and never replaces a path.
    """

    manifest = verify_candidate(candidate, trust_root=trust_root)
    manifest_digest = _manifest_sha256(Path(candidate))
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise CandidateError(f"destino de promoção já existe: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=destination.parent))
    published = False
    try:
        for path in _artifact_files(Path(candidate)):
            relative = PurePosixPath(*path.relative_to(Path(candidate)).parts).as_posix()
            target = staging.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target, follow_symlinks=False)
            os.chmod(target, 0o644)
        # Revalidate the exact bytes that will be promoted.  This closes the
        # source mutation window between the initial preflight and staging
        # copy; a changed candidate can never become a trusted destination.
        current_manifest = verify_candidate(Path(candidate), trust_root=trust_root)
        if current_manifest != manifest or _manifest_sha256(Path(candidate)) != manifest_digest:
            raise CandidateError("candidato mudou durante a preparação da promoção")
        verify_candidate(staging, trust_root=trust_root)
        try:
            _atomic_publish_no_replace(staging, destination)
            published = True
        except FileExistsError as error:
            raise CandidateError(
                f"destino de promoção já existe ou foi criado concorrentemente: {destination}"
            ) from error
        except OSError as error:
            raise CandidateError(
                f"não foi possível publicar o candidato atomicamente: {destination}"
            ) from error
        verify_candidate(destination, trust_root=trust_root)
    except Exception:
        # Once the root was atomically published, preserve it for inspection;
        # removing a committed tree after a post-publish read failure could
        # destroy a valid release.  Before publication the staging tree is
        # private and is removed by the finally block below.
        raise
    finally:
        if not published and staging.exists():
            shutil.rmtree(staging)
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="prepara e valida candidato x86QW sem rebuild")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="copiar builds já produzidos e gerar metadata")
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--version", required=True)
    prepare.add_argument("--commit", required=True)
    prepare.add_argument("--generated-at", required=True)
    prepare.add_argument(
        "--ownership-fragment",
        action="append",
        type=Path,
        required=True,
        help="fragmento JSON de ownership produzido por um builder (pode repetir)",
    )
    verify = commands.add_parser("verify", help="verificar candidato sem reconstruí-lo")
    verify.add_argument("candidate", type=Path)
    verify.add_argument(
        "--allow-pending-evidence",
        action="store_true",
        help="aceitar um release-evidence.json pending legado, se fornecido",
    )
    verify.add_argument(
        "--trust-root",
        type=Path,
        help="root público para autenticar criptograficamente a evidência completa",
    )
    promote = commands.add_parser("promote", help="promover candidato sem sobrescrever destino")
    promote.add_argument("candidate", type=Path)
    promote.add_argument("destination", type=Path)
    promote.add_argument(
        "--trust-root",
        type=Path,
        help="root público opcional para autenticar evidência legada explícita",
    )
    rehearse = commands.add_parser(
        "rehearse",
        help="copiar um candidato verificado para uma árvore local sem publicar",
    )
    rehearse.add_argument("candidate", type=Path)
    rehearse.add_argument("destination", type=Path)
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
                ownership_fragments=options.ownership_fragment,
            )
        elif options.command == "verify":
            verify_candidate(
                options.candidate,
                allow_pending_evidence=options.allow_pending_evidence,
                trust_root=options.trust_root,
            )
        elif options.command == "rehearse":
            promote_candidate(options.candidate, options.destination)
        else:
            promote_candidate(
                options.candidate,
                options.destination,
                trust_root=options.trust_root,
            )
    except CandidateError as error:
        print(f"[ERRO] {error}")
        return 1
    print(f"[OK] Candidato {options.command} validado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
