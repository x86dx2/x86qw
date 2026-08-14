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
import plistlib
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath


PROJECT = "x86qw"
MAX_JSON_BYTES = 1024 * 1024
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CANONICAL_CASES = (
    "install-clean-space-unicode",
    "install-existing-space-unicode",
    "migration-0.7.13-real",
    "client-stable-window-map-exit",
    "client-nightly-window-map-exit",
    "game-ktx",
    "game-ktx-frogbot",
    "game-final-arena",
    "game-pro-x",
    "game-team-fortress",
    "game-td2",
    "mvdsv-mvd",
    "qtv-stream",
    "qwfwd-forward",
    "lifecycle-update",
    "lifecycle-update-apply",
    "lifecycle-upgrade",
    "lifecycle-upgrade-apply",
    "lifecycle-verify",
    "lifecycle-repair",
    "lifecycle-repair-corruption",
    "lifecycle-migrate-apply",
    "lifecycle-cleanup",
    "lifecycle-uninstall",
    "lifecycle-purge",
)
_BOUND_METADATA = frozenset({
    "checksums.txt", "ownership.json", "sbom.spdx.json", "provenance.json",
    "mirrors.json", "candidate.json", "release-evidence.json",
    "evidence-root.json", "release-receipt.json",
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
    "client-stable-window-map-exit": ("stable", (), "dm6"),
    "client-nightly-window-map-exit": ("nightly", (), "dm6"),
    "game-ktx": ("stable", (), "dm6"),
    "game-ktx-frogbot": (
        "stable", (
            "+set", "k_fb_enabled", "1",
            "+set", "k_fb_skill", "5",
            "+set", "k_fb_name_0", "/x86QW",
            "+set", "k_fb_name_team_0", "/x86QW",
            "+set", "k_fb_name_enemy_0", "/x86QW",
            "+set", "k_defmap", "dm6",
            "+set", "k_defmode", "1on1",
            "+exec", "x86qw-native-smoke-frogbot.cfg",
            "+map", "dm6",
        ), "dm6",
    ),
    "game-final-arena": (
        "stable", ("-game", "arena", "+sv_gamedir", "arena", "+sv_progtype", "0"), "23ar-a",
    ),
    "game-pro-x": (
        "stable", ("-game", "prox", "+sv_gamedir", "prox", "+sv_progtype", "0"), "proxmap1",
    ),
    "game-team-fortress": (
        "stable", ("-game", "fortress", "+sv_gamedir", "fortress", "+sv_progtype", "0"), "2fort5r",
    ),
    "game-td2": (
        "stable", ("-game", "td2", "+sv_gamedir", "td2", "+sv_progtype", "0"), "dm6",
    ),
}
_CLIENT_CONTENT = {
    "client-stable-window-map-exit": ("qw", None, "dm6"),
    "client-nightly-window-map-exit": ("qw", None, "dm6"),
    "game-ktx": ("qw", "qw/ktx.pk3", "dm6"),
    "game-ktx-frogbot": ("qw", "qw/ktx.pk3", "dm6"),
    "game-final-arena": ("arena", "arena/arena.pk3", "23ar-a"),
    "game-pro-x": ("prox", "prox/qwprogs.dat", "proxmap1"),
    "game-team-fortress": ("fortress", "fortress/qwprogs.dat", "2fort5r"),
    "game-td2": ("td2", "td2/qwprogs.dat", "dm6"),
}
_CLIENT_READY_TIMEOUT_SECONDS = 25
_CLIENT_BUNDLE_ID_PREFIX = "com.x86qw.native.smoke."
_WINDOW_PROBE_SOURCE = r'''
import CoreGraphics

let targetPid = Int(CommandLine.arguments.dropFirst().first ?? "-1") ?? -1
let windows = CGWindowListCopyWindowInfo(
    [.optionAll, .excludeDesktopElements],
    kCGNullWindowID,
) as? [[String: Any]] ?? []
for window in windows {
    guard let ownerPid = window[kCGWindowOwnerPID as String] as? Int,
          ownerPid == targetPid else {
        continue
    }
    let owner = window[kCGWindowOwnerName as String] as? String ?? ""
    let name = window[kCGWindowName as String] as? String ?? ""
    print("\(owner)\t\(name)")
}
'''


def _native_frogbot_config_payload() -> bytes:
    """Return the derived KTX config used by the real post-map smoke."""

    return (
        'tempalias x86qw_native_frogbot "cmd botcmd skill 5;cmd botcmd addbot 5"\n'
        'tempalias on_enter "exec x86qw-ktx.cfg;x86qw_native_frogbot"\n'
    ).encode("ascii")


def _prepare_native_frogbot_config(target: Path) -> Path:
    """Materialize the same top-level KTX event alias used by the launcher."""

    target = Path(target).absolute()
    if target.is_symlink() or not target.is_dir():
        raise CandidateCaseError("destino nativo ausente ou inseguro para Frogbot")
    config_directory = target / "qw"
    if config_directory.is_symlink() or not config_directory.is_dir():
        raise CandidateCaseError("diretório qw ausente ou inseguro para Frogbot")
    config = config_directory / "x86qw-native-smoke-frogbot.cfg"
    _write_target_bytes(config, _native_frogbot_config_payload(), "configuração nativa de Frogbot")
    return config


def _remove_native_frogbot_config(config: Path) -> None:
    """Remove only the derived config created for this smoke case."""

    config = Path(config)
    if config.is_symlink():
        raise CandidateCaseError("configuração nativa de Frogbot foi trocada por symlink")
    if config.exists():
        if not config.is_file():
            raise CandidateCaseError("configuração nativa de Frogbot não é arquivo regular")
        config.unlink()
_SERVICE_SUFFIXES = {
    "mvdsv-mvd": ("runtime/servers/mvdsv/", "runtime/macos-arm64/mvdsv"),
    "qtv-stream": ("runtime/services/qtv/", "runtime/macos-arm64/qtv"),
    "qwfwd-forward": ("runtime/services/qwfwd/", "runtime/macos-arm64/qwfwd"),
}
_SERVICE_READY_TIMEOUT_SECONDS = 30
_SERVICE_START_ATTEMPTS = 3
_SERVICE_MVD_MAP = "dm6"
_SERVICE_MVD_GAMECODE = ("qwprogs.qvm", "qwprogs.map")
_SERVICE_OOB = b"\xff\xff\xff\xff"
_INSTALLER_CASES = {
    "install-clean-space-unicode": ("install",),
    "install-existing-space-unicode": ("install",),
    "migration-0.7.13-real": ("migrate",),
    "lifecycle-update": ("--dry-run", "update"),
    "lifecycle-update-apply": ("update",),
    "lifecycle-upgrade": ("--dry-run", "--yes", "upgrade"),
    "lifecycle-upgrade-apply": ("--yes", "upgrade"),
    "lifecycle-verify": ("--json", "verify"),
    "lifecycle-repair": ("--dry-run", "repair"),
    "lifecycle-repair-corruption": ("repair",),
    "lifecycle-migrate-apply": ("migrate",),
    "lifecycle-cleanup": ("cleanup",),
    "lifecycle-uninstall": ("uninstall",),
    "lifecycle-purge": ("uninstall", "--purge"),
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
    if not isinstance(name, str) or not name or "\\" in name:
        raise CandidateCaseError("membro ZIP nativo inválido")
    directory = name.endswith("/")
    name = name[:-1] if directory else name
    if not name:
        raise CandidateCaseError("membro ZIP nativo inválido")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CandidateCaseError("membro ZIP nativo inseguro")
    normalized = path.as_posix()
    return f"{normalized}/" if directory else normalized


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


def _extract_installer_launchers(destination_root: Path) -> None:
    """Materialize both launchers beside the candidate-owned zipapp."""

    destination_root = Path(destination_root)
    snapshot = destination_root / "archive.zip"
    if snapshot.is_symlink() or not snapshot.is_file():
        raise CandidateCaseError("archive do instalador ausente no scratch nativo")
    launcher_modes = {"x86qw.sh": 0o700, "x86qw.cmd": 0o600}
    try:
        with zipfile.ZipFile(snapshot) as bundle:
            for launcher_name, mode in launcher_modes.items():
                matches: list[tuple[str, zipfile.ZipInfo]] = []
                for info in bundle.infolist():
                    member = _zip_member_name(info.filename)
                    if member.endswith(f"/{launcher_name}") and not member.endswith("/"):
                        matches.append((member, info))
                if len(matches) != 1:
                    raise CandidateCaseError(
                        f"launcher nativo ausente ou ambíguo ({launcher_name}): {len(matches)} encontrados",
                    )
                member, info = matches[0]
                member_mode = (info.external_attr >> 16) & 0o170000
                if member_mode not in {0, stat.S_IFREG}:
                    raise CandidateCaseError(
                        f"launcher nativo possui tipo especial: {launcher_name}"
                    )
                if info.file_size > 256 * 1024:
                    raise CandidateCaseError(
                        f"launcher nativo excede o limite: {launcher_name}"
                    )
                output = destination_root / launcher_name
                with bundle.open(member, "r") as source, output.open("xb") as target:
                    shutil.copyfileobj(source, target, length=64 * 1024)
                os.chmod(output, mode)
    except CandidateCaseError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise CandidateCaseError("archive do instalador nativo inválido") from error


def _extract_macos_app(archive: CandidateArtifact, scratch: Path, label: str) -> Path:
    """Extract one complete, regular-file macOS app bundle and return its binary."""

    destination_root = Path(scratch) / "extracted" / label
    destination_root.mkdir(parents=True, exist_ok=True)
    snapshot = _snapshot_artifact(archive, destination_root / "archive.zip")
    app_root: str | None = None
    files: set[str] = set()
    try:
        with zipfile.ZipFile(snapshot) as bundle:
            for info in bundle.infolist():
                member = _zip_member_name(info.filename)
                directory = member.endswith("/")
                relative = PurePosixPath(member.rstrip("/"))
                if not relative.parts:
                    raise CandidateCaseError("membro ZIP nativo inválido")
                current_root = relative.parts[0]
                if not current_root.endswith(".app"):
                    raise CandidateCaseError("bundle macOS nativo possui raiz inválida")
                if app_root is None:
                    app_root = current_root
                elif current_root != app_root:
                    raise CandidateCaseError("bundle macOS nativo possui raízes múltiplas")
                if directory:
                    continue
                mode = (info.external_attr >> 16) & 0o170000
                if mode not in {0, stat.S_IFREG}:
                    raise CandidateCaseError("bundle macOS nativo contém tipo especial")
                normalized = relative.as_posix()
                if normalized in files:
                    raise CandidateCaseError("bundle macOS nativo contém membro duplicado")
                files.add(normalized)
                output = destination_root.joinpath(*relative.parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info, "r") as source, output.open("xb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
    except CandidateCaseError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise CandidateCaseError(f"bundle macOS nativo inválido: {archive.path}") from error
    if app_root is None:
        raise CandidateCaseError("bundle macOS nativo vazio")
    app = destination_root / app_root
    executable = app / "Contents/MacOS/ezQuake"
    if (
        not executable.is_file()
        or not (app / "Contents/Info.plist").is_file()
        or not (app / "Contents/Resources").is_dir()
    ):
        raise CandidateCaseError("bundle macOS nativo incompleto")
    os.chmod(executable, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return executable


def _isolate_macos_app(prepared: PreparedCase, case: str, scratch: Path) -> PreparedCase:
    """Give the smoke a private bundle identity without mutating user preferences."""

    if sys.platform != "darwin":
        return prepared
    source_app = prepared.executable
    while source_app != source_app.parent and not source_app.name.endswith(".app"):
        source_app = source_app.parent
    if not source_app.name.endswith(".app"):
        raise CandidateCaseError("bundle macOS nativo ausente para isolamento")
    isolation_root = Path(scratch) / "isolated-app"
    if isolation_root.exists() or isolation_root.is_symlink():
        raise CandidateCaseError("destino do bundle macOS isolado já existe")
    isolated_app = isolation_root / source_app.name
    try:
        shutil.copytree(source_app, isolated_app)
        plist_path = isolated_app / "Contents/Info.plist"
        document = plistlib.loads(plist_path.read_bytes())
        if not isinstance(document, dict):
            raise CandidateCaseError("Info.plist macOS nativo inválido")
        bundle_id = _CLIENT_BUNDLE_ID_PREFIX + hashlib.sha256(case.encode("utf-8")).hexdigest()[:16]
        document["CFBundleIdentifier"] = bundle_id
        document["CFBundleName"] = "x86QW Native Smoke"
        plist_path.write_bytes(plistlib.dumps(document, fmt=plistlib.FMT_BINARY, sort_keys=True))
        signature = isolated_app / "Contents/_CodeSignature"
        if signature.is_symlink():
            raise CandidateCaseError("assinatura do bundle macOS isolado usa symlink")
        if signature.exists():
            shutil.rmtree(signature)
        result = subprocess.run(
            ["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(isolated_app)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise CandidateCaseError("não foi possível assinar ad hoc o bundle macOS isolado")
    except CandidateCaseError:
        raise
    except (OSError, plistlib.InvalidFileException, ValueError, subprocess.SubprocessError) as error:
        raise CandidateCaseError("não foi possível preparar o bundle macOS isolado") from error
    executable = isolated_app / "Contents/MacOS/ezQuake"
    return PreparedCase(
        executable=executable,
        argv=(str(executable), *prepared.argv[1:]),
        cwd=prepared.cwd,
        artifact=prepared.artifact,
        shell=prepared.shell,
    )


def _pak_contains(path: Path, member: str) -> bool:
    try:
        with path.open("rb") as stream:
            header = stream.read(12)
            if len(header) != 12 or header[:4] != b"PACK":
                return False
            directory_offset = int.from_bytes(header[4:8], "little")
            directory_size = int.from_bytes(header[8:12], "little")
            if directory_size <= 0 or directory_size % 64 or directory_size > 8 * 1024 * 1024:
                return False
            stream.seek(0, os.SEEK_END)
            if directory_offset < 12 or directory_offset + directory_size > stream.tell():
                return False
            stream.seek(directory_offset)
            wanted = member.casefold()
            for _ in range(directory_size // 64):
                entry = stream.read(64)
                name = entry[:56].split(b"\0", 1)[0].decode("utf-8", errors="replace")
                if name.replace("\\", "/").casefold() == wanted:
                    return True
    except (OSError, ValueError):
        return False
    return False


def _archive_contains(path: Path, member: str) -> bool:
    if path.suffix.casefold() == ".pak":
        return _pak_contains(path, member)
    try:
        with zipfile.ZipFile(path) as archive:
            wanted = member.casefold()
            return any(
                name.rstrip("/").casefold() == wanted
                for name in archive.namelist()
            )
    except (OSError, zipfile.BadZipFile, KeyError):
        return False


def _client_content_evidence(target: Path, case: str) -> dict[str, object]:
    gamedir, gamecode, map_name = _CLIENT_CONTENT[case]
    target = Path(target)
    map_member = f"maps/{map_name}.bsp"
    roots = [target / gamedir]
    if gamedir != "id1":
        roots.append(target / "id1")
    map_sources: list[Path] = []
    for root in roots:
        direct = root / map_member
        if direct.is_file() and not direct.is_symlink():
            map_sources.append(direct)
        for pattern in ("*.pk3", "*.pak"):
            for archive in sorted(root.glob(pattern)):
                if archive.is_file() and not archive.is_symlink() and _archive_contains(archive, map_member):
                    map_sources.append(archive)
    if len(map_sources) != 1:
        raise CandidateCaseError(
            f"conteúdo nativo insuficiente para {case}: mapa {map_name} encontrado em {len(map_sources)} fontes"
        )
    gamecode_path = target / gamecode if gamecode is not None else None
    if gamecode_path is not None and (not gamecode_path.is_file() or gamecode_path.is_symlink()):
        raise CandidateCaseError(f"gamecode nativo ausente para {case}")
    return {
        "gamedir": gamedir,
        "map": map_name,
        "map_source": map_sources[0].relative_to(target).as_posix(),
        "gamecode_package": gamecode,
    }


def _window_probe_executable(scratch: Path) -> Path:
    destination = Path(scratch) / ".x86qw-window-probe"
    if (
        destination.is_file()
        and not destination.is_symlink()
        and os.access(destination, os.X_OK)
    ):
        return destination
    temporary = Path(scratch) / f".{destination.name}.{os.getpid()}.tmp"
    try:
        result = subprocess.run(
            ["/usr/bin/swiftc", "-O", "-o", str(temporary), "-"],
            input=_WINDOW_PROBE_SOURCE,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise CandidateCaseError("não foi possível compilar o observador CoreGraphics do macOS")
        if not temporary.is_file() or temporary.is_symlink():
            raise CandidateCaseError("o observador CoreGraphics do macOS não produziu um executável regular")
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        os.replace(temporary, destination)
        return destination
    except CandidateCaseError:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        raise CandidateCaseError("não foi possível preparar o observador CoreGraphics do macOS") from error
    finally:
        temporary.unlink(missing_ok=True)


def _window_titles(pid: int, scratch: Path) -> str:
    if sys.platform != "darwin":
        return ""
    quote = chr(34)
    script = (
        "tell application " + quote + "System Events" + quote
        + " to get name of every window of (first process whose unix id is "
        + str(pid) + ")"
    )
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    try:
        probe = _window_probe_executable(scratch)
        result = subprocess.run(
            [str(probe), str(pid)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
    except (CandidateCaseError, OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    titles = [
        line.split("\t", 1)[1].strip()
        for line in result.stdout.splitlines()
        if "\t" in line and line.split("\t", 1)[1].strip()
    ]
    return ", ".join(titles)


def _open_files(pid: int) -> str:
    if sys.platform != "darwin":
        return ""
    result = subprocess.run(
        ["/usr/sbin/lsof", "-p", str(pid)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def _console_log_paths(target: Path, gamedir: str) -> tuple[Path, ...]:
    paths = [Path(target) / gamedir / "qconsole.log", Path(target) / "qw/qconsole.log"]
    if gamedir != "id1":
        paths.append(Path(target) / "id1/qconsole.log")
    return tuple(dict.fromkeys(paths))


def _gamecode_log(target: Path, gamedir: str) -> str | None:
    for path in _console_log_paths(target, gamedir):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            lowered = line.casefold()
            if "loading vm file" in lowered or "progs.dat" in lowered or "qwprogs" in lowered:
                return line.strip()[:240] or None
    return None


def _frogbot_log_evidence(target: Path) -> dict[str, object]:
    """Read the candidate-owned KTX log for an actual named Frogbot launch."""

    chunks: list[str] = []
    for path in _console_log_paths(target, "qw"):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    log = "\n".join(chunks)
    lowered = log.casefold()
    return {
        # KTX reports the control command, not necessarily the product word
        # "Frogbot".  The command is therefore the stable runtime evidence;
        # the map/window/gamecode assertions establish that it ran in the
        # candidate-owned KTX process.
        "frogbot_spawned": "botcmd addbot" in lowered,
        "frogbot_skill": bool(re.search(r"botcmd\s+skill\s+5\b", lowered)),
        "frogbot_named": "x86qw" in lowered and (
            "k_fb_name_0" in lowered or "/x86qw" in lowered
        ),
        "frogbot_log": log[-2048:],
    }


def _run_client_process(
    prepared: PreparedCase,
    case: str,
    target: Path,
    environment: dict[str, str],
    scratch: Path,
) -> tuple[int, bytes, bytes, dict[str, object]]:
    """Observe a real window/map/gamecode, then terminate the smoke process safely."""

    content = _client_content_evidence(target, case)
    gamedir = str(content["gamedir"])
    map_name = str(content["map"])
    map_source = str(content["map_source"])
    gamecode_package = content["gamecode_package"]
    for stale_log in _console_log_paths(target, gamedir):
        if stale_log.is_file() and not stale_log.is_symlink():
            stale_log.unlink()
    process = subprocess.Popen(
        prepared.argv,
        cwd=prepared.cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=prepared.shell,
    )
    window_title = ""
    open_files = ""
    deadline = time.monotonic() + _CLIENT_READY_TIMEOUT_SECONDS
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise CandidateCaseError(f"ezQuake terminou antes de comprovar {case}")
            window_title = _window_titles(process.pid, scratch)
            open_files = _open_files(process.pid)
            target_prefix = str(Path(target).absolute())
            # The engine closes the source BSP and gamecode after loading them;
            # the durable native proof is the real window title plus the
            # candidate-owned console log, while lsof only guards against a
            # fallback to another basedir.
            files_ready = target_prefix in open_files
            frogbot_ready = (
                all(_frogbot_log_evidence(target).get(field) is True for field in (
                    "frogbot_spawned", "frogbot_skill", "frogbot_named",
                ))
                if case == "game-ktx-frogbot" else True
            )
            if map_name.casefold() in window_title.casefold() and files_ready and frogbot_ready:
                break
            time.sleep(0.25)
        else:
            diagnostics = [
                f"window={window_title[:160]!r}",
                f"target_open={str(Path(target).absolute()) in open_files}",
                f"gamecode={_gamecode_log(target, gamedir)!r}",
            ]
            if case == "game-ktx-frogbot":
                frogbot = _frogbot_log_evidence(target)
                diagnostics.extend([
                    f"frogbot_spawned={frogbot['frogbot_spawned']}",
                    f"frogbot_skill={frogbot['frogbot_skill']}",
                    f"frogbot_named={frogbot['frogbot_named']}",
                    f"frogbot_log={str(frogbot['frogbot_log'])[-512:]!r}",
                ])
            raise CandidateCaseError(
                f"ezQuake não comprovou janela/mapa/conteúdo para {case}: "
                + "; ".join(diagnostics)
            )
        process.send_signal(signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
        process_exit_code = process.returncode
        gamecode_log = _gamecode_log(target, gamedir)
        if gamecode_package is not None and gamecode_log is None:
            raise CandidateCaseError(f"gamecode não foi comprovado no log para {case}")
        observation = {
            "window_title": window_title[:240],
            "map": map_name,
            "gamecode_log": gamecode_log,
            "content": content,
            "termination": "controlled",
            "process_exit_code": process_exit_code,
        }
        if case == "game-ktx-frogbot":
            frogbot = _frogbot_log_evidence(target)
            if not all(frogbot.get(field) is True for field in (
                "frogbot_spawned", "frogbot_skill", "frogbot_named",
            )):
                raise CandidateCaseError("KTX não comprovou o Frogbot nomeado no log")
            observation.update(frogbot)
        return 0, stdout or b"", stderr or b"", observation
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise


def _service_config_artifact(candidate: Candidate, case: str) -> CandidateArtifact:
    if case == "mvdsv-mvd":
        predicate = lambda name: (
            name.startswith("runtime/servers/mvdsv/") and name.endswith("/x86qw/server.cfg")
        )
    elif case == "qtv-stream":
        predicate = lambda name: (
            name.startswith("runtime/services/qtv/") and name.endswith("/x86qw/qtv.cfg")
        )
    else:
        predicate = lambda name: (
            name.startswith("runtime/services/qwfwd/") and name.endswith("/x86qw/qwfwd.cfg")
        )
    return _artifact_matching(candidate, predicate, f"configuração nativa de {case}")


def _materialize_qwfwd_config(
    artifact: CandidateArtifact, destination: Path, port: int,
) -> Path:
    """Derive an isolated QWFWD config without changing candidate bytes."""

    if type(port) is not int or not 1 <= port <= 65535:
        raise CandidateCaseError("porta efêmera do QWFWD inválida")
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise CandidateCaseError(f"configuração QWFWD derivada já existe: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    snapshot = destination.with_name(f".{destination.name}.candidate")
    try:
        _snapshot_artifact(artifact, snapshot)
        payload = snapshot.read_bytes()
        lines = payload.splitlines(keepends=True)
        matches = [
            (index, match)
            for index, line in enumerate(lines)
            if (match := re.fullmatch(
                rb"([ \t]*set[ \t]+net_port[ \t]+)([0-9]+)([ \t]*)(\r?\n)?",
                line,
            ))
        ]
        if len(matches) != 1:
            raise CandidateCaseError(
                "configuração QWFWD precisa conter exatamente uma definição de net_port",
            )
        index, match = matches[0]
        lines[index] = (
            match.group(1) + str(port).encode("ascii") + match.group(3)
            + (match.group(4) or b"")
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(b"".join(lines))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(destination, stat.S_IRUSR | stat.S_IWUSR)
        return destination
    except CandidateCaseError:
        destination.unlink(missing_ok=True)
        raise
    except (OSError, UnicodeError) as error:
        destination.unlink(missing_ok=True)
        raise CandidateCaseError("não foi possível materializar a configuração QWFWD") from error
    finally:
        snapshot.unlink(missing_ok=True)


def _ensure_exact_file(artifact: CandidateArtifact, destination: Path) -> Path:
    destination = Path(destination)
    if destination.is_symlink():
        raise CandidateCaseError(f"destino de serviço usa symlink: {destination}")
    if destination.exists():
        if not destination.is_file() or _digest(destination) != (artifact.size, artifact.sha256):
            raise CandidateCaseError(f"configuração de serviço diverge: {destination}")
        return destination
    return _snapshot_artifact(artifact, destination)


def _write_exclusive_bytes(destination: Path, value: bytes) -> Path:
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise CandidateCaseError(f"arquivo derivado de serviço já existe: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise CandidateCaseError(f"não foi possível materializar arquivo de serviço: {destination}") from error
    return destination


def _materialize_mvdsv_gamecode(target: Path) -> None:
    """Expose verified KTX QVM members to MVDSV, which does not load PK3 code."""

    target = Path(target)
    game_dir = target / "qw"
    archive_path = game_dir / "ktx.pk3"
    if game_dir.is_symlink() or not game_dir.is_dir():
        raise CandidateCaseError("diretório qw ausente ou inseguro para MVDSV")
    if archive_path.is_symlink() or not archive_path.is_file():
        raise CandidateCaseError("ktx.pk3 ausente no candidato instalado")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            selected: dict[str, zipfile.ZipInfo] = {}
            for info in archive.infolist():
                name = _zip_member_name(info.filename).rstrip("/")
                if name in _SERVICE_MVD_GAMECODE:
                    if name in selected or info.is_dir():
                        raise CandidateCaseError("ktx.pk3 possui gamecode duplicado ou diretório")
                    mode = (info.external_attr >> 16) & 0o170000
                    if mode not in {0, stat.S_IFREG} or info.file_size > 32 * 1024 * 1024:
                        raise CandidateCaseError("gamecode do KTX possui tipo ou tamanho inválido")
                    selected[name] = info
            if set(selected) != set(_SERVICE_MVD_GAMECODE):
                raise CandidateCaseError("ktx.pk3 não contém qwprogs.qvm e qwprogs.map")
            for name in _SERVICE_MVD_GAMECODE:
                destination = game_dir / name
                if destination.is_symlink() or destination.is_dir():
                    raise CandidateCaseError(f"gamecode derivado inseguro: {destination}")
                with archive.open(selected[name], "r") as source:
                    value = source.read(32 * 1024 * 1024 + 1)
                if len(value) > 32 * 1024 * 1024:
                    raise CandidateCaseError("gamecode do KTX excede o limite nativo")
                if destination.exists():
                    if destination.read_bytes() != value:
                        raise CandidateCaseError(f"gamecode derivado diverge: {destination}")
                else:
                    _write_exclusive_bytes(destination, value)
    except CandidateCaseError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise CandidateCaseError("ktx.pk3 inválido para MVDSV") from error


def _prepare_mvdsv_target(candidate: Candidate, target: Path) -> None:
    target = Path(target)
    config = _service_config_artifact(candidate, "mvdsv-mvd")
    _ensure_exact_file(config, target / "qw/server.cfg")
    for stale_log in _console_log_paths(target, "qw"):
        if stale_log.is_file() and not stale_log.is_symlink():
            stale_log.unlink()
    _materialize_mvdsv_gamecode(target)
    demos = target / "qw/demos"
    if demos.is_symlink() or (demos.exists() and not demos.is_dir()):
        raise CandidateCaseError("diretório de demos MVDSV inseguro")
    demos.mkdir(parents=True, exist_ok=True)


def _terminate_service_process(process: subprocess.Popen[bytes]) -> tuple[bytes, int]:
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
    try:
        output, _ = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate(timeout=5)
    return output or b"", int(process.returncode)


def _wait_tcp_listener(process: subprocess.Popen[bytes], port: int, label: str) -> None:
    deadline = time.monotonic() + _SERVICE_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise CandidateCaseError(f"{label} terminou antes de abrir a porta nativa")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise CandidateCaseError(f"{label} não abriu a porta nativa {port}")


def _start_tcp_service(
    command_factory: Callable[[], tuple[tuple[str, ...], int]],
    *,
    cwd: Path,
    environment: dict[str, str],
    label: str,
) -> tuple[subprocess.Popen[bytes], tuple[str, ...], int]:
    """Start a service whose ephemeral TCP port can be lost before bind.

    The port probe used by the native cases necessarily closes its socket
    before the third-party process starts.  A short retry is therefore part
    of the contract for an early bind failure; a service that stays alive but
    never becomes ready is not retried and keeps its useful timeout error.
    """

    for attempt in range(1, _SERVICE_START_ATTEMPTS + 1):
        argv, port = command_factory()
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
        )
        try:
            _wait_tcp_listener(process, port, label)
            return process, argv, port
        except CandidateCaseError as error:
            exited_early = process.poll() is not None
            output, _ = _terminate_service_process(process)
            if not exited_early or attempt == _SERVICE_START_ATTEMPTS:
                detail = output.decode("utf-8", errors="replace").strip()
                if detail:
                    raise CandidateCaseError(
                        f"{error}: {detail[-512:]}"
                    ) from error
                raise

    raise AssertionError("serviço TCP terminou sem resultado")


def _mvd_record(target: Path, process: subprocess.Popen[bytes]) -> tuple[Path, int, str]:
    if process.stdin is None:
        raise CandidateCaseError("MVDSV não aceitou stdin para gravação MVD")
    process.stdin.write(b"record native-smoke\n")
    process.stdin.flush()
    demos = Path(target) / "qw/demos"
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        for path in sorted(demos.glob("native-smoke*.mvd")):
            if path.is_symlink() or not path.is_file() or path.stat().st_size < 64:
                continue
            value = path.read_bytes()
            if b"MVD1" not in value[:64] or b"\\map\\dm6" not in value:
                continue
            digest = hashlib.sha256(value).hexdigest()
            return path, len(value), digest
        if process.poll() is not None:
            break
        time.sleep(0.1)
    raise CandidateCaseError("MVDSV não produziu um MVD válido")


def _run_mvdsv_service(
    prepared: PreparedCase,
    candidate: Candidate,
    target: Path,
    environment: dict[str, str],
) -> tuple[int, bytes, bytes, dict[str, object]]:
    _prepare_mvdsv_target(candidate, target)
    def command_factory() -> tuple[tuple[str, ...], int]:
        argv = _service_runtime_argv(prepared, "mvdsv-mvd")
        stream_index = argv.index("+qtv_streamport") + 1
        return argv, int(argv[stream_index])

    process, _argv, _stream_port = _start_tcp_service(
        command_factory,
        cwd=prepared.cwd,
        environment=environment,
        label="MVDSV",
    )
    try:
        server_port_ready = True
        mvd_path, mvd_size, mvd_sha256 = _mvd_record(target, process)
        output, process_exit_code = _terminate_service_process(process)
        gamecode_log = _gamecode_log(target, "qw")
        output_text = output.decode("utf-8", errors="replace")
        if gamecode_log is None:
            for line in output_text.splitlines():
                if "loading vm file" in line.casefold() or "qwprogs" in line.casefold():
                    gamecode_log = line.strip()[:240] or None
                    break
        server_ready = (
            server_port_ready
            and gamecode_log is not None
        )
        if not server_ready:
            raise CandidateCaseError("MVDSV não comprovou servidor, gamecode e mapa")
        observation = {
            "service": "mvdsv",
            "server_ready": True,
            "map": _SERVICE_MVD_MAP,
            "gamecode_log": gamecode_log,
            "mvd_valid": True,
            "mvd_size": mvd_size,
            "mvd_sha256": mvd_sha256,
            "termination": "controlled",
            "process_exit_code": process_exit_code,
        }
        return 0, output, b"", observation
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.communicate()
        raise


def _prepare_qtv_config(candidate: Candidate, prepared: PreparedCase, http_port: int, stream_port: int) -> None:
    artifact = _service_config_artifact(candidate, "qtv-stream")
    destination = Path(prepared.cwd) / "qtv.cfg"
    _snapshot_artifact(artifact, destination)
    try:
        document = destination.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise CandidateCaseError("configuração QTV inválida") from error
    pattern = re.compile(r'(?m)^(listen_address\s+")[^"]+("\s*)$')
    document, replacements = pattern.subn(rf'\g<1>127.0.0.1:{http_port}\g<2>', document, count=1)
    if replacements != 1 or "http_enabled 1" not in document:
        raise CandidateCaseError("configuração QTV não habilita HTTP/listen_address")
    document += f"\nqtv 127.0.0.1:{stream_port}\n"
    try:
        destination.write_text(document, encoding="utf-8")
        (Path(prepared.cwd) / "demos").mkdir(exist_ok=True)
    except OSError as error:
        raise CandidateCaseError("não foi possível preparar configuração QTV") from error


def _http_probe(port: int) -> tuple[int, bytes]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/nowplaying/")
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            return int(response.status), response.read(256 * 1024)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        return 0, b""


def _qtv_stream_probe(port: int) -> tuple[bool, bytes]:
    request = b'QTV\nVERSION: 1\nSOURCE: 1\nUSERINFO: "\\name\\native"\n\n'
    received = bytearray()
    deadline = time.monotonic() + 8
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as stream:
            stream.settimeout(0.5)
            stream.sendall(request)
            while time.monotonic() < deadline:
                try:
                    chunk = stream.recv(8192)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                received.extend(chunk)
                if b"QTVSV" in received and b"BEGIN:" in received and len(received) > 64:
                    return True, bytes(received)
    except OSError:
        return False, bytes(received)
    return False, bytes(received)


def _run_qtv_service(
    prepared: PreparedCase,
    candidate: Candidate,
    target: Path,
    scratch: Path,
    environment: dict[str, str],
) -> tuple[int, bytes, bytes, dict[str, object]]:
    _prepare_mvdsv_target(candidate, target)
    mvd_artifact = _artifact_matching(
        candidate,
        lambda name: name.startswith("runtime/servers/mvdsv/") and name.endswith("/runtime/macos-arm64/mvdsv"),
        "MVDSV para QTV",
    )
    dependency = Path(scratch) / "dependencies/mvdsv"
    _snapshot_artifact(mvd_artifact, dependency)
    os.chmod(dependency, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    def command_factory() -> tuple[tuple[str, ...], int]:
        mvd_port = _free_port(socket.SOCK_DGRAM)
        stream_port = _free_port(socket.SOCK_STREAM)
        return (
            (
                str(dependency), "-mem", "64", "-basedir", str(target),
                "-port", str(mvd_port), "+qtv_streamport", str(stream_port),
                "+sv_progtype", "2", "+map", _SERVICE_MVD_MAP,
            ),
            stream_port,
        )

    mvd, _mvd_command, stream_port = _start_tcp_service(
        command_factory,
        cwd=target,
        environment=environment,
        label="MVDSV upstream do QTV",
    )
    qtv: subprocess.Popen[bytes] | None = None
    try:
        http_port = _free_port(socket.SOCK_STREAM)
        _prepare_qtv_config(candidate, prepared, http_port, stream_port)
        qtv = subprocess.Popen(
            prepared.argv,
            cwd=prepared.cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
        )
        http_status = 0
        body = b""
        deadline = time.monotonic() + _SERVICE_READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if qtv.poll() is not None:
                raise CandidateCaseError("QTV terminou antes de servir HTTP")
            http_status, body = _http_probe(http_port)
            if http_status == 200 and _SERVICE_MVD_MAP.encode() in body and b"No streams" not in body:
                break
            time.sleep(0.15)
        else:
            raise CandidateCaseError("QTV não comprovou HTTP e upstream no mapa esperado")
        stream_readable, stream_bytes = _qtv_stream_probe(http_port)
        if not stream_readable:
            raise CandidateCaseError("QTV não comprovou stream jogável")
        qtv_output, qtv_exit = _terminate_service_process(qtv)
        qtv = None
        mvd_output, _mvd_exit = _terminate_service_process(mvd)
        mvd = None
        observation = {
            "service": "qtv",
            "http_ready": True,
            "http_status": http_status,
            "upstream_map": _SERVICE_MVD_MAP,
            "stream_readable": True,
            "stream_header": stream_bytes[:256].decode("latin-1", errors="replace"),
            "stream_bytes": len(stream_bytes),
            "termination": "controlled",
            "process_exit_code": qtv_exit,
        }
        return 0, qtv_output + mvd_output, b"", observation
    except BaseException:
        if qtv is not None and qtv.poll() is None:
            qtv.kill()
            qtv.communicate()
        if mvd.poll() is None:
            mvd.kill()
        mvd.communicate()
        raise


def _recv_udp(sock: socket.socket, predicate: object, timeout: float = 2.0) -> bytes:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(0.05, deadline - time.monotonic())
        sock.settimeout(remaining)
        try:
            value, _address = sock.recvfrom(65535)
        except socket.timeout:
            continue
        if predicate(value):  # type: ignore[operator]
            return value
    raise CandidateCaseError("QWFWD não respondeu ao protocolo UDP nativo")


def _qwfwd_challenge(client: socket.socket, proxy_port: int) -> bytes:
    deadline = time.monotonic() + _SERVICE_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        client.sendto(_SERVICE_OOB + b"getchallenge\n", ("127.0.0.1", proxy_port))
        try:
            value = _recv_udp(client, lambda item: item.startswith(_SERVICE_OOB + b"c"), timeout=0.5)
            return value
        except CandidateCaseError:
            time.sleep(0.1)
    raise CandidateCaseError("QWFWD não abriu o endpoint UDP nativo")


def _qwfwd_challenge_token(challenge_reply: bytes) -> str:
    prefix = _SERVICE_OOB + b"c"
    if not isinstance(challenge_reply, bytes) or not challenge_reply.startswith(prefix):
        raise CandidateCaseError("QWFWD retornou challenge UDP inválido")
    token = challenge_reply[len(prefix):].split(b"\0", 1)[0].strip()
    if not token:
        raise CandidateCaseError("QWFWD retornou challenge UDP vazio")
    try:
        value = token.decode("ascii")
    except UnicodeDecodeError as error:
        raise CandidateCaseError("QWFWD retornou challenge UDP não-ASCII") from error
    if re.fullmatch(r"-?[0-9]+", value) is None:
        raise CandidateCaseError("QWFWD retornou challenge UDP não numérico")
    return value


def _qwfwd_remote_ready_packet(value: bytes) -> bool:
    """Recognize a server response that follows the forwarded connection."""

    return isinstance(value, bytes) and value.startswith(_SERVICE_OOB + b"n")


def _run_qwfwd_service(
    prepared: PreparedCase,
    candidate: Candidate,
    environment: dict[str, str],
) -> tuple[int, bytes, bytes, dict[str, object]]:
    config = _service_config_artifact(candidate, "qwfwd-forward")
    argv = _service_runtime_argv(prepared, "qwfwd-forward")
    proxy_port = int(argv[1])
    _materialize_qwfwd_config(config, Path(prepared.cwd) / "qwfwd.cfg", proxy_port)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as fake_server:
        fake_server.bind(("127.0.0.1", 0))
        fake_port = int(fake_server.getsockname()[1])
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            process = subprocess.Popen(
                argv,
                cwd=prepared.cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=False,
            )
            try:
                client.bind(("127.0.0.1", 0))
                challenge_reply = _qwfwd_challenge(client, proxy_port)
                challenge = _qwfwd_challenge_token(challenge_reply)
                connect = (
                    _SERVICE_OOB
                    + f'connect 28 1234 {challenge} "\\name\\native\\topcolor\\4\\bottomcolor\\4\\prx\\127.0.0.1:{fake_port}"\n'.encode()
                )
                client.sendto(connect, ("127.0.0.1", proxy_port))
                fake_server.settimeout(_SERVICE_READY_TIMEOUT_SECONDS)
                _challenge_request, qwfwd_address = fake_server.recvfrom(65535)
                fake_server.sendto(_SERVICE_OOB + b"c777\n", qwfwd_address)
                _connect_request, qwfwd_address = fake_server.recvfrom(65535)
                fake_server.sendto(_SERVICE_OOB + b"j", qwfwd_address)
                # QWFWD can still be processing the server's connection packet
                # when the client sends its first datagram.  A forwarded print
                # packet is the observable barrier after the peer is connected.
                fake_server.sendto(_SERVICE_OOB + b"n\nNATIVE-READY\n", qwfwd_address)
                _recv_udp(
                    client,
                    _qwfwd_remote_ready_packet,
                    timeout=_SERVICE_READY_TIMEOUT_SECONDS,
                )
                client.sendto(b"NATIVE-FORWARD", ("127.0.0.1", proxy_port))
                forwarded, proxy_address = fake_server.recvfrom(65535)
                if forwarded != b"NATIVE-FORWARD":
                    raise CandidateCaseError("QWFWD alterou o payload encaminhado")
                fake_server.sendto(b"NATIVE-RETURN", proxy_address)
                returned = _recv_udp(
                    client,
                    lambda value: value == b"NATIVE-RETURN",
                    timeout=_SERVICE_READY_TIMEOUT_SECONDS,
                )
                output, process_exit_code = _terminate_service_process(process)
                observation = {
                    "service": "qwfwd",
                    "udp_forwarded": True,
                    "response_returned": returned == b"NATIVE-RETURN",
                    "termination": "controlled",
                    "process_exit_code": process_exit_code,
                }
                return 0, output, b"", observation
            except BaseException:
                if process.poll() is None:
                    process.kill()
                process.communicate()
                raise


def _run_service_process(
    prepared: PreparedCase,
    case: str,
    candidate: Candidate,
    target: Path,
    scratch: Path,
    environment: dict[str, str],
) -> tuple[int, bytes, bytes, dict[str, object]]:
    if case == "mvdsv-mvd":
        return _run_mvdsv_service(prepared, candidate, target, environment)
    if case == "qtv-stream":
        return _run_qtv_service(prepared, candidate, target, scratch, environment)
    return _run_qwfwd_service(prepared, candidate, environment)


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
    _extract_installer_launchers(pyz.parent)
    if arguments == ("install",):
        arguments = (
            "--online-only",
            "--platform", "macos",
            "--channel", "stable",
            "--release", _stable_macos_release(candidate),
            "--native-profile", "complete",
            "install",
        )
    # The target is deliberately shared by all installer cases and contains a
    # space plus Unicode. The first install owns creation of the target.
    target = Path(state_root) / _INSTALLATION_TARGET
    Path(state_root).mkdir(parents=True, exist_ok=True)
    argv = (sys.executable, str(pyz), *arguments, str(target))
    return PreparedCase(executable=pyz, argv=argv, cwd=Path(scratch), artifact=archive)


def _client_command(
    candidate: Candidate,
    channel: str,
    game_arguments: tuple[str, ...],
    map_name: str,
    scratch: Path,
    state_root: Path,
) -> PreparedCase:
    archive = _artifact_matching(
        candidate,
        lambda name: (
            name.startswith(f"runtime/clients/ezquake/{channel}/")
            and "/macos-universal/" in name
            and name.endswith(".zip")
        ),
        f"ezQuake {channel} macOS universal",
    )
    binary = _extract_macos_app(archive, scratch, f"client-{channel}")
    target = Path(state_root) / _INSTALLATION_TARGET
    args: tuple[str, ...] = (
        "-nohome", "-basedir", str(target),
        "-nosound", "-window", "-width", "1280", "-height", "720",
        "-clientport", "0", "-condebug", *game_arguments,
        "+cfg_save_onquit", "0", "+sb_findroutes", "0", "+sb_autoupdate", "0",
        "+map", map_name,
    )
    return PreparedCase(
        executable=binary, argv=(str(binary), *args), cwd=target, artifact=archive,
    )


def _free_port(socket_type: int) -> int:
    with socket.socket(socket.AF_INET, socket_type) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _service_runtime_argv(prepared: PreparedCase, case: str) -> tuple[str, ...]:
    argv = list(prepared.argv)
    if case == "mvdsv-mvd":
        argv[argv.index("-port") + 1] = str(_free_port(socket.SOCK_DGRAM))
        argv[argv.index("+qtv_streamport") + 1] = str(_free_port(socket.SOCK_STREAM))
    elif case == "qwfwd-forward":
        argv[1] = str(_free_port(socket.SOCK_DGRAM))
    return tuple(argv)


def _service_command(
    candidate: Candidate,
    case: str,
    scratch: Path,
    state_root: Path,
) -> PreparedCase:
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
    service_dir = staged.parent.parent / case
    service_dir.mkdir(parents=True, exist_ok=True)
    target = Path(state_root) / _INSTALLATION_TARGET
    if case == "mvdsv-mvd":
        argv = (
            str(staged),
            "-mem", "64",
            "-basedir", str(target),
            "-port", "0",
            "+qtv_streamport", "0",
            "+sv_progtype", "2",
            "+map", _SERVICE_MVD_MAP,
        )
        cwd = target
    elif case == "qtv-stream":
        argv = (str(staged), "exec", str(service_dir / "qtv.cfg"))
        cwd = service_dir
    else:
        argv = (str(staged), "0", "127.0.0.1")
        cwd = service_dir
    return PreparedCase(
        executable=artifact.path,
        argv=argv,
        cwd=cwd,
        artifact=artifact,
    )


def _run_installed_launcher_contract(
    *,
    candidate: Candidate,
    target: Path,
    environment: Mapping[str, str],
) -> dict[str, object]:
    """Exercise the launcher written by the candidate installer in-place."""

    target = Path(target).absolute()
    if target.is_symlink() or not target.is_dir():
        raise CandidateCaseError("destino instalado do launcher ausente ou inseguro")
    launcher_name = "x86qw.cmd" if os.name == "nt" else "x86qw.sh"
    launcher = target / launcher_name
    if launcher.is_symlink() or not launcher.is_file():
        raise CandidateCaseError(f"launcher instalado ausente ou inseguro: {launcher_name}")

    commands: tuple[tuple[str, ...], ...] = (
        ("help",),
        ("version",),
        ("changes",),
        ("migrate", "--dry-run"),
    )
    observed: list[dict[str, object]] = []
    help_output = ""
    version_output = ""
    child_environment = dict(environment)
    child_environment.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
    for arguments in commands:
        try:
            completed = subprocess.run(
                [str(launcher), *arguments],
                cwd=target,
                env=child_environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise CandidateCaseError(
                f"launcher instalado não executou {' '.join(arguments)}: {error}"
            ) from error
        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
        if completed.returncode != 0:
            detail = stderr.strip() or stdout.strip() or "sem diagnóstico"
            raise CandidateCaseError(
                f"launcher instalado falhou em {' '.join(arguments)} "
                f"(código {completed.returncode}): {detail[-512:]}"
            )
        if arguments == ("help",):
            help_output = stdout + stderr
        elif arguments == ("version",):
            version_output = stdout + stderr
        observed.append({"name": arguments[0], "exit_code": int(completed.returncode)})

    return {
        "launcher": launcher_name,
        "commands": observed,
        "help_lists_changes": "changes" in help_output,
        "help_lists_migrate": "migrate" in help_output,
        "version_matches": candidate.version in version_output,
        "changes_executed": observed[2]["exit_code"] == 0,
        "migrate_dry_run_executed": observed[3]["exit_code"] == 0,
        "termination": "controlled",
        "process_exit_code": 0,
    }


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
        return _service_command(candidate, case, scratch, state_root)
    if case in _CLIENT_CASES:
        channel, game_arguments, map_name = _CLIENT_CASES[case]
        return _client_command(
            candidate, channel, game_arguments, map_name, scratch, state_root,
        )
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
    if case == "lifecycle-purge":
        if status != "uninstalled" or target.is_symlink() or not target.is_dir():
            raise CandidateCaseError(
                "lifecycle-purge exige a instalação compartilhada já desinstalada"
            )
        return "uninstalled"
    if status != "installed" or target.is_symlink() or not target.is_dir():
        raise CandidateCaseError(f"{case} exige a instalação compartilhada já instalada")
    return "installed"


def _candidate_fixture_artifact(candidate: Candidate, relative: str) -> CandidateArtifact:
    artifact = candidate.artifacts.get(relative)
    if artifact is None:
        raise CandidateCaseError(f"fixture nativa ausente no candidato: {relative}")
    return artifact


def _regular_target_file(path: Path, label: str) -> Path:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise CandidateCaseError(f"arquivo de {label} ausente ou inseguro: {path}")
    return path


def _target_digest(path: Path, label: str) -> str:
    regular = _regular_target_file(path, label)
    return _digest(regular)[1]


def _write_target_bytes(path: Path, payload: bytes, label: str) -> None:
    path = Path(path)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise CandidateCaseError(f"destino de {label} é inseguro: {path}")
    if path.parent.is_symlink() or (path.parent.exists() and not path.parent.is_dir()):
        raise CandidateCaseError(f"diretório de {label} é inseguro: {path.parent}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.native-tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise CandidateCaseError(f"não foi possível escrever {label}: {path}") from error


def _preserve_native_user_files(target: Path) -> dict[str, dict[str, object]]:
    personal: dict[str, dict[str, object]] = {}
    for relative, payload in (
        ("qw/configs/x86qw-native-legacy.cfg", b"seta name x86qw-native-legacy\n"),
        ("qw/demos/x86qw-native-legacy.mvd", b"MVD1 x86qw native migration demo\n"),
    ):
        path = Path(target) / relative
        if not path.exists():
            _write_target_bytes(path, payload, "arquivo pessoal nativo")
        digest = _target_digest(path, "arquivo pessoal nativo")
        personal[relative] = {"sha256": digest, "size": path.stat().st_size}
    paks: dict[str, dict[str, object]] = {}
    for name in ("pak0.pak", "pak1.pak"):
        path = Path(target) / "id1" / name
        digest = _target_digest(path, f"PAK {name}")
        paks[f"id1/{name}"] = {"sha256": digest, "size": path.stat().st_size}
    return {"personal": personal, "paks": paks}


def _prepare_legacy_state(
    candidate: Candidate, target: Path, *, source_version: str,
) -> dict[str, object]:
    """Install candidate-owned historical metadata for a real migration apply."""

    prefix = "runtime/native-smoke/macos-arm64/fixtures/migrations/0.7.13/"
    fixture_state = _candidate_fixture_artifact(candidate, prefix + ".x86qw/state.json")
    fixture_version = _candidate_fixture_artifact(candidate, prefix + "VERSION")
    version = fixture_version.path.read_text(encoding="utf-8").strip()
    if version != source_version:
        raise CandidateCaseError("fixture de migração 0.7.13 diverge da versão esperada")
    try:
        document = json.loads(
            fixture_state.path.read_text(encoding="utf-8"),
            object_pairs_hook=_no_duplicates,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CandidateCaseError("fixture de estado 0.7.13 inválida") from error
    if (
        not isinstance(document, dict)
        or document.get("format") != 1
        or document.get("project") != PROJECT
    ):
        raise CandidateCaseError("fixture de estado 0.7.13 não é formato legado")
    document["installation_version"] = source_version
    state_path = _regular_target_file(
        Path(target) / ".x86qw/state.json", "estado de instalação",
    )

    # The install case has just written current-format receipts.  Replacing
    # only state.json would create an intentionally contradictory installation
    # (legacy state plus an authenticated RC CLI receipt), so seed the complete
    # legacy metadata shape before invoking the real migration code.  Managed
    # files are removed only after checking that they are regular files; user
    # payloads and the candidate-owned CLI bytes remain untouched.
    legacy_pairs = (
        ("cli.receipt", ".x86qw/cli/receipt"),
        ("ktx.receipt", ".x86qw/components/ktx/receipt"),
        ("ktx.inventory", ".x86qw/components/ktx/inventory"),
    )
    for name, canonical_relative in legacy_pairs:
        fixture = _candidate_fixture_artifact(candidate, prefix + ".x86qw/" + name)
        canonical = Path(target) / canonical_relative
        if canonical.is_symlink() or (canonical.exists() and not canonical.is_file()):
            raise CandidateCaseError(f"metadado canônico legado é inseguro: {canonical_relative}")
        if canonical.exists():
            canonical.unlink()
        _write_target_bytes(
            Path(target) / ".x86qw" / name,
            fixture.path.read_bytes(),
            f"recibo legado {name}",
        )
    preserved = _preserve_native_user_files(target)
    before = _target_digest(state_path, "estado de instalação")
    _write_target_bytes(
        state_path,
        (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        "estado legado de instalação",
    )
    return {
        "source_version": source_version,
        "fixture_state_sha256": fixture_state.sha256,
        "fixture_version_sha256": fixture_version.sha256,
        "state_before_sha256": before,
        **preserved,
    }


def _prepare_repair_corruption(target: Path) -> dict[str, object]:
    preserved = _preserve_native_user_files(target)
    candidates = ("mvdsv", "qtv/qtv", "qwfwd/qwfwd")
    for relative in candidates:
        path = Path(target) / relative
        if path.is_symlink() or not path.is_file():
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        if not mode & stat.S_IXUSR:
            continue
        _target_digest(path, f"runtime {relative}")
        os.chmod(path, mode & ~0o111)
        if stat.S_IMODE(path.stat().st_mode) & 0o111:
            raise CandidateCaseError(f"não foi possível corromper permissão nativa: {relative}")
        return {
            "path": relative,
            "payload_sha256": _target_digest(path, f"runtime {relative}"),
            "corrupted_mode": mode & ~0o111,
            **preserved,
        }
    raise CandidateCaseError("nenhum runtime executável disponível para o caso de repair")


def _prepare_uninstall_context(target: Path) -> dict[str, object]:
    return _preserve_native_user_files(target)


def _prepare_purge_context(
    candidate: Candidate,
    target: Path,
    scratch: Path,
    state_root: Path,
    environment: Mapping[str, str],
) -> dict[str, object]:
    """Rehydrate ownership with the candidate before exercising purge."""

    rehydrate_scratch = Path(scratch) / "purge-rehydrate"
    prepared = _installer_command(
        candidate, ("install",), rehydrate_scratch, state_root,
    )
    try:
        result = subprocess.run(
            prepared.argv,
            cwd=prepared.cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=prepared.shell,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise CandidateCaseError("reinstalação de ownership para purge falhou") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        raise CandidateCaseError(
            "reinstalação de ownership para purge falhou: " + (detail[-512:] or "sem diagnóstico")
        )
    if not Path(target).is_dir() or not (Path(target) / ".x86qw/state.json").is_file():
        raise CandidateCaseError("reinstalação de ownership não publicou estado gerenciado")
    return {"rehydrated": True, **_preserve_native_user_files(target)}


def _validate_uninstall_apply(target: Path, context: Mapping[str, object]) -> dict[str, object]:
    personal_ok, pak_ok = _validate_preserved_files(target, context)
    managed_paths = (
        "mvdsv", "qtv", "qwfwd", "ezQuake Stable.app", "ezQuake Nightly.app",
        "x86qw.sh", "x86qw.cmd", "x86qw.pyz",
    )
    installation_removed = not any((Path(target) / relative).exists() for relative in managed_paths)
    if not installation_removed or not personal_ok or not pak_ok:
        raise CandidateCaseError("uninstall não removeu os gerenciados ou alterou dados pessoais")
    return {
        "installation_removed": True,
        "personal_preserved": personal_ok,
        "pak_preserved": pak_ok,
        "termination": "controlled",
        "process_exit_code": 0,
    }


def _validate_purge_apply(target: Path, context: Mapping[str, object]) -> dict[str, object]:
    if os.path.lexists(target):
        raise CandidateCaseError("purge deixou o destino de instalação existente")
    personal = context.get("personal")
    if not isinstance(personal, Mapping):
        raise CandidateCaseError("contexto de purge nativo inválido")
    personal_removed = all(
        isinstance(relative, str) and not os.path.lexists(Path(target) / relative)
        for relative in personal
    )
    if not personal_removed:
        raise CandidateCaseError("purge preservou dados pessoais que deveriam ser removidos")
    return {
        "installation_removed": True,
        "personal_removed": True,
        "termination": "controlled",
        "process_exit_code": 0,
    }


def _validate_preserved_files(target: Path, context: Mapping[str, object]) -> tuple[bool, bool]:
    personal = context.get("personal")
    paks = context.get("paks")
    if not isinstance(personal, Mapping) or not isinstance(paks, Mapping):
        raise CandidateCaseError("contexto de preservação nativo inválido")
    personal_ok = True
    for relative, identity in personal.items():
        if not isinstance(relative, str) or not isinstance(identity, Mapping):
            personal_ok = False
            continue
        path = Path(target) / relative
        try:
            personal_ok = personal_ok and _target_digest(path, "arquivo pessoal nativo") == identity.get("sha256")
        except CandidateCaseError:
            personal_ok = False
    pak_ok = True
    for relative, identity in paks.items():
        if not isinstance(relative, str) or not isinstance(identity, Mapping):
            pak_ok = False
            continue
        try:
            pak_ok = pak_ok and _target_digest(Path(target) / relative, "PAK nativo") == identity.get("sha256")
        except CandidateCaseError:
            pak_ok = False
    return personal_ok, pak_ok


def _state_document(target: Path) -> tuple[Path, dict[str, object], str]:
    path = _regular_target_file(Path(target) / ".x86qw/state.json", "estado de instalação")
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CandidateCaseError("estado de instalação não é JSON válido") from error
    if not isinstance(value, dict):
        raise CandidateCaseError("estado de instalação não é objeto JSON")
    return path, value, hashlib.sha256(payload).hexdigest()


def _validate_state_apply(
    target: Path, context: Mapping[str, object], *, label: str,
) -> dict[str, object]:
    _path, state, after_digest = _state_document(target)
    if state.get("format") != 2:
        raise CandidateCaseError(f"{label} não convergiu o estado para formato 2")
    before_digest = context.get("state_before_sha256")
    if not isinstance(before_digest, str) or before_digest == after_digest:
        raise CandidateCaseError(f"{label} não aplicou uma mutação de estado")
    personal_ok, pak_ok = _validate_preserved_files(target, context)
    if not personal_ok or not pak_ok:
        raise CandidateCaseError(f"{label} alterou dados pessoais ou PAKs registrados")
    return {
        "state_before_sha256": before_digest,
        "state_after_sha256": after_digest,
        "state_converged": True,
        "no_downgrade": state.get("installation_version") in {"1.0.0", "1.0.0-rc.1"},
        "profile_preserved": isinstance(state.get("profile"), str),
        "mutation_applied": True,
        "personal_preserved": personal_ok,
        "pak_preserved": pak_ok,
        "termination": "controlled",
        "process_exit_code": 0,
    }


def _validate_migration_apply(
    target: Path, context: Mapping[str, object], *, label: str,
) -> dict[str, object]:
    result = _validate_state_apply(target, context, label=label)
    return {
        "source_version": context.get("source_version"),
        "fixture_state_sha256": context.get("fixture_state_sha256"),
        "fixture_version_sha256": context.get("fixture_version_sha256"),
        "state_before_sha256": result["state_before_sha256"],
        "state_after_sha256": result["state_after_sha256"],
        "migration_applied": result["mutation_applied"],
        "state_converged": result["state_converged"],
        "personal_preserved": result["personal_preserved"],
        "pak_preserved": result["pak_preserved"],
        "termination": "controlled",
        "process_exit_code": 0,
    }


def _validate_repair_apply(target: Path, context: Mapping[str, object]) -> dict[str, object]:
    relative = context.get("path")
    payload_digest = context.get("payload_sha256")
    if not isinstance(relative, str) or not isinstance(payload_digest, str):
        raise CandidateCaseError("contexto de repair nativo inválido")
    path = _regular_target_file(Path(target) / relative, "runtime reparado")
    restored = bool(stat.S_IMODE(path.stat().st_mode) & stat.S_IXUSR)
    restored = restored and _target_digest(path, "runtime reparado") == payload_digest
    personal_ok, pak_ok = _validate_preserved_files(target, context)
    if not restored or not personal_ok or not pak_ok:
        raise CandidateCaseError("repair nativo não restaurou o runtime preservando dados")
    return {
        "path": relative,
        "repair_applied": True,
        "corruption_restored": True,
        "personal_preserved": personal_ok,
        "pak_preserved": pak_ok,
        "termination": "controlled",
        "process_exit_code": 0,
    }


def _cleanup_case_scratch(case_scratch: Path) -> None:
    case_scratch = Path(case_scratch).absolute()
    if case_scratch.is_symlink():
        raise CandidateCaseError("scratch do caso usa symlink")
    if not case_scratch.exists():
        return
    if not case_scratch.is_dir():
        raise CandidateCaseError("scratch do caso não é diretório")
    shutil.rmtree(case_scratch)


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
        "X86QW_NATIVE_CANDIDATE_ROOT": str(candidate.root),
        "X86QW_CANDIDATE_COMMIT": candidate.commit,
        "X86QW_NATIVE_STATE_ROOT": str(state_root),
        "X86QW_TEST_WINDOWED": "1",
        "X86QW_TEST_CONSOLE_LOG": "1",
    }
    target = Path(state_root) / _INSTALLATION_TARGET
    case_context: dict[str, object] | None = None
    if case == "migration-0.7.13-real":
        case_context = _prepare_legacy_state(candidate, target, source_version="0.7.13")
    elif case == "lifecycle-update-apply":
        case_context = _prepare_legacy_state(candidate, target, source_version="0.7.13")
    elif case == "lifecycle-upgrade-apply":
        case_context = _prepare_legacy_state(candidate, target, source_version="0.7.13")
    elif case == "lifecycle-migrate-apply":
        case_context = _prepare_legacy_state(candidate, target, source_version="0.7.13")
    elif case == "lifecycle-repair-corruption":
        case_context = _prepare_repair_corruption(target)
    elif case == "lifecycle-uninstall":
        case_context = _prepare_uninstall_context(target)
    elif case == "lifecycle-purge":
        case_context = None
    native_frogbot_config: Path | None = None
    try:
        if case == "lifecycle-purge":
            case_context = _prepare_purge_context(
                candidate, target, scratch, state_root, environment,
            )
        if case == "game-ktx-frogbot":
            native_frogbot_config = _prepare_native_frogbot_config(target)
        observation: dict[str, object] | None = None
        if case in _CLIENT_CASES and sys.platform == "darwin":
            prepared = _isolate_macos_app(prepared, case, scratch)
            exit_code, stdout, stderr, observation = _run_client_process(
                prepared, case, Path(state_root) / _INSTALLATION_TARGET, environment, scratch,
            )
            result = subprocess.CompletedProcess(prepared.argv, exit_code, stdout, stderr)
        elif case in _SERVICE_SUFFIXES and sys.platform == "darwin":
            exit_code, stdout, stderr, observation = _run_service_process(
                prepared,
                case,
                candidate,
                Path(state_root) / _INSTALLATION_TARGET,
                scratch,
                environment,
            )
            result = subprocess.CompletedProcess(prepared.argv, exit_code, stdout, stderr)
        else:
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
        if case == CANONICAL_CASES[0] and exit_code == 0:
            observation = _run_installed_launcher_contract(
                candidate=candidate,
                target=target,
                environment=environment,
            )
        if exit_code == 0 and case_context is not None:
            if case in {"migration-0.7.13-real", "lifecycle-migrate-apply"}:
                observation = _validate_migration_apply(
                    target, case_context, label=case,
                )
            elif case == "lifecycle-repair-corruption":
                observation = _validate_repair_apply(target, case_context)
            elif case == "lifecycle-uninstall":
                observation = _validate_uninstall_apply(target, case_context)
            elif case == "lifecycle-purge":
                observation = _validate_purge_apply(target, case_context)
            else:
                observation = _validate_state_apply(target, case_context, label=case)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"[ERRO] execução nativa falhou: {case}: {error}", file=sys.stderr)
        exit_code = 127
        result = subprocess.CompletedProcess(prepared.argv, exit_code, b"", str(error).encode())
    finally:
        if native_frogbot_config is not None:
            _remove_native_frogbot_config(native_frogbot_config)
        _cleanup_case_scratch(scratch)
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    passed = exit_code == 0
    state_after = (
        "uninstalled"
        if case in {"lifecycle-uninstall", "lifecycle-purge"} and passed
        else "installed"
    )
    if passed:
        _write_state(state_root, status=state_after, case=case)
    value: dict[str, object] = {
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
    }
    if observation is not None:
        value["observations"] = observation
    _write_receipt(receipt, value, candidate=candidate)
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
