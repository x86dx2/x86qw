#!/usr/bin/env python3
"""Verify the assembled site root without treating Cloudflare 403 as audience OK."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import TextIO


AUDIENCE_MARKER = "owner-only"
MAX_BODY_BYTES = 2 * 1024 * 1024
Fetch = Callable[[str], tuple[int, bytes]]


class RootProbeError(RuntimeError):
    """The assembled or live site root does not prove owner-only audience."""


def verify_root_probe(
    *,
    assembled_html: bytes,
    live_status: int,
    live_body: bytes,
    marker: str = AUDIENCE_MARKER,
) -> dict[str, object]:
    needle = marker.encode("ascii")
    if needle not in assembled_html:
        raise RootProbeError("assembled site HTML lacks owner-only")
    if live_status == 200:
        if needle not in live_body:
            raise RootProbeError("live root 200 lacks owner-only")
        return {
            "status": "verified",
            "live_status": 200,
            "live_root": marker,
        }
    if live_status == 403:
        return {
            "status": "verified-assembled",
            "live_status": 403,
            "live_root": "cloudflare_challenge",
        }
    raise RootProbeError(f"live root HTTP {live_status}")


def _fetch(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        headers={
            "Cache-Control": "no-cache",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status), response.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as error:
        return int(error.code), error.read(MAX_BODY_BYTES + 1)


def main(
    argv: list[str] | None = None,
    *,
    fetch: Fetch = _fetch,
    stdout: TextIO = sys.stdout,
) -> int:
    parser = argparse.ArgumentParser(description="Probe assembled and live site roots.")
    parser.add_argument("--assembled", required=True, type=Path)
    parser.add_argument("--live-url", required=True)
    parser.add_argument("--report", required=True, type=Path)
    options = parser.parse_args(argv)
    assembled = options.assembled.read_bytes()
    if len(assembled) > MAX_BODY_BYTES:
        raise RootProbeError("assembled site HTML exceeds the probe limit")
    live_status, live_body = fetch(options.live_url)
    if len(live_body) > MAX_BODY_BYTES:
        raise RootProbeError("live root body exceeds the probe limit")
    result = verify_root_probe(
        assembled_html=assembled,
        live_status=live_status,
        live_body=live_body,
    )
    options.report.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), file=stdout)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RootProbeError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1)
