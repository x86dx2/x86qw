#!/usr/bin/env python3
"""Renew the public TUF timestamp and redeploy only that file.

The online timestamp key is the only key this command loads.  It starts from
the live ``timestamp.json`` (never from a stale checkout, which would mint a
second ``N+1`` with different bytes), renews it with
``tuf_timestamp_renewal.py``, writes the result into the checked-out site
projection, assembles the site generation exactly like the release workflows
and refuses to deploy unless the only file that differs from the live site is
``api/v1/trust/metadata/timestamp.json``.

``prepare`` leaves the deployable tree in ``<work-dir>/site``; the caller
deploys it and then runs ``confirm`` to check the published bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
for wheel in sorted((PROJECT_ROOT / "maintenance/vendor/wheels").glob("*.whl")):
    sys.path.insert(0, str(wheel))

from maintenance.tools.assemble_site_release import assemble_site_release  # noqa: E402
from maintenance.tools.render_release_site import render_release_site  # noqa: E402
from maintenance.tools.tuf_timestamp_renewal import (  # noqa: E402
    TimestampRenewalError,
    renew_timestamp,
)
from x86qw_runtime.io.downloader import (  # noqa: E402
    BoundedMetadata,
    DownloadError,
    RetryPolicy,
    download,
)


SITE_URL = "https://qw.x86.com.br/"
PROJECTION = PROJECT_ROOT / "site/public"
TRUST = PROJECTION / "api/v1/trust"
ROOT = PROJECT_ROOT / "maintenance/trust/root.json"
CATALOG = PROJECTION / "api/v1/catalog.json"
PRODUCT = PROJECTION / "api/v1/product.json"
TIMESTAMP_PATH = "api/v1/trust/metadata/timestamp.json"
# Deploy provenance and release truth describe the last release deploy; a
# timestamp renewal carries the live bytes forward unchanged.
LIVE_CARRIED = ("api/v1/release-truth.json", "version", "api/v1/version", "api/v1/health")
# Cloudflare injects this bot-management script into served HTML.
EDGE_SCRIPT = re.compile(rb"<script>\(function\(\)\{function c\(\).*?cdn-cgi/challenge-platform.*?</script>", re.DOTALL)
NO_STORE_HEADERS = (
    "\n/api/v1/product.json\n  Cache-Control: no-store, no-cache, must-revalidate\n"
    "  CDN-Cache-Control: no-store\n  Access-Control-Allow-Origin: *\n"
    "\n/api/v1/release-truth.json\n  Cache-Control: no-store, no-cache, must-revalidate\n"
    "  CDN-Cache-Control: no-store\n  Access-Control-Allow-Origin: *\n"
)
MAX_LIVE_BYTES = 16 * 1024 * 1024


class RenewalError(RuntimeError):
    """The renewal cannot be proven safe."""


def fetch(path: str) -> bytes:
    try:
        result = download(BoundedMetadata(
            url=SITE_URL + path,
            maximum_size=MAX_LIVE_BYTES,
            deadline_seconds=60,
            retry=RetryPolicy(attempts=2),
            headers={"Cache-Control": "no-cache", "User-Agent": "x86qw-timestamp-renewal/1"},
            label=f"site público {path or '/'}",
        ))
    except DownloadError as error:
        raise RenewalError(f"não foi possível baixar {SITE_URL + path}: {error}") from error
    if result.data is None:
        raise RenewalError(f"download de {path} não retornou bytes")
    return result.data


def signed(payload: bytes) -> dict:
    return json.loads(payload)["signed"]


def current_generation(destination: Path) -> int:
    """Copy the generation the projection's timestamp points at."""

    timestamp = (TRUST / "metadata/timestamp.json").read_bytes()
    version = signed(timestamp)["meta"]["snapshot.json"]["version"]
    snapshot = (TRUST / f"metadata/{version}.snapshot.json").read_bytes()
    targets_version = signed(snapshot)["meta"]["targets.json"]["version"]
    targets = (TRUST / f"metadata/{targets_version}.targets.json").read_bytes()
    catalog_digest = signed(targets)["targets"]["catalog/catalog.json"]["hashes"]["sha256"]
    files = {
        "metadata/1.root.json": TRUST / "metadata/1.root.json",
        f"metadata/{version}.snapshot.json": TRUST / f"metadata/{version}.snapshot.json",
        f"metadata/{targets_version}.targets.json": TRUST / f"metadata/{targets_version}.targets.json",
        "metadata/timestamp.json": TRUST / "metadata/timestamp.json",
        f"targets/catalog/{catalog_digest}.catalog.json": TRUST / f"targets/catalog/{catalog_digest}.catalog.json",
    }
    for relative, source in files.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    if hashlib.sha256(CATALOG.read_bytes()).hexdigest() != catalog_digest:
        raise RenewalError("catálogo do checkout diverge do target TUF corrente")
    return signed(timestamp)["version"]


