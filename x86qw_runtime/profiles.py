"""User-owned profile files, separated from managed defaults, cache and personal data."""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath


DATA_CLASSES = ("profile", "cache", "personal")
DEFAULT_PROFILE_BUNDLE = "x86qw-profile.zip"
_PROFILE_FILES = (
    "ezquake/configs/config.cfg",
    "ezquake/configs/preset.cfg",
    "qtv/qtv.cfg",
    "qwfwd/qwfwd.cfg",
)
_PROFILE_ROOTS = frozenset({"arena", "fortress", "prox", "qw", "td2"})
_CACHE_ROOTS = ("ezquake/sb/cache", "ezquake/temp")
_PERSONAL_ROOTS = (
    "ezquake/.ezquake_history",
    "ezquake/qconsole.log",
    "qw/qconsole.log",
    "qw/demos",
    "qw/screenshots",
    "logs",
    "td2/demos",
)
_BUNDLE_NOTICE = (
    "Modo owner-only: perfil user-owned. Sem upload automático. "
    "Este zip contém só configurações pessoais, sem cache, demos ou bytes gerenciados.\n"
)


def is_user_profile_path(relative: str) -> bool:
    """Return True when a POSIX path is a user-owned profile file."""

    path = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or path.is_absolute()
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        return False
    if relative in _PROFILE_FILES:
        return True
    return (
        len(path.parts) == 2
        and path.parts[0] in _PROFILE_ROOTS
        and path.name.startswith("x86qw-")
        and path.suffix in {".cfg", ".json"}
    )


def classify_install_data(target: Path) -> dict[str, tuple[str, ...]]:
    """List existing profile, cache and personal paths under one install."""

    target = Path(target)
    profile = [path for path in _PROFILE_FILES if _regular_file(target / path)]
    if target.is_dir() and not target.is_symlink():
        for root in sorted(_PROFILE_ROOTS):
            directory = target / root
            if not directory.is_dir() or directory.is_symlink():
                continue
            for child in sorted(directory.iterdir()):
                relative = f"{root}/{child.name}"
                if relative not in profile and is_user_profile_path(relative) and _regular_file(child):
                    profile.append(relative)
    return {
        "profile": tuple(profile),
        "cache": tuple(path for path in _CACHE_ROOTS if _exists(target / path)),
        "personal": tuple(path for path in _PERSONAL_ROOTS if _exists(target / path)),
    }


def render_profile_report(report: Mapping[str, Sequence[str]]) -> str:
    lines = ["x86QW profile — owner-only", ""]
    for key in DATA_CLASSES:
        items = list(report[key])
        lines.append(f"{key}: {len(items)}")
        lines.extend(f"  {item}" for item in items)
    return "\n".join(lines) + "\n"


def backup_user_profile(target: Path, destination: Path) -> Path:
    """Write a zip of user-owned profile files that does not touch the install."""

    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise OSError("profile backup destination already exists")
    files = classify_install_data(target)["profile"]
    payloads: dict[str, bytes] = {}
    entries = []
    for relative in files:
        payload = (Path(target) / relative).read_bytes()
        payloads[relative] = payload
        entries.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size": len(payload),
            }
        )
    manifest = {"format": 1, "kind": "profile", "files": entries}
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name + ".tmp")
    try:
        with zipfile.ZipFile(staging, "w") as archive:
            archive.writestr("NOTICE.txt", _BUNDLE_NOTICE)
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n",
            )
            for relative, payload in payloads.items():
                archive.writestr(relative, payload)
        staging.replace(destination)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    return destination


def restore_user_profile(archive: Path, target: Path) -> tuple[str, ...]:
    """Restore profile files byte-for-byte and roll back on failure."""

    target = Path(target)
    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
        extra = names - {"NOTICE.txt", "manifest.json"}
        manifest = json.loads(bundle.read("manifest.json").decode("utf-8"))
        if manifest.get("kind") != "profile" or manifest.get("format") != 1:
            raise ValueError("profile backup has an invalid manifest")
        planned = tuple(item["path"] for item in manifest["files"])
        if extra != set(planned):
            raise ValueError("profile backup contents diverge from the manifest")
        payloads: dict[str, bytes] = {}
        for item in manifest["files"]:
            relative = str(item["path"])
            if not is_user_profile_path(relative):
                raise ValueError(f"profile backup contains a non-profile path: {relative}")
            payload = bundle.read(relative)
            if hashlib.sha256(payload).hexdigest() != item["sha256"] or len(payload) != item["size"]:
                raise ValueError(f"profile backup hash mismatch: {relative}")
            payloads[relative] = payload

    originals: dict[str, bytes | None] = {}
    try:
        for relative, payload in payloads.items():
            destination = target.joinpath(*PurePosixPath(relative).parts)
            if destination.is_symlink():
                raise OSError(f"profile destination is a symlink: {relative}")
            originals[relative] = destination.read_bytes() if destination.is_file() else None
            destination.parent.mkdir(parents=True, exist_ok=True)
            staging = destination.with_name(destination.name + ".restore-tmp")
            try:
                staging.write_bytes(payload)
                staging.replace(destination)
            finally:
                staging.unlink(missing_ok=True)
    except BaseException:
        for relative, payload in originals.items():
            destination = target.joinpath(*PurePosixPath(relative).parts)
            if payload is None:
                destination.unlink(missing_ok=True)
            else:
                destination.write_bytes(payload)
        raise
    return tuple(payloads)


def _regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _exists(path: Path) -> bool:
    return path.exists() and not path.is_symlink()
