"""Candidate-owned native smoke entrypoint.

The workflow copies this stdlib-only file into the immutable candidate before
the candidate manifest is written.  It validates candidate bytes first and
then dispatches one literal, closed case to a candidate-owned runtime.  The
driver never downloads, checks out, or invokes a shell command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path, PurePosixPath


PROJECT = "x86qw"
MAX_JSON_BYTES = 1024 * 1024
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CANONICAL_CASES = (
    "install-clean-space-unicode",
    "install-existing-space-unicode",
    "client-stable-window-map-exit",
    "client-nightly-window-map-exit",
    "game-ktx",
    "game-final-arena",
    "game-pro-x",
    "game-team-fortress",
    "game-td2",
    "mvdsv-mvd",
    "qtv-stream",
    "qwfwd-forward",
    "lifecycle-update",
    "lifecycle-upgrade",
    "lifecycle-verify",
    "lifecycle-repair",
    "lifecycle-cleanup",
    "lifecycle-uninstall",
)
_BOUND_METADATA = frozenset({
    "checksums.txt", "ownership.json", "sbom.spdx.json", "provenance.json",
    "mirrors.json", "candidate.json", "release-evidence.json",
})


class CandidateCaseError(RuntimeError):
    """Candidate bytes or the closed case protocol are not trustworthy."""


@dataclass(frozen=True)
class CandidateArtifact:
    name: str
    path: Path
    size: int
    sha256: str


@dataclass(frozen=True)
class Candidate:
    root: Path
    version: str
    commit: str
    artifacts: Mapping[str, CandidateArtifact]


@dataclass(frozen=True)
class PreparedCase:
    """A fully resolved command whose executable belongs to the candidate."""

    executable: Path
    argv: tuple[str, ...]
    cwd: Path
    artifact: CandidateArtifact
    shell: bool = False


_CLIENT_CASES = {
    "client-stable-window-map-exit": ("stable", None),
    "client-nightly-window-map-exit": ("nightly", None),
    "game-ktx": ("stable", "ktx"),
    "game-final-arena": ("stable", "final-arena"),
    "game-pro-x": ("stable", "pro-x"),
    "game-team-fortress": ("stable", "team-fortress"),
    "game-td2": ("stable", "td2"),
}
_SERVICE_SUFFIXES = {
    "mvdsv-mvd": ("runtime/servers/mvdsv/", "runtime/macos-arm64/mvdsv"),
    "qtv-stream": ("runtime/services/qtv/", "runtime/macos-arm64/qtv"),
    "qwfwd-forward": ("runtime/services/qwfwd/", "runtime/macos-arm64/qwfwd"),
}
_INSTALLER_CASES = {
    "install-clean-space-unicode": ("install",),
    "install-existing-space-unicode": ("install",),
    "lifecycle-update": ("--dry-run", "update"),
    "lifecycle-upgrade": ("--dry-run", "--yes", "upgrade"),
    "lifecycle-verify": ("--json", "verify"),
    "lifecycle-repair": ("--dry-run", "repair"),
    "lifecycle-cleanup": ("cleanup",),
    "lifecycle-uninstall": ("uninstall",),
}
_INSTALLATION_TARGET = "instalação espaço"
_STATE_FILENAME = "lifecycle-state.json"
_STATE_FORMAT = 1
_STATES = frozenset({"clean", "installed", "uninstalled"})


def validate_case_name(value: object) -> str:
    if not isinstance(value, str) or value not in CANONICAL_CASES:
        raise CandidateCaseError(f"caso nativo desconhecido: {value!r}")
    return value


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CandidateCaseError(f"JSON contém chave duplicada: {key}")
        result[key] = value
    return result


def _read_manifest(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise CandidateCaseError("manifest do candidato ausente ou inseguro")
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise CandidateCaseError("manifest do candidato excede 1 MiB")
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicates,
        )
    except CandidateCaseError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CandidateCaseError("manifest do candidato inválido") from error
    if not isinstance(value, dict):
        raise CandidateCaseError("manifest do candidato precisa ser objeto")
    return value


def _safe_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise CandidateCaseError("caminho de artifact inválido")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CandidateCaseError("caminho de artifact inseguro")
    return path.as_posix()


def _digest(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
    except OSError as error:
        raise CandidateCaseError(f"não foi possível ler artifact: {path}") from error
    return size, digest.hexdigest()


def load_candidate(root: Path) -> Candidate:
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise CandidateCaseError(f"candidato ausente ou inseguro: {root}")
    manifest = _read_manifest(root / "candidate.json")
    if manifest.get("project") != PROJECT:
        raise CandidateCaseError("manifest não pertence ao projeto x86qw")
    version = manifest.get("version")
    commit = manifest.get("commit")
    if not isinstance(version, str) or not version:
        raise CandidateCaseError("versão do candidato inválida")
    if not isinstance(commit, str) or SHA40.fullmatch(commit) is None:
        raise CandidateCaseError("commit do candidato inválido")
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, Mapping) or not raw_artifacts:
        raise CandidateCaseError("manifest não declara artifacts")
    artifacts: dict[str, CandidateArtifact] = {}
    for raw_name, raw_identity in raw_artifacts.items():
        name = _safe_relative(raw_name)
        if not isinstance(raw_identity, Mapping) or set(raw_identity) != {"size", "sha256"}:
            raise CandidateCaseError(f"identidade de artifact inválida: {name}")
        size = raw_identity.get("size")
        sha256 = raw_identity.get("sha256")
        if type(size) is not int or size < 0 or not isinstance(sha256, str) or SHA256.fullmatch(sha256) is None:
            raise CandidateCaseError(f"identidade de artifact inválida: {name}")
        path = root.joinpath(*PurePosixPath(name).parts)
        current = path
        while current != root:
            if current.is_symlink():
                raise CandidateCaseError(f"artifact usa symlink: {name}")
            current = current.parent
        if not path.is_file() or path.is_symlink():
            raise CandidateCaseError(f"artifact ausente ou inseguro: {name}")
        actual_size, actual_sha256 = _digest(path)
        if (actual_size, actual_sha256) != (size, sha256):
            raise CandidateCaseError(f"bytes do artifact divergem: {name}")
        artifacts[name] = CandidateArtifact(name, path, size, sha256)

    registered = set(artifacts)
    actual = {
        PurePosixPath(*path.relative_to(root).parts).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    unexpected = actual - registered - _BOUND_METADATA
    if unexpected:
        raise CandidateCaseError(f"candidate contém artifact não registrado: {sorted(unexpected)[0]}")
    return Candidate(root=root, version=version, commit=commit, artifacts=artifacts)


def _artifact_matching(candidate: Candidate, predicate: object, label: str) -> CandidateArtifact:
    matches = [artifact for name, artifact in candidate.artifacts.items() if predicate(name)]  # type: ignore[operator]
    if len(matches) != 1:
        raise CandidateCaseError(
            f"artefato nativo ausente ou ambíguo ({label}): {len(matches)} encontrados",
        )
    return matches[0]


def _stable_macos_release(candidate: Candidate) -> str:
    matches = []
    for artifact in candidate.artifacts.values():
        parts = PurePosixPath(artifact.name).parts
        if (
            len(parts) == 7
            and parts[:4] == ("runtime", "clients", "ezquake", "stable")
            and parts[5] == "macos-universal"
            and parts[6] == "ezQuake-macOS-universal.zip"
        ):
            matches.append(parts[4])
    if len(matches) != 1:
        raise CandidateCaseError(
            "artefato nativo ausente ou ambíguo (ezQuake stable macOS universal): "
            f"{len(matches)} encontrados",
        )
    return matches[0]


def _snapshot_artifact(artifact: CandidateArtifact, destination: Path) -> Path:
    """Copy one candidate artifact into scratch without losing its identity."""

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = (artifact.size, artifact.sha256)
    if _digest(artifact.path) != expected:
        raise CandidateCaseError(f"bytes do artifact divergiram antes do snapshot: {artifact.path}")
    copied_digest = hashlib.sha256()
    copied_size = 0
    try:
        with artifact.path.open("rb") as source, destination.open("xb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
                copied_size += len(chunk)
                copied_digest.update(chunk)
        if (copied_size, copied_digest.hexdigest()) != expected:
            raise CandidateCaseError(f"snapshot do artifact divergiu: {artifact.path}")
        if _digest(artifact.path) != expected:
            raise CandidateCaseError(f"bytes do artifact mudaram durante o snapshot: {artifact.path}")
    except CandidateCaseError:
        raise
    except OSError as error:
        raise CandidateCaseError(f"não foi possível criar snapshot do artifact: {artifact.path}") from error
    return destination


def _zip_member_name(name: str) -> str:
    if not isinstance(name, str) or not name or name.endswith("/") or "\\" in name:
        raise CandidateCaseError("membro ZIP nativo inválido")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CandidateCaseError("membro ZIP nativo inseguro")
    return path.as_posix()


def _extract_zip_member(archive: CandidateArtifact, suffix: str, scratch: Path, label: str) -> Path:
    destination_root = Path(scratch) / "extracted" / label
    destination_root.mkdir(parents=True, exist_ok=True)
    snapshot = _snapshot_artifact(archive, destination_root / "archive.zip")
    try:
        with zipfile.ZipFile(snapshot) as bundle:
            members = [_zip_member_name(name) for name in bundle.namelist()]
            selected = [name for name in members if name.endswith(suffix)]
            if len(selected) != 1:
                raise CandidateCaseError(
                    f"membro nativo ausente ou ambíguo ({suffix}): {len(selected)} encontrados",
                )
            member = selected[0]
            output = destination_root / Path(*PurePosixPath(member).parts).name
            with bundle.open(member, "r") as source, output.open("xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
    except CandidateCaseError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise CandidateCaseError(f"archive nativo inválido: {archive.path}") from error
    os.chmod(output, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return output


def _installer_command(
    candidate: Candidate,
    arguments: tuple[str, ...],
    scratch: Path,
    state_root: Path,
) -> PreparedCase:
    archive = _artifact_matching(
        candidate,
        lambda name: name.startswith("installer/") and name.endswith(".zip"),
        "installer",
    )
    pyz = _extract_zip_member(archive, "/x86qw.pyz", scratch, "installer")
    if arguments == ("install",):
        arguments = (
            "--platform", "macos",
            "--channel", "stable",
            "--release", _stable_macos_release(candidate),
            "--without-components",
            "install",
        )
    # The target is deliberately shared by all installer cases and contains a
    # space plus Unicode. The first install owns creation of the target.
    target = Path(state_root) / _INSTALLATION_TARGET
    Path(state_root).mkdir(parents=True, exist_ok=True)
    argv = (sys.executable, str(pyz), *arguments, str(target))
    return PreparedCase(executable=pyz, argv=argv, cwd=Path(scratch), artifact=archive)


def _client_command(candidate: Candidate, channel: str, game: str | None, scratch: Path) -> PreparedCase:
    archive = _artifact_matching(
        candidate,
        lambda name: (
            name.startswith(f"runtime/clients/ezquake/{channel}/")
            and "/macos-universal/" in name
            and name.endswith(".zip")
        ),
        f"ezQuake {channel} macOS universal",
    )
    binary = _extract_zip_member(archive, "Contents/MacOS/ezQuake", scratch, f"client-{channel}")
    args: tuple[str, ...] = ("-nosound", "-window")
    if game is not None:
        args += ("-game", game)
    args += ("+map", "dm6", "+quit")
    return PreparedCase(
        executable=binary, argv=(str(binary), *args), cwd=Path(scratch), artifact=archive,
    )


def _service_command(candidate: Candidate, case: str, scratch: Path) -> PreparedCase:
    prefix, suffix = _SERVICE_SUFFIXES[case]
    artifact = _artifact_matching(
        candidate,
        lambda name: name.startswith(prefix) and name.endswith(suffix),
        case,
    )
    # Runtime files are copied into candidates with mode 0644.  The runner
    # stages and makes the exact bytes executable immediately before launch.
    staged = Path(scratch) / "services" / Path(suffix).name
    staged.parent.mkdir(parents=True, exist_ok=True)
    _snapshot_artifact(artifact, staged)
    os.chmod(staged, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return PreparedCase(
        executable=artifact.path,
        argv=(str(staged), "-version"),
        cwd=Path(scratch),
        artifact=artifact,
    )


def build_case_command(
    *, candidate: Candidate, case: str, scratch: Path, state_root: Path | None = None,
) -> PreparedCase:
    """Resolve one canonical case without guessing an executable by pathname."""

    validate_case_name(case)
    scratch = Path(scratch).absolute()
    state_root = scratch if state_root is None else Path(state_root).absolute()
    if scratch == candidate.root or candidate.root in scratch.parents:
        raise CandidateCaseError("scratch nativo precisa ficar fora do candidato")
    if state_root == candidate.root or candidate.root in state_root.parents:
        raise CandidateCaseError("estado nativo precisa ficar fora do candidato")
    scratch.mkdir(parents=True, exist_ok=True)
    if case in _SERVICE_SUFFIXES:
        return _service_command(candidate, case, scratch)
    if case in _CLIENT_CASES:
        channel, game = _CLIENT_CASES[case]
        return _client_command(candidate, channel, game, scratch)
    if case in _INSTALLER_CASES:
        arguments = _INSTALLER_CASES[case]
        return _installer_command(candidate, arguments, scratch, state_root)
    raise CandidateCaseError(f"caso nativo sem dispatch fechado: {case}")


def _state_path(state_root: Path) -> Path:
    return Path(state_root) / _STATE_FILENAME


def _read_state(state_root: Path) -> str | None:
    path = _state_path(state_root)
    if not path.exists():
        if path.is_symlink():
            raise CandidateCaseError("estado do lifecycle usa symlink")
        return None
    if path.is_symlink() or not path.is_file():
        raise CandidateCaseError("estado do lifecycle é inseguro")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CandidateCaseError("estado do lifecycle é inválido") from error
    if (
        not isinstance(value, Mapping)
        or set(value) != {"format", "status", "last_case"}
        or value.get("format") != _STATE_FORMAT
        or value.get("status") not in _STATES
        or not isinstance(value.get("last_case"), str)
    ):
        raise CandidateCaseError("estado do lifecycle é inválido")
    return str(value["status"])


def _prepare_state(case: str, state_root: Path) -> str:
    state_root = Path(state_root).absolute()
    state_root.mkdir(parents=True, exist_ok=True)
    status = _read_state(state_root)
    target = state_root / _INSTALLATION_TARGET
    if case == CANONICAL_CASES[0]:
        if status is not None or os.path.lexists(target):
            raise CandidateCaseError("install-clean exige scratch sem instalação anterior")
        return "clean"
    if status != "installed" or target.is_symlink() or not target.is_dir():
        raise CandidateCaseError(f"{case} exige a instalação compartilhada já instalada")
    return "installed"


def _write_state(state_root: Path, *, status: str, case: str) -> None:
    if status not in _STATES:
        raise CandidateCaseError(f"estado de lifecycle desconhecido: {status}")
    path = _state_path(state_root)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = {
        "format": _STATE_FORMAT,
        "status": status,
        "last_case": case,
    }
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise CandidateCaseError("não foi possível persistir estado do lifecycle") from error


def _write_receipt(path: Path, value: Mapping[str, object], *, candidate: Candidate) -> None:
    path = Path(path).absolute()
    if path == candidate.root or candidate.root in path.parents:
        raise CandidateCaseError("recibo nativo precisa ficar fora do candidato")
    if path.exists() or path.is_symlink():
        raise CandidateCaseError(f"recibo nativo já existe: {path}")
    if path.parent.is_symlink():
        raise CandidateCaseError("diretório do recibo nativo usa symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise CandidateCaseError("não foi possível escrever recibo nativo") from error


def _run_case(
    candidate: Candidate,
    case: str,
    scratch: Path,
    *,
    state_root: Path,
    receipt: Path,
) -> int:
    state_before = _prepare_state(case, state_root)
    prepared = build_case_command(
        candidate=candidate,
        case=case,
        scratch=scratch,
        state_root=state_root,
    )
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(scratch),
        "TMPDIR": str(scratch),
        "X86QW_CANDIDATE_ROOT": str(candidate.root),
        "X86QW_CANDIDATE_COMMIT": candidate.commit,
        "X86QW_NATIVE_STATE_ROOT": str(state_root),
    }
    try:
        result = subprocess.run(
            prepared.argv,
            cwd=prepared.cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=prepared.shell,
            timeout=300,
            check=False,
        )
        exit_code = int(result.returncode)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"[ERRO] execução nativa falhou: {case}: {error}", file=sys.stderr)
        exit_code = 127
        result = subprocess.CompletedProcess(prepared.argv, exit_code, b"", str(error).encode())
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    passed = exit_code == 0
    state_after = "uninstalled" if case == CANONICAL_CASES[-1] and passed else "installed"
    if passed:
        _write_state(state_root, status=state_after, case=case)
    _write_receipt(
        receipt,
        {
            "format": 1,
            "project": PROJECT,
            "protocol": "x86qw-native-case-v1",
            "case": case,
            "artifact": {
                "name": prepared.artifact.name,
                "size": prepared.artifact.size,
                "sha256": prepared.artifact.sha256,
            },
            "execution": {
                "status": "passed" if passed else "failed",
                "exit_code": exit_code,
            },
            "state": {"before": state_before, "after": state_after if passed else state_before},
        },
        candidate=candidate,
    )
    return exit_code


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="executor nativo candidato-owned x86QW")
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--scratch-root", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    options = parser.parse_args(arguments)
    try:
        candidate = load_candidate(options.candidate_root)
        validate_case_name(options.case)
        scratch_root = Path(options.scratch_root).absolute()
        if scratch_root == candidate.root or candidate.root in scratch_root.parents:
            raise CandidateCaseError("scratch nativo precisa ficar fora do candidato")
        case_scratch = scratch_root / "cases" / options.case
        return _run_case(
            candidate,
            options.case,
            case_scratch,
            state_root=scratch_root,
            receipt=options.receipt,
        )
    except CandidateCaseError as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1


__all__ = [
    "CANONICAL_CASES", "Candidate", "CandidateArtifact", "CandidateCaseError",
    "PreparedCase", "build_case_command", "load_candidate", "main", "validate_case_name",
]


if __name__ == "__main__":
    raise SystemExit(main())
