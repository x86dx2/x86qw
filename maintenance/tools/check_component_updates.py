#!/usr/bin/env python3
"""Check whether tracked x86QW component sources still represent their latest upstreams."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

try:
    from .component_releases import load_releases
    from .public_upstreams import git_remote_revision, github_latest_release
except ImportError:  # Execucao direta
    from component_releases import load_releases
    from public_upstreams import git_remote_revision, github_latest_release


ROOT = Path(__file__).resolve().parents[2]
COMPONENTS = ROOT / "maintenance/inventory/components.json"
RELEASES = ROOT / "maintenance/inventory/component-releases.json"
USER_AGENT = "x86qw-freshness/1"


def remote_fingerprint(url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    size = 0
    with urllib.request.urlopen(request, timeout=120) as response:
        while block := response.read(1024 * 1024):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def check_updates(releases: dict[str, object], *, online: bool) -> list[dict[str, str]]:
    reference = releases["reference"]
    assert isinstance(reference, dict)
    reference_revision = str(reference["revision"])
    current_reference = reference_revision
    if online:
        repository = str(reference["repository"])
        current_reference = git_remote_revision(repository, "refs/heads/master")

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
        if release["strategy"] == "reference-overlay" and isinstance(upstream, dict):
            actual = str(upstream["release"])
            if current_reference != reference_revision:
                status = "update-available"
        if online and isinstance(upstream, dict) and isinstance(upstream.get("repository"), str):
            actual = github_latest_release(str(upstream["repository"]))
            if actual != upstream["release"]:
                status = "update-available"
        elif online and release["strategy"] == "upstream-package":
            artifacts = release["artifacts"]
            assert isinstance(artifacts, list) and len(artifacts) == 1
            artifact = artifacts[0]
            assert isinstance(artifact, dict)
            fingerprint = remote_fingerprint(str(artifact["url"]))
            if fingerprint != (artifact["size"], artifact["sha256"]):
                actual = "source-artifact-changed"
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
