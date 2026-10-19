"""Run the Python CLI from this checkout with an isolated installer cache.

usage: pyrun.py ARGS...   (same arguments as the launcher passes to x86qw.pyz)

X86QW_CACHE_DIR, when set, replaces the per-user installer cache so
cleanup/uninstall/purge oracles never touch the real cache. HOME should
point at a scratch directory for the same reason (~/.local/bin link).
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "dist/installer/bin"))
sys.path.insert(0, str(REPO))

import manager  # noqa: E402

cache = os.environ.get("X86QW_CACHE_DIR")
if cache:
    adapter = manager.host_adapter

    def user_cache_directory(name, *args, **kwargs):
        return Path(cache)

    adapter.user_cache_directory = user_cache_directory

raise SystemExit(manager.main(sys.argv[1:]))
