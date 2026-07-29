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
    remove_zip_members,
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
        self.assertEqual("1.47+nquake.e4cb23d40aa2+x86qw.7", ktx["version"])
        self.assertEqual("1.46-dev", ktx["embedded_version"])
        self.assertEqual("upstream-current", ktx["freshness"])
        path = ktx["artifacts"][0]["distribution_path"]
        self.assertEqual("nquake-ktx", component_for_artifact_path(releases, path))
        td2 = releases["components"]["total-destruction-2"]
        self.assertEqual("2.22+x86qw.5", td2["version"])
        self.assertEqual("upstream-package", td2["strategy"])
        td2_path = td2["artifacts"][0]["distribution_path"]
        self.assertEqual("total-destruction-2", component_for_artifact_path(releases, td2_path))
        self.assertTrue(path.startswith("mods/ktx/"))
        self.assertTrue(td2_path.startswith("mods/td2/"))
        self.assertEqual("ktx", ktx["distribution_component"])
        self.assertEqual("td2", td2["distribution_component"])

    def test_td2_restores_the_omitted_original_chainsaw_down_sound(self) -> None:
        context = load_source_context(
            ROOT / "dist",
            ROOT / "maintenance/inventory/components.json",
            ROOT / "maintenance/inventory/component-releases.json",
        )
        _, _, payloads = resolve_component_payloads(context, "total-destruction-2")
        members = {member: payload for _, member, payload, _ in payloads}
        saw = members["payload/td2/sound/weapons/saw.wav"]
        saw_down = members["payload/td2/sound/weapons/saw_down.wav"]
        self.assertEqual(7_576, len(saw_down))
        self.assertEqual(
            "1a4b26b7537507d5f93a575087864b8531894a7daa8441c68533e2e997fbea28",
            hashlib.sha256(saw_down).hexdigest(),
        )
        self.assertEqual(saw, saw_down)

    def test_nested_pk3_rewrite_changes_only_the_selected_member(self) -> None:
        original = io.BytesIO()
        with zipfile.ZipFile(original, "w") as package:
            package.writestr("configs/default.cfg", b"same")
            package.writestr("qwprogs.qvm", b"old")
        rebuilt = rewrite_zip_members(original.getvalue(), {"qwprogs.qvm": b"new"})
        with zipfile.ZipFile(io.BytesIO(rebuilt)) as package:
            self.assertEqual(b"same", package.read("configs/default.cfg"))
            self.assertEqual(b"new", package.read("qwprogs.qvm"))

    def test_nested_pk3_can_add_only_an_explicitly_declared_member(self) -> None:
        original = io.BytesIO()
        with zipfile.ZipFile(original, "w") as package:
            package.writestr("qwprogs.qvm", b"qvm")
        with self.assertRaisesRegex(ValueError, "override target is missing"):
            rewrite_zip_members(original.getvalue(), {"qwprogs.map": b"symbols"})
        rebuilt = rewrite_zip_members(
            original.getvalue(), {"qwprogs.map": b"symbols"}, {"qwprogs.map"},
        )
        with zipfile.ZipFile(io.BytesIO(rebuilt)) as package:
            self.assertEqual(b"qvm", package.read("qwprogs.qvm"))
            self.assertEqual(b"symbols", package.read("qwprogs.map"))

    def test_ktx_package_includes_the_official_qvm_symbol_map(self) -> None:
        context = load_source_context(
            ROOT / "dist",
            ROOT / "maintenance/inventory/components.json",
            ROOT / "maintenance/inventory/component-releases.json",
        )
        _, _, payloads = resolve_component_payloads(context, "nquake-ktx")
        members = {member: payload for _, member, payload, _ in payloads}
        with zipfile.ZipFile(io.BytesIO(members["payload/qw/ktx.pk3"])) as package:
            self.assertEqual(1_578_544, len(package.read("qwprogs.qvm")))
            self.assertEqual(112_973, len(package.read("qwprogs.map")))
            server_runtime = package.read("mvdsv.cfg")
            self.assertIn(b"sv_progtype                   2", server_runtime)
            self.assertNotIn(b"sv_progtype                   1", server_runtime)

    def test_bootstrap_limits_textures_without_changing_the_preserved_snapshot(self) -> None:
        context = load_source_context(
            ROOT / "dist",
            ROOT / "maintenance/inventory/components.json",
            ROOT / "maintenance/inventory/component-releases.json",
        )
        _, _, payloads = resolve_component_payloads(context, "nquake-bootstrap")
        members = {member: payload for _, member, payload, _ in payloads}
        packaged = members["payload/qw/nquake_default.cfg"]
        preserved = (
            ROOT / "dist/distributions/nquake/e4cb23d40aa202335b5dafe4e8f1e8d424caac0d/non-gpl/qw/nquake_default.cfg"
        ).read_bytes()
        self.assertIn(b'gl_max_size                          "16384"', packaged)
        self.assertNotIn(b'gl_max_size                          "32768"', packaged)
        self.assertNotRegex(packaged, rb"(?m)^r_fx_geometry\s")
        self.assertNotRegex(packaged, rb"(?m)^cl_verify_qwprotocol\s")
        self.assertIn(b"r_fx_geometry is not exposed", packaged)
        self.assertIn(b"keep the ezQuake cl_verify_qwprotocol default", packaged)
        self.assertIn(b'gl_max_size                          "32768"', preserved)
        self.assertRegex(preserved, rb"(?m)^r_fx_geometry\s")
        self.assertRegex(preserved, rb"(?m)^cl_verify_qwprotocol\s")

    def test_bootstrap_installs_only_runtime_configs_and_temporary_aliases(self) -> None:
        context = load_source_context(
            ROOT / "dist",
            ROOT / "maintenance/inventory/components.json",
            ROOT / "maintenance/inventory/component-releases.json",
        )
        _, _, payloads = resolve_component_payloads(context, "nquake-bootstrap")
        members = {member: payload for _, member, payload, _ in payloads}
        self.assertIn("payload/qw/autoexec.cfg", members)
        self.assertIn("defaults/qw/x86qw-user.cfg", members)
        self.assertFalse(any("samples/" in member for member in members))
        autoexec = members["payload/qw/autoexec.cfg"].decode("utf-8")
        self.assertNotRegex(autoexec, r"(?im)^\s*alias\s+")
        self.assertRegex(autoexec, r"(?im)^\s*tempalias\s+")
        aliases = [
            line.split()[1].casefold()
            for line in autoexec.splitlines()
            if line.casefold().startswith("tempalias ")
        ]
        self.assertEqual(len(aliases), len(set(aliases)))
        self.assertTrue({"_startup_message_10", "_startup_message_11", "_startup_message_12"} <= set(aliases))
        self.assertNotIn('spectator 0', autoexec)
        self.assertNotIn('maxspectators 8', autoexec)
        self.assertNotIn('exec configs/config.cfg', autoexec)
        self.assertIn('sb_listcache 0', autoexec)
        self.assertIn('cfg_use_gamedir 0', autoexec)

    def test_td2_runtime_package_excludes_reference_material(self) -> None:
        context = load_source_context(
            ROOT / "dist",
            ROOT / "maintenance/inventory/components.json",
            ROOT / "maintenance/inventory/component-releases.json",
        )
        _, _, payloads = resolve_component_payloads(context, "total-destruction-2")
        members = {member for _, member, _, _ in payloads}
        self.assertIn("payload/td2/qwprogs.dat", members)
        self.assertIn("payload/td2/x86qw-td2.cfg", members)
        self.assertFalse(any(member.casefold().endswith((".qc", ".doc")) for member in members))
        self.assertFalse(any("/source" in member.casefold() for member in members))
        self.assertFalse(any("password" in member.casefold() for member in members))

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

    def test_archive_member_can_be_removed_without_changing_gamecode(self) -> None:
        original = io.BytesIO()
        with zipfile.ZipFile(original, "w") as package:
            package.writestr("configs/config.cfg", b"legacy")
            package.writestr("qwprogs.dat", b"gamecode")
        rebuilt = remove_zip_members(original.getvalue(), {"configs/config.cfg"})
        with zipfile.ZipFile(io.BytesIO(rebuilt)) as package:
            self.assertNotIn("configs/config.cfg", package.namelist())
            self.assertEqual(b"gamecode", package.read("qwprogs.dat"))

    def test_pro_x_package_keeps_legacy_configs_out_of_the_runtime_path(self) -> None:
        context = load_source_context(
            ROOT / "dist",
            ROOT / "maintenance/inventory/components.json",
            ROOT / "maintenance/inventory/component-releases.json",
        )
        _, _, payloads = resolve_component_payloads(context, "pro-x")
        members = {member: payload for _, member, payload, _ in payloads}
        self.assertNotIn("payload/prox/configs/nquake-legacy.cfg", members)
        self.assertIn("payload/prox/x86qw-prox.cfg", members)
        self.assertIn("payload/prox/qw_server.cfg", members)
        self.assertIn("defaults/prox/x86qw-prox-user.cfg", members)
        self.assertIn("payload/prox/qwprogs.dat", members)
        self.assertNotIn("payload/prox/configs/config.cfg", members)

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
        self.assertIn("payload/prox/qwprogs.dat", prox_members)
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
        self.assertEqual(
            [
                "https://web.archive.org/web/20150217054944id_/http://www.bendarling.net/downloads/prox/prox_11.zip",
                td2_artifact["url"],
            ],
            [call.args[0] for call in fingerprint.call_args_list],
        )


if __name__ == "__main__":
    unittest.main()
