from __future__ import annotations

import hashlib
import io
import json
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
    build_pak_members,
    load_source_context,
    move_zip_members,
    read_pak_members,
    remove_pak_members,
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
    def test_each_playable_mod_declares_its_characteristic_composition(self) -> None:
        catalog = load_catalog(ROOT / "maintenance/inventory/components.json")
        components = components_by_id(catalog)
        releases = load_releases(
            ROOT / "maintenance/inventory/component-releases.json",
            ROOT / "maintenance/inventory/components.json",
        )["components"]

        def origins(identifier: str) -> set[tuple[str, str]]:
            return {
                (str(source.get("origin", "reference")), str(source["mode"]))
                for source in components[identifier]["sources"]
            }

        self.assertEqual("upstream-composed", releases["ktx"]["strategy"])
        self.assertEqual({("reference", "archive-base"), ("release", "archive")}, origins("ktx"))
        self.assertEqual("reference-overlay", releases["final-arena"]["strategy"])
        self.assertEqual({("reference", "overlay")}, origins("final-arena"))
        self.assertEqual("upstream-package", releases["pro-x"]["strategy"])
        self.assertEqual({("reference", "preserve"), ("release", "overlay")}, origins("pro-x"))
        self.assertEqual("reference-overlay", releases["team-fortress"]["strategy"])
        self.assertEqual({("reference", "overlay"), ("release", "overlay")}, origins("team-fortress"))
        self.assertEqual("upstream-package", releases["total-destruction-2"]["strategy"])
        self.assertEqual({("release", "overlay"), ("release", "preserve")}, origins("total-destruction-2"))
        for identifier in ("ktx", "final-arena", "pro-x", "team-fortress", "total-destruction-2"):
            self.assertTrue(components[identifier].get("project_sources"), identifier)

    def test_playable_mods_have_no_duplicate_virtual_runtime_paths(self) -> None:
        context = load_source_context(
            ROOT / "dist",
            ROOT / "maintenance/inventory/components.json",
            ROOT / "maintenance/inventory/component-releases.json",
        )
        for identifier in (
            "ktx", "final-arena", "pro-x", "team-fortress", "total-destruction-2",
        ):
            with self.subTest(component=identifier):
                _, _, payloads = resolve_component_payloads(context, identifier)
                virtual_paths: list[str] = []
                for _, member, payload, _ in payloads:
                    if not member.startswith("payload/"):
                        continue
                    runtime_path = member.removeprefix("payload/")
                    gamedir = runtime_path.split("/", 1)[0]
                    if runtime_path.casefold().endswith((".pk3", ".zip")):
                        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                            virtual_paths.extend(
                                f"{gamedir}/{name}".casefold()
                                for name in archive.namelist() if not name.endswith("/")
                            )
                    elif runtime_path.casefold().endswith(".pak"):
                        virtual_paths.extend(
                            f"{gamedir}/{name}".casefold()
                            for name, _ in read_pak_members(payload)
                        )
                    else:
                        virtual_paths.append(runtime_path.casefold())
                self.assertEqual(
                    len(virtual_paths), len(set(virtual_paths)),
                    f"{identifier} contains two files for the same virtual runtime path",
                )

    def test_release_inventory_covers_every_component_and_tracks_independent_origins(self) -> None:
        components = load_catalog(ROOT / "maintenance/inventory/components.json")
        releases = load_releases(
            ROOT / "maintenance/inventory/component-releases.json",
            ROOT / "maintenance/inventory/components.json",
        )
        self.assertEqual(set(components_by_id(components)), set(releases["components"]))
        ktx = releases["components"]["ktx"]
        self.assertEqual("1.47+x86qw.3", ktx["version"])
        self.assertEqual("upstream-composed", ktx["strategy"])
        self.assertEqual("upstream-current", ktx["freshness"])
        path = ktx["artifacts"][0]["distribution_path"]
        self.assertEqual("ktx", component_for_artifact_path(releases, path))
        td2 = releases["components"]["total-destruction-2"]
        self.assertEqual("2.22+x86qw.2", td2["version"])
        self.assertEqual("upstream-package", td2["strategy"])
        td2_path = td2["artifacts"][0]["distribution_path"]
        self.assertEqual("total-destruction-2", component_for_artifact_path(releases, td2_path))
        self.assertTrue(path.startswith("mods/ktx/"))
        self.assertTrue(td2_path.startswith("mods/td2/"))
        self.assertEqual("ktx", ktx["distribution_component"])
        self.assertEqual("td2", td2["distribution_component"])
        arena = releases["components"]["final-arena"]
        self.assertEqual("final-arena", arena["distribution_component"])
        self.assertEqual(2, len(arena["artifacts"]))

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

    def test_compatibility_payloads_are_explicit_and_byte_verified(self) -> None:
        context = load_source_context(
            ROOT / "dist",
            ROOT / "maintenance/inventory/components.json",
            ROOT / "maintenance/inventory/component-releases.json",
        )
        expected = {
            "pro-x": (
                "payload/prox/maps/proxmap1.ent", 42181,
                "276fbb5d2ed499ad90c50b9e7bd0d3da734b2d8bdc976e56f67cfd846d5909cc",
            ),
            "team-fortress": (
                "payload/fortress/qwprogs.dat", 828268,
                "bf6bd491fe7c4a74c6d02cf525cd838dba86ebf251623a770180423bee02665b",
            ),
            "total-destruction-2": (
                "payload/td2/qwprogs.dat", 377488,
                "4553b62ec28109efdecc95bb4b40487e014063dd0bb59aad9301d03b09ee1ff1",
            ),
        }
        for component, (member, size, digest) in expected.items():
            with self.subTest(component=component):
                _, _, payloads = resolve_component_payloads(context, component)
                payload = {name: data for _, name, data, _ in payloads}[member]
                self.assertEqual(size, len(payload))
                self.assertEqual(digest, hashlib.sha256(payload).hexdigest())

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
        release, source_revision, payloads = resolve_component_payloads(context, "ktx")
        self.assertEqual("upstream-composed", release["strategy"])
        self.assertEqual("ce329889f97cc5bacf85b6388d3c5d8f242769fd", source_revision)
        members = {member: payload for _, member, payload, _ in payloads}
        with zipfile.ZipFile(io.BytesIO(members["payload/qw/ktx.pk3"])) as package:
            names = set(package.namelist())
            self.assertEqual(710, len(names))
            self.assertEqual(1_578_544, len(package.read("qwprogs.qvm")))
            self.assertEqual(112_973, len(package.read("qwprogs.map")))
            self.assertIn("bots/maps/anarena.bot", names)
            self.assertEqual(382, sum(name.startswith("locs/") for name in names))
            self.assertIn("configs/usermodes/dmm4base.cfg", names)
            self.assertIn("sound/ca/sffinal.wav", names)
            ktx_config = package.read("ktx.cfg")
            self.assertIn(b"set sv_maxrate                500000", ktx_config)
            self.assertIn(b"k_defmode is selected by the launcher", ktx_config)
            self.assertNotIn(b"set k_defmode                 4on4", ktx_config)
            server_runtime = package.read("mvdsv.cfg")
            self.assertIn(b"sv_progtype                   2", server_runtime)
            self.assertNotIn(b"sv_progtype                   1", server_runtime)

    def test_ktx_layer_policy_rejects_an_unreviewed_conflict(self) -> None:
        context = load_source_context(
            ROOT / "dist",
            ROOT / "maintenance/inventory/components.json",
            ROOT / "maintenance/inventory/component-releases.json",
        )
        policy = ROOT / "dist/mods/ktx/1.47/x86qw/merge-policy.json"
        altered = json.loads(policy.read_text(encoding="utf-8"))
        altered["conflicts"] = [
            conflict for conflict in altered["conflicts"] if conflict["member"] != "server.cfg"
        ]
        with mock.patch("component_sources.json.loads", return_value=altered):
            with self.assertRaisesRegex(ValueError, "conflict"):
                resolve_component_payloads(context, "ktx")

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

    def test_pak_member_can_be_removed_without_changing_other_assets(self) -> None:
        original = build_pak_members([
            ("qwprogs.dat", b"2.8"),
            ("locs/2fort5r.loc", b"location data"),
        ])
        rebuilt = remove_pak_members(original, {"qwprogs.dat"})
        self.assertEqual(
            [("locs/2fort5r.loc", b"location data")],
            read_pak_members(rebuilt),
        )

    def test_team_fortress_replaces_legacy_gamecode_but_keeps_nquake_locs(self) -> None:
        context = load_source_context(
            ROOT / "dist",
            ROOT / "maintenance/inventory/components.json",
            ROOT / "maintenance/inventory/component-releases.json",
        )
        _, _, payloads = resolve_component_payloads(context, "team-fortress")
        members = {member: (source, payload, overrides) for source, member, payload, overrides in payloads}
        source, gamecode, overrides = members["payload/fortress/qwprogs.dat"]
        self.assertEqual("dist/mods/team-fortress/2.9/x86qw/runtime/qwprogs.dat", source)
        self.assertEqual(828_268, len(gamecode))
        self.assertEqual(
            "bf6bd491fe7c4a74c6d02cf525cd838dba86ebf251623a770180423bee02665b",
            hashlib.sha256(gamecode).hexdigest(),
        )
        self.assertEqual("qwprogs.dat", overrides[-1]["target"])
        misc_source, misc_pak, overrides = members["payload/fortress/misc.pak"]
        self.assertEqual("addon-fortress/fortress/misc.pak", misc_source)
        pak_members = dict(read_pak_members(misc_pak))
        self.assertNotIn("qwprogs.dat", pak_members)
        self.assertEqual(34, sum(name.startswith("locs/") for name in pak_members))
        self.assertIn({"removed": "qwprogs.dat"}, overrides)
        _, pak0, pak0_overrides = members["payload/fortress/pak0.pak"]
        _, pak1, _ = members["payload/fortress/pak1.pak"]
        pak0_members = dict(read_pak_members(pak0))
        pak1_members = dict(read_pak_members(pak1))
        self.assertNotIn("sound/weapons/detpack.wav", pak0_members)
        self.assertIn("sound/weapons/detpack.wav", pak1_members)
        self.assertIn({"removed": "sound/weapons/detpack.wav"}, pak0_overrides)

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