def key_id_for(key: Path) -> str:
    """Return the root timestamp key id matching a PKCS#8 Ed25519 PEM."""

    from securesystemslib._vendor.ed25519.ed25519 import publickey_unsafe
    import base64

    lines = key.read_text(encoding="ascii").strip().splitlines()
    der = base64.b64decode("".join(lines[1:-1]))
    public = publickey_unsafe(der[-32:]).hex()
    root = json.loads(ROOT.read_bytes())["signed"]
    for keyid in root["roles"]["timestamp"]["keyids"]:
        if root["keys"][keyid]["keyval"]["public"] == public:
            return keyid
    raise RenewalError("a chave fornecida não é uma chave timestamp da root")


def assemble(work: Path, trust_repository: Path) -> Path:
    current = work / "current-site"
    render_release_site(
        source=PROJECTION, catalog=CATALOG, product=PRODUCT, output=current,
        bootstrap_source=PROJECT_ROOT / "dist/installer/bin",
    )
    source = work / "site-source"
    shutil.copytree(current, source)
    with (source / "_headers").open("a", encoding="utf-8") as headers:
        headers.write(NO_STORE_HEADERS)
    for relative in LIVE_CARRIED:
        (source / relative).write_bytes(fetch(relative))
    output = work / "site/public"
    output.parent.mkdir(parents=True)
    shutil.copyfile(PROJECT_ROOT / "site/wrangler.jsonc", work / "site/wrangler.jsonc")
    assemble_site_release(
        site_source=source, catalog=CATALOG, product=PRODUCT,
        trust_repository=trust_repository, output=output,
    )
    return output


def changed_against_live(tree: Path) -> list[str]:
    changed = []
    for path in sorted(tree.rglob("*")):
        if not path.is_file() or path.name == "_headers":
            continue
        relative = path.relative_to(tree).as_posix()
        live = fetch("" if relative == "index.html" else relative)
        local = path.read_bytes()
        if relative.endswith(".html"):
            live = EDGE_SCRIPT.sub(b"", live)
        if live != local:
            changed.append(relative)
    return changed


def prepare(timestamp_key: Path, lease_hours: int, work: Path) -> dict[str, object]:
    work.mkdir(parents=True, exist_ok=True)
    live_timestamp = fetch(TIMESTAMP_PATH)
    if live_timestamp != (TRUST / "metadata/timestamp.json").read_bytes():
        raise RenewalError(
            "timestamp do checkout diverge do publicado; sincronize a projeção antes de renovar"
        )
    source = work / "source"
    version = current_generation(source)
    renewed = work / "renewed"
    report = renew_timestamp(
        repository=source,
        root=ROOT,
        catalog=CATALOG,
        timestamp_key=timestamp_key,
        key_id=key_id_for(timestamp_key),
        output=renewed,
        report=work / "report.json",
        lease_hours=lease_hours,
    )
    if report["renewed"]["timestamp_version"] != version + 1:
        raise RenewalError("renovação não produziu exatamente N+1")
    tree = assemble(work, renewed)
    changed = changed_against_live(tree)
    if changed != [TIMESTAMP_PATH]:
        raise RenewalError(f"o deploy mudaria arquivos além do timestamp: {changed}")
    shutil.copyfile(renewed / "metadata/timestamp.json", TRUST / "metadata/timestamp.json")
    return {
        "status": "prepared",
        "timestamp_version": report["renewed"]["timestamp_version"],
        "expires": report["renewed"]["expires"],
        "site": str(tree.parent),
    }


def confirm() -> dict[str, object]:
    if fetch(TIMESTAMP_PATH) != (TRUST / "metadata/timestamp.json").read_bytes():
        raise RenewalError("timestamp publicado diverge do renovado")
    return {"status": "confirmed", "timestamp_version": signed((TRUST / "metadata/timestamp.json").read_bytes())["version"]}


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare", help="renova e monta o site sem publicar")
    prepare_parser.add_argument("--timestamp-key", type=Path, required=True)
    prepare_parser.add_argument("--lease-hours", type=int, default=30 * 24)
    prepare_parser.add_argument("--work-dir", type=Path)
    commands.add_parser("confirm", help="confere o timestamp publicado após o deploy")
    options = parser.parse_args(arguments)
    if options.command == "prepare":
        work = Path(options.work_dir or tempfile.mkdtemp(prefix="x86qw-timestamp-renewal-"))
        result = prepare(options.timestamp_key, options.lease_hours, work)
    else:
        result = confirm()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RenewalError, TimestampRenewalError) as error:
        print(f"[ERRO] {error}", file=sys.stderr)
        raise SystemExit(1)
