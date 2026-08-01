import contextlib
import importlib.util
import io
import json
import os
import plistlib
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from maintenance.tools.build_installer_bundle import zipapp_bytes


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("install_qw", ROOT / "dist/installer/bin/manager.py")
install_qw = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = install_qw
SPEC.loader.exec_module(install_qw)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        install_qw.console.configure(verbose=False, no_color=True)

    def make_installer(self, root):
        project = root / "project"
        target = project / "quake-world"
        cache = root / "cache" / "x86qw"
        target.mkdir(parents=True)
        cache.parent.mkdir()
        return install_qw.Installer(project, target, cache), target, cache

    def test_public_zipapp_embeds_the_declarative_ktx_mode_catalog(self):
        with zipfile.ZipFile(io.BytesIO(zipapp_bytes("9.9.9"))) as application:
            catalog = json.loads(application.read("_x86qw/ktx-modes.json"))
            bot_names = json.loads(application.read("_x86qw/ktx-frogbot-names.json"))
            self.assertIn("session_control.py", application.namelist())
        self.assertEqual(1, catalog["format"])
        self.assertEqual("ktx", catalog["game"])
        self.assertEqual("duel", catalog["modes"][0]["id"])
        self.assertIn("race", [mode["id"] for mode in catalog["modes"]])
        self.assertEqual("one-piece", bot_names["theme"])
        luffy = bot_names["groups"][0]["characters"][0]
        self.assertEqual("Luffy", luffy["name"])
        self.assertEqual({"name"}, set(luffy))

    @staticmethod
    def write_installer_bundle(path: Path, version: str) -> None:
        identity = json.dumps({"format": 1, "project": "x86qw", "version": version})
        with zipfile.ZipFile(path, "w") as package:
            prefix = f"x86qw-installer-{version}"
            package.writestr(f"{prefix}/x86qw.pyz", zipapp_bytes(version))
            package.writestr(f"{prefix}/installer.json", identity)

    @staticmethod
    def write_cli_receipt(target: Path, version: str, *, legacy: bool = False) -> Path:
        relative = install_qw.LEGACY_CLI_RECEIPT if legacy else install_qw.CLI_RECEIPT
        receipt = target / relative
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(
            json.dumps({"format": 1, "project": "x86qw", "version": version}) + "\n",
            encoding="utf-8",
        )
        return receipt

    @staticmethod
    def write_ezquake_fixture(
        installer, target: Path, spec, channel: str, *, payload: bytes | None = None,
    ):
        selection = "3.6.9" if channel == "stable" else "20260616-101233_a86996a"
        runtime = target / spec.runtime(channel)
        if payload is not None:
            runtime.parent.mkdir(parents=True, exist_ok=True)
            runtime.write_bytes(payload)
        binary_hash = install_qw.file_hash(runtime) if runtime.is_file() else "b" * 64
        receipt_path = target / spec.receipt(channel)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt = {
            "format": "1", "platform": spec.key, "architecture": spec.architecture,
            "channel": channel, "selection": selection,
            "install_name": spec.runtime(channel), "bundle_version": selection,
            "artifact_name": spec.stable_archive if channel == "stable" else selection + spec.nightly_suffix,
            "artifact_url": "https://example.invalid/" + (
                spec.stable_archive if channel == "stable" else selection + spec.nightly_suffix
            ),
            "artifact_sha256": "a" * 64, "binary_sha256": binary_hash,
        }
        installer.write_ezquake_receipt_record(receipt_path, receipt)
        return receipt_path, receipt, runtime

    def test_repository_preserves_the_registered_paks_as_core_sources(self):
        expected = {
            "pak0.pak": install_qw.ID1_PAK0_SHA256,
            "pak1.pak": install_qw.ID1_PAK1_SHA256,
        }
        for name, digest in expected.items():
            with self.subTest(name=name):
                pak = ROOT / "dist/game-data/id1" / name
                self.assertTrue(pak.is_file())
                self.assertFalse(pak.is_symlink())
                with pak.open("rb") as source:
                    self.assertEqual(b"PACK", source.read(4))
                self.assertEqual(digest, install_qw.file_hash(pak))

    def test_cancel_before_selection_leaves_no_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            target.rmdir()
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch("builtins.input", side_effect=KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        installer.install()
            installer.cleanup_stage()
            self.assertFalse(target.exists())

    def test_cancel_after_new_install_lock_leaves_no_session_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "new-install"
            installer = mock.Mock()
            installer.target = target

            def cancel(*, platform=None, before_mutation=None):
                del platform
                before_mutation()
                raise KeyboardInterrupt

            installer.install.side_effect = cancel
            with mock.patch.object(install_qw, "Installer", return_value=installer):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(130, install_qw.main(["install", str(target)]))
            self.assertFalse(target.exists())

    def test_development_install_receives_registered_paks_from_core_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            target.rmdir()
            bundled = installer.project_root / "dist/game-data/id1"
            bundled.mkdir(parents=True)
            pak0 = b"PACK" + b"pak0"
            pak1 = b"PACK" + b"pak1"
            (bundled / "pak0.pak").write_bytes(pak0)
            (bundled / "pak1.pak").write_bytes(pak1)
            with mock.patch.object(install_qw, "ID1_PAK0_SHA256", install_qw.hashlib.sha256(pak0).hexdigest()):
                with mock.patch.object(install_qw, "ID1_PAK1_SHA256", install_qw.hashlib.sha256(pak1).hexdigest()):
                    installer.validate_target("install")
                    with contextlib.redirect_stdout(io.StringIO()):
                        installer.provision_install_target()
                    installer.check_paks()
            self.assertEqual(pak0, (target / "id1/pak0.pak").read_bytes())
            self.assertEqual(pak1, (target / "id1/pak1.pak").read_bytes())

    def test_public_install_downloads_registered_paks_as_a_separate_core_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "quake-world"
            installer = install_qw.Installer(ROOT, target, root / "cache", online_only=True)
            pak0 = b"PACK" + b"public-pak0"
            pak1 = b"PACK" + b"public-pak1"
            revision = "a" * 64
            version = "1.0.0"
            archive = root / f"{install_qw.CORE_ID1_PACKAGE}-{version}.zip"
            members = []
            with zipfile.ZipFile(archive, "w") as package:
                for name, payload in (("pak0.pak", pak0), ("pak1.pak", pak1)):
                    member = f"payload/id1/{name}"
                    members.append({
                        "path": member,
                        "sha256": install_qw.hashlib.sha256(payload).hexdigest(),
                    })
                    package.writestr(member, payload)
                package.writestr("_x86qw/component.json", json.dumps({
                    "format": 1,
                    "project": "x86qw",
                    "package": install_qw.CORE_ID1_PACKAGE,
                    "version": version,
                    "source_revision": revision,
                    "members": members,
                }))
            record = {
                "package": install_qw.CORE_ID1_PACKAGE,
                "version": version,
                "source_revision": revision,
                "origin_url": "https://example.invalid/core.zip",
            }
            with mock.patch.object(
                install_qw, "ID1_PAK0_SHA256", install_qw.hashlib.sha256(pak0).hexdigest(),
            ):
                with mock.patch.object(
                    install_qw, "ID1_PAK1_SHA256", install_qw.hashlib.sha256(pak1).hexdigest(),
                ):
                    installer.prepare_install_target()
                    installer.stage = target / ".stage"
                    installer.stage.mkdir()
                    with mock.patch.object(installer, "core_id1_package_record", return_value=record):
                        with mock.patch.object(installer, "download_component_package", return_value=archive):
                            with contextlib.redirect_stdout(io.StringIO()):
                                installer.provision_install_target()
                    installer.check_paks()
            self.assertEqual(pak0, (target / "id1/pak0.pak").read_bytes())
            self.assertEqual(pak1, (target / "id1/pak1.pak").read_bytes())

    def test_missing_target_is_still_rejected_for_non_install_actions(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            target.rmdir()
            with self.assertRaisesRegex(install_qw.InstallerError, "não existe"):
                installer.validate_target("verify")

    def test_existing_pak_is_never_overwritten_by_the_bundled_copy(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            bundled = installer.project_root / "dist/game-data/id1"
            bundled.mkdir(parents=True)
            valid = b"PACK" + b"registered"
            for name in ("pak0.pak", "pak1.pak"):
                (bundled / name).write_bytes(valid)
            (target / "id1").mkdir()
            existing = target / "id1/pak0.pak"
            existing.write_bytes(b"PACKpersonal")
            digest = install_qw.hashlib.sha256(valid).hexdigest()
            with mock.patch.object(install_qw, "ID1_PAK0_SHA256", digest):
                with mock.patch.object(install_qw, "ID1_PAK1_SHA256", digest):
                    with self.assertRaisesRegex(install_qw.InstallerError, "versão registrada"):
                        installer.provision_install_target()
            self.assertEqual(b"PACKpersonal", existing.read_bytes())

    def test_platform_is_detected_without_prompting(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            for system, expected in install_qw.HOST_PLATFORMS.items():
                with self.subTest(system=system):
                    output = io.StringIO()
                    with mock.patch.object(install_qw.host_platform, "system", return_value=system):
                        with mock.patch("builtins.input") as prompt:
                            with contextlib.redirect_stdout(output):
                                self.assertEqual(expected, installer.select_platform().key)
                    prompt.assert_not_called()
                    self.assertIn("Sistema detectado automaticamente", output.getvalue())

    def test_platform_override_prepares_a_cross_platform_client(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            output = io.StringIO()
            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch("builtins.input") as prompt:
                    with contextlib.redirect_stdout(output):
                        self.assertEqual("windows", installer.select_platform("windows").key)
            prompt.assert_not_called()
            self.assertIn("Windows x64", output.getvalue())
            self.assertIn("host detectado: macOS", output.getvalue())

    def test_service_payload_keeps_only_the_selected_platform_at_operational_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            cases = (
                ("macos", "Darwin", "arm64", "macos-arm64", ""),
                ("linux", "Linux", "x86_64", "linux-amd64", ""),
                ("windows", "Windows", "AMD64", "windows-x64", ".exe"),
            )
            for system, host, machine, variant, suffix in cases:
                with self.subTest(system=system):
                    installer.spec = install_qw.PLATFORMS[system]
                    managed = Path(temporary) / f"managed-{system}"
                    for identifier in ("mvdsv", "qtv", "qwfwd"):
                        entries = [
                            entry for entry in installer.components[identifier]["project_sources"]
                            if entry.get("platform") is not None
                        ]
                        for entry in entries:
                            path = managed / str(entry["destination"])
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_bytes(str(entry["platform"]).encode())
                        with mock.patch.object(
                            install_qw.host_platform, "system", return_value=host,
                        ), mock.patch.object(
                            install_qw.host_platform, "machine", return_value=machine,
                        ):
                            installer.normalize_component_platform_payload(identifier, managed)
                        selected = next(entry for entry in entries if entry["platform"] == variant)
                        destination = managed / str(selected["install_destination"])
                        self.assertEqual(variant.encode(), destination.read_bytes())
                        self.assertFalse((managed / "platforms" / identifier).exists())
                        executable = identifier + suffix
                        expected = (
                            Path(executable)
                            if identifier == "mvdsv"
                            else Path(identifier) / executable
                        )
                        self.assertEqual(expected, destination.relative_to(managed))
                        destination.unlink()

    def test_unknown_host_requires_an_explicit_platform(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            with mock.patch.object(install_qw.host_platform, "system", return_value="Haiku"):
                with self.assertRaisesRegex(install_qw.InstallerError, "--platform macos"):
                    installer.select_platform()
                self.assertEqual("linux", installer.select_platform("linux").key)

    def test_invalid_channel_is_explained_and_reprompted(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                with mock.patch("builtins.input", side_effect=["beta", "1"]):
                    self.assertEqual("stable", installer.choose_channel())
            self.assertIn("Opção inválida. Digite 1 para stable ou 2 para nightly.", output.getvalue())
            self.assertIn("Canal selecionado: stable", output.getvalue())

    def test_native_macos_install_rejects_an_open_ezquake(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["macos"]
            process = mock.Mock(returncode=0, stdout="1234\n", stderr="")
            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(install_qw.subprocess, "run", return_value=process):
                    with self.assertRaisesRegex(install_qw.InstallerError, "Feche o ezQuake"):
                        installer.ensure_macos_ezquake_closed()

    def test_native_macos_install_clears_stale_game_directory_preferences(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["macos"]
            closed = mock.Mock(returncode=1, stdout="", stderr="")
            deleted = mock.Mock(returncode=0, stdout="", stderr="")
            missing = mock.Mock(returncode=1, stdout="", stderr="Domain not found.")
            responses = [closed, deleted, missing, deleted]
            output = io.StringIO()
            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(install_qw.subprocess, "run", side_effect=responses) as run:
                    with contextlib.redirect_stdout(output):
                        installer.reset_macos_game_directory()
            self.assertEqual(4, run.call_count)
            for key, call in zip(install_qw.MACOS_DIRECTORY_KEYS, run.call_args_list[1:]):
                self.assertEqual(
                    ["defaults", "delete", install_qw.MACOS_PREFERENCES_DOMAIN, key],
                    call.args[0],
                )
            self.assertIn("Seleção antiga", output.getvalue())

    def test_macos_preferences_are_untouched_for_cross_platform_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["windows"]
            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(install_qw.subprocess, "run") as run:
                    installer.reset_macos_game_directory()
            run.assert_not_called()

    def test_macos_bundle_removes_sandbox_and_uses_the_full_display(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            app = target / "ezQuake.app"
            plist = app / "Contents/Info.plist"
            plist.parent.mkdir(parents=True)
            with plist.open("wb") as destination:
                plistlib.dump({"CFBundleName": "ezQuake"}, destination)
            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(installer, "macos_app_is_sandboxed", side_effect=[True, False]):
                    with mock.patch.object(installer, "run_command") as command:
                        self.assertTrue(installer.prepare_macos_app(app))
            self.assertEqual(
                ["codesign", "--force", "--deep", "--sign", "-", str(app)],
                command.call_args_list[0].args[0],
            )
            self.assertEqual(
                ["codesign", "--verify", "--deep", "--strict", str(app)],
                command.call_args_list[1].args[0],
            )
            with plist.open("rb") as source:
                metadata = plistlib.load(source)
            self.assertIs(False, metadata[install_qw.MACOS_SAFE_AREA_KEY])

    def test_macos_fullscreen_repair_is_visible_in_the_update_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            rows = []
            with mock.patch.object(installer, "latest_release", return_value=(
                "3.6.9", ("https://example.invalid/ezQuake-macOS-universal.zip",), "a" * 64,
            )):
                with mock.patch.object(installer, "macos_app_needs_preparation", return_value=True):
                    self.assertTrue(installer.update_runtime(
                        spec, "stable", {"selection": "3.6.9"}, dry_run=True, plan_rows=rows,
                    ))
            self.assertEqual(1, len(rows))
            self.assertEqual("Reparar", rows[0].action)
            self.assertEqual("área segura", rows[0].installed)
            self.assertEqual("tela inteira", rows[0].available)

    def test_nquake_startup_state_reports_pending_and_loaded(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            config = target / "ezquake/configs/config.cfg"
            config.parent.mkdir(parents=True)
            for marker, expected in (("1", "aguardando a primeira execução"), ("0", "carregadas pelo ezQuake")):
                with self.subTest(marker=marker):
                    config.write_text(f'set _nquake_first_startup "{marker}"\n', encoding="utf-8")
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        installer.report_nquake_startup_state(["nquake-bootstrap"])
                    self.assertIn(expected, output.getvalue())

    def test_nightly_catalog_can_expand_without_overwhelming_initial_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["macos"]
            installer.channel = "nightly"
            catalog = []
            for day in range(20, 5, -1):
                version = f"202607{day:02d}-120000_abcdef0"
                name = version + installer.spec.nightly_suffix
                url = f"https://downloads.x86.com.br/x86qw/{name}"
                catalog.append((version, (url,), "a" * 64))

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                with mock.patch.object(installer, "nightly_catalog", return_value=catalog):
                    with mock.patch("builtins.input", side_effect=["t", "13"]):
                        installer.choose_release()
            rendered = output.getvalue()
            self.assertIn("... mais 3 versões. Digite t para mostrar todas.", rendered)
            self.assertGreaterEqual(rendered.count(catalog[12][0]), 1)
            self.assertIn(f"Versão selecionada: {catalog[12][0]}", rendered)

    def test_x86qw_catalog_is_filtered_and_requires_redistribution_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["macos"]
            filename = installer.spec.stable_archive
            package = {
                "component": "ezquake", "version": "3.6.9", "channel": "stable",
                "platform": "macos", "architecture": "universal", "filename": filename,
                "size": 42, "sha256": "a" * 64,
                "origin_url": f"https://example.invalid/original/{filename}",
                "license": "GPL-2.0", "license_url": "https://example.invalid/LICENSE",
                "source_urls": ["https://example.invalid/source.tar.gz"],
                "redistribution_reviewed": True,
                "urls": [f"https://downloads.x86.com.br/x86qw/{filename}"],
            }
            catalog = {"format": 1, "project": "x86qw", "packages": [package]}
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "http_get", return_value=json.dumps(catalog).encode()):
                    self.assertEqual(
                        [("3.6.9", tuple(package["urls"]), "a" * 64)],
                        installer.stable_catalog(),
                    )
            package["redistribution_reviewed"] = False
            installer._public_catalog = None
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "http_get", return_value=json.dumps(catalog).encode()):
                    with self.assertRaises(install_qw.InstallerError):
                        installer.stable_catalog()

    def test_public_catalog_is_reused_between_client_and_components(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["macos"]
            catalog = json.loads((ROOT / "site/public/api/v1/catalog.json").read_text())
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "http_get", return_value=json.dumps(catalog).encode()) as get:
                    installer.stable_catalog()
                    installer.component_package_record("nquake-bootstrap")
            get.assert_called_once_with(
                install_qw.CATALOG_URL, timeout=install_qw.CATALOG_TIMEOUT, attempts=1,
            )

    def test_online_mode_asks_for_a_target_and_ignores_local_distribution(self):
        online = install_qw.parse_arguments(["--online-only"], ROOT)
        local = install_qw.parse_arguments([], ROOT)
        self.assertIsNone(online.target)
        self.assertEqual(ROOT / "quake-world", local.target)
        with contextlib.redirect_stdout(io.StringIO()):
            with mock.patch("builtins.input", return_value=""):
                self.assertEqual(
                    Path.home() / "Games/x86qw",
                    install_qw.choose_public_target(),
                )
            with mock.patch("builtins.input", return_value="~/Games/meu-qw"):
                self.assertEqual(
                    Path.home() / "Games/meu-qw",
                    install_qw.choose_public_target(),
                )
        explicit = install_qw.parse_arguments(
            ["--online-only", "install", "/tmp/meu-x86qw"], ROOT,
        )
        self.assertEqual(Path("/tmp/meu-x86qw"), explicit.target)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "bundle"
            target = root / "game"
            (project / "site/public/api/v1").mkdir(parents=True)
            (project / "site/public/api/v1/catalog.json").write_text(
                '{"format":1,"project":"local-wrong","packages":[]}', encoding="utf-8",
            )
            artifact = project / "dist/test/file.zip"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"local")
            installer = install_qw.Installer(project, target, online_only=True)
            remote = {"format": 1, "project": "x86qw", "packages": []}
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "http_get", return_value=json.dumps(remote).encode()) as get:
                    self.assertEqual(remote, installer.public_catalog("remote"))
            get.assert_called_once_with(
                install_qw.CATALOG_URL, timeout=install_qw.CATALOG_TIMEOUT, attempts=1,
            )
            self.assertIsNone(installer.distribution_artifact(
                "test/file.zip", "file.zip", expected_size=5,
                expected_sha256=install_qw.hashlib.sha256(b"local").hexdigest(),
            ))

    def test_online_install_preserves_a_self_contained_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "x86qw"
            target.mkdir()
            stale = target / ".x86qw/cli/dist/game-data/id1/pak0.pak"
            stale.parent.mkdir(parents=True)
            stale.write_bytes(b"legacy bundled PAK")
            bundle = Path(temporary) / "bundle"
            bundle.mkdir()
            application = bundle / "x86qw.pyz"
            application.write_bytes(zipapp_bytes("1.0.6"))
            for name in ("x86qw.sh", "x86qw.cmd"):
                (bundle / name).write_bytes((ROOT / "dist/installer/bin" / name).read_bytes())
            installer = install_qw.Installer(ROOT, target, online_only=True)
            installer.project_root = bundle
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(
                    installer, "installer_bundle_identity",
                    return_value={"format": 1, "project": "x86qw", "version": "1.0.6"},
                ):
                    installer.install_online_cli()
            self.assertEqual(
                [target / ".x86qw/cli/receipt", target / ".x86qw/cli/x86qw.pyz"],
                sorted(path for path in (target / ".x86qw/cli").rglob("*") if path.is_file()),
            )
            self.assertTrue((target / "x86qw.cmd").is_file())
            self.assertEqual(
                (ROOT / "dist/installer/bin/x86qw.sh").read_bytes(),
                (target / "x86qw.sh").read_bytes(),
            )
            self.assertFalse((target / "x86qw").exists())
            self.assertEqual(
                (ROOT / "dist/installer/bin/x86qw.cmd").read_bytes(),
                (target / "x86qw.cmd").read_bytes(),
            )
            self.assertEqual("1.0.6", installer.installed_cli_version())
            if os.name == "nt":
                launcher = target / "x86qw.cmd"
                command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher)]
                usage = "Uso: x86qw.cmd <comando>"
                play_usage = "play"
            else:
                launcher = target / "x86qw.sh"
                command = [str(launcher)]
                usage = "Uso: ./x86qw.sh <comando>"
                play_usage = "./x86qw.sh play"
                self.assertTrue(os.access(launcher, os.X_OK))
            environment = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            result = subprocess.run(
                command, text=True, encoding="utf-8", capture_output=True,
                check=False, env=environment,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("x86QW 1.0.6", result.stdout)
            self.assertIn(usage, result.stdout)
            self.assertIn(play_usage, result.stdout)
            self.assertIn("upgrade", result.stdout)
            self.assertNotIn("components", result.stdout)
            for argument in ("version", "--version"):
                with self.subTest(argument=argument):
                    version = subprocess.run(
                        [*command, argument], text=True, encoding="utf-8",
                        capture_output=True, check=False, env=environment,
                    )
                    self.assertEqual(0, version.returncode, version.stderr)
                    self.assertEqual("x86QW 1.0.6\n", version.stdout)
            rejected = subprocess.run(
                [*command, "install"], text=True, encoding="utf-8",
                capture_output=True, check=False, env=environment,
            )
            self.assertEqual(2, rejected.returncode)
            self.assertIn("comando desconhecido", rejected.stderr)
            play = subprocess.run(
                [*command, "play", "--help"], text=True, encoding="utf-8",
                capture_output=True, check=False, env=environment,
            )
            self.assertEqual(0, play.returncode, play.stderr)
            self.assertIn("Abre os mods locais", play.stdout)

    def test_flat_metadata_is_migrated_into_contextual_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            metadata = target / ".x86qw"
            (metadata / "cli").mkdir(parents=True)
            (metadata / "cli/x86qw.pyz").write_bytes(b"zipapp")
            self.write_cli_receipt(target, "1.0.5", legacy=True)

            spec = install_qw.PLATFORMS["macos"]
            legacy_client = target / spec.legacy_receipt("stable")
            installer.write_ezquake_receipt_record(legacy_client, {
                "format": "1", "platform": "macos", "architecture": "universal",
                "channel": "stable", "selection": "3.6.9",
                "install_name": spec.runtime("stable"), "bundle_version": "3.6.9",
                "artifact_name": spec.stable_archive,
                "artifact_url": f"https://example.invalid/{spec.stable_archive}",
                "artifact_sha256": "a" * 64, "binary_sha256": "b" * 64,
            })

            legacy_receipt, legacy_inventory = (
                target / relative
                for relative in installer.legacy_component_metadata("nquake-bootstrap")
            )
            legacy_inventory.write_text(f"qw/test.cfg\t{'c' * 64}\n", encoding="utf-8")
            installer.write_component_receipt(
                "nquake-bootstrap", "test", "https://example.invalid/component.zip",
                legacy_inventory, legacy_receipt,
            )

            self.assertTrue(installer.legacy_metadata_present())
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(installer.migrate_metadata_layout())
            self.assertFalse(installer.legacy_metadata_present())
            self.assertEqual("1.0.5", installer.installed_cli_version())
            self.assertTrue((metadata / "cli/receipt").is_file())
            self.assertTrue((metadata / "clients/ezquake/macos/stable.receipt").is_file())
            self.assertTrue((metadata / "components/nquake-bootstrap/receipt").is_file())
            self.assertTrue((metadata / "components/nquake-bootstrap/inventory").is_file())
            self.assertFalse(legacy_client.exists())
            self.assertFalse(legacy_receipt.exists())
            self.assertFalse(legacy_inventory.exists())
            self.assertFalse(installer.migrate_metadata_layout())

    def test_installed_cli_rejects_installation_actions(self):
        for action in ("install", "components", "presets"):
            with self.subTest(action=action):
                with self.assertRaises(SystemExit) as raised:
                    with contextlib.redirect_stderr(io.StringIO()):
                        install_qw.parse_arguments(
                            ["--online-only", "--installed-cli", action, "/tmp/x86qw"], ROOT,
                        )
                self.assertEqual(2, raised.exception.code)
        parsed = install_qw.parse_arguments(
            ["--online-only", "--installed-cli", "update", "/tmp/x86qw"], ROOT,
        )
        self.assertEqual("update", parsed.action)
        upgrade = install_qw.parse_arguments(
            ["--online-only", "--installed-cli", "--dry-run", "upgrade", "/tmp/x86qw"], ROOT,
        )
        self.assertEqual("upgrade", upgrade.action)
        self.assertTrue(upgrade.dry_run)
        confirmed = install_qw.parse_arguments(
            ["--online-only", "--installed-cli", "update", "/tmp/x86qw", "--yes"], ROOT,
        )
        self.assertTrue(confirmed.yes)

    def test_yes_is_reserved_for_update_and_upgrade(self):
        with self.assertRaises(SystemExit) as raised:
            with contextlib.redirect_stderr(io.StringIO()):
                install_qw.parse_arguments(["verify", "--yes"], ROOT)
        self.assertEqual(2, raised.exception.code)

    def test_platform_override_is_available_only_during_installation(self):
        parsed = install_qw.parse_arguments(["install", "--platform", "windows"], ROOT)
        self.assertEqual("windows", parsed.platform)
        with self.assertRaises(SystemExit) as raised:
            with contextlib.redirect_stderr(io.StringIO()):
                install_qw.parse_arguments(["verify", "--platform", "linux"], ROOT)
        self.assertEqual(2, raised.exception.code)

    def test_main_passes_platform_override_to_installation(self):
        target = Path("/tmp/x86qw-platform-test")
        installer = mock.Mock()
        installer.target = target
        with mock.patch.object(install_qw, "Installer", return_value=installer):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    0,
                    install_qw.main(["install", str(target), "--platform", "linux"]),
                )
        installer.install.assert_called_once()
        self.assertEqual("linux", installer.install.call_args.kwargs["platform"])
        self.assertTrue(callable(installer.install.call_args.kwargs["before_mutation"]))

    def test_active_service_lock_blocks_every_mutating_manager_action(self):
        cases = (
            ["install"], ["components"], ["presets"], ["update"],
            ["update", "--dry-run"], ["upgrade"], ["upgrade", "--dry-run"],
            ["repair"], ["repair", "--dry-run"], ["cleanup"],
            ["cleanup", "--personal-data"], ["uninstall"], ["uninstall", "--purge"],
        )
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            (target / ".x86qw").mkdir()
            active = install_qw.session_control.InstallationLock.acquire(
                target, "host", "service",
            )
            try:
                for arguments in cases:
                    with self.subTest(arguments=arguments):
                        installer = mock.Mock()
                        installer.target = target
                        if arguments[0] == "install":
                            installer.install.side_effect = (
                                lambda *, platform=None, before_mutation=None: before_mutation()
                            )
                        with mock.patch.object(install_qw, "Installer", return_value=installer):
                            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                                result = install_qw.main([
                                    arguments[0], str(target), *arguments[1:],
                                ])
                        self.assertEqual(1, result)
                        installer.install_online_cli.assert_not_called()
                        installer.manage_components.assert_not_called()
                        installer.manage_presets.assert_not_called()
                        installer.update.assert_not_called()
                        installer.upgrade.assert_not_called()
                        installer.repair.assert_not_called()
                        installer.cleanup_cache.assert_not_called()
                        installer.uninstall.assert_not_called()
                        installer.purge.assert_not_called()
            finally:
                active.release()

    def test_read_only_and_gameplay_actions_remain_available_during_maintenance(self):
        self.assertTrue(
            {"version", "verify", "hub", "play"}.isdisjoint(
                install_qw.session_control.LOCK_COMMANDS
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            (target / ".x86qw").mkdir()
            active = install_qw.session_control.InstallationLock.acquire(
                target, "repair", "maintenance",
            )
            try:
                installer = mock.Mock()
                installer.target = target
                with mock.patch.object(install_qw, "Installer", return_value=installer):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(0, install_qw.main(["verify", str(target)]))
                        self.assertEqual(0, install_qw.main(["hub", str(target)]))
                with mock.patch("gameplay.main", return_value=0):
                    self.assertEqual(0, install_qw.main(["play"]))
                installer.verify_installation.assert_called_once()
                installer.browse_hub.assert_called_once()
            finally:
                active.release()

    def test_cli_update_handoff_downloads_before_final_process_acquires_lock(self):
        target = Path("/tmp/x86qw-handoff-lock-test")
        old_cli = mock.Mock()
        old_cli.target = target
        old_cli.handoff_cli_update.return_value = True
        with mock.patch.object(install_qw, "Installer", return_value=old_cli):
            with mock.patch.object(
                install_qw.session_control.InstallationLock,
                "acquire",
                side_effect=AssertionError("CLI antiga adquiriu o lock"),
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(0, install_qw.main([
                        "--online-only", "--installed-cli", "update", str(target),
                    ]))
        old_cli.handoff_cli_update.assert_called_once()

        final_cli = mock.Mock()
        final_cli.target = target
        final_cli.update.return_value = False
        final_cli.cli_update_plan_row.return_value = None
        operation_lock = mock.Mock()
        with mock.patch.object(install_qw, "Installer", return_value=final_cli):
            with mock.patch.object(
                install_qw.session_control.InstallationLock,
                "acquire", return_value=operation_lock,
            ) as acquire:
                with mock.patch("services.recover_sessions"):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(0, install_qw.main([
                            "--online-only", "--installed-cli", "--skip-cli-update",
                            "update", str(target),
                        ]))
        acquire.assert_called_once_with(target, "update", "maintenance")
        final_cli.handoff_cli_update.assert_not_called()
        operation_lock.release.assert_called_once()

    def test_update_shows_homebrew_style_plan_and_requires_confirmation(self):
        target = Path("/tmp/x86qw-confirmation-test")
        confirm = install_qw.Installer.confirm_update_plan
        installer = mock.Mock()
        installer.target = target
        def update(*, dry_run, preview=False, plan_rows=None):
            del preview
            if dry_run and plan_rows is not None:
                plan_rows.append(install_qw.UpdatePlanRow(
                    "Componente", "Configuração base nQuake", "1", "2", "Atualizar", 15360,
                ))
            return True
        installer.update.side_effect = update
        installer.confirm_update_plan.side_effect = confirm
        output = io.StringIO()
        with mock.patch.object(install_qw, "Installer", return_value=installer):
            with mock.patch("builtins.input", return_value="no"):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(0, install_qw.main(["update", str(target)]))
        self.assertEqual(1, installer.update.call_count)
        self.assertTrue(installer.update.call_args.kwargs["dry_run"])
        self.assertIn("==> Would update 1 outdated package", output.getvalue())
        self.assertIn("Configuração base nQuake", output.getvalue())
        self.assertIn("1 -> 2 (15.4KB)", output.getvalue())
        self.assertIn("nenhum arquivo do jogo foi alterado", output.getvalue())

    def test_upgrade_yes_shows_plan_and_applies_without_prompting(self):
        target = Path("/tmp/x86qw-confirmation-test")
        confirm = install_qw.Installer.confirm_update_plan
        installer = mock.Mock()
        installer.target = target
        def upgrade(*, dry_run, preview=False, plan_rows=None):
            del preview
            if dry_run and plan_rows is not None:
                plan_rows.append(install_qw.UpdatePlanRow(
                    "Componente", "Total Destruction 2", "2.21", "2.22", "Atualizar",
                ))
            return True
        installer.upgrade.side_effect = upgrade
        installer.confirm_update_plan.side_effect = confirm
        output = io.StringIO()
        with mock.patch.object(install_qw, "Installer", return_value=installer):
            with mock.patch("builtins.input") as prompt:
                with contextlib.redirect_stdout(output):
                    self.assertEqual(0, install_qw.main(["upgrade", str(target), "--yes"]))
        prompt.assert_not_called()
        self.assertEqual(2, installer.upgrade.call_count)
        self.assertTrue(installer.upgrade.call_args_list[0].kwargs["dry_run"])
        self.assertFalse(installer.upgrade.call_args_list[1].kwargs["dry_run"])
        self.assertIn("confirmado automaticamente por --yes", output.getvalue())

    def test_update_dry_run_only_shows_plan_without_confirmation(self):
        target = Path("/tmp/x86qw-confirmation-test")
        installer = mock.Mock()
        installer.target = target
        def update(*, dry_run, preview=False, plan_rows=None):
            del preview
            if dry_run and plan_rows is not None:
                plan_rows.append(install_qw.UpdatePlanRow(
                    "Cliente", "ezQuake macOS stable", "3.6.9", "3.6.10", "Atualizar",
                ))
            return True
        installer.update.side_effect = update
        with mock.patch.object(install_qw, "Installer", return_value=installer):
            with mock.patch("builtins.input") as prompt:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(0, install_qw.main(["update", str(target), "--dry-run"]))
        prompt.assert_not_called()
        self.assertEqual(1, installer.update.call_count)
        self.assertTrue(installer.update.call_args.kwargs["dry_run"])

    def test_update_without_changes_exits_before_confirmation_and_application(self):
        target = Path("/tmp/x86qw-no-update-test")
        installer = mock.Mock()
        installer.target = target
        installer.update.return_value = False
        output = io.StringIO()
        with mock.patch.object(install_qw, "Installer", return_value=installer):
            with mock.patch("builtins.input") as prompt:
                with contextlib.redirect_stdout(output):
                    self.assertEqual(0, install_qw.main(["update", str(target)]))
        prompt.assert_not_called()
        installer.confirm_update_plan.assert_not_called()
        self.assertEqual(1, installer.update.call_count)
        self.assertIn("Nenhuma atualização disponível", output.getvalue())
        self.assertNotIn("Aplicação do plano", output.getvalue())

    def test_upgrade_without_changes_exits_before_confirmation_and_application(self):
        target = Path("/tmp/x86qw-no-upgrade-test")
        installer = mock.Mock()
        installer.target = target
        installer.upgrade.return_value = False
        output = io.StringIO()
        with mock.patch.object(install_qw, "Installer", return_value=installer):
            with mock.patch("builtins.input") as prompt:
                with contextlib.redirect_stdout(output):
                    self.assertEqual(0, install_qw.main(["upgrade", str(target)]))
        prompt.assert_not_called()
        installer.confirm_update_plan.assert_not_called()
        self.assertEqual(1, installer.upgrade.call_count)
        self.assertIn("Nenhuma novidade disponível", output.getvalue())
        self.assertNotIn("Aplicação do plano", output.getvalue())

    def test_noninteractive_update_requires_yes(self):
        with mock.patch("builtins.input", side_effect=EOFError):
            with self.assertRaisesRegex(install_qw.InstallerError, "use --yes"):
                install_qw.Installer.confirm_update_plan("update", assume_yes=False)

    def test_update_confirmation_accepts_homebrew_y_prompt(self):
        with mock.patch("builtins.input", return_value="y") as prompt:
            self.assertTrue(
                install_qw.Installer.confirm_update_plan("update", assume_yes=False)
            )
        self.assertIn("[y/n]", prompt.call_args.args[0])

    def test_component_update_only_selects_already_installed_outdated_items(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            receipts = {
                "ktx": {"selection": "1.46"},
                "total-destruction-2": {"selection": "2.22"},
            }
            packages = {
                "ktx": {"version": "1.47+x86qw.2"},
                "total-destruction-2": {"version": "2.22"},
            }
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(
                    installer, "installed_components",
                    return_value=["ktx", "total-destruction-2"],
                ):
                    with mock.patch.object(
                        installer, "validate_component_pair",
                        side_effect=lambda identifier: (True, [], receipts[identifier]),
                    ):
                        with mock.patch.object(
                            installer, "component_package_record",
                            side_effect=lambda identifier: packages[identifier],
                        ):
                            self.assertEqual(["ktx"], installer.outdated_installed_components())

    def test_component_update_never_downgrades_a_newer_x86qw_overlay(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            output = io.StringIO()
            with mock.patch.object(installer, "installed_components", return_value=["ktx"]):
                with mock.patch.object(
                    installer, "validate_component_pair",
                    return_value=(True, [], {"selection": "1.47+x86qw.3"}),
                ):
                    with mock.patch.object(
                        installer, "component_package_record",
                        return_value={"version": "1.47+x86qw.2"},
                    ):
                        with contextlib.redirect_stdout(output):
                            self.assertEqual([], installer.outdated_installed_components())
            self.assertIn("é mais novo que o catálogo", output.getvalue())

    def test_existing_installation_profile_is_inferred_and_persisted(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            recommended = list(installer.component_catalog["profiles"]["recommended"])
            with mock.patch.object(installer, "installed_components", return_value=recommended):
                with contextlib.redirect_stdout(io.StringIO()):
                    state = installer.load_install_state(persist_migration=True)
            self.assertEqual("recommended", state["profile"])
            self.assertEqual([], state["requested_components"])
            self.assertEqual(recommended, state["recorded_components"])
            persisted = json.loads((target / install_qw.INSTALL_STATE).read_text(encoding="utf-8"))
            self.assertEqual(state, persisted)

    def test_historical_profile_fingerprint_recognizes_clients_that_skipped_releases(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            old_recommended = list(installer.component_catalog["profiles"]["recommended"])
            installer.component_catalog["profiles"]["recommended"] = [*old_recommended, "future-feature"]
            with mock.patch.object(installer, "installed_components", return_value=old_recommended):
                state = installer.infer_install_state()
            self.assertEqual("recommended", state["profile"])
            self.assertEqual([], state["requested_components"])

    def test_nonstandard_existing_installation_becomes_a_safe_custom_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installed = ["nquake-bootstrap", "total-destruction-2"]
            with mock.patch.object(installer, "installed_components", return_value=installed):
                state = installer.infer_install_state()
            self.assertEqual("custom", state["profile"])
            self.assertEqual(installed, state["requested_components"])

    def test_stale_custom_state_is_recovered_as_its_historical_complete_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installed = [
                identifier
                for identifier in installer.component_catalog["profiles"]["complete"]
                if identifier not in {"mvdsv", "qwfwd", "qtv"}
            ]
            fingerprint = install_qw.profile_fingerprint(installed)
            self.assertIn(fingerprint, installer.component_catalog["profile_history"]["complete"])
            state = {
                "format": 1,
                "project": "x86qw",
                "profile": "custom",
                "requested_components": ["ktx"],
                "recorded_components": installed,
                "known_components": list(installer.components),
            }
            metadata = (target / install_qw.INSTALL_STATE).parent
            metadata.mkdir(parents=True)
            (target / install_qw.INSTALL_STATE).write_text(
                json.dumps(state), encoding="utf-8",
            )
            with mock.patch.object(installer, "installed_components", return_value=installed):
                with contextlib.redirect_stdout(io.StringIO()):
                    migrated = installer.load_install_state(persist_migration=True)
            self.assertEqual("complete", migrated["profile"])
            self.assertEqual([], migrated["requested_components"])
            persisted = json.loads((target / install_qw.INSTALL_STATE).read_text(encoding="utf-8"))
            self.assertEqual("complete", persisted["profile"])
            self.assertEqual([], persisted["requested_components"])

    def test_valid_custom_state_is_not_reclassified_as_a_named_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installed = list(installer.component_catalog["profiles"]["essential"])
            state = {
                "format": 1,
                "project": "x86qw",
                "profile": "custom",
                "requested_components": installed,
                "recorded_components": installed,
                "known_components": list(installer.components),
            }
            metadata = (target / install_qw.INSTALL_STATE).parent
            metadata.mkdir(parents=True)
            (target / install_qw.INSTALL_STATE).write_text(
                json.dumps(state), encoding="utf-8",
            )
            with mock.patch.object(installer, "installed_components", return_value=installed):
                loaded = installer.load_install_state(persist_migration=True)
            self.assertEqual("custom", loaded["profile"])
            self.assertEqual(installed, loaded["requested_components"])

    def test_obsolete_nquake_sounds_is_removed_from_saved_component_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            state = {
                "format": 1,
                "project": "x86qw",
                "profile": "custom",
                "requested_components": ["ktx", "nquake-sounds"],
                "recorded_components": ["ktx", "nquake-sounds"],
                "known_components": [*installer.components, "nquake-sounds"],
            }
            migrated = installer.current_install_state(state)
            self.assertEqual(["ktx"], migrated["requested_components"])
            self.assertEqual(["ktx"], migrated["recorded_components"])
            self.assertNotIn("nquake-sounds", migrated["known_components"])

    def test_format_one_state_migrates_once_without_changing_custom_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            selected = ["ktx", "qtv", "qwfwd"]
            historical = {
                "format": 1,
                "project": "x86qw",
                "profile": "custom",
                "requested_components": list(selected),
                "recorded_components": list(selected),
                "known_components": list(installer.components),
            }
            state_path = target / install_qw.INSTALL_STATE
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps(historical), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "installed_components", return_value=list(selected)):
                    migrated = installer.load_install_state(persist_migration=True)
                    loaded_again = installer.load_install_state(persist_migration=True)
            self.assertEqual(2, migrated["format"])
            self.assertEqual("custom", migrated["profile"])
            self.assertEqual(selected, migrated["requested_components"])
            self.assertEqual(selected, migrated["recorded_components"])
            self.assertEqual([], migrated["capabilities"])
            self.assertEqual(
                install_qw.profile_fingerprint(selected), migrated["component_fingerprint"],
            )
            self.assertEqual(migrated, loaded_again)
            persisted = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(migrated, persisted)

    def test_format_two_state_preserves_empty_installation_capabilities(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            selected = ["mvdsv", "qtv"]
            capabilities: list[str] = []
            state = {
                "format": 2,
                "project": "x86qw",
                "profile": "custom",
                "requested_components": list(selected),
                "recorded_components": list(selected),
                "known_components": list(installer.components),
                "capabilities": capabilities,
                "component_fingerprint": install_qw.profile_fingerprint(selected),
            }
            state_path = target / install_qw.INSTALL_STATE
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with mock.patch.object(installer, "installed_components", return_value=list(selected)):
                loaded = installer.load_install_state(persist_migration=True)
            self.assertEqual(capabilities, loaded["capabilities"])
            self.assertEqual(state, json.loads(state_path.read_text(encoding="utf-8")))

    def test_format_two_state_rejects_undeclared_installation_capabilities(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            selected = ["mvdsv", "qtv"]
            state = {
                "format": 2,
                "project": "x86qw",
                "profile": "custom",
                "requested_components": list(selected),
                "recorded_components": list(selected),
                "known_components": list(installer.components),
                "capabilities": ["qualquer-coisa"],
                "component_fingerprint": install_qw.profile_fingerprint(selected),
            }
            with self.assertRaisesRegex(install_qw.InstallerError, "Capacidades"):
                installer.validate_install_state(state)

    def test_repair_plans_missing_stable_and_nightly_clients_at_recorded_version(self):
        for channel in ("stable", "nightly"):
            with self.subTest(channel=channel), tempfile.TemporaryDirectory() as temporary:
                installer, target, _ = self.make_installer(Path(temporary))
                spec = install_qw.PLATFORMS["linux"]
                _, receipt, _ = self.write_ezquake_fixture(installer, target, spec, channel)
                release = (receipt["selection"], (f"https://example.invalid/{receipt['artifact_name']}",), "a" * 64)
                with mock.patch.object(installer, "client_catalog_release", return_value=release):
                    issues, diagnostics = installer.client_repair_assessment()
                self.assertEqual([], diagnostics)
                self.assertEqual(1, len(issues))
                self.assertEqual("payload", issues[0].mode)
                self.assertEqual(receipt["selection"], issues[0].release[0])

    @unittest.skipIf(os.name == "nt", "permissão executável usa bits POSIX")
    def test_repair_detects_appimage_without_execution_as_local_fix(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["linux"]
            elf = bytearray(64)
            elf[:5] = b"\x7fELF\x02"
            struct.pack_into("<H", elf, 18, 62)
            _, receipt, runtime = self.write_ezquake_fixture(
                installer, target, spec, "stable", payload=bytes(elf),
            )
            runtime.chmod(0o600)
            with mock.patch.object(
                installer, "client_catalog_release", side_effect=AssertionError("catálogo consultado"),
            ):
                issues, diagnostics = installer.client_repair_assessment()
            self.assertEqual([], diagnostics)
            self.assertEqual("permission", issues[0].mode)
            self.assertEqual("local-repair", issues[0].category)

    def test_repair_detects_corrupt_windows_exe_and_incomplete_macos_bundle(self):
        for platform in ("windows", "macos"):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as temporary:
                installer, target, _ = self.make_installer(Path(temporary))
                spec = install_qw.PLATFORMS[platform]
                receipt_path, receipt, runtime = self.write_ezquake_fixture(
                    installer, target, spec, "stable",
                    payload=b"corrupt" if platform == "windows" else None,
                )
                if platform == "macos":
                    runtime.mkdir(parents=True)
                release = (receipt["selection"], (f"https://example.invalid/{receipt['artifact_name']}",), "a" * 64)
                with mock.patch.object(installer, "client_catalog_release", return_value=release):
                    issues, diagnostics = installer.client_repair_assessment()
                self.assertEqual([], diagnostics)
                self.assertEqual(receipt_path, issues[0].receipt_path)
                self.assertEqual("payload", issues[0].mode)

    def test_repair_detects_missing_macos_preparation_without_replacing_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            _, receipt, runtime = self.write_ezquake_fixture(installer, target, spec, "stable")
            runtime.mkdir(parents=True)
            with mock.patch.object(
                installer, "client_catalog_release", side_effect=AssertionError("catálogo consultado"),
            ):
                with mock.patch.object(installer, "check_runtime"):
                    with mock.patch.object(installer, "macos_app_needs_preparation", return_value=True):
                        issues, diagnostics = installer.client_repair_assessment()
            self.assertEqual([], diagnostics)
            self.assertEqual("macos-preparation", issues[0].mode)
            self.assertEqual("local-repair", issues[0].category)

    @unittest.skipIf(os.name == "nt", "permissão executável usa bits POSIX")
    def test_offline_client_permission_repair_completes_without_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["linux"]
            elf = bytearray(64)
            elf[:5] = b"\x7fELF\x02"
            struct.pack_into("<H", elf, 18, 62)
            _, _, runtime = self.write_ezquake_fixture(
                installer, target, spec, "stable", payload=bytes(elf),
            )
            runtime.chmod(0o600)
            with mock.patch.object(
                installer, "client_catalog_release", side_effect=AssertionError("catálogo consultado"),
            ):
                issues, diagnostics = installer.client_repair_assessment()
            assessment = install_qw.RepairAssessment((), False, (), False, tuple(issues), tuple(diagnostics))
            state = {"profile": "none", "requested_components": [], "known_components": [], "capabilities": []}
            with mock.patch.object(installer, "repair_plan", return_value=assessment):
                with mock.patch.object(installer, "load_install_state", return_value=state):
                    with mock.patch.object(installer, "write_install_state"):
                        with mock.patch.object(installer, "verify_installation"):
                            self.assertTrue(installer.repair(
                                dry_run=False, plan_rows=[], allow_download=False,
                            ))
            self.assertTrue(os.access(runtime, os.X_OK))

    def test_offline_macos_preparation_repair_completes_without_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            receipt_path, receipt, runtime = self.write_ezquake_fixture(
                installer, target, spec, "stable",
            )
            runtime.mkdir(parents=True)
            with mock.patch.object(installer, "check_runtime"):
                with mock.patch.object(installer, "macos_app_needs_preparation", return_value=True):
                    with mock.patch.object(
                        installer, "client_catalog_release", side_effect=AssertionError("catálogo consultado"),
                    ):
                        issues, diagnostics = installer.client_repair_assessment()
            assessment = install_qw.RepairAssessment((), False, (), False, tuple(issues), tuple(diagnostics))
            state = {"profile": "none", "requested_components": [], "known_components": [], "capabilities": []}
            with mock.patch.object(installer, "repair_plan", return_value=assessment):
                with mock.patch.object(installer, "repair_installed_macos_runtime") as prepare:
                    with mock.patch.object(installer, "load_install_state", return_value=state):
                        with mock.patch.object(installer, "write_install_state"):
                            with mock.patch.object(installer, "verify_installation"):
                                self.assertTrue(installer.repair(
                                    dry_run=False, plan_rows=[], allow_download=False,
                                ))
            prepare.assert_called_once_with(spec, "stable", receipt_path, receipt)

    def test_repair_diagnoses_partial_component_metadata_in_both_directions(self):
        for missing in ("receipt", "inventory"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temporary:
                installer, target, _ = self.make_installer(Path(temporary))
                receipt, inventory = (
                    target / relative
                    for relative in installer.component_metadata("ktx")
                )
                receipt.parent.mkdir(parents=True)
                present = inventory if missing == "receipt" else receipt
                present.write_text("parcial\n", encoding="utf-8")
                valid, diagnostics = installer.component_metadata_assessment()
                self.assertNotIn("ktx", valid)
                self.assertTrue(any("ktx" in diagnostic for diagnostic in diagnostics))
                self.assertTrue(present.exists())

    def test_repair_reconstructs_missing_state_from_complete_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            receipt, inventory = (
                target / relative for relative in installer.component_metadata("ktx")
            )
            receipt.parent.mkdir(parents=True)
            inventory.write_text("", encoding="utf-8")
            installer.write_component_receipt(
                "ktx", "test", "https://example.invalid/ktx.zip", inventory, receipt,
            )
            with mock.patch.object(installer, "check_paks"):
                with mock.patch.object(installer, "verify_component", return_value=0):
                    with mock.patch.object(installer, "play_support_player") as player:
                        player.return_value.available_local_games.return_value = []
                        player.return_value.local_play_support_issues.return_value = []
                        with mock.patch.object(installer, "verify_qw_package_order"):
                            assessment = installer.repair_plan()
            self.assertIsNotNone(assessment.recovered_state)
            self.assertIn("ktx", assessment.recovered_state["recorded_components"])
            self.assertEqual((), assessment.metadata_diagnostics)

    def test_repair_diagnoses_runtime_without_receipt_and_invalid_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["windows"]
            runtime = target / spec.runtime("stable")
            runtime.write_bytes(b"unmanaged")
            _, diagnostics = installer.client_repair_assessment()
            self.assertTrue(any("runtime presente sem recibo" in item for item in diagnostics))
            receipt = target / spec.receipt("stable")
            receipt.parent.mkdir(parents=True)
            receipt.write_text("partial\n", encoding="utf-8")
            _, diagnostics = installer.client_repair_assessment()
            self.assertTrue(any("recibo inválido ou parcial" in item for item in diagnostics))
            self.assertEqual(b"unmanaged", runtime.read_bytes())

    def test_repair_uses_exact_recorded_client_release_without_downgrade(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["linux"]
            recorded = ("3.6.10", ("https://example.invalid/recorded.zip",), "b" * 64)
            older = ("3.6.9", ("https://example.invalid/older.zip",), "a" * 64)
            with mock.patch.object(installer, "stable_catalog", return_value=[recorded, older]):
                self.assertEqual(recorded, installer.client_catalog_release(spec, "stable", "3.6.10"))
                self.assertIsNone(installer.client_catalog_release(spec, "stable", "3.6.11"))

    def test_repair_without_changes_and_dry_run_do_not_mutate(self):
        empty = install_qw.RepairAssessment((), False, (), False, (), ())
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            with mock.patch.object(installer, "repair_plan", return_value=empty):
                self.assertFalse(installer.repair(dry_run=True, plan_rows=[]))
                self.assertFalse(installer.repair(dry_run=False, plan_rows=[]))

    def test_installed_cli_repair_reports_payload_plan_without_downloading(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["linux"]
            receipt_path, receipt, _ = self.write_ezquake_fixture(installer, target, spec, "stable")
            release = (receipt["selection"], (f"https://example.invalid/{receipt['artifact_name']}",), "a" * 64)
            issue = install_qw.ClientRepairIssue(
                spec, "stable", receipt_path, receipt, "runtime ausente", "payload", release,
            )
            assessment = install_qw.RepairAssessment((), False, (), False, (issue,), ())
            rows = []
            with mock.patch.object(installer, "repair_plan", return_value=assessment):
                self.assertTrue(installer.repair(dry_run=True, plan_rows=rows))
                self.assertEqual("Cliente", rows[0].kind)
                with self.assertRaisesRegex(install_qw.InstallerError, "reexecute o bootstrap"):
                    installer.repair(dry_run=False, plan_rows=[], allow_download=False)

    @unittest.skipIf(os.name == "nt", "permissão executável usa bits POSIX")
    def test_local_repair_preserves_personal_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            runtime = target / "runtime"
            runtime.write_bytes(b"runtime")
            runtime.chmod(0o600)
            personal = target / "ezquake/configs/config.cfg"
            personal.parent.mkdir(parents=True)
            personal.write_text("bind x impulse 7\n", encoding="utf-8")
            assessment = install_qw.RepairAssessment((), False, (runtime,), False, (), ())
            state = {
                "profile": "none", "requested_components": [], "known_components": [],
                "capabilities": [],
            }
            with mock.patch.object(installer, "repair_plan", return_value=assessment):
                with mock.patch.object(installer, "load_install_state", return_value=state):
                    with mock.patch.object(installer, "write_install_state"):
                        with mock.patch.object(installer, "verify_installation"):
                            self.assertTrue(installer.repair(
                                dry_run=False, plan_rows=[], allow_download=False,
                            ))
            self.assertTrue(os.access(runtime, os.X_OK))
            self.assertEqual("bind x impulse 7\n", personal.read_text(encoding="utf-8"))

    def test_upgrade_adds_only_components_newly_required_by_the_recorded_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            desired = list(installer.component_catalog["profiles"]["essential"])
            installed = desired[:-1]
            state = {
                "format": 1,
                "project": "x86qw",
                "profile": "essential",
                "requested_components": [],
                "recorded_components": list(installed),
                "known_components": list(installer.components),
            }

            def install_missing(selected):
                installed.extend(selected)

            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "update", return_value=False):
                    with mock.patch.object(installer, "load_install_state", return_value=state):
                        with mock.patch.object(installer, "installed_components", side_effect=lambda: list(installed)):
                            with mock.patch.object(installer, "install_components", side_effect=install_missing) as apply:
                                with mock.patch.object(installer, "verify_installation"):
                                    self.assertTrue(installer.upgrade())
            apply.assert_called_once_with([desired[-1]])
            persisted = json.loads((target / install_qw.INSTALL_STATE).read_text(encoding="utf-8"))
            self.assertEqual(desired, persisted["recorded_components"])

    def test_upgrade_dry_run_reports_new_profile_components_without_applying_them(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            desired = list(installer.component_catalog["profiles"]["essential"])
            state = {
                "format": 1,
                "project": "x86qw",
                "profile": "essential",
                "requested_components": [],
                "recorded_components": desired[:-1],
                "known_components": list(installer.components),
            }
            output = io.StringIO()
            plan_rows = []
            with contextlib.redirect_stdout(output):
                with mock.patch.object(installer, "update", return_value=False):
                    with mock.patch.object(installer, "load_install_state", return_value=state):
                        with mock.patch.object(installer, "installed_components", return_value=desired[:-1]):
                            with mock.patch.object(
                                installer, "component_package_record", return_value={"version": "nova"},
                            ):
                                with mock.patch.object(installer, "install_components") as apply:
                                    self.assertTrue(installer.upgrade(
                                        dry_run=True, plan_rows=plan_rows,
                                    ))
            apply.assert_not_called()
            self.assertEqual("Adicionar", plan_rows[0].action)
            self.assertEqual("não instalado", plan_rows[0].installed)
            self.assertFalse((target / install_qw.INSTALL_STATE).exists())

    def test_upgrade_dry_run_with_complete_profile_does_not_open_an_install_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            desired = list(installer.component_catalog["profiles"]["essential"])
            state = {
                "format": 1,
                "project": "x86qw",
                "profile": "essential",
                "requested_components": [],
                "recorded_components": desired,
                "known_components": list(installer.components),
            }
            plan_rows = []
            with mock.patch.object(installer, "update", return_value=False):
                with mock.patch.object(installer, "load_install_state", return_value=state):
                    with mock.patch.object(installer, "installed_components", return_value=desired):
                        with mock.patch.object(installer, "install_components") as apply:
                            self.assertFalse(installer.upgrade(
                                dry_run=True, plan_rows=plan_rows,
                            ))
            apply.assert_not_called()
            self.assertEqual([], plan_rows)

    def test_release_update_never_downgrades_an_installed_client(self):
        self.assertTrue(install_qw.Installer.release_is_newer("3.6.10", "3.6.9", "stable"))
        self.assertFalse(install_qw.Installer.release_is_newer("3.6.8", "3.6.9", "stable"))
        self.assertTrue(
            install_qw.Installer.release_is_newer(
                "20260729-120000_abcdef0", "20260728-120000_abcdef0", "nightly",
            )
        )

    def test_cli_update_hands_off_to_the_validated_new_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, target, _ = self.make_installer(root)
            self.write_cli_receipt(target, "1.0.4")
            archive = root / "x86qw-installer-1.0.5.zip"
            self.write_installer_bundle(archive, "1.0.5")
            record = {"version": "1.0.5"}
            completed = subprocess.CompletedProcess([], 0)
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "installer_bundle_record", return_value=record):
                    with mock.patch.object(installer, "download_component_package", return_value=archive):
                        with mock.patch.object(install_qw.subprocess, "run", return_value=completed) as run:
                            self.assertTrue(installer.handoff_cli_update(
                                "upgrade", dry_run=False, assume_yes=True,
                            ))
            command = run.call_args.args[0]
            self.assertIn("--installed-cli", command)
            self.assertIn("--skip-cli-update", command)
            self.assertIn("--yes", command)
            self.assertEqual(["upgrade", str(target)], command[-2:])
            self.assertFalse(any(target.glob(".x86qw-update.*")))

    def test_cli_update_selects_only_the_current_bundle_from_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            base = {
                "component": "installer",
                "package": "x86qw-installer",
                "channel": "content",
                "platform": "any",
                "architecture": "any",
                "size": 1,
                "sha256": "a" * 64,
                "urls": ["https://example.invalid/x86qw-installer-1.0.1.zip"],
                "redistribution_reviewed": True,
            }
            historical = dict(
                base, version="1.0.1", current=False,
                filename="x86qw-installer-1.0.1.zip",
            )
            current = dict(
                base, version="1.0.20", current=True,
                filename="x86qw-installer-1.0.20.zip",
                urls=["https://example.invalid/x86qw-installer-1.0.20.zip"],
            )
            installer._public_catalog = {
                "format": 1, "project": "x86qw", "packages": [historical, current],
            }

            self.assertEqual("1.0.20", installer.installer_bundle_record()["version"])

    def test_cli_update_never_downgrades_itself(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            self.write_cli_receipt(target, "1.0.6")
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(
                    installer, "installer_bundle_record", return_value={"version": "1.0.4"},
                ):
                    self.assertFalse(installer.handoff_cli_update("update", dry_run=False))

    def test_cli_update_dry_run_uses_new_bundle_to_show_the_complete_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, target, _ = self.make_installer(root)
            self.write_cli_receipt(target, "1.0.4")
            archive = root / "x86qw-installer-1.0.5.zip"
            self.write_installer_bundle(archive, "1.0.5")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                with mock.patch.object(
                    installer, "installer_bundle_record", return_value={"version": "1.0.5"},
                ):
                    with mock.patch.object(installer, "download_component_package", return_value=archive):
                        with mock.patch.object(
                            install_qw.subprocess, "run",
                            return_value=subprocess.CompletedProcess([], 0),
                        ) as run:
                            self.assertTrue(installer.handoff_cli_update("upgrade", dry_run=True))
            command = run.call_args.args[0]
            self.assertIn("--dry-run", command)
            self.assertIn("--skip-cli-update", command)
            self.assertNotIn("CLI x86QW disponível", output.getvalue())

    def test_resilient_connection_uses_reachable_dns_address_without_waiting(self):
        class FakeSocket:
            def __init__(self, reachable):
                self.reachable = reachable
                self.closed = False
                self.timeout = None

            def setblocking(self, value):
                del value

            def connect_ex(self, address):
                del address
                return install_qw.errno.EINPROGRESS

            def getsockopt(self, level, option):
                del level, option
                return 0 if self.reachable else install_qw.errno.EHOSTUNREACH

            def settimeout(self, value):
                self.timeout = value

            def close(self):
                self.closed = True

        class FakeSelector:
            def __init__(self):
                self.registered = []

            def register(self, connection, event):
                del event
                self.registered.append(connection)

            def unregister(self, connection):
                self.registered.remove(connection)

            def get_map(self):
                return {id(connection): connection for connection in self.registered}

            def select(self, timeout):
                del timeout
                reachable = next(connection for connection in self.registered if connection.reachable)
                return [(mock.Mock(fileobj=reachable), None)]

            def close(self):
                pass

        sockets = [FakeSocket(False), FakeSocket(True)]
        candidates = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.0.2.1", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443)),
        ]
        with mock.patch.object(install_qw.socket, "getaddrinfo", return_value=candidates):
            with mock.patch.object(install_qw.socket, "socket", side_effect=sockets):
                with mock.patch.object(install_qw.selectors, "DefaultSelector", FakeSelector):
                    connection = install_qw.create_resilient_connection(("example.invalid", 443), timeout=2)
        self.assertIs(sockets[1], connection)
        self.assertTrue(sockets[0].closed)
        self.assertFalse(sockets[1].closed)
        self.assertEqual(2, sockets[1].timeout)

    def test_resilient_connection_limits_dns_resolution_time(self):
        blocked = threading.Event()
        started = time.monotonic()
        with mock.patch.object(install_qw.socket, "getaddrinfo", side_effect=lambda *args: blocked.wait(1)):
            with self.assertRaisesRegex(TimeoutError, "resolver example.invalid"):
                install_qw.create_resilient_connection(("example.invalid", 443), timeout=0.01)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_public_catalog_falls_back_to_the_next_mirror(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.online_only = True
            catalog = {"format": 1, "project": "x86qw", "packages": []}
            payload = json.dumps(catalog).encode()
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(
                    installer,
                    "http_get",
                    side_effect=[install_qw.InstallerError("timeout"), payload],
                ) as get:
                    self.assertEqual(catalog, installer.public_catalog("remote"))
            self.assertEqual(2, get.call_count)
            self.assertEqual(
                install_qw.CATALOG_URLS[1], get.call_args_list[1].args[0],
            )

    def test_download_falls_back_to_the_next_catalog_mirror(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["macos"]
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            installer.prepare_cache()
            payload = b"verified archive"
            filename = installer.spec.stable_archive
            installer.selected_version = "3.6.9"
            installer.channel = "stable"
            installer.app_archive_name = filename
            installer.app_checksum_kind = "sha256"
            installer.app_expected_checksum = install_qw.hashlib.sha256(payload).hexdigest()
            installer.app_urls = (
                f"https://first.invalid/{filename}",
                f"https://second.invalid/{filename}",
            )
            installer.app_url = installer.app_urls[0]

            def fake_http_get(url, destination=None, headers=None):
                del headers
                if url == installer.app_urls[0]:
                    raise install_qw.InstallerError("first mirror unavailable")
                destination.write_bytes(payload)
                return b""

            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "http_get", side_effect=fake_http_get):
                    archive = installer.ensure_archive()
            self.assertEqual(payload, archive.read_bytes())
            self.assertEqual(installer.app_urls[1], installer.app_url)

    def test_client_artifact_is_loaded_from_the_versioned_distribution_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "quake-world"
            target.mkdir()
            cache = root / "cache/x86qw"
            cache.parent.mkdir()
            installer = install_qw.Installer(ROOT, target, cache)
            installer.spec = install_qw.PLATFORMS["macos"]
            installer.channel = "stable"
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            installer.prepare_cache()
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch("builtins.input", return_value="1"):
                    installer.choose_release()
                with mock.patch.object(installer, "http_get", side_effect=AssertionError("network used")):
                    artifact = installer.ensure_archive()
            source = ROOT / "dist" / installer.app_distribution_path
            self.assertEqual(source.read_bytes(), artifact.read_bytes())

    def test_nquake_confirmation_defaults_to_no_and_reprompts_invalid_answer(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            with mock.patch("builtins.input", return_value=""):
                self.assertFalse(installer.confirm_components())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                with mock.patch("builtins.input", side_effect=["talvez", "sim"]):
                    self.assertTrue(installer.confirm_components())
            self.assertIn("Resposta inválida. Digite s para sim ou n para não.", output.getvalue())

    def test_human_readable_sizes(self):
        self.assertEqual("0 B", install_qw.format_bytes(0))
        self.assertEqual("1.0 KiB", install_qw.format_bytes(1024))
        self.assertEqual("1.5 MiB", install_qw.format_bytes(1572864))

    def test_technical_details_are_opt_in(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            install_qw.console.detail("oculto")
            install_qw.console.configure(verbose=True, no_color=True)
            install_qw.console.detail("visível")
        self.assertNotIn("oculto", output.getvalue())
        self.assertIn("visível", output.getvalue())

    def test_cache_is_owned_and_cleanup_removes_only_it(self):
        self.assertEqual("x86qw", install_qw.CACHE_DIR_NAME)
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, cache = self.make_installer(Path(temporary))
            installer.prepare_cache()
            payload = cache / "bin/artifact.zip"
            payload.parent.mkdir()
            payload.write_bytes(b"artifact")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.cleanup_cache()
            self.assertFalse(cache.exists())
            self.assertTrue(cache.parent.exists())

    def test_unmarked_cache_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, cache = self.make_installer(Path(temporary))
            cache.mkdir()
            (cache / "foreign").write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(install_qw.InstallerError, "não pertencem ao instalador"):
                installer.prepare_cache()
            self.assertEqual("keep", (cache / "foreign").read_text(encoding="utf-8"))

    def test_purge_removes_the_entire_installation_and_owned_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, cache = self.make_installer(Path(temporary))
            (target / ".x86qw").mkdir()
            (target / "id1").mkdir()
            (target / "id1/pak0.pak").write_bytes(b"remove")
            (target / "qw").mkdir()
            (target / "qw/remove.txt").write_text("remove", encoding="utf-8")
            (target / "personal.txt").write_text("remove", encoding="utf-8")
            installer.prepare_cache()
            (cache / "payload").write_text("remove", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.purge()
            self.assertFalse(target.exists())
            self.assertFalse(cache.exists())

    def test_purge_keeps_operation_lock_until_final_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            (target / ".x86qw").mkdir()
            (target / "personal.txt").write_text("remove", encoding="utf-8")
            operation_lock = install_qw.session_control.InstallationLock.acquire(
                target, "uninstall", "maintenance",
            )
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    installer.purge(preserve_operation_lock=True)
                self.assertTrue(operation_lock.path.is_file())
                self.assertFalse((target / "personal.txt").exists())
            finally:
                operation_lock.release()
            install_qw.remove_empty_directories(target / ".x86qw")
            target.rmdir()
            self.assertFalse(target.exists())

    def test_regular_uninstall_removes_the_cli_and_preserves_id1(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            cli = target / ".x86qw/cli/x86qw.pyz"
            cli.parent.mkdir(parents=True)
            cli.write_text("# cli\n", encoding="utf-8")
            self.write_cli_receipt(target, "1.0.5")
            (target / "x86qw.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (target / "x86qw.cmd").write_bytes(
                (ROOT / "dist/installer/bin/x86qw.cmd").read_bytes()
            )
            (target / "id1").mkdir()
            (target / "id1/pak0.pak").write_bytes(b"preserve")
            with contextlib.redirect_stdout(io.StringIO()):
                installer.uninstall()
            self.assertFalse((target / "x86qw.sh").exists())
            if os.name == "nt":
                # manager.py cannot remove the active batch safely; x86qw.cmd
                # deletes itself after a successful uninstall.
                self.assertTrue((target / "x86qw.cmd").exists())
                self.assertIn(
                    'if "%X86QW_EXIT%"=="0" del "%~f0"',
                    (target / "x86qw.cmd").read_text(encoding="utf-8"),
                )
            else:
                self.assertFalse((target / "x86qw.cmd").exists())
            self.assertFalse((target / ".x86qw/cli").exists())
            self.assertEqual(b"preserve", (target / "id1/pak0.pak").read_bytes())

    def test_purge_without_an_installation_still_removes_owned_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, cache = self.make_installer(Path(temporary))
            target.rmdir()
            installer.prepare_cache()
            with contextlib.redirect_stdout(io.StringIO()):
                installer.purge()
            self.assertFalse(cache.exists())

    def test_purge_rejects_a_directory_without_x86qw_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            (target / "unrelated.txt").write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(install_qw.InstallerError, "sem identidade x86QW"):
                installer.purge()
            self.assertTrue((target / "unrelated.txt").is_file())

    def test_cleanup_removes_current_and_legacy_owned_native_caches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, _, _ = self.make_installer(root)
            current = root / "native/x86qw"
            legacy = root / "native/x86-qw"
            current.mkdir(parents=True)
            legacy.mkdir()
            (current / install_qw.CACHE_MARKER_NAME).write_text(
                install_qw.CACHE_MARKER_VALUE + "\n", encoding="utf-8",
            )
            legacy_name, legacy_marker, legacy_value = install_qw.LEGACY_CACHE
            self.assertEqual("x86-qw", legacy_name)
            (legacy / legacy_marker).write_text(legacy_value + "\n", encoding="utf-8")
            installer._cache_root = None
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "resolve_cache_root", return_value=current):
                    installer.cleanup_cache()
            self.assertFalse(current.exists())
            self.assertFalse(legacy.exists())

    def test_zip_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.zip"
            destination = root / "output"
            destination.mkdir()
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("../escape", b"bad")
            with self.assertRaisesRegex(install_qw.InstallerError, "unsafe archive path"):
                install_qw.safe_extract_zip(archive, destination)
            self.assertFalse((root / "escape").exists())

    def test_windows_drive_archive_path_is_rejected(self):
        with self.assertRaisesRegex(install_qw.InstallerError, "unsafe archive path"):
            install_qw.archive_relative_path("C:/escape")

    def test_portable_binary_formats(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            linux = Path(temporary) / "ezquake.AppImage"
            elf = bytearray(64)
            elf[:5] = b"\x7fELF\x02"
            struct.pack_into("<H", elf, 18, 62)
            linux.write_bytes(elf)
            if os.name != "nt":
                linux.chmod(0o755)
            self.assertEqual(64, len(installer.inspect_portable_binary(install_qw.PLATFORMS["linux"], linux)))

            windows = Path(temporary) / "ezquake.exe"
            pe = bytearray(512)
            pe[:2] = b"MZ"
            struct.pack_into("<I", pe, 0x3C, 0x80)
            pe[0x80:0x84] = b"PE\0\0"
            struct.pack_into("<H", pe, 0x84, 0x8664)
            struct.pack_into("<H", pe, 0x98, 0x20B)
            windows.write_bytes(pe)
            self.assertEqual(64, len(installer.inspect_portable_binary(install_qw.PLATFORMS["windows"], windows)))

    def test_recursive_delete_does_not_follow_symlinks(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            protected = outside / "keep"
            protected.write_text("keep", encoding="utf-8")
            owned = root / "owned"
            owned.mkdir()
            try:
                (owned / "link").symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation is not permitted")
            install_qw.remove_path(owned)
            self.assertEqual("keep", protected.read_text(encoding="utf-8"))

    @unittest.skipUnless(os.environ.get("X86_QW_NETWORK_TESTS") == "1", "network tests disabled")
    def test_latest_official_artifacts_for_every_platform_and_channel(self):
        for platform_name in install_qw.PLATFORMS:
            for channel in ("stable", "nightly"):
                with self.subTest(platform=platform_name, channel=channel):
                    with tempfile.TemporaryDirectory() as temporary:
                        installer, target, _ = self.make_installer(Path(temporary))
                        installer.spec = install_qw.PLATFORMS[platform_name]
                        installer.channel = channel
                        installer.stage = target / ".integration-stage"
                        installer.stage.mkdir()
                        with contextlib.redirect_stdout(io.StringIO()):
                            with mock.patch("builtins.input", return_value="1"):
                                installer.choose_release()
                            installer.prepare_cache()
                            archive = installer.ensure_archive()
                            installer.prepare_runtime(archive)
                            receipt = installer.stage / "receipt"
                            installer.write_ezquake_receipt(receipt)
                        self.assertTrue(receipt.is_file())


if __name__ == "__main__":
    unittest.main()
