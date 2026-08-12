#!/usr/bin/env python3
"""Monitor the public TUF lease without signing or publishing metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from maintenance.tools.verify_public_tuf import (  # noqa: E402
    DEFAULT_BASE_URL,
    _base_urls,
    _network_fetcher,
)
from x86qw_runtime.io.metadata import read_bounded_regular_file  # noqa: E402
from x86qw_runtime.trust import load_trusted_catalog  # noqa: E402


METADATA_NAME = re.compile(r"^(?:\d+\.)?(timestamp|snapshot|targets)\.json$")
UTC = timezone.utc


class PublicTufMonitorError(RuntimeError):
    """The public TUF chain or its lease is not healthy."""


class RecordingFetcher:
    """Keep the authenticated metadata bytes for lease reporting."""

    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.records: dict[str, bytes] = {}

    def download_bytes(self, url: str, max_length: int) -> bytes:
        payload = self.delegate.download_bytes(url, max_length)
        self.records[url] = payload
        return payload

    @contextmanager
    def download_file(self, url: str, max_length: int):
        payload = self.download_bytes(url, max_length)
        from io import BytesIO

        with BytesIO(payload) as stream:
            yield stream


def _metadata_payload(records: dict[str, bytes], role: str) -> bytes:
    candidates = [
        payload
        for url, payload in records.items()
        if (match := METADATA_NAME.fullmatch(Path(urlsplit(url).path).name))
        and match.group(1) == role
    ]
    if len(candidates) != 1:
        raise PublicTufMonitorError(
            f"metadata TUF {role} observada {len(candidates)} vez(es); esperado exatamente uma"
        )
    return candidates[0]


def _expires(payload: bytes, role: str) -> tuple[int, datetime]:
    try:
        document = json.loads(payload.decode("utf-8"))
        signed = document["signed"]
        version = signed["version"]
        expires = signed["expires"]
        when = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicTufMonitorError(f"metadata TUF {role} é inválida para monitoramento") from error
    if type(version) is not int or version < 1 or when.tzinfo is None:
        raise PublicTufMonitorError(f"metadata TUF {role} possui versão/expiração inválida")
    return version, when.astimezone(UTC)


def monitor_public_tuf(
    *, root: Path, base_url: str = DEFAULT_BASE_URL, warning_hours: int = 72,
) -> dict[str, object]:
    if type(warning_hours) is not int or not 1 <= warning_hours <= 8760:
        raise PublicTufMonitorError("warning_hours deve estar entre 1 e 8760")
    root = Path(root)
    try:
        root_bytes = read_bounded_regular_file(root, maximum_size=512 * 1024)
    except OSError as error:
        raise PublicTufMonitorError(f"root TUF incorporada ausente ou insegura: {root}") from error
    metadata_url, target_url = _base_urls(base_url)
    fetcher = RecordingFetcher(_network_fetcher())
    try:
        with tempfile.TemporaryDirectory(prefix="x86qw-tuf-monitor-metadata-") as metadata_dir, \
                tempfile.TemporaryDirectory(prefix="x86qw-tuf-monitor-targets-") as target_dir:
            catalog = load_trusted_catalog(
                bootstrap_root=root_bytes,
                metadata_dir=Path(metadata_dir),
                target_dir=Path(target_dir),
                metadata_base_url=metadata_url,
                target_base_url=target_url,
                fetcher=fetcher,
            )
    except Exception as error:
        if isinstance(error, PublicTufMonitorError):
            raise
        raise PublicTufMonitorError(f"cadeia TUF pública não autenticou: {error}") from error
    now = datetime.now(UTC)
    expires: dict[str, str] = {}
    versions: dict[str, int] = {}
    for role in ("timestamp", "snapshot", "targets"):
        version, expiry = _expires(_metadata_payload(fetcher.records, role), role)
        versions[role] = version
        expires[role] = expiry.isoformat().replace("+00:00", "Z")
        if expiry <= now + timedelta(hours=warning_hours):
            raise PublicTufMonitorError(
                f"lease TUF {role} expira em menos de {warning_hours} horas: {expires[role]}"
            )
    target_payloads = [
        payload for url, payload in fetcher.records.items()
        if Path(urlsplit(url).path).name.endswith(".catalog.json")
    ]
    target_payload = target_payloads[-1] if target_payloads else json.dumps(catalog, sort_keys=True).encode()
    return {
        "format": 1,
        "project": "x86qw",
        "status": "healthy",
        "catalog_sha256": hashlib.sha256(target_payload).hexdigest(),
        "package_count": len(catalog.get("packages", [])),
        "metadata_versions": versions,
        "metadata_expires": expires,
        "warning_hours": warning_hours,
        "checked_at": now.isoformat().replace("+00:00", "Z"),
    }


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--warning-hours", type=int, default=72)
    options = parser.parse_args(arguments)
    try:
        result = monitor_public_tuf(
            root=options.root, base_url=options.base_url, warning_hours=options.warning_hours,
        )
    except (OSError, PublicTufMonitorError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
