from __future__ import annotations

import hashlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_nquake_packages import rewrite_zip_members  # noqa: E402
from nquake_components import components_by_id, load_catalog  # noqa: E402
from nquake_releases import (  # noqa: E402
    component_for_artifact_path,
    load_releases,
    verified_artifact_members,
)


class NquakeReleaseTests(unittest.TestCase):
    def test_release_inventory_covers_every_component_and_tracks_ktx_147(self) -> None:
        components = load_catalog(ROOT / "inventory/nquake-components.json")
        releases = load_releases(
            ROOT / "inventory/nquake-releases.json",
            ROOT / "inventory/nquake-components.json",
        )
        self.assertEqual(set(components_by_id(components)), set(releases["components"]))
        ktx = releases["components"]["nquake-ktx"]
        self.assertEqual("1.47+nquake.e4cb23d40aa2", ktx["version"])
        self.assertEqual("1.46-dev", ktx["embedded_version"])
        self.assertEqual("upstream-current", ktx["freshness"])
        path = ktx["artifacts"][0]["archive_path"]
        self.assertEqual("nquake-ktx", component_for_artifact_path(releases, path))

    def test_nested_pk3_rewrite_changes_only_the_selected_member(self) -> None:
        original = io.BytesIO()
        with zipfile.ZipFile(original, "w") as package:
            package.writestr("configs/default.cfg", b"same")
            package.writestr("qwprogs.qvm", b"old")
        rebuilt = rewrite_zip_members(original.getvalue(), {"qwprogs.qvm": b"new"})
        with zipfile.ZipFile(io.BytesIO(rebuilt)) as package:
            self.assertEqual(b"same", package.read("configs/default.cfg"))
            self.assertEqual(b"new", package.read("qwprogs.qvm"))

    def test_preserved_release_artifact_and_consumed_member_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as package:
                package.writestr("qwprogs.qvm", b"qvm")
            data = payload.getvalue()
            relative = "components/nquake/releases/nquake-ktx/test/qwprogs-qvm.zip"
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(data)
            artifact = {
                "archive_path": relative, "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "members": [{
                    "path": "qwprogs.qvm", "size": 3,
                    "sha256": hashlib.sha256(b"qvm").hexdigest(),
                }],
            }
            self.assertEqual({"qwprogs.qvm": b"qvm"}, verified_artifact_members(root, artifact))


if __name__ == "__main__":
    unittest.main()
