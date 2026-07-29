from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "maintenance/tools"))

from component_sources import (  # noqa: E402
    load_source_context,
    move_zip_members,
    resolve_component_payloads,
    rewrite_zip_members,
)
from check_component_updates import check_updates  # noqa: E402
from components import components_by_id, load_catalog  # noqa: E402
from component_releases import (  # noqa: E402
    component_for_artifact_path,
    load_releases,
    verified_artifact_members,
    verified_package_files,
)


class ComponentReleaseTests(unittest.TestCase):
    def test_release_inventory_covers_every_component_and_tracks_independent_origins(self) -> None:
        components = load_catalog(ROOT / "maintenance/inventory/components.json")
        releases = load_releases(
            ROOT / "maintenance/inventory/component-releases.json",
            ROOT / "maintenance/inventory/components.json",
        )
        self.assertEqual(set(components_by_id(components)), set(releases["components"]))
        ktx = releases["components"]["nquake-ktx"]
        self.assertEqual("1.47+nquake.e4cb23d40aa2+x86qw.1", ktx["version"])
        self.assertEqual("1.46-dev", ktx["embedded_version"])
        self.assertEqual("upstream-current", ktx["freshness"])
        path = ktx["artifacts"][0]["distribution_path"]
        self.assertEqual("nquake-ktx", component_for_artifact_path(releases, path))
        td2 = releases["components"]["total-destruction-2"]
        self.assertEqual("2.22+x86qw.1", td2["version"])
        self.assertEqual("upstream-package", td2["strategy"])
        td2_path = td2["artifacts"][0]["distribution_path"]
        self.assertEqual("total-destruction-2", component_for_artifact_path(releases, td2_path))
        self.assertTrue(path.startswith("mods/ktx/"))
        self.assertTrue(td2_path.startswith("mods/td2/"))
        self.assertEqual("ktx", ktx["distribution_component"])
        self.assertEqual("td2", td2["distribution_component"])

    def test_nested_pk3_rewrite_changes_only_the_selected_member(self) -> None:
        original = io.BytesIO()
        with zipfile.ZipFile(original, "w") as package:
            package.writestr("configs/default.cfg", b"same")
            package.writestr("qwprogs.qvm", b"old")
        rebuilt = rewrite_zip_members(original.getvalue(), {"qwprogs.qvm": b"new"})
        with zipfile.ZipFile(io.BytesIO(rebuilt)) as package:
            self.assertEqual(b"same", package.read("configs/default.cfg"))
            self.assertEqual(b"new", package.read("qwprogs.qvm"))

    def test_legacy_runtime_config_is_renamed_without_changing_its_content(self) -> None:
        original = io.BytesIO()
        with zipfile.ZipFile(original, "w") as package:
            package.writestr("configs/config.cfg", b"legacy")
            package.writestr("qwprogs.dat", b"gamecode")
        rebuilt = move_zip_members(
            original.getvalue(),
            {"configs/config.cfg": "configs/nquake-pk3-legacy.cfg"},
        )
        with zipfile.ZipFile(io.BytesIO(rebuilt)) as package:
            self.assertNotIn("configs/config.cfg", package.namelist())
            self.assertEqual(b"legacy", package.read("configs/nquake-pk3-legacy.cfg"))
            self.assertEqual(b"gamecode", package.read("qwprogs.dat"))

    def test_pro_x_package_keeps_legacy_configs_out_of_the_runtime_path(self) -> None:
        context = load_source_context(
            ROOT / "dist",
            ROOT / "maintenance/inventory/components.json",
            ROOT / "maintenance/inventory/component-releases.json",
        )
        _, _, payloads = resolve_component_payloads(context, "pro-x")
        members = {member: payload for _, member, payload, _ in payloads}
        self.assertIn("payload/prox/configs/nquake-legacy.cfg", members)
        self.assertIn("payload/prox/x86qw-prox.cfg", members)
        self.assertIn("defaults/prox/x86qw-prox-user.cfg", members)
        with zipfile.ZipFile(io.BytesIO(members["payload/prox/prox.pk3"])) as package:
            self.assertNotIn("configs/config.cfg", package.namelist())
            self.assertIn("configs/nquake-pk3-legacy.cfg", package.namelist())

    def test_final_arena_and_pro_x_are_distinct_packages(self) -> None:
        context = load_source_context(
            ROOT / "dist",
            ROOT / "maintenance/inventory/components.json",
            ROOT / "maintenance/inventory/component-releases.json",
        )
        _, _, arena_payloads = resolve_component_payloads(context, "final-arena")
        _, _, prox_payloads = resolve_component_payloads(context, "pro-x")
        arena_members = {member for _, member, _, _ in arena_payloads}
        prox_members = {member for _, member, _, _ in prox_payloads}
        self.assertIn("payload/arena/arena.pk3", arena_members)
        self.assertNotIn("payload/prox/prox.pk3", arena_members)
        self.assertIn("payload/prox/prox.pk3", prox_members)
        self.assertNotIn("payload/arena/arena.pk3", prox_members)

    def test_preserved_release_artifact_and_consumed_member_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, "w") as package:
                package.writestr("qwprogs.qvm", b"qvm")
            data = payload.getvalue()
            relative = "mods/ktx/test/qwprogs-qvm.zip"
            path = root / relative
            path.parent.mkdir(parents=True)
            path.write_bytes(data)
            artifact = {
                "distribution_path": relative, "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "members": [{
                    "path": "qwprogs.qvm", "size": 3,
                    "sha256": hashlib.sha256(b"qvm").hexdigest(),
                }],
            }
            self.assertEqual({"qwprogs.qvm": b"qvm"}, verified_artifact_members(root, artifact))

    def test_standalone_tar_package_is_verified_without_extracting_unsafe_members(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            relative = "mods/td2/test/td2.tar.gz"
            path = root / relative
            path.parent.mkdir(parents=True)
            with tarfile.open(path, "w:gz") as package:
                info = tarfile.TarInfo("td2/qwprogs.dat")
                info.size = 4
                package.addfile(info, io.BytesIO(b"game"))
            artifact = {
                "distribution_path": relative,
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            self.assertEqual({"td2/qwprogs.dat": b"game"}, verified_package_files(root, artifact))

            with tarfile.open(path, "w:gz") as package:
                info = tarfile.TarInfo("../escape")
                info.size = 3
                package.addfile(info, io.BytesIO(b"bad"))
            artifact["size"] = path.stat().st_size
            artifact["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "unsafe standalone artifact member"):
                verified_package_files(root, artifact)

    def test_standalone_update_check_verifies_the_pinned_source_without_github_metadata(self) -> None:
        releases = load_releases(
            ROOT / "maintenance/inventory/component-releases.json",
            ROOT / "maintenance/inventory/components.json",
        )
        td2_artifact = releases["components"]["total-destruction-2"]["artifacts"][0]
        with mock.patch(
            "check_component_updates.git_remote_revision",
            return_value=releases["reference"]["revision"],
        ), mock.patch("check_component_updates.github_latest_release", return_value="1.47"):
            with mock.patch("check_component_updates.remote_fingerprint", return_value=(
                td2_artifact["size"], td2_artifact["sha256"],
            )) as fingerprint:
                results = check_updates(releases, online=True)
        td2 = next(result for result in results if result["component"] == "total-destruction-2")
        self.assertEqual("current", td2["status"])
        fingerprint.assert_called_once_with(td2_artifact["url"])


if __name__ == "__main__":
    unittest.main()
