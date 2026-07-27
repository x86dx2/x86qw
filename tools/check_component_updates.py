#!/usr/bin/env python3
"""Check whether tracked nQuake component sources still represent their latest upstreams."""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path

from nquake_releases import load_releases


ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "inventory/nquake-components.json"
RELEASES = ROOT / "inventory/nquake-releases.json"
USER_AGENT = "x86qw-freshness/1"


def github_json(path: str) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"https://api.github.com/{path}", headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def check_updates(releases: dict[str, object], *, online: bool) -> list[dict[str, str]]:
    reference = releases["reference"]
    assert isinstance(reference, dict)
    reference_revision = str(reference["revision"])
    current_reference = reference_revision
    if online:
        response = github_json("repos/nQuake/distfiles/commits/master")
        if not isinstance(response, dict) or not isinstance(response.get("sha"), str):
            raise ValueError("invalid nQuake upstream response")
        current_reference = response["sha"]

    results: list[dict[str, str]] = []
    components = releases["components"]
    assert isinstance(components, dict)
    for identifier, release in components.items():
        assert isinstance(release, dict)
        expected = str(release["version"])
        actual = expected
        status = "current"
        if release["strategy"] == "reference-snapshot" and current_reference != reference_revision:
            actual = current_reference[:12]
            status = "update-available"
        upstream = release.get("upstream")
        if online and isinstance(upstream, dict):
            response = github_json(f"repos/{upstream['repository']}/releases/latest")
            if not isinstance(response, dict) or not isinstance(response.get("tag_name"), str):
                raise ValueError(f"invalid upstream release response: {identifier}")
            actual = response["tag_name"]
            if actual != upstream["release"]:
                status = "update-available"
        results.append({
            "component": identifier,
            "installed": expected,
            "latest_source": actual,
            "status": status,
            "strategy": str(release["strategy"]),
        })
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="valida somente o inventário, sem consultar upstreams")
    parser.add_argument("--json", action="store_true", help="emite o resultado em JSON")
    arguments = parser.parse_args()
    releases = load_releases(RELEASES, COMPONENTS)
    results = check_updates(releases, online=not arguments.offline)
    if arguments.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for result in results:
            marker = "OK" if result["status"] == "current" else "UPDATE"
            print(f"[{marker}] {result['component']}: {result['installed']} ({result['strategy']})")
            if result["status"] != "current":
                print(f"         upstream atual: {result['latest_source']}")
    outdated = sum(result["status"] != "current" for result in results)
    print(f"{len(results)} componente(s) verificados; {outdated} atualização(ões) disponível(is).")
    return 2 if outdated else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"component update check failed: {error}")
        raise SystemExit(1)
