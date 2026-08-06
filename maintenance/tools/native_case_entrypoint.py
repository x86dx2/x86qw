"""Candidate-owned native smoke entrypoint.

The workflow copies this stdlib-only file into the immutable candidate before
the candidate manifest is written.  It validates candidate bytes first and
then dispatches one literal, closed case to a candidate-owned runtime.  The
driver never downloads, checks out, or invokes a shell command.
"""

from __future__ import annotations

import hashlib
import json
import argparse
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
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
        artifacts[name] = CandidateArtifact(path, size, sha256)

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
    try:
        with zipfile.ZipFile(archive.path) as bundle:
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


def _installer_command(candidate: Candidate, arguments: tuple[str, ...], scratch: Path) -> PreparedCase:
    archive = _artifact_matching(
        candidate,
        lambda name: name.startswith("installer/") and name.endswith(".zip"),
        "installer",
    )
    pyz = _extract_zip_member(archive, "/x86qw.pyz", scratch, "installer")
    target = Path(scratch) / "installation space"
    target.mkdir(parents=True, exist_ok=True)
    # The target is deliberately outside the immutable candidate and contains
    # a space plus Unicode in the two install cases below.
    if arguments and arguments[0] == "install":
        target = Path(scratch) / "instalação espaço"
    argv = (sys.executable, str(pyz), *arguments, str(target))
    return PreparedCase(executable=pyz, argv=argv, cwd=Path(scratch))


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
    return PreparedCase(executable=binary, argv=(str(binary), *args), cwd=Path(scratch))


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
    shutil.copyfile(artifact.path, staged)
    os.chmod(staged, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return PreparedCase(executable=artifact.path, argv=(str(staged), "-version"), cwd=Path(scratch))


def build_case_command(*, candidate: Candidate, case: str, scratch: Path) -> PreparedCase:
    """Resolve one canonical case without guessing an executable by pathname."""

    validate_case_name(case)
    scratch = Path(scratch).absolute()
    if scratch == candidate.root or candidate.root in scratch.parents:
        raise CandidateCaseError("scratch nativo precisa ficar fora do candidato")
    scratch.mkdir(parents=True, exist_ok=True)
    if case in _SERVICE_SUFFIXES:
        return _service_command(candidate, case, scratch)
    if case in _CLIENT_CASES:
        channel, game = _CLIENT_CASES[case]
        return _client_command(candidate, channel, game, scratch)
    if case in _INSTALLER_CASES:
        arguments = _INSTALLER_CASES[case]
        return _installer_command(candidate, arguments, scratch)
    raise CandidateCaseError(f"caso nativo sem dispatch fechado: {case}")


def _run_case(candidate: Candidate, case: str, scratch: Path) -> int:
    system = platform.system()
    machine = platform.machine().casefold()
    if system != "Darwin" or machine != "arm64":
        print(f"[NOT-RUN] host {system}/{machine} não é macOS arm64", file=sys.stderr)
        return 2
    prepared = build_case_command(candidate=candidate, case=case, scratch=scratch)
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(scratch),
        "TMPDIR": str(scratch),
        "X86QW_CANDIDATE_ROOT": str(candidate.root),
        "X86QW_CANDIDATE_COMMIT": candidate.commit,
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
    except (OSError, subprocess.SubprocessError) as error:
        raise CandidateCaseError(f"execução nativa falhou: {case}") from error
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    return int(result.returncode)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="executor nativo candidato-owned x86QW")
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--case", required=True)
    options = parser.parse_args(arguments)
    try:
        candidate = load_candidate(options.candidate_root)
        with tempfile.TemporaryDirectory(prefix="x86qw-native-case-") as temporary:
            return _run_case(candidate, options.case, Path(temporary))
    except CandidateCaseError as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1


__all__ = [
    "CANONICAL_CASES", "Candidate", "CandidateArtifact", "CandidateCaseError",
    "PreparedCase", "build_case_command", "load_candidate", "main", "validate_case_name",
]


if __name__ == "__main__":
    raise SystemExit(main())
