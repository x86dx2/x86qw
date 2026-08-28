#!/usr/bin/env python3
"""Verify the live release-truth authority and its public alias."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from x86qw_runtime.io.remote import RemoteClient


DEFAULT_POINTER = ROOT / "docs/post-1.0/release-truth-current.json"
MAX_RELEASE_TRUTH_BYTES = 1024 * 1024


class _QuietReporter:
    def detail(self, message: str) -> None:
        del message

    def warning(self, message: str) -> None:
        del message

    def download_progress(
        self, received: int, total: int | None, *, done: bool = False
    ) -> None:
        del received, total, done


def _utc_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("TUF timestamp expiry must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_release_truth(
    document: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Validate the audience boundary and the minimum live health contract."""

    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    status = document.get("status", {})
    authorities = document.get("authorities", {})
    candidate = authorities.get("candidate_release", {})
    tuf = authorities.get("deployment", {}).get("tuf", {})

    if status.get("main") != "GREEN":
        raise ValueError("live main status is not GREEN")
    if status.get("tuf") != "HEALTHY" or tuf.get("operational_status") != "HEALTHY":
        raise ValueError("live TUF status is not HEALTHY")
    if candidate.get("audience") != "owner-only":
        raise ValueError("release audience is not owner-only")
    if candidate.get("external_public_authorized") is not False:
        raise ValueError("external-public authorization must remain false")
    if status.get("external_public") != "NO-GO":
        raise ValueError("external-public status must remain NO-GO")

    expiry = _utc_timestamp(str(tuf.get("timestamp_expiry", "")))
    seconds_remaining = int((expiry - observed_at).total_seconds())
    if seconds_remaining <= 0:
        raise ValueError("live TUF timestamp is expired")

    snapshot_commit = str(document.get("snapshot_commit", ""))
    if len(snapshot_commit) != 40 or any(
        character not in "0123456789abcdef" for character in snapshot_commit
    ):
        raise ValueError("snapshot_commit is not a full lowercase Git SHA")

    return {
        "snapshot_commit": snapshot_commit,
        "release_audience": candidate["audience"],
        "external_public": status["external_public"],
        "tuf_timestamp_expiry": expiry.isoformat().replace("+00:00", "Z"),
        "tuf_seconds_remaining": seconds_remaining,
    }


def _fetch_json(
    url: str, timeout: float, client: RemoteClient
) -> tuple[bytes, dict[str, Any]]:
    body = client.get(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "x86qw-release-truth-verifier/1",
        },
        maximum_size=MAX_RELEASE_TRUTH_BYTES,
        timeout=timeout,
        attempts=1,
    )
    document = json.loads(body)
    if not isinstance(document, dict):
        raise ValueError(f"{url} did not return a JSON object")
    return body, document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pointer", type=Path, default=DEFAULT_POINTER)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)

    try:
        pointer = json.loads(args.pointer.read_text(encoding="utf-8"))
        client = RemoteClient(_QuietReporter())
        primary_body, primary = _fetch_json(
            pointer["authority_url"], args.timeout, client
        )
        alias_body, _alias = _fetch_json(pointer["alias_url"], args.timeout, client)
        if primary_body != alias_body:
            raise ValueError("canonical and alias release-truth bytes diverge")
        summary = validate_release_truth(primary)
        summary.update(
            {
                "authority_url": pointer["authority_url"],
                "alias_url": pointer["alias_url"],
                "canonical_alias_equal": True,
            }
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"release-truth verification failed: {error}", file=sys.stderr)
        return 1

    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
