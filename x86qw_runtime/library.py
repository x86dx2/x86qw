"""Local favorites and recents with closed origin and freshness."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LIBRARY_PATH = "qw/x86qw-library.json"
ORIGINS = frozenset({"user", "hub", "local"})
MAX_FAVORITES = 20
MAX_RECENTS = 20
_ADDRESS = re.compile(r"^[A-Za-z0-9_.:\[\]-]+:[0-9]{1,5}$")
_ENTRY_FIELDS = ("address", "title", "origin", "freshness")


def load_library(target: Path) -> dict[str, tuple[dict[str, str], ...]]:
    """Return favorites and recents without creating the library file."""

    path = Path(target) / LIBRARY_PATH
    if path.is_symlink():
        raise ValueError("library path is a symlink")
    if not path.is_file():
        return {"favorites": (), "recents": ()}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("library file is not valid JSON") from error
    return _document(payload)


def add_favorite(
    target: Path,
    address: str,
    *,
    title: str = "",
    now: datetime | None = None,
) -> dict[str, str]:
    """Pin a user-owned server and refresh its freshness."""

    entry = _entry(address, title=title, origin="user", now=now)
    document = load_library(target)
    favorites = _upsert(document["favorites"], entry, limit=MAX_FAVORITES, drop=False)
    _write_library(target, favorites, document["recents"])
    return entry


def remove_favorite(target: Path, address: str) -> str:
    """Unpin a favorite without touching recents."""

    normalized = normalize_address(address)
    document = load_library(target)
    favorites = tuple(item for item in document["favorites"] if item["address"] != normalized)
    if len(favorites) == len(document["favorites"]):
        raise ValueError(f"favorite not found: {normalized}")
    _write_library(target, favorites, document["recents"])
    return normalized


def record_recent(
    target: Path,
    address: str,
    *,
    title: str = "",
    origin: str,
    now: datetime | None = None,
) -> dict[str, str]:
    """Record a recently joined server, newest first."""

    entry = _entry(address, title=title, origin=origin, now=now)
    document = load_library(target)
    recents = _upsert(document["recents"], entry, limit=MAX_RECENTS, drop=True)
    _write_library(target, document["favorites"], recents)
    return entry


def render_library_report(report: Mapping[str, Sequence[Mapping[str, str]]]) -> str:
    lines = ["x86QW library — owner-only", ""]
    for key in ("favorites", "recents"):
        items = list(report[key])
        lines.append(f"{key}: {len(items)}")
        for item in items:
            lines.append(
                f"  {item['address']}  {item['title']}  {item['origin']}  {item['freshness']}"
            )
    return "\n".join(lines) + "\n"


def discover_servers(
    remote: Sequence[Mapping[str, object]] | None,
    library: Mapping[str, Sequence[Mapping[str, str]]],
) -> tuple[dict[str, object], ...]:
    """Return remote hub rows when present, otherwise the local library."""

    if remote:
        return tuple(dict(item) for item in remote)
    seen: set[str] = set()
    servers: list[dict[str, object]] = []
    for key in ("favorites", "recents"):
        for item in library[key]:
            address = item["address"]
            if address in seen:
                continue
            seen.add(address)
            servers.append({
                "address": address,
                "title": item["title"],
                "origin": item["origin"],
                "freshness": item["freshness"],
                "mode": "-",
                "settings": {"hostname": item["title"], "mode": "-", "map": "-"},
                "players": [],
                "qtv_stream": None,
            })
    return tuple(servers)


def normalize_address(value: str) -> str:
    if not isinstance(value, str) or not _ADDRESS.fullmatch(value):
        raise ValueError(f"invalid server address: {value!r}")
    host, port_text = value.rsplit(":", 1)
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ValueError(f"invalid server port: {value!r}")
    if host.startswith("[") and host.endswith("]"):
        return f"{host}:{port}"
    return f"{host.casefold()}:{port}"


def _entry(address: str, *, title: str, origin: str, now: datetime | None) -> dict[str, str]:
    if origin not in ORIGINS:
        raise ValueError(f"invalid library origin: {origin!r}")
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("library clock must be timezone-aware")
    normalized = normalize_address(address)
    label = _title(title, normalized)
    freshness = clock.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "address": normalized,
        "title": label,
        "origin": origin,
        "freshness": freshness,
    }


def _title(value: str, fallback: str) -> str:
    cleaned = "".join(
        character if character.isprintable() and character != "\ufffd" else "?"
        for character in value
    ).strip()[:4096]
    return cleaned or fallback


def _document(payload: object) -> dict[str, tuple[dict[str, str], ...]]:
    if not isinstance(payload, dict) or payload.get("format") != 1 or payload.get("kind") != "library":
        raise ValueError("library file has an invalid manifest")
    if set(payload) != {"format", "kind", "favorites", "recents"}:
        raise ValueError("library file has extra or missing fields")
    return {
        "favorites": _entries(payload["favorites"], "favorites"),
        "recents": _entries(payload["recents"], "recents"),
    }


def _entries(value: object, label: str) -> tuple[dict[str, str], ...]:
    if not isinstance(value, list) or len(value) > (MAX_FAVORITES if label == "favorites" else MAX_RECENTS):
        raise ValueError(f"library {label} is invalid")
    items = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != set(_ENTRY_FIELDS):
            raise ValueError(f"library {label} entry is invalid")
        entry = _entry(
            str(raw["address"]),
            title=str(raw["title"]),
            origin=str(raw["origin"]),
            now=_parse_freshness(raw["freshness"]),
        )
        if entry["address"] in seen:
            raise ValueError(f"library {label} contains duplicate addresses")
        seen.add(entry["address"])
        items.append(entry)
    return tuple(items)


def _parse_freshness(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("library freshness is invalid")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _upsert(
    items: Sequence[Mapping[str, str]],
    entry: Mapping[str, str],
    *,
    limit: int,
    drop: bool,
) -> tuple[dict[str, str], ...]:
    updated = [dict(entry)]
    updated.extend(dict(item) for item in items if item["address"] != entry["address"])
    if len(updated) > limit:
        if drop:
            updated = updated[:limit]
        else:
            raise ValueError("library favorites are full")
    return tuple(updated)


def _write_library(
    target: Path,
    favorites: Sequence[Mapping[str, str]],
    recents: Sequence[Mapping[str, str]],
) -> None:
    destination = Path(target) / LIBRARY_PATH
    if destination.is_symlink():
        raise ValueError("library path is a symlink")
    payload: dict[str, Any] = {
        "format": 1,
        "kind": "library",
        "favorites": [dict(item) for item in favorites],
        "recents": [dict(item) for item in recents],
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
