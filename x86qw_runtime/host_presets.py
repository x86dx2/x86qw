"""Declarative local hosting presets without secrets."""

from __future__ import annotations

import json
import re
from argparse import Namespace
from collections.abc import Mapping
from pathlib import Path
from typing import Any


HOST_PRESETS_PATH = "qw/x86qw-host-presets.json"
MAX_PRESETS = 20
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
_SECRET_FIELDS = (
    "password",
    "spectator_password",
    "rcon_password",
    "qtv_password",
    "prompt_password",
    "prompt_spectator_password",
    "prompt_rcon_password",
    "prompt_qtv_password",
    "password_file",
    "spectator_password_file",
    "rcon_password_file",
    "qtv_password_file",
)
_PRESET_FIELDS = (
    "name",
    "game",
    "mode",
    "map",
    "bind",
    "port",
    "hostname",
    "maxclients",
    "no_mvd",
    "with_qtv",
    "qtv_bind",
    "qtv_port",
    "with_proxy",
    "proxy_bind",
    "proxy_port",
)


def load_host_presets(target: Path) -> dict[str, dict[str, object]]:
    """Return named host presets without creating the file."""

    path = Path(target) / HOST_PRESETS_PATH
    if path.is_symlink():
        raise ValueError("host preset path is a symlink")
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("host preset file is not valid JSON") from error
    return _document(payload)


def save_host_preset(target: Path, name: str, options: Namespace) -> dict[str, object]:
    """Persist one secret-free host layout under the install."""

    _reject_secrets(options)
    preset = _preset_from_options(name, options)
    presets = load_host_presets(target)
    presets[preset["name"]] = preset  # type: ignore[index]
    if len(presets) > MAX_PRESETS:
        raise ValueError("host presets are full")
    _write_presets(target, presets)
    return preset


def apply_host_preset(options: Namespace, preset: Mapping[str, object]) -> None:
    """Copy a stored layout onto host CLI options."""

    options.selection = str(preset["game"])
    options.game = str(preset["game"])
    options.mode = str(preset["mode"]) or None
    options.map = str(preset["map"])
    options.bind = str(preset["bind"])
    options.port = int(preset["port"])  # type: ignore[arg-type]
    options.hostname = str(preset["hostname"]) or None
    options.maxclients = int(preset["maxclients"])  # type: ignore[arg-type]
    options.no_mvd = bool(preset["no_mvd"])
    options.with_qtv = bool(preset["with_qtv"])
    options.qtv_bind = str(preset["qtv_bind"])
    options.qtv_port = int(preset["qtv_port"])  # type: ignore[arg-type]
    options.with_proxy = bool(preset["with_proxy"])
    options.proxy_bind = str(preset["proxy_bind"])
    options.proxy_port = int(preset["proxy_port"])  # type: ignore[arg-type]


def _reject_secrets(options: Namespace) -> None:
    for field in _SECRET_FIELDS:
        if getattr(options, field, None):
            raise ValueError("host presets cannot store passwords or password sources")


def _preset_from_options(name: str, options: Namespace) -> dict[str, object]:
    if not _NAME.fullmatch(name):
        raise ValueError(f"invalid host preset name: {name!r}")
    game = str(getattr(options, "game", "") or "")
    map_name = str(getattr(options, "map", "") or "")
    if not game or not map_name:
        raise ValueError("host preset requires game and map")
    mode = getattr(options, "mode", None)
    hostname = getattr(options, "hostname", None) or ""
    return {
        "name": name,
        "game": game,
        "mode": "" if mode is None else str(mode),
        "map": map_name,
        "bind": str(options.bind),
        "port": int(options.port),
        "hostname": str(hostname),
        "maxclients": int(options.maxclients),
        "no_mvd": bool(options.no_mvd),
        "with_qtv": bool(options.with_qtv),
        "qtv_bind": str(options.qtv_bind),
        "qtv_port": int(options.qtv_port),
        "with_proxy": bool(options.with_proxy),
        "proxy_bind": str(options.proxy_bind),
        "proxy_port": int(options.proxy_port),
    }


def _document(payload: object) -> dict[str, dict[str, object]]:
    if not isinstance(payload, dict) or payload.get("format") != 1 or payload.get("kind") != "host-presets":
        raise ValueError("host preset file has an invalid manifest")
    if set(payload) != {"format", "kind", "presets"}:
        raise ValueError("host preset file has extra or missing fields")
    raw = payload["presets"]
    if not isinstance(raw, list) or len(raw) > MAX_PRESETS:
        raise ValueError("host presets are invalid")
    presets: dict[str, dict[str, object]] = {}
    for item in raw:
        preset = _closed_preset(item)
        name = str(preset["name"])
        if name in presets:
            raise ValueError(f"duplicate host preset: {name}")
        presets[name] = preset
    return presets


def _closed_preset(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(_PRESET_FIELDS):
        raise ValueError("host preset entry is invalid")
    name = value["name"]
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("host preset name is invalid")
    port = value["port"]
    maxclients = value["maxclients"]
    qtv_port = value["qtv_port"]
    proxy_port = value["proxy_port"]
    for field, item in (
        ("port", port),
        ("qtv_port", qtv_port),
        ("proxy_port", proxy_port),
    ):
        if isinstance(item, bool) or not isinstance(item, int) or not 1024 <= item <= 65535:
            raise ValueError(f"host preset {field} is invalid")
    if isinstance(maxclients, bool) or not isinstance(maxclients, int) or not 1 <= maxclients <= 32:
        raise ValueError("host preset maxclients is invalid")
    for flag in ("no_mvd", "with_qtv", "with_proxy"):
        if type(value[flag]) is not bool:
            raise ValueError(f"host preset {flag} is invalid")
    for field in ("game", "mode", "map", "bind", "hostname", "qtv_bind", "proxy_bind"):
        if not isinstance(value[field], str):
            raise ValueError(f"host preset {field} is invalid")
    if not value["game"] or not value["map"]:
        raise ValueError("host preset requires game and map")
    return {field: value[field] for field in _PRESET_FIELDS}


def _write_presets(target: Path, presets: Mapping[str, Mapping[str, object]]) -> None:
    destination = Path(target) / HOST_PRESETS_PATH
    if destination.is_symlink():
        raise ValueError("host preset path is a symlink")
    payload: dict[str, Any] = {
        "format": 1,
        "kind": "host-presets",
        "presets": [dict(presets[name]) for name in sorted(presets)],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name + ".tmp")
    try:
        staging.write_text(encoded, encoding="utf-8")
        staging.replace(destination)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    finally:
        staging.unlink(missing_ok=True)
