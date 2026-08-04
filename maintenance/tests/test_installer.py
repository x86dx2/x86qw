import contextlib
import importlib.util
import io
import json
import os
import plistlib
import shlex
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

from maintenance.tools import downloader as bounded_downloader
from maintenance.tools.build_installer_bundle import public_bootstrap_assignments, zipapp_bytes
from x86qw_runtime.io import atomic as atomic_io
from x86qw_runtime import state as runtime_state
from x86qw_runtime import receipts as runtime_receipts


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

    @contextlib.contextmanager
    def component_catalog_unavailable(self):
        with mock.patch.object(
            install_qw, "load_component_catalog",
            side_effect=AssertionError("component catalog was loaded"),
        ), mock.patch.object(
            install_qw, "load_runtime_catalog",
            side_effect=AssertionError("runtime component catalog was loaded"),
        ), mock.patch.object(
            install_qw, "read_zipapp_json",
            side_effect=AssertionError("zipapp component catalog was loaded"),
        ):
            yield

    def test_hub_does_not_load_the_component_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.component_catalog_unavailable():
                installer, _, _ = self.make_installer(Path(temporary))
                installer.remote.get = lambda *args, **kwargs: json.dumps([{
                    "address": "127.0.0.1:28501",
                    "players": [],
                }]).encode("utf-8")
                installer.reject_target_symlinks()
                with contextlib.redirect_stdout(io.StringIO()):
                    servers = installer.hub_servers()

            self.assertEqual("127.0.0.1:28501", servers[0]["address"])

    def test_cleanup_does_not_load_the_component_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.component_catalog_unavailable():
                installer, target, _ = self.make_installer(Path(temporary))
                state = target / install_qw.INSTALL_STATE
                state.parent.mkdir(parents=True)
                state.write_text(json.dumps({
                    "format": 2,
                    "project": "x86qw",
                    "profile": "none",
                    "requested_components": [],
                    "recorded_components": [],
                    "known_components": [],
                    "capabilities": [],
                    "component_fingerprint": (
                        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
                    ),
                }), encoding="utf-8")
                runtime_cache = target / "ezquake/temp/download.tmp"
                runtime_cache.parent.mkdir(parents=True)
                runtime_cache.write_bytes(b"cache")

                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(
                        (1, 0),
                        installer.cleanup_data(downloads=False, personal_data=False),
                    )

            self.assertFalse(runtime_cache.exists())

    def test_component_access_still_requires_a_valid_component_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.component_catalog_unavailable():
                installer, _, _ = self.make_installer(Path(temporary))
                with mock.patch.object(
                    install_qw, "load_component_catalog",
                    side_effect=install_qw.InstallerError("invalid component catalog"),
                ), self.assertRaisesRegex(
                    install_qw.InstallerError, "invalid component catalog",
                ):
                    installer.choose_components()

    def test_legacy_client_receipt_is_not_component_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["linux"]
            canonical, _, _ = self.write_ezquake_fixture(
                installer, target, spec, "stable",
            )
            legacy = target / spec.legacy_receipt("stable")
            canonical.replace(legacy)

            self.assertEqual(
                ("client:linux:stable",),
                installer.managed_installation_identity(),
            )

    def stage_backed_component_install(self, installer, target, installed, stages):
        marker = target / "qw/component-transaction.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("old\n", encoding="utf-8")

        def install(selected):
            stage = installer.stage
            self.assertIsNotNone(stage)
            assert stage is not None
            stages.append(stage)
            backup = stage / "component-transaction.backup"

            def apply():
                backup.write_bytes(marker.read_bytes())
                marker.write_text("new\n", encoding="utf-8")
                installed.extend(selected)
                return backup, tuple(selected)

            def rollback(token):
                saved, added = token
                marker.write_bytes(saved.read_bytes())
                for identifier in added:
                    installed.remove(identifier)

            plan = install_qw.MutationPlan(
                identifier="test:component-stage-lifetime",
                summary="exercise component stage lifetime",
                steps=(install_qw.MutationStep(
                    key="payload",
                    description="publish test payload",
                    observe=marker.read_bytes,
                    apply=apply,
                    rollback=rollback,
                ),),
            )
            return (install_qw.execute_mutation(install_qw.prepare_mutation(plan)),)

        return marker, install

    def state_failure_after_optional_commit(self, installer, committed, state_stages):
        write_state = installer.write_install_state

        def fail(*args, **kwargs):
            stage = installer.stage
            self.assertIsNotNone(stage, "component stage was cleaned before state commit")
            assert stage is not None
            self.assertTrue(stage.is_dir())
            state_stages.append(stage)
            if committed:
                write_state(*args, **kwargs)
            raise install_qw.PersistenceError(
                "injected state durability failure", committed=committed,
            )

        return fail

    def test_component_state_transaction_preserves_stage_on_incomplete_rollback(self):
        """Recovery backups must survive when an inverse cannot complete."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            stage = installer._create_stage(".rollback-recovery.")
            recovery = stage / "payload.backup"
            recovery.write_bytes(b"recover me\n")

            with mock.patch.object(
                installer,
                "rollback_component_transactions",
                side_effect=install_qw.InstallerError("injected rollback failure"),
            ), mock.patch.object(
                installer, "cleanup_stage", wraps=installer.cleanup_stage,
            ) as cleanup:
                with self.assertRaisesRegex(
                    install_qw.InstallerError, "injected rollback failure",
                ):
                    with installer.component_state_transaction():
                        raise install_qw.InstallerError("injected operation failure")

            cleanup.assert_not_called()
            self.assertEqual(stage, installer.stage)
            self.assertEqual(b"recover me\n", recovery.read_bytes())

            installer.cleanup_stage()
            installer.stage = None

    def test_direct_maintenance_builders_find_the_runtime_boundary(self):
        scripts = (
            "build_component_packages.py",
            "build_core_package.py",
            "build_installer_bundle.py",
        )
        with tempfile.TemporaryDirectory() as temporary:
            for name in scripts:
                with self.subTest(script=name):
                    completed = subprocess.run(
                        [sys.executable, str(ROOT / "maintenance/tools" / name), "--help"],
                        cwd=temporary,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    self.assertEqual(0, completed.returncode, completed.stderr)

    def test_future_bootstrap_registration_updates_version_hash_and_size_together(self):
        shell, powershell = public_bootstrap_assignments({
            "version": "0.7.2",
            "sha256": "a" * 64,
            "size": 234567,
        })
        self.assertEqual({
            "INSTALLER_VERSION": "0.7.2",
            "INSTALLER_SHA256": "a" * 64,
            "INSTALLER_SIZE": "234567",
        }, shell)
        self.assertEqual({
            "$InstallerVersion": "0.7.2",
            "$InstallerSha256": "a" * 64,
            "$InstallerSize": "234567",
        }, powershell)

    @unittest.skipUnless(os.name == "posix", "bootstrap público Unix requer ambiente POSIX")
    def test_public_unix_bootstrap_never_executes_a_partial_response(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sentinel = root / "partial-executed"
            curl = root / "curl"
            curl.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' 'printf partial >\"$X86QW_TEST_SENTINEL\"'\n"
                "exit 63\n",
                encoding="utf-8",
            )
            curl.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = os.pathsep.join((str(root), "/usr/bin", "/bin"))
            environment["TMPDIR"] = str(root)
            environment["X86QW_TEST_SENTINEL"] = str(sentinel)
            result = subprocess.run(
                shlex.split(install_qw.PUBLIC_UNIX_BOOTSTRAP_COMMAND),
                capture_output=True,
                check=False,
                env=environment,
                text=True,
                timeout=10,
            )
            leftovers = list(root.glob("x86qw-bootstrap.*"))
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(sentinel.exists())
        self.assertEqual([], leftovers)

    @unittest.skipUnless(os.name == "posix", "bootstrap público Unix requer ambiente POSIX")
    def test_public_unix_bootstrap_bounds_unknown_length_before_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sentinel = root / "oversized-executed"
            curl = root / "curl"
            curl.write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                "prefix = 'printf oversized >\\\"$X86QW_TEST_SENTINEL\\\"\\n'\n"
                "try:\n"
                "    sys.stdout.write(prefix + ('#' * 300000))\n"
                "    sys.stdout.flush()\n"
                "except BrokenPipeError:\n"
                "    os._exit(23)\n",
                encoding="utf-8",
            )
            curl.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = os.pathsep.join((str(root), "/usr/bin", "/bin"))
            environment["TMPDIR"] = str(root)
            environment["X86QW_TEST_SENTINEL"] = str(sentinel)
            result = subprocess.run(
                shlex.split(install_qw.PUBLIC_UNIX_BOOTSTRAP_COMMAND),
                capture_output=True,
                check=False,
                env=environment,
                text=True,
                timeout=10,
            )
            leftovers = list(root.glob("x86qw-bootstrap.*"))
        self.assertNotEqual(0, result.returncode)
        self.assertFalse(sentinel.exists())
        self.assertIn("bootstrap excedeu 262144 bytes", result.stderr)
        self.assertEqual([], leftovers)

    @unittest.skipUnless(os.name == "posix", "bootstrap público Unix requer ambiente POSIX")
    def test_public_unix_bootstrap_forwards_arguments_and_exit_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            received = root / "received-arguments"
            curl = root / "curl"
            curl.write_text(
                "#!/bin/sh\n"
                "cat <<'BOOTSTRAP'\n"
                "printf '%s\\n' \"$@\" >\"$X86QW_TEST_SENTINEL\"\n"
                "exit 7\n"
                "BOOTSTRAP\n",
                encoding="utf-8",
            )
            curl.chmod(0o755)
            environment = os.environ.copy()
            environment["PATH"] = os.pathsep.join((str(root), "/usr/bin", "/bin"))
            environment["TMPDIR"] = str(root)
            environment["X86QW_TEST_SENTINEL"] = str(received)
            result = subprocess.run(
                [
                    *shlex.split(install_qw.PUBLIC_UNIX_BOOTSTRAP_COMMAND),
                    "--platform", "windows", "caminho com espaço",
                ],
                capture_output=True,
                check=False,
                env=environment,
                text=True,
                timeout=10,
            )
            arguments = received.read_text(encoding="utf-8").splitlines()
            leftovers = list(root.glob("x86qw-bootstrap.*"))
        self.assertEqual(7, result.returncode)
        self.assertEqual(
            ["--platform", "windows", "caminho com espaço"], arguments,
        )
        self.assertEqual([], leftovers)

    def test_public_zipapp_embeds_the_declarative_ktx_mode_catalog(self):
        with zipfile.ZipFile(io.BytesIO(zipapp_bytes("9.9.9"))) as application:
            catalog = json.loads(application.read("_x86qw/ktx-modes.json"))
            bot_names = json.loads(application.read("_x86qw/ktx-frogbot-names.json"))
            self.assertNotIn("session_control.py", application.namelist())
            self.assertIn("python_runtime.py", application.namelist())
            self.assertIn("x86qw_runtime/io/downloader.py", application.namelist())
            self.assertNotIn("maintenance/tools/downloader.py", application.namelist())
            self.assertIn("x86qw_runtime/io/private_fs.py", application.namelist())
            self.assertIn("x86qw_runtime/platform/__init__.py", application.namelist())
            self.assertIn("x86qw_runtime/platform/windows_acl.py", application.namelist())
            self.assertIn(
                b"require_supported_runtime()",
                application.read("__main__.py"),
            )
        self.assertEqual(1, catalog["format"])
        self.assertEqual("ktx", catalog["game"])
        self.assertEqual("duel", catalog["modes"][0]["id"])
        self.assertIn("race", [mode["id"] for mode in catalog["modes"]])
        self.assertEqual("one-piece", bot_names["theme"])
        luffy = bot_names["groups"][0]["characters"][0]
        self.assertEqual("Luffy", luffy["name"])
        self.assertEqual({"name"}, set(luffy))

    def test_fresh_zipapp_runs_version_and_help_without_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            application = root / "x86qw.pyz"
            application.write_bytes(zipapp_bytes("9.9.9"))
            (root / "sitecustomize.py").write_text(
                "import sys\n"
                "def reject_network(event, args):\n"
                "    if event in {'socket.connect', 'socket.getaddrinfo'}:\n"
                "        raise RuntimeError('network access is forbidden in zipapp smoke')\n"
                "sys.addaudithook(reject_network)\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(root)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"

            version = subprocess.run(
                [sys.executable, str(application), "--version"],
                capture_output=True,
                check=False,
                env=environment,
                text=True,
                timeout=30,
            )
            help_result = subprocess.run(
                [sys.executable, str(application), "--help"],
                capture_output=True,
                check=False,
                env=environment,
                text=True,
                timeout=30,
            )

        self.assertEqual(0, version.returncode, version.stderr)
        self.assertEqual("x86QW 9.9.9\n", version.stdout)
        self.assertEqual("", version.stderr)
        self.assertEqual(0, help_result.returncode, help_result.stderr)
        self.assertIn("usage: x86qw", help_result.stdout)
        self.assertIn("x86QW 9.9.9", help_result.stdout)
        self.assertEqual("", help_result.stderr)

    @staticmethod
    def write_installer_bundle(
        path: Path,
        version: str,
        *,
        omit: str | None = None,
        extra_member: str | None = None,
    ) -> None:
        identity = json.dumps({"format": 1, "project": "x86qw", "version": version})
        members = {
            "x86qw.pyz": zipapp_bytes(version),
            "VERSION": f"{version}\n",
            "x86qw.sh": "#!/bin/sh\n",
            "x86qw.cmd": "@echo off\r\n",
            "installer.json": identity,
            "dist/installer/bin/manager.py": "#!/usr/bin/env python3\n",
            "_x86qw/installer.json": identity,
        }
        with zipfile.ZipFile(path, "w") as package:
            prefix = f"x86qw-installer-{version}"
            for name, payload in members.items():
                if name != omit:
                    package.writestr(f"{prefix}/{name}", payload)
            if extra_member is not None:
                package.writestr(f"{prefix}/{extra_member}", b"unexpected")

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
        artifact_sha256: str = "a" * 64,
        binary_sha256: str | None = None,
    ):
        selection = "3.6.9" if channel == "stable" else "20260616-101233_a86996a"
        runtime = target / spec.runtime(channel)
        if payload is not None:
            runtime.parent.mkdir(parents=True, exist_ok=True)
            runtime.write_bytes(payload)
        binary_hash = (
            binary_sha256
            or (install_qw.file_hash(runtime) if runtime.is_file() else "b" * 64)
        )
        receipt_path = target / spec.receipt(channel)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        bundle_version = selection
        if spec.key == "macos" and channel == "nightly":
            bundle_version = f"3.6.9-g{selection.rsplit('_', 1)[-1]}"
        receipt = {
            "format": "1", "platform": spec.key, "architecture": spec.architecture,
            "channel": channel, "selection": selection,
            "install_name": spec.runtime(channel), "bundle_version": bundle_version,
            "artifact_name": spec.stable_archive if channel == "stable" else selection + spec.nightly_suffix,
            "artifact_url": "https://example.invalid/" + (
                spec.stable_archive if channel == "stable" else selection + spec.nightly_suffix
            ),
            "artifact_sha256": artifact_sha256, "binary_sha256": binary_hash,
        }
        installer.write_ezquake_receipt_record(receipt_path, receipt)
        return receipt_path, receipt, runtime

    def prepare_runtime_commit_fixture(self, root: Path):
        installer, target, _ = self.make_installer(root)
        spec = install_qw.PLATFORMS["linux"]
        receipt_path, receipt, runtime = self.write_ezquake_fixture(
            installer, target, spec, "stable", payload=b"old-runtime\n",
        )
        original_receipt = receipt_path.read_bytes()
        installer.spec = spec
        installer.channel = "stable"
        installer.stage = target / ".stage"
        installer.stage.mkdir()
        prepared = installer.stage / "prepared-runtime"
        prepared.write_bytes(b"new-runtime\n")
        staged_receipt = installer.stage / "staged-receipt"
        updated = dict(receipt)
        updated.update({
            "selection": "3.6.10",
            "bundle_version": "3.6.10",
            "binary_sha256": install_qw.file_hash(prepared),
        })
        installer.write_ezquake_receipt_record(staged_receipt, updated)
        return (
            installer, target, spec, runtime, receipt_path, original_receipt,
            prepared, staged_receipt, updated,
        )

    @staticmethod
    def macos_bundle_members(version: str = "3.6.9") -> dict[str, bytes]:
        binary = b"".join((
            struct.pack(">II", 0xCAFEBABE, 2),
            struct.pack(">IIIII", 0x01000007, 0, 0, 0, 0),
            struct.pack(">IIIII", 0x0100000C, 0, 0, 0, 0),
        ))
        return {
            "Contents/Info.plist": plistlib.dumps({
                "CFBundleIdentifier": "com.ezquake.ezQuake",
                "CFBundleShortVersionString": version,
                "CFBundleVersion": version,
            }),
            "Contents/MacOS/ezQuake": binary,
            "Contents/_CodeSignature/CodeResources": b"upstream-code-resources",
        }

    @classmethod
    def write_macos_bundle(cls, app: Path, version: str = "3.6.9") -> dict[str, bytes]:
        members = cls.macos_bundle_members(version)
        for relative, contents in members.items():
            destination = app / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(contents)
        return members

    @classmethod
    def write_macos_archive(cls, archive: Path, version: str = "3.6.9") -> dict[str, bytes]:
        members = cls.macos_bundle_members(version)
        with zipfile.ZipFile(archive, "w") as package:
            for relative, contents in members.items():
                package.writestr(f"ezQuake.app/{relative}", contents)
        return members

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

    def test_core_paks_roll_back_as_one_unit_when_second_promotion_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            bundled = installer.project_root / "dist/game-data/id1"
            bundled.mkdir(parents=True)
            pak0 = b"PACK" + b"pak0"
            pak1 = b"PACK" + b"pak1"
            (bundled / "pak0.pak").write_bytes(pak0)
            (bundled / "pak1.pak").write_bytes(pak1)
            replace = os.replace

            def fail_second_pak(source, destination):
                if Path(destination) == target / "id1/pak1.pak":
                    raise OSError("simulated second PAK promotion failure")
                return replace(source, destination)

            with mock.patch.object(
                install_qw, "ID1_PAK0_SHA256",
                install_qw.hashlib.sha256(pak0).hexdigest(),
            ), mock.patch.object(
                install_qw, "ID1_PAK1_SHA256",
                install_qw.hashlib.sha256(pak1).hexdigest(),
            ), mock.patch.object(
                install_qw.os, "replace", side_effect=fail_second_pak,
            ):
                with self.assertRaises(install_qw.InstallerError):
                    installer.provision_install_target()

            self.assertFalse((target / "id1/pak0.pak").exists())
            self.assertFalse((target / "id1/pak1.pak").exists())
            self.assertFalse(any((target / "id1").glob(".*.x86qw-part")))

    def test_core_paks_keep_an_inverse_for_the_parent_install_transaction(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            bundled = installer.project_root / "dist/game-data/id1"
            bundled.mkdir(parents=True)
            pak0 = b"PACK" + b"pak0"
            pak1 = b"PACK" + b"pak1"
            (bundled / "pak0.pak").write_bytes(pak0)
            (bundled / "pak1.pak").write_bytes(pak1)
            with mock.patch.object(
                install_qw, "ID1_PAK0_SHA256",
                install_qw.hashlib.sha256(pak0).hexdigest(),
            ), mock.patch.object(
                install_qw, "ID1_PAK1_SHA256",
                install_qw.hashlib.sha256(pak1).hexdigest(),
            ):
                result = installer.provision_install_target()
            self.assertIsInstance(result, install_qw.MutationResult)
            assert result is not None
            install_qw.rollback_mutation(result)
            self.assertFalse((target / "id1/pak0.pak").exists())
            self.assertFalse((target / "id1/pak1.pak").exists())

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
                        if system != "windows" and os.name != "nt":
                            self.assertEqual(0o755, destination.stat().st_mode & 0o777)
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
            state = {
                "basedir": "/Games/old",
                "version": 7,
                "NSOSPLastRootDirectory": b"bookmark",
                "volume": 0.5,
            }

            def export(_domain):
                return dict(state)

            def publish(_domain, values):
                state.clear()
                state.update(values)

            output = io.StringIO()
            with mock.patch.object(
                install_qw.host_platform, "system", return_value="Darwin",
            ), mock.patch.object(
                installer, "ensure_macos_ezquake_closed",
            ), mock.patch.object(
                install_qw.macos, "_export_preference_domain", side_effect=export,
            ), mock.patch.object(
                install_qw.macos, "_publish_preference_domain", side_effect=publish,
            ), contextlib.redirect_stdout(output):
                result = installer.reset_macos_game_directory()

            self.assertIsInstance(result, install_qw.MutationResult)
            self.assertEqual({"volume": 0.5}, state)
            self.assertIn("Seleção antiga", output.getvalue())

    def test_macos_preferences_are_untouched_for_cross_platform_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["windows"]
            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(install_qw.subprocess, "run") as run:
                    installer.reset_macos_game_directory()
            run.assert_not_called()

    def test_existing_macos_channel_preserves_shared_bookmark_during_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            installer.spec = spec
            self.write_ezquake_fixture(installer, target, spec, "stable")

            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                self.assertFalse(installer.macos_game_directory_reset_required())

    def test_first_native_macos_install_resets_stale_bookmark(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["macos"]

            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                self.assertTrue(installer.macos_game_directory_reset_required())

    def test_macos_preference_reset_rolls_back_when_install_verification_fails(self):
        """A failed installation must restore the user's previous bookmark."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            (target / "id1").mkdir()
            (target / "id1/pak0.pak").write_bytes(b"pak0")
            (target / "id1/pak1.pak").write_bytes(b"pak1")
            preference = {"basedir": "/Games/previous"}

            runtime_result = install_qw.execute_mutation(install_qw.prepare_mutation(
                install_qw.MutationPlan(
                    identifier="test:runtime-before-preferences",
                    summary="publish the runtime",
                    steps=(install_qw.MutationStep(
                        key="runtime",
                        description="publish a runtime fixture",
                        observe=lambda: None,
                        apply=lambda: None,
                        rollback=lambda _token: None,
                    ),),
                )
            ))

            def reset_preferences():
                old_value = preference["basedir"]
                return install_qw.execute_mutation(install_qw.prepare_mutation(
                    install_qw.MutationPlan(
                        identifier="test:macos-preferences",
                        summary="clear the previous bookmark",
                        steps=(install_qw.MutationStep(
                            key="bookmark",
                            description="clear the previous bookmark",
                            observe=lambda: preference.get("basedir"),
                            apply=lambda: preference.pop("basedir", None),
                            rollback=lambda _token: preference.update(basedir=old_value),
                        ),),
                    )
                ))

            def select_platform(_platform=None):
                installer.spec = install_qw.PLATFORMS["macos"]

            def choose_channel():
                installer.channel = "stable"

            def choose_release():
                installer.selected_version = "3.6.9"

            def create_stage(_prefix):
                installer.stage = target / ".stage"
                installer.stage.mkdir()

            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(
                    installer, "select_platform", side_effect=select_platform,
                ))
                stack.enter_context(mock.patch.object(
                    installer, "choose_channel", side_effect=choose_channel,
                ))
                stack.enter_context(mock.patch.object(
                    installer, "choose_release", side_effect=choose_release,
                ))
                stack.enter_context(mock.patch.object(
                    installer, "macos_game_directory_reset_required", return_value=True,
                ))
                stack.enter_context(mock.patch.object(installer, "ensure_macos_ezquake_closed"))
                stack.enter_context(mock.patch.object(installer, "check_runtime_destination_ownership"))
                stack.enter_context(mock.patch.object(installer, "prepare_install_target"))
                stack.enter_context(mock.patch.object(installer, "reject_target_symlinks"))
                stack.enter_context(mock.patch.object(
                    installer, "_create_stage", side_effect=create_stage,
                ))
                stack.enter_context(mock.patch.object(
                    installer, "provision_install_target", return_value=None,
                ))
                stack.enter_context(mock.patch.object(installer, "check_paks"))
                stack.enter_context(mock.patch.object(installer, "prepare_cache"))
                stack.enter_context(mock.patch.object(
                    installer, "ensure_archive", return_value=target / "archive",
                ))
                stack.enter_context(mock.patch.object(
                    installer, "prepare_runtime", return_value=target / "prepared",
                ))
                stack.enter_context(mock.patch.object(installer, "write_ezquake_receipt"))
                stack.enter_context(mock.patch.object(installer, "ensure_metadata_directory"))
                stack.enter_context(mock.patch.object(
                    installer, "commit_runtime", return_value=runtime_result,
                ))
                stack.enter_context(mock.patch.object(
                    installer, "reset_macos_game_directory", side_effect=reset_preferences,
                ))
                stack.enter_context(mock.patch.object(
                    installer, "confirm_components", return_value=False,
                ))
                stack.enter_context(mock.patch.object(installer, "write_install_state"))
                stack.enter_context(mock.patch.object(
                    installer, "verify_installation",
                    side_effect=install_qw.InstallerError("final verification failed"),
                ))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                with self.assertRaisesRegex(
                    install_qw.InstallerError, "final verification failed",
                ):
                    installer.install()

            self.assertEqual({"basedir": "/Games/previous"}, preference)

    def test_install_decides_bookmark_reset_only_after_acquiring_the_operation_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            (target / "id1").mkdir()
            (target / "id1/pak0.pak").write_bytes(b"pak0")
            (target / "id1/pak1.pak").write_bytes(b"pak1")
            lock_acquired = False

            def acquire_lock():
                nonlocal lock_acquired
                lock_acquired = True

            def reset_required():
                self.assertTrue(lock_acquired)
                return False

            def select_platform(_platform=None):
                installer.spec = install_qw.PLATFORMS["macos"]

            def choose_channel():
                installer.channel = "stable"

            def choose_release():
                installer.selected_version = "3.6.9"

            def create_stage(_prefix):
                installer.stage = target / ".stage"
                installer.stage.mkdir()

            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(
                    installer, "select_platform", side_effect=select_platform,
                ))
                stack.enter_context(mock.patch.object(
                    installer, "choose_channel", side_effect=choose_channel,
                ))
                stack.enter_context(mock.patch.object(
                    installer, "choose_release", side_effect=choose_release,
                ))
                stack.enter_context(mock.patch.object(
                    installer, "macos_game_directory_reset_required",
                    side_effect=reset_required,
                ))
                stack.enter_context(mock.patch.object(installer, "ensure_macos_ezquake_closed"))
                stack.enter_context(mock.patch.object(installer, "check_runtime_destination_ownership"))
                stack.enter_context(mock.patch.object(installer, "prepare_install_target"))
                stack.enter_context(mock.patch.object(installer, "reject_target_symlinks"))
                stack.enter_context(mock.patch.object(
                    installer, "_create_stage", side_effect=create_stage,
                ))
                stack.enter_context(mock.patch.object(
                    installer, "provision_install_target", return_value=None,
                ))
                stack.enter_context(mock.patch.object(installer, "check_paks"))
                stack.enter_context(mock.patch.object(installer, "prepare_cache"))
                stack.enter_context(mock.patch.object(
                    installer, "ensure_archive", return_value=target / "archive",
                ))
                stack.enter_context(mock.patch.object(
                    installer, "prepare_runtime", return_value=target / "prepared",
                ))
                stack.enter_context(mock.patch.object(installer, "write_ezquake_receipt"))
                stack.enter_context(mock.patch.object(installer, "ensure_metadata_directory"))
                stack.enter_context(mock.patch.object(installer, "commit_runtime"))
                stack.enter_context(mock.patch.object(
                    installer, "confirm_components", return_value=False,
                ))
                stack.enter_context(mock.patch.object(installer, "write_install_state"))
                stack.enter_context(mock.patch.object(installer, "verify_installation"))
                stack.enter_context(mock.patch.object(
                    installer, "installed_components", return_value=[],
                ))
                reset = stack.enter_context(mock.patch.object(
                    installer, "reset_macos_game_directory",
                ))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                installer.install(before_mutation=acquire_lock)

            reset.assert_not_called()

    def test_macos_nightly_bundle_removes_sandbox_and_uses_the_full_display(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.channel = "nightly"
            app = target / "ezQuake.app"
            plist = app / "Contents/Info.plist"
            plist.parent.mkdir(parents=True)
            with plist.open("wb") as destination:
                plistlib.dump({"CFBundleName": "ezQuake"}, destination)
            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(installer, "macos_app_is_sandboxed", side_effect=[True, False]):
                    with mock.patch.object(installer, "run_command") as command:
                        self.assertTrue(installer.prepare_macos_nightly_app(app))
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

    def test_macos_local_preparation_refuses_the_stable_channel(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.channel = "stable"
            app = target / "ezQuake.app"
            plist = app / "Contents/Info.plist"
            plist.parent.mkdir(parents=True)
            original = plistlib.dumps({"CFBundleName": "ezQuake"})
            plist.write_bytes(original)

            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(installer, "run_command") as command:
                    with self.assertRaisesRegex(
                        install_qw.InstallerError, "somente.*nightly",
                    ):
                        installer.prepare_macos_nightly_app(app)

            command.assert_not_called()
            self.assertEqual(original, plist.read_bytes())

    def test_stable_macos_runtime_preserves_upstream_signed_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["macos"]
            installer.channel = "stable"
            installer.selected_version = "3.6.9"
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            archive = installer.stage / installer.spec.stable_archive
            expected = self.write_macos_archive(archive)

            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(
                    installer, "macos_app_is_sandboxed", side_effect=[True, False],
                ):
                    with mock.patch.object(installer, "run_command") as command:
                        prepared = installer.prepare_runtime(archive)

            actual = {
                path.relative_to(prepared).as_posix(): path.read_bytes()
                for path in prepared.rglob("*") if path.is_file()
            }
            self.assertEqual(expected, actual)
            self.assertFalse(any(
                "--sign" in call.args[0] for call in command.call_args_list
            ))

    def test_stable_macos_identity_table_matches_the_registered_upstream_archive(self):
        archive = (
            ROOT
            / "dist/clients/ezquake/stable/3.6.9/macos-universal"
            / "ezQuake-macOS-universal.zip"
        )
        artifact_sha256 = install_qw.file_hash(archive)
        identities = install_qw.MACOS_STABLE_BINARY_IDENTITIES[artifact_sha256]
        with zipfile.ZipFile(archive) as package:
            binary = package.read("ezQuake.app/Contents/MacOS/ezQuake")

        self.assertEqual(
            identities["upstream"], install_qw.hashlib.sha256(binary).hexdigest(),
        )

    def test_stable_macos_runtime_rejects_invalid_upstream_signature_before_promotion(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["macos"]
            installer.channel = "stable"
            installer.selected_version = "3.6.9"
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            archive = installer.stage / installer.spec.stable_archive
            self.write_macos_archive(archive)

            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(
                    installer, "run_command",
                    side_effect=install_qw.InstallerError("assinatura upstream inválida"),
                ):
                    with self.assertRaisesRegex(
                        install_qw.InstallerError, "assinatura upstream inválida",
                    ):
                        installer.prepare_runtime(archive)

            self.assertTrue(archive.is_file())
            self.assertFalse((installer.stage / "prepared-runtime").exists())

    def test_upstream_stable_macos_runtime_is_accepted_without_local_preparation(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            _, _, runtime = self.write_ezquake_fixture(installer, target, spec, "stable")
            self.write_macos_bundle(runtime)

            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(installer, "check_runtime"):
                    with mock.patch.object(installer, "macos_app_is_sandboxed", return_value=True):
                        try:
                            choices = installer.host_runtimes()
                        except install_qw.InstallerError as error:
                            self.fail(str(error))

            self.assertEqual([("ezQuake stable 3.6.9", runtime)], choices)

    def test_legacy_resigned_stable_macos_runtime_requires_upstream_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            receipt_path, receipt, runtime = self.write_ezquake_fixture(
                installer, target, spec, "stable",
                artifact_sha256="2ccea8f214c91e5fc92b5cb195c81ac584f75a551d11a58de56e5e7951eec7ed",
                binary_sha256="e24524761d8ff10c57a8ecbb2fdc7ce29d1bd78641cfaecf49644d8881e2422a",
            )
            self.write_macos_bundle(runtime)
            plist = runtime / "Contents/Info.plist"
            metadata = plistlib.loads(plist.read_bytes())
            metadata[install_qw.MACOS_SAFE_AREA_KEY] = False
            plist.write_bytes(plistlib.dumps(metadata))
            release = (
                receipt["selection"],
                (f"https://example.invalid/{receipt['artifact_name']}",),
                "a" * 64,
            )

            with mock.patch.object(install_qw.host_platform, "system", return_value="Linux"):
                with mock.patch.object(installer, "check_runtime"):
                    with mock.patch.object(
                        installer, "client_catalog_release", return_value=release,
                    ):
                        issues, diagnostics = installer.client_repair_assessment()

            self.assertEqual([], diagnostics)
            self.assertEqual(1, len(issues))
            self.assertEqual(receipt_path, issues[0].receipt_path)
            self.assertEqual("payload", issues[0].mode)
            self.assertEqual("payload-required", issues[0].category)
            self.assertIn("bundle upstream integral", issues[0].reason)

    def test_verify_explains_how_to_restore_a_legacy_resigned_stable_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            self.write_ezquake_fixture(
                installer, target, spec, "stable",
                artifact_sha256="2ccea8f214c91e5fc92b5cb195c81ac584f75a551d11a58de56e5e7951eec7ed",
                binary_sha256="e24524761d8ff10c57a8ecbb2fdc7ce29d1bd78641cfaecf49644d8881e2422a",
            )
            runtime = target / spec.stable_runtime
            self.write_macos_bundle(runtime)
            plist = runtime / "Contents/Info.plist"
            metadata = plistlib.loads(plist.read_bytes())
            metadata[install_qw.MACOS_SAFE_AREA_KEY] = False
            plist.write_bytes(plistlib.dumps(metadata))

            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(installer, "check_runtime"):
                    with mock.patch.object(installer, "macos_app_is_sandboxed", return_value=False):
                        with self.assertRaisesRegex(
                            install_qw.InstallerError,
                            "bundle upstream integral.*bootstrap",
                        ):
                            installer.verify_ezquake_variants()

    def test_stable_bundle_from_another_artifact_is_not_rewritten_by_inference(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            self.write_ezquake_fixture(installer, target, spec, "stable")
            runtime = target / spec.stable_runtime
            self.write_macos_bundle(runtime)
            plist = runtime / "Contents/Info.plist"
            metadata = plistlib.loads(plist.read_bytes())
            metadata[install_qw.MACOS_SAFE_AREA_KEY] = False
            plist.write_bytes(plistlib.dumps(metadata))

            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(installer, "check_runtime"):
                    with mock.patch.object(installer, "macos_app_is_sandboxed", return_value=False):
                        try:
                            choices = installer.host_runtimes()
                        except install_qw.InstallerError as error:
                            self.fail(str(error))

            self.assertEqual([("ezQuake stable 3.6.9", runtime)], choices)

    def test_upstream_stable_binary_is_not_mistaken_for_a_legacy_local_rewrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            artifact_sha256 = (
                "2ccea8f214c91e5fc92b5cb195c81ac584f75a551d11a58de56e5e7951eec7ed"
            )
            upstream_binary_sha256 = (
                "14633b5d4201e9460250ad236fde2e4ad579a6ddbaf81301830099d8cf004f33"
            )
            self.write_ezquake_fixture(
                installer, target, spec, "stable",
                artifact_sha256=artifact_sha256,
                binary_sha256=upstream_binary_sha256,
            )
            runtime = target / spec.stable_runtime
            self.write_macos_bundle(runtime)
            plist = runtime / "Contents/Info.plist"
            metadata = plistlib.loads(plist.read_bytes())
            metadata[install_qw.MACOS_SAFE_AREA_KEY] = False
            plist.write_bytes(plistlib.dumps(metadata))

            with mock.patch.object(install_qw.host_platform, "system", return_value="Linux"):
                with mock.patch.object(installer, "check_runtime"):
                    with mock.patch.object(
                        installer, "client_catalog_release",
                        side_effect=AssertionError("payload consultado"),
                    ):
                        issues, diagnostics = installer.client_repair_assessment()

            self.assertEqual([], diagnostics)
            self.assertEqual([], issues)

    def test_unrecognized_stable_binary_is_not_rewritten_by_inference(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            runtime = target / "ezQuake Stable.app"
            self.write_macos_bundle(runtime)
            plist = runtime / "Contents/Info.plist"
            metadata = plistlib.loads(plist.read_bytes())
            metadata[install_qw.MACOS_SAFE_AREA_KEY] = False
            plist.write_bytes(plistlib.dumps(metadata))

            with mock.patch.object(install_qw.host_platform, "system", return_value="Linux"):
                action = installer.macos_runtime_action(
                    runtime,
                    "stable",
                    "2ccea8f214c91e5fc92b5cb195c81ac584f75a551d11a58de56e5e7951eec7ed",
                    "c" * 64,
                )

            self.assertIsNone(action)

    def test_macos_nightly_fullscreen_repair_is_visible_in_the_update_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            rows = []
            with mock.patch.object(installer, "latest_release", return_value=(
                "20260616-101233_a86996a",
                ("https://example.invalid/20260616-101233_a86996a_ezQuake-macOS-universal.zip",),
                "a" * 64,
            )):
                with mock.patch.object(installer, "macos_app_needs_preparation", return_value=True):
                    self.assertTrue(installer.update_runtime(
                        spec, "nightly", {
                            "selection": "20260616-101233_a86996a",
                            "artifact_sha256": "a" * 64,
                            "binary_sha256": "b" * 64,
                        },
                        dry_run=True, plan_rows=rows,
                    ))
            self.assertEqual(1, len(rows))
            self.assertEqual("Reparar", rows[0].action)
            self.assertEqual("área segura", rows[0].installed)
            self.assertEqual("tela inteira", rows[0].available)

    def test_same_version_macos_repair_retains_inverse_for_parent_update(self):
        """A later update failure must restore an offline nightly preparation."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            receipt_path, receipt, runtime = self.write_ezquake_fixture(
                installer, target, spec, "nightly",
            )
            self.write_macos_bundle(runtime, receipt["bundle_version"])
            receipt_before = receipt_path.read_bytes()
            results = []

            def prepare(app):
                (app / "Contents/x86qw-prepared").write_bytes(b"prepared")
                return True

            selected = (
                receipt["selection"],
                (f"https://example.invalid/{receipt['artifact_name']}",),
                receipt["artifact_sha256"],
            )
            with mock.patch.object(
                installer, "latest_release", return_value=selected,
            ), mock.patch.object(
                installer, "macos_app_needs_preparation", return_value=True,
            ), mock.patch.object(
                installer, "ensure_macos_ezquake_closed",
            ), mock.patch.object(
                installer, "prepare_macos_nightly_app", side_effect=prepare,
            ), mock.patch.object(
                installer,
                "inspect_macos_app",
                return_value=(receipt["bundle_version"], "c" * 64),
            ):
                self.assertTrue(installer.update_runtime(
                    spec,
                    "nightly",
                    receipt,
                    dry_run=False,
                    mutation_results=results,
                ))

            installer.rollback_component_transactions(
                results, install_qw.InstallerError("late state failure"),
            )
            self.assertFalse((runtime / "Contents/x86qw-prepared").exists())
            self.assertEqual(receipt_before, receipt_path.read_bytes())

    def test_legacy_resigned_stable_macos_runtime_is_restored_by_update(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            runtime = target / spec.stable_runtime
            self.write_macos_bundle(runtime)
            plist = runtime / "Contents/Info.plist"
            metadata = plistlib.loads(plist.read_bytes())
            metadata[install_qw.MACOS_SAFE_AREA_KEY] = False
            plist.write_bytes(plistlib.dumps(metadata))
            rows = []
            selected = (
                "3.6.9",
                ("https://example.invalid/ezQuake-macOS-universal.zip",),
                "a" * 64,
            )

            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(installer, "latest_release", return_value=selected):
                    with mock.patch.object(installer, "macos_app_is_sandboxed", return_value=False):
                        changed = installer.update_runtime(
                            spec, "stable", {
                                "selection": "3.6.9",
                                "artifact_sha256": "2ccea8f214c91e5fc92b5cb195c81ac584f75a551d11a58de56e5e7951eec7ed",
                                "binary_sha256": "e24524761d8ff10c57a8ecbb2fdc7ce29d1bd78641cfaecf49644d8881e2422a",
                            },
                            dry_run=True, plan_rows=rows,
                        )

            self.assertTrue(changed)
            self.assertEqual(1, len(rows))
            self.assertEqual("Restaurar", rows[0].action)
            self.assertEqual("bundle re-assinado localmente", rows[0].installed)
            self.assertEqual("bundle upstream integral", rows[0].available)

    def test_stable_macos_update_preserves_the_security_scoped_bookmark(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            receipt_path, receipt, runtime = self.write_ezquake_fixture(
                installer, target, spec, "stable",
                artifact_sha256=(
                    "2ccea8f214c91e5fc92b5cb195c81ac584f75a551d11a58de56e5e7951eec7ed"
                ),
                binary_sha256="e24524761d8ff10c57a8ecbb2fdc7ce29d1bd78641cfaecf49644d8881e2422a",
            )
            self.write_macos_bundle(runtime)
            plist = runtime / "Contents/Info.plist"
            metadata = plistlib.loads(plist.read_bytes())
            metadata[install_qw.MACOS_SAFE_AREA_KEY] = False
            plist.write_bytes(plistlib.dumps(metadata))
            selected = (
                receipt["selection"],
                (f"https://example.invalid/{receipt['artifact_name']}",),
                receipt["artifact_sha256"],
            )

            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(installer, "latest_release", return_value=selected):
                    with mock.patch.object(installer, "macos_app_is_sandboxed", return_value=False):
                        with mock.patch.object(installer, "ensure_macos_ezquake_closed"):
                            with mock.patch.object(installer, "check_runtime_destination_ownership"):
                                with mock.patch.object(installer, "prepare_cache"):
                                    with mock.patch.object(installer, "ensure_archive", return_value=target / "archive.zip"):
                                        with mock.patch.object(installer, "prepare_runtime", return_value=target / "prepared"):
                                            with mock.patch.object(installer, "write_ezquake_receipt"):
                                                with mock.patch.object(installer, "commit_runtime"):
                                                    with mock.patch.object(
                                                        installer,
                                                        "reset_macos_game_directory",
                                                        side_effect=AssertionError("bookmark removido"),
                                                    ):
                                                        self.assertTrue(installer.update_runtime(
                                                            spec, "stable", receipt, dry_run=False,
                                                        ))

            self.assertTrue(receipt_path.is_file())

    def test_stable_macos_runtime_commit_failure_restores_runtime_and_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            receipt_path, _, runtime = self.write_ezquake_fixture(
                installer, target, spec, "stable",
                artifact_sha256=(
                    "2ccea8f214c91e5fc92b5cb195c81ac584f75a551d11a58de56e5e7951eec7ed"
                ),
                binary_sha256="e24524761d8ff10c57a8ecbb2fdc7ce29d1bd78641cfaecf49644d8881e2422a",
            )
            runtime.mkdir(parents=True)
            legacy_marker = runtime / "legacy-marker"
            legacy_marker.write_bytes(b"legacy")
            original_receipt = receipt_path.read_bytes()
            installer.spec = spec
            installer.channel = "stable"
            installer.stage = target / ".stage"
            installer.stage.mkdir()
            prepared = installer.stage / "prepared-runtime"
            prepared.mkdir()
            (prepared / "upstream-marker").write_bytes(b"upstream")
            staged_receipt = installer.stage / "staged-receipt"
            staged_receipt.write_bytes(original_receipt)

            with mock.patch.object(
                install_qw,
                "atomic_write_bytes",
                side_effect=install_qw.AtomicWriteError(
                    "disco indisponível", committed=False,
                ),
            ):
                with self.assertRaises(install_qw.PersistenceError):
                    installer.commit_runtime(prepared, staged_receipt)

            self.assertEqual(b"legacy", legacy_marker.read_bytes())
            self.assertEqual(original_receipt, receipt_path.read_bytes())
            self.assertFalse((runtime / "upstream-marker").exists())

    def test_runtime_receipt_precommit_failure_restores_the_compatible_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            (
                installer, _, _, runtime, receipt_path, original_receipt,
                prepared, staged_receipt, _,
            ) = self.prepare_runtime_commit_fixture(Path(temporary))

            def truncate_then_fail(path, payload, **kwargs):
                Path(path).write_bytes(b"truncated-receipt\n")
                raise install_qw.AtomicWriteError(
                    "injected receipt promotion failure", committed=False,
                )

            with mock.patch.object(
                install_qw, "atomic_write_bytes", side_effect=truncate_then_fail,
            ):
                with self.assertRaises(install_qw.PersistenceError) as raised:
                    installer.commit_runtime(prepared, staged_receipt)

            self.assertFalse(raised.exception.committed)
            self.assertEqual(b"old-runtime\n", runtime.read_bytes())
            self.assertEqual(original_receipt, receipt_path.read_bytes())

    def test_runtime_receipt_committed_error_keeps_the_new_compatible_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            (
                installer, _, spec, runtime, receipt_path, _,
                prepared, staged_receipt, updated,
            ) = self.prepare_runtime_commit_fixture(Path(temporary))
            atomic_write = install_qw.atomic_write_bytes

            def commit_then_fail(path, payload, **kwargs):
                atomic_write(path, payload, **kwargs)
                raise install_qw.AtomicWriteError(
                    "injected receipt directory fsync failure", committed=True,
                )

            with mock.patch.object(
                install_qw, "atomic_write_bytes", side_effect=commit_then_fail,
            ):
                with self.assertRaises(install_qw.PersistenceError) as raised:
                    installer.commit_runtime(prepared, staged_receipt)

            self.assertTrue(raised.exception.committed)
            self.assertEqual(b"new-runtime\n", runtime.read_bytes())
            persisted = installer.validate_ezquake_receipt(
                receipt_path, spec, "stable",
            )
            self.assertEqual(updated, persisted)
            self.assertEqual(install_qw.file_hash(runtime), persisted["binary_sha256"])

    def test_install_keeps_client_inverse_until_state_outcome(self):
        for committed in (False, True):
            with self.subTest(committed=committed), tempfile.TemporaryDirectory() as temporary:
                installer, target, _ = self.make_installer(Path(temporary))
                spec = install_qw.PLATFORMS["linux"]
                receipt_path, old_receipt, runtime = self.write_ezquake_fixture(
                    installer, target, spec, "stable", payload=b"old-runtime\n",
                )
                old_receipt_bytes = receipt_path.read_bytes()
                for name in ("pak0.pak", "pak1.pak"):
                    pak = target / "id1" / name
                    pak.parent.mkdir(parents=True, exist_ok=True)
                    pak.write_bytes(name.encode("ascii"))
                installer.spec = spec
                installer.channel = "stable"
                installer.selected_version = "3.6.10"
                installer.app_archive_name = spec.stable_archive
                installer.app_url = f"https://example.invalid/{spec.stable_archive}"
                installer.app_archive_sha256 = "c" * 64
                state_stages: list[Path] = []

                def prepare_runtime(_archive):
                    assert installer.stage is not None
                    prepared = installer.stage / "prepared-runtime"
                    prepared.write_bytes(b"new-runtime\n")
                    installer.app_bundle_version = "3.6.10"
                    installer.app_binary_sha256 = install_qw.file_hash(prepared)
                    return prepared

                patches = (
                    mock.patch.object(installer, "select_platform"),
                    mock.patch.object(installer, "choose_channel"),
                    mock.patch.object(installer, "choose_release"),
                    mock.patch.object(installer, "macos_game_directory_reset_required", return_value=False),
                    mock.patch.object(installer, "ensure_macos_ezquake_closed"),
                    mock.patch.object(installer, "check_runtime_destination_ownership"),
                    mock.patch.object(installer, "prepare_install_target"),
                    mock.patch.object(installer, "reject_target_symlinks"),
                    mock.patch.object(
                        installer, "provision_install_target", return_value=None,
                    ),
                    mock.patch.object(installer, "check_paks"),
                    mock.patch.object(installer, "prepare_cache"),
                    mock.patch.object(installer, "ensure_archive", return_value=target / "archive"),
                    mock.patch.object(installer, "prepare_runtime", side_effect=prepare_runtime),
                    mock.patch.object(installer, "confirm_components", return_value=False),
                    mock.patch.object(installer, "installed_components", return_value=[]),
                    mock.patch.object(
                        installer, "write_install_state",
                        side_effect=self.state_failure_after_optional_commit(
                            installer, committed, state_stages,
                        ),
                    ),
                )
                with contextlib.ExitStack() as stack:
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    for patcher in patches:
                        stack.enter_context(patcher)
                    with self.assertRaises(install_qw.PersistenceError):
                        installer.install()

                if committed:
                    self.assertEqual(b"new-runtime\n", runtime.read_bytes())
                    persisted = installer.validate_ezquake_receipt(
                        receipt_path, spec, "stable",
                    )
                    self.assertEqual("3.6.10", persisted["selection"])
                    self.assertTrue((target / install_qw.INSTALL_STATE).is_file())
                else:
                    self.assertEqual(b"old-runtime\n", runtime.read_bytes())
                    self.assertEqual(old_receipt_bytes, receipt_path.read_bytes())
                    self.assertEqual(
                        old_receipt,
                        installer.validate_ezquake_receipt(receipt_path, spec, "stable"),
                    )
                    self.assertFalse((target / install_qw.INSTALL_STATE).exists())
                installer.cleanup_stage()
                installer.stage = None

    def test_install_rolls_back_new_core_paks_when_later_preparation_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            marker = target / "id1/pak-transaction-marker"
            marker.parent.mkdir(parents=True)
            for name in ("pak0.pak", "pak1.pak"):
                (marker.parent / name).write_bytes(name.encode("ascii"))

            def provision():
                def apply():
                    marker.write_bytes(b"installed")
                    return marker

                def rollback(path):
                    path.unlink()

                plan = install_qw.MutationPlan(
                    identifier="test:core-pak-parent",
                    summary="retain core PAK inverse",
                    steps=(install_qw.MutationStep(
                        key="paks", description="publish PAK marker",
                        observe=lambda: marker.exists(),
                        apply=apply, rollback=rollback,
                    ),),
                )
                return install_qw.execute_mutation(
                    install_qw.prepare_mutation(plan)
                )

            patches = (
                mock.patch.object(installer, "select_platform"),
                mock.patch.object(installer, "choose_channel"),
                mock.patch.object(installer, "choose_release"),
                mock.patch.object(
                    installer, "macos_game_directory_reset_required", return_value=False,
                ),
                mock.patch.object(installer, "ensure_macos_ezquake_closed"),
                mock.patch.object(installer, "check_runtime_destination_ownership"),
                mock.patch.object(installer, "prepare_install_target"),
                mock.patch.object(installer, "reject_target_symlinks"),
                mock.patch.object(
                    installer, "provision_install_target", side_effect=provision,
                ),
                mock.patch.object(installer, "check_paks"),
                mock.patch.object(
                    installer, "prepare_cache",
                    side_effect=install_qw.InstallerError("simulated cache failure"),
                ),
            )
            with contextlib.ExitStack() as stack:
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                for patcher in patches:
                    stack.enter_context(patcher)
                with self.assertRaisesRegex(install_qw.InstallerError, "cache failure"):
                    installer.install()
            self.assertFalse(marker.exists())
            installer.cleanup_stage()

    def test_legacy_stable_update_commits_the_unmodified_upstream_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            artifact_sha256 = (
                "2ccea8f214c91e5fc92b5cb195c81ac584f75a551d11a58de56e5e7951eec7ed"
            )
            receipt_path, receipt, runtime = self.write_ezquake_fixture(
                installer, target, spec, "stable",
                artifact_sha256=artifact_sha256,
                binary_sha256="e24524761d8ff10c57a8ecbb2fdc7ce29d1bd78641cfaecf49644d8881e2422a",
            )
            self.write_macos_bundle(runtime)
            plist = runtime / "Contents/Info.plist"
            metadata = plistlib.loads(plist.read_bytes())
            metadata[install_qw.MACOS_SAFE_AREA_KEY] = False
            plist.write_bytes(plistlib.dumps(metadata))
            archive = target / spec.stable_archive
            expected = self.write_macos_archive(archive)
            selected = (
                receipt["selection"],
                (f"https://example.invalid/{receipt['artifact_name']}",),
                artifact_sha256,
            )

            def provide_archive():
                installer.app_archive_sha256 = artifact_sha256
                return archive

            with mock.patch.object(install_qw.host_platform, "system", return_value="Darwin"):
                with mock.patch.object(installer, "latest_release", return_value=selected):
                    with mock.patch.object(installer, "macos_app_is_sandboxed", return_value=False):
                        with mock.patch.object(installer, "ensure_macos_ezquake_closed"):
                            with mock.patch.object(installer, "check_runtime_destination_ownership"):
                                with mock.patch.object(installer, "prepare_cache"):
                                    with mock.patch.object(
                                        installer, "ensure_archive", side_effect=provide_archive,
                                    ):
                                        with mock.patch.object(installer, "run_command") as command:
                                            self.assertTrue(installer.update_runtime(
                                                spec, "stable", receipt, dry_run=False,
                                            ))

            actual = {
                path.relative_to(runtime).as_posix(): path.read_bytes()
                for path in runtime.rglob("*") if path.is_file()
            }
            self.assertEqual(expected, actual)
            restored = installer.validate_ezquake_receipt(receipt_path, spec, "stable")
            self.assertEqual(
                install_qw.file_hash(runtime / "Contents/MacOS/ezQuake"),
                restored["binary_sha256"],
            )
            self.assertFalse(any(
                "--sign" in call.args[0] for call in command.call_args_list
            ))

    def test_macos_nightly_local_repair_preserves_the_shared_bookmark(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            receipt_path, receipt, runtime = self.write_ezquake_fixture(
                installer, target, spec, "nightly",
            )
            self.write_macos_bundle(runtime, receipt["bundle_version"])

            with mock.patch.object(installer, "macos_app_needs_preparation", return_value=True):
                with mock.patch.object(installer, "ensure_macos_ezquake_closed"):
                    with mock.patch.object(installer, "prepare_macos_nightly_app", return_value=True):
                        with mock.patch.object(
                            installer, "inspect_macos_app",
                            return_value=(receipt["bundle_version"], "c" * 64),
                        ):
                            with mock.patch.object(
                                installer, "clear_macos_game_directory",
                                side_effect=AssertionError("bookmark removido"),
                            ):
                                updated = installer.repair_installed_macos_runtime(
                                    spec, "nightly", receipt_path, receipt,
                                )

            self.assertEqual("c" * 64, updated["binary_sha256"])

    def test_macos_nightly_repair_rolls_back_bundle_when_receipt_commit_fails(self):
        """Offline preparation and its receipt must publish as one generation."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            receipt_path, receipt, runtime = self.write_ezquake_fixture(
                installer, target, spec, "nightly",
            )
            self.write_macos_bundle(runtime, receipt["bundle_version"])
            receipt_before = receipt_path.read_bytes()
            mutation_results = []

            def prepare(app):
                (app / "Contents/x86qw-prepared").write_bytes(b"prepared")
                return True

            with mock.patch.object(
                installer, "macos_app_needs_preparation", return_value=True,
            ), mock.patch.object(
                installer, "ensure_macos_ezquake_closed",
            ), mock.patch.object(
                installer, "prepare_macos_nightly_app", side_effect=prepare,
            ), mock.patch.object(
                installer,
                "inspect_macos_app",
                return_value=(receipt["bundle_version"], "c" * 64),
            ), mock.patch.object(
                installer,
                "_apply_runtime_receipt",
                side_effect=OSError("simulated receipt promotion failure"),
            ):
                with self.assertRaises(install_qw.InstallerError):
                    installer.repair_installed_macos_runtime(
                        spec,
                        "nightly",
                        receipt_path,
                        receipt,
                        mutation_results=mutation_results,
                    )

            self.assertFalse((runtime / "Contents/x86qw-prepared").exists())
            self.assertEqual(receipt_before, receipt_path.read_bytes())
            self.assertEqual([], mutation_results)

    def test_macos_nightly_local_repair_configures_runtime_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            receipt_path, receipt, runtime = self.write_ezquake_fixture(
                installer, target, spec, "nightly",
            )
            self.write_macos_bundle(runtime, receipt["bundle_version"])

            with mock.patch.object(install_qw.host_platform, "system", return_value="Linux"):
                with mock.patch.object(installer, "macos_app_needs_preparation", return_value=True):
                    with mock.patch.object(installer, "ensure_macos_ezquake_closed"):
                        with mock.patch.object(
                            installer, "inspect_macos_app",
                            return_value=(receipt["bundle_version"], "c" * 64),
                        ):
                            updated = installer.repair_installed_macos_runtime(
                                spec, "nightly", receipt_path, receipt,
                            )

            self.assertIs(spec, installer.spec)
            self.assertEqual("nightly", installer.channel)
            self.assertEqual("c" * 64, updated["binary_sha256"])

    def test_update_output_wraps_without_cutting_names_or_versions(self):
        row = install_qw.UpdatePlanRow(
            kind="component", item="componente-com-um-nome-longo",
            installed="1.47+x86qw.17", available="1.47+x86qw.123456789",
            action="Atualizar", size=123456,
        )
        output = io.StringIO()
        with mock.patch.object(
            install_qw.shutil, "get_terminal_size",
            return_value=os.terminal_size((48, 24)),
        ), contextlib.redirect_stdout(output):
            install_qw.console.update_plan([row], "update")
            install_qw.console.download_result(
                "x86QW Package Manifest com nome longo", size=123456,
            )
        rendered = output.getvalue()
        self.assertIn("componente-com-um-nome-longo", rendered)
        self.assertIn("Instalado  | 1.47+x86qw.17", rendered)
        self.assertIn("Disponível | 1.47+x86qw.123456789", rendered)
        self.assertIn("x86QW Package Manifest com nome longo", rendered)
        self.assertIn("Baixado | 123.5KB/123.5KB", rendered)

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
                with mock.patch.object(
                    installer.remote,
                    "get_mirrors",
                    return_value=(json.dumps(catalog).encode(), install_qw.CATALOG_URL),
                ):
                    self.assertEqual(
                        [("3.6.9", tuple(package["urls"]), "a" * 64)],
                        installer.stable_catalog(),
                    )
            package["redistribution_reviewed"] = False
            installer._public_catalog = None
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(
                    installer.remote,
                    "get_mirrors",
                    return_value=(json.dumps(catalog).encode(), install_qw.CATALOG_URL),
                ):
                    with self.assertRaises(install_qw.InstallerError):
                        installer.stable_catalog()

    def test_public_catalog_is_reused_between_client_and_components(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.spec = install_qw.PLATFORMS["macos"]
            catalog = json.loads(
                (ROOT / "site/public/api/v1/catalog.json").read_text(encoding="utf-8")
            )
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(
                    installer.remote,
                    "get_mirrors",
                    return_value=(json.dumps(catalog).encode(), install_qw.CATALOG_URL),
                ) as get:
                    installer.stable_catalog()
                    installer.component_package_record("nquake-bootstrap")
            get.assert_called_once_with(
                install_qw.CATALOG_URLS,
                maximum_size=install_qw.CATALOG_MAX_BYTES,
                timeout=install_qw.CATALOG_TIMEOUT,
                attempts=1,
                mirror_label="Catálogo",
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
                with mock.patch.object(
                    installer.remote,
                    "get_mirrors",
                    return_value=(json.dumps(remote).encode(), install_qw.CATALOG_URL),
                ) as get:
                    self.assertEqual(remote, installer.public_catalog("remote"))
            get.assert_called_once_with(
                install_qw.CATALOG_URLS,
                maximum_size=install_qw.CATALOG_MAX_BYTES,
                timeout=install_qw.CATALOG_TIMEOUT,
                attempts=1,
                mirror_label="Catálogo",
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
            installed_shell = (target / "x86qw.sh").read_text(encoding="utf-8")
            installed_batch = (target / "x86qw.cmd").read_text(encoding="utf-8")
            self.assertNotIn("@X86QW_PYTHON@", installed_shell)
            self.assertNotIn("@X86QW_PYTHON@", installed_batch)
            self.assertIn(os.fspath(Path(sys.executable)), installed_shell)
            self.assertIn(os.fspath(Path(sys.executable)).replace("%", "%%"), installed_batch)
            self.assertFalse((target / "x86qw").exists())
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

    def test_online_cli_validates_launcher_templates_before_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "destino"
            target.mkdir()
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "x86qw.pyz").write_bytes(zipapp_bytes("1.0.6"))
            (bundle / "x86qw.sh").write_text("#!/bin/sh\nsem marcador\n", encoding="utf-8")
            (bundle / "x86qw.cmd").write_bytes(
                (ROOT / "dist/installer/bin/x86qw.cmd").read_bytes()
            )
            installer = install_qw.Installer(ROOT, target, online_only=True)
            installer.project_root = bundle
            with mock.patch.object(
                installer, "installer_bundle_identity",
                return_value={"format": 1, "project": "x86qw", "version": "1.0.6"},
            ):
                with self.assertRaisesRegex(install_qw.InstallerError, "Launcher público inválido"):
                    installer.install_online_cli()
            self.assertFalse((target / ".x86qw").exists())
            self.assertFalse((target / "x86qw.sh").exists())
            self.assertFalse((target / "x86qw.cmd").exists())

    def test_online_cli_rolls_back_every_generation_file_when_batch_launcher_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "destino"
            cli = target / ".x86qw/cli"
            cli.mkdir(parents=True)
            old_identity = {"format": 1, "project": "x86qw", "version": "1.0.5"}
            old_receipt = json.dumps(old_identity, sort_keys=True).encode("utf-8") + b"\n"
            old_paths = {
                cli / "x86qw.pyz": b"old application",
                cli / "receipt": old_receipt,
                target / ".x86qw/cli.receipt": old_receipt,
                target / "x86qw.sh": b"old shell launcher\n",
                target / "x86qw.cmd": b"old batch launcher\r\n",
            }
            for path, payload in old_paths.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            if os.name != "nt":
                (target / "x86qw.sh").chmod(0o751)
                (target / "x86qw.cmd").chmod(0o640)
            old_modes = {
                path: path.stat().st_mode & 0o777 for path in old_paths
            }

            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "x86qw.pyz").write_bytes(zipapp_bytes("1.0.6"))
            for name in ("x86qw.sh", "x86qw.cmd"):
                (bundle / name).write_bytes(
                    (ROOT / "dist/installer/bin" / name).read_bytes()
                )
            installer = install_qw.Installer(ROOT, target, online_only=True)
            installer.project_root = bundle
            apply_payload = installer._apply_runtime_payload

            def fail_batch_launcher(prepared, destination):
                if destination == target / "x86qw.cmd":
                    raise OSError("simulated batch launcher promotion failure")
                return apply_payload(prepared, destination)

            with mock.patch.object(
                installer,
                "installer_bundle_identity",
                return_value={"format": 1, "project": "x86qw", "version": "1.0.6"},
            ), mock.patch.object(
                installer, "_apply_runtime_payload", side_effect=fail_batch_launcher,
            ):
                with self.assertRaises(install_qw.InstallerError):
                    installer.install_online_cli()

            for path, payload in old_paths.items():
                self.assertEqual(payload, path.read_bytes(), path)
                self.assertEqual(old_modes[path], path.stat().st_mode & 0o777, path)
            self.assertFalse((target / ".x86qw/staging").exists())
            self.assertFalse(any(target.rglob("*.new")))

    def test_online_cli_retains_its_inverse_until_the_parent_transaction_finishes(self):
        """A later generation failure must restore every previous CLI file."""

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "destino"
            cli = target / ".x86qw/cli"
            cli.mkdir(parents=True)
            old_identity = {"format": 1, "project": "x86qw", "version": "1.0.5"}
            old_receipt = json.dumps(old_identity, sort_keys=True).encode("utf-8") + b"\n"
            old_paths = {
                cli / "x86qw.pyz": b"old application",
                cli / "receipt": old_receipt,
                target / "x86qw.sh": b"old shell launcher\n",
                target / "x86qw.cmd": b"old batch launcher\r\n",
            }
            for path, payload in old_paths.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)

            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "x86qw.pyz").write_bytes(zipapp_bytes("1.0.6"))
            for name in ("x86qw.sh", "x86qw.cmd"):
                (bundle / name).write_bytes(
                    (ROOT / "dist/installer/bin" / name).read_bytes()
                )
            installer = install_qw.Installer(ROOT, target, online_only=True)
            installer.project_root = bundle

            with mock.patch.object(
                installer,
                "installer_bundle_identity",
                return_value={"format": 1, "project": "x86qw", "version": "1.0.6"},
            ), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    install_qw.InstallerError, "late generation failure",
                ):
                    with installer.component_state_transaction() as results:
                        installer.install_online_cli(mutation_results=results)
                        raise install_qw.InstallerError("late generation failure")

            for path, payload in old_paths.items():
                self.assertEqual(payload, path.read_bytes(), path)
            self.assertFalse((target / ".x86qw/staging").exists())

    def test_online_cli_failed_first_install_leaves_no_metadata_or_launcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "destino"
            target.mkdir()
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "x86qw.pyz").write_bytes(zipapp_bytes("1.0.6"))
            for name in ("x86qw.sh", "x86qw.cmd"):
                (bundle / name).write_bytes(
                    (ROOT / "dist/installer/bin" / name).read_bytes()
                )
            installer = install_qw.Installer(ROOT, target, online_only=True)
            installer.project_root = bundle
            apply_payload = installer._apply_runtime_payload

            def fail_batch_launcher(prepared, destination):
                if destination == target / "x86qw.cmd":
                    raise OSError("simulated batch launcher promotion failure")
                return apply_payload(prepared, destination)

            with mock.patch.object(
                installer,
                "installer_bundle_identity",
                return_value={"format": 1, "project": "x86qw", "version": "1.0.6"},
            ), mock.patch.object(
                installer, "_apply_runtime_payload", side_effect=fail_batch_launcher,
            ):
                with self.assertRaises(install_qw.InstallerError):
                    installer.install_online_cli()

            self.assertFalse((target / ".x86qw").exists())
            self.assertFalse((target / "x86qw.sh").exists())
            self.assertFalse((target / "x86qw.cmd").exists())

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

    def test_metadata_layout_migration_rolls_back_every_context_on_late_failure(self):
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
            legacy_inventory.write_text(
                f"qw/test.cfg\t{'c' * 64}\n", encoding="utf-8",
            )
            installer.write_component_receipt(
                "nquake-bootstrap", "test", "https://example.invalid/component.zip",
                legacy_inventory, legacy_receipt,
            )
            legacy_snapshot = {
                path: path.read_bytes()
                for path in (target / ".x86qw").glob("*.receipt")
            }
            legacy_snapshot.update({
                legacy_inventory: legacy_inventory.read_bytes(),
            })

            with mock.patch.object(
                installer, "commit_component_metadata",
                side_effect=OSError("simulated late metadata migration failure"),
            ):
                with self.assertRaises(install_qw.InstallerError):
                    installer.migrate_metadata_layout()

            for path, payload in legacy_snapshot.items():
                self.assertEqual(payload, path.read_bytes(), path)
            self.assertFalse((metadata / "cli/receipt").exists())
            self.assertFalse((metadata / "clients").exists())
            self.assertFalse((metadata / "components").exists())

    def test_metadata_layout_keeps_inverses_for_parent_update_transaction(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            metadata = target / ".x86qw"
            (metadata / "cli").mkdir(parents=True)
            (metadata / "cli/x86qw.pyz").write_bytes(b"zipapp")
            legacy = self.write_cli_receipt(target, "1.0.5", legacy=True)
            original = legacy.read_bytes()
            results = []

            self.assertTrue(installer.migrate_metadata_layout(results))
            self.assertTrue((metadata / "cli/receipt").is_file())
            self.assertFalse(legacy.exists())
            self.assertTrue(results)

            installer.rollback_component_transactions(
                results, install_qw.InstallerError("simulated parent failure"),
            )
            self.assertEqual(original, legacy.read_bytes())
            self.assertFalse((metadata / "cli/receipt").exists())
            installer.cleanup_stage()

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

    def test_cli_publication_failure_rolls_back_the_content_update(self):
        """Content and the installed CLI must publish as one generation."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            marker = target / "qw/content-generation.txt"
            marker.parent.mkdir(parents=True)
            marker.write_bytes(b"old\n")

            def update(*, dry_run, plan_rows=None, mutation_results=None, **_options):
                if dry_run:
                    assert plan_rows is not None
                    plan_rows.append(install_qw.UpdatePlanRow(
                        "Componente", "KTX", "old", "new", "Atualizar",
                    ))
                    return True
                old_payload = marker.read_bytes()
                result = install_qw.execute_mutation(install_qw.prepare_mutation(
                    install_qw.MutationPlan(
                        identifier="test:content-cli-generation",
                        summary="publish content before the CLI",
                        steps=(install_qw.MutationStep(
                            key="content",
                            description="replace the managed content",
                            observe=marker.read_bytes,
                            apply=lambda: marker.write_bytes(b"new\n"),
                            rollback=lambda _token: marker.write_bytes(old_payload),
                        ),),
                    )
                ))
                if mutation_results is not None:
                    mutation_results.append(result)
                return True

            installer.update = mock.Mock(side_effect=update)
            installer.handoff_cli_update = mock.Mock(return_value=False)
            installer.cli_update_plan_row = mock.Mock(return_value=None)
            installer.confirm_update_plan = mock.Mock(return_value=True)
            installer.install_online_cli = mock.Mock(
                side_effect=install_qw.InstallerError("CLI publication failed"),
            )
            installer.validate_target = mock.Mock()
            installer.reject_target_symlinks = mock.Mock()
            operation_lock = mock.Mock()

            with mock.patch.object(
                install_qw, "Installer", return_value=installer,
            ), mock.patch.object(
                install_qw.session_control.InstallationLock,
                "acquire",
                return_value=operation_lock,
            ), mock.patch(
                "x86qw_runtime.supervisor.sessions.recover_sessions",
            ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO(),
            ):
                self.assertEqual(1, install_qw.main([
                    "--online-only", "--installed-cli", "--skip-cli-update",
                    "update", str(target), "--yes",
                ]))

            self.assertEqual(b"old\n", marker.read_bytes())

    def test_cli_only_handoff_publishes_without_rewriting_content(self):
        """A validated CLI plan must not depend on a content update."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            published = target / "cli-published"
            installer.update = mock.Mock(return_value=False)
            installer.handoff_cli_update = mock.Mock(return_value=False)
            installer.cli_update_plan_row = mock.Mock(return_value=install_qw.UpdatePlanRow(
                "CLI", "x86QW", "1.0.5", "1.0.6", "Atualizar",
            ))
            installer.confirm_update_plan = mock.Mock(return_value=True)
            installer.install_online_cli = mock.Mock(
                side_effect=lambda **_options: published.write_bytes(b"published\n"),
            )
            installer.validate_target = mock.Mock()
            installer.reject_target_symlinks = mock.Mock()
            operation_lock = mock.Mock()

            with mock.patch.object(
                install_qw, "Installer", return_value=installer,
            ), mock.patch.object(
                install_qw.session_control.InstallationLock,
                "acquire",
                return_value=operation_lock,
            ), mock.patch(
                "x86qw_runtime.supervisor.sessions.recover_sessions",
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, install_qw.main([
                    "--online-only", "--installed-cli", "--skip-cli-update",
                    "update", str(target), "--yes",
                ]))

            self.assertTrue(published.is_file())
            self.assertEqual(b"published\n", published.read_bytes())
            self.assertEqual(1, installer.update.call_count)
            self.assertTrue(installer.update.call_args.kwargs["dry_run"])

    def test_maintenance_recovery_does_not_load_service_or_gameplay_entrypoints(self):
        """Maintenance must recover journals through the canonical runtime boundary."""
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            target.mkdir()
            installer = mock.Mock()
            installer.target = target
            installer.require_managed_installation_identity.return_value = ("state",)
            installer.cleanup_data.return_value = (0, 0)
            operation_lock = mock.Mock()
            with mock.patch.object(install_qw, "Installer", return_value=installer):
                with mock.patch.object(
                    install_qw.session_control.InstallationLock,
                    "acquire", return_value=operation_lock,
                ):
                    with mock.patch.object(
                        install_qw,
                        "load_services_module",
                        side_effect=AssertionError(
                            "manutenção carregou o entrypoint de serviços"
                        ),
                    ):
                        with contextlib.redirect_stdout(io.StringIO()):
                            self.assertEqual(0, install_qw.main(["cleanup", str(target)]))
            operation_lock.confirm_recovery.assert_called_once_with()
            operation_lock.release.assert_called_once_with(restore_reclaimed=False)

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
        self.assertIn("==> Plano: atualizar 1 pacote desatualizado", output.getvalue())
        self.assertIn("Configuração base nQuake", output.getvalue())
        self.assertIn("1 -> 2 (15.4KB)", output.getvalue())
        self.assertIn("nenhum arquivo do jogo foi alterado", output.getvalue())

    def test_upgrade_yes_shows_plan_and_applies_without_prompting(self):
        target = Path("/tmp/x86qw-confirmation-test")
        confirm = install_qw.Installer.confirm_update_plan
        installer = mock.Mock()
        installer.target = target
        def upgrade(*, dry_run, preview=False, plan_rows=None, mutation_results=None):
            del mutation_results
            del preview
            if dry_run and plan_rows is not None:
                plan_rows.append(install_qw.UpdatePlanRow(
                    "Componente", "Total Destruction 2", "2.21", "2.22", "Atualizar",
                ))
            return True
        installer.upgrade.side_effect = upgrade
        installer.confirm_update_plan.side_effect = confirm
        @contextlib.contextmanager
        def transaction():
            yield []
        installer.component_state_transaction.side_effect = transaction
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

    def test_update_keeps_component_stage_until_state_outcome(self):
        for committed in (False, True):
            with self.subTest(committed=committed), tempfile.TemporaryDirectory() as temporary:
                installer, target, _ = self.make_installer(Path(temporary))
                for name in ("pak0.pak", "pak1.pak"):
                    pak = target / "id1" / name
                    pak.parent.mkdir(parents=True, exist_ok=True)
                    pak.write_bytes(name.encode("ascii"))
                installed: list[str] = []
                component_stages: list[Path] = []
                state_stages: list[Path] = []
                marker, install_components = self.stage_backed_component_install(
                    installer, target, installed, component_stages,
                )
                state = {
                    "format": 2,
                    "project": "x86qw",
                    "profile": "custom",
                    "requested_components": ["ktx"],
                    "recorded_components": [],
                    "known_components": list(installer.components),
                    "capabilities": [],
                    "component_fingerprint": install_qw.profile_fingerprint([]),
                }
                spec = install_qw.PLATFORMS["linux"]
                receipt = {"selection": "3.6.9"}

                patches = (
                    mock.patch.object(installer, "preflight_ezquake_receipts"),
                    mock.patch.object(installer, "preflight_component_receipts"),
                    mock.patch.object(installer, "legacy_metadata_present", return_value=False),
                    mock.patch.object(installer, "check_paks"),
                    mock.patch.object(installer, "load_install_state", return_value=state),
                    mock.patch.object(installer, "current_install_state", return_value=state),
                    mock.patch.object(installer, "installed_legacy_component_replacements", return_value={}),
                    mock.patch.object(installer, "installed_legacy_component_removals", return_value=[]),
                    mock.patch.object(install_qw, "PLATFORMS", {"linux": spec}),
                    mock.patch.object(installer, "ezquake_receipt_path", return_value=target / "receipt"),
                    mock.patch.object(installer, "validate_ezquake_receipt", return_value=receipt),
                    mock.patch.object(installer, "check_runtime"),
                    mock.patch.object(installer, "update_runtime", return_value=False),
                    mock.patch.object(installer, "outdated_installed_components", return_value=["ktx"]),
                    mock.patch.object(installer, "installed_components", side_effect=lambda: list(installed)),
                    mock.patch.object(installer, "install_components", side_effect=install_components),
                    mock.patch.object(installer, "reconcile_play_support", return_value=False),
                    mock.patch.object(installer, "desired_components", return_value=[]),
                    mock.patch.object(
                        installer, "write_install_state",
                        side_effect=self.state_failure_after_optional_commit(
                            installer, committed, state_stages,
                        ),
                    ),
                )
                with contextlib.ExitStack() as stack:
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    for patcher in patches:
                        stack.enter_context(patcher)
                    with self.assertRaises(install_qw.PersistenceError):
                        installer.update()

                self.assertEqual([component_stages[0]], state_stages)
                self.assertFalse(component_stages[0].exists())
                self.assertIsNone(installer.stage)
                expected = "new\n" if committed else "old\n"
                self.assertEqual(expected, marker.read_text(encoding="utf-8"))
                state_path = target / install_qw.INSTALL_STATE
                self.assertEqual(committed, state_path.is_file())
                self.assertEqual(["ktx"] if committed else [], installed)

    def test_update_keeps_client_stage_until_state_outcome(self):
        for committed in (False, True):
            with self.subTest(committed=committed), tempfile.TemporaryDirectory() as temporary:
                installer, target, _ = self.make_installer(Path(temporary))
                spec = install_qw.PLATFORMS["linux"]
                receipt_path, old_receipt, runtime = self.write_ezquake_fixture(
                    installer, target, spec, "stable", payload=b"old-runtime\n",
                )
                old_receipt_bytes = receipt_path.read_bytes()
                for name in ("pak0.pak", "pak1.pak"):
                    pak = target / "id1" / name
                    pak.parent.mkdir(parents=True, exist_ok=True)
                    pak.write_bytes(name.encode("ascii"))
                state_stages: list[Path] = []
                state = {
                    "format": 2,
                    "project": "x86qw",
                    "profile": "none",
                    "requested_components": [],
                    "recorded_components": [],
                    "known_components": list(installer.components),
                    "capabilities": [],
                    "component_fingerprint": install_qw.profile_fingerprint([]),
                }
                selected = (
                    "3.6.10",
                    (f"https://example.invalid/{spec.stable_archive}",),
                    "c" * 64,
                )

                def prepare_runtime(_archive):
                    self.assertIsNotNone(installer.stage)
                    assert installer.stage is not None
                    prepared = installer.stage / "prepared-runtime"
                    prepared.write_bytes(b"new-runtime\n")
                    installer.app_archive_sha256 = "c" * 64
                    installer.app_bundle_version = "3.6.10"
                    installer.app_binary_sha256 = install_qw.file_hash(prepared)
                    return prepared

                patches = (
                    mock.patch.object(installer, "preflight_ezquake_receipts"),
                    mock.patch.object(installer, "preflight_component_receipts"),
                    mock.patch.object(installer, "legacy_metadata_present", return_value=False),
                    mock.patch.object(installer, "check_paks"),
                    mock.patch.object(installer, "load_install_state", return_value=state),
                    mock.patch.object(installer, "current_install_state", return_value=state),
                    mock.patch.object(installer, "installed_legacy_component_replacements", return_value={}),
                    mock.patch.object(installer, "installed_legacy_component_removals", return_value=[]),
                    mock.patch.object(install_qw, "PLATFORMS", {"linux": spec}),
                    mock.patch.object(installer, "check_runtime"),
                    mock.patch.object(installer, "latest_release", return_value=selected),
                    mock.patch.object(installer, "check_runtime_destination_ownership"),
                    mock.patch.object(installer, "prepare_cache"),
                    mock.patch.object(installer, "ensure_archive", return_value=target / "archive"),
                    mock.patch.object(installer, "prepare_runtime", side_effect=prepare_runtime),
                    mock.patch.object(installer, "outdated_installed_components", return_value=[]),
                    mock.patch.object(installer, "installed_components", return_value=[]),
                    mock.patch.object(installer, "reconcile_play_support", return_value=False),
                    mock.patch.object(installer, "desired_components", return_value=[]),
                    mock.patch.object(
                        installer, "write_install_state",
                        side_effect=self.state_failure_after_optional_commit(
                            installer, committed, state_stages,
                        ),
                    ),
                )
                with contextlib.ExitStack() as stack:
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    for patcher in patches:
                        stack.enter_context(patcher)
                    with self.assertRaises(install_qw.PersistenceError):
                        installer.update()

                self.assertEqual(1, len(state_stages))
                self.assertFalse(state_stages[0].exists())
                self.assertIsNone(installer.stage)
                if committed:
                    self.assertEqual(b"new-runtime\n", runtime.read_bytes())
                    persisted = installer.validate_ezquake_receipt(
                        receipt_path, spec, "stable",
                    )
                    self.assertEqual("3.6.10", persisted["selection"])
                    self.assertEqual(
                        install_qw.file_hash(runtime), persisted["binary_sha256"],
                    )
                    self.assertTrue((target / install_qw.INSTALL_STATE).is_file())
                else:
                    self.assertEqual(b"old-runtime\n", runtime.read_bytes())
                    self.assertEqual(old_receipt_bytes, receipt_path.read_bytes())
                    self.assertEqual(
                        old_receipt,
                        installer.validate_ezquake_receipt(receipt_path, spec, "stable"),
                    )
                    self.assertFalse((target / install_qw.INSTALL_STATE).exists())

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

    def test_install_state_fsync_failure_preserves_previous_bytes(self):
        """Bypassing atomic I/O would replace state even though durability failed."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            state_path = target / install_qw.INSTALL_STATE
            state_path.parent.mkdir(parents=True)
            previous = b"estado-anterior\n"
            state_path.write_bytes(previous)

            with mock.patch.object(
                atomic_io.os,
                "fsync",
                side_effect=OSError("injected state fsync failure"),
            ):
                with self.assertRaises(install_qw.InstallerError):
                    installer.write_install_state("none", [], known=[])

            self.assertEqual(state_path.read_bytes(), previous)
            self.assertEqual(list(state_path.parent.glob(".state.json.*.tmp")), [])

    def test_install_state_transaction_restores_present_and_absent_parent_snapshots(self):
        for present in (False, True):
            with self.subTest(present=present), tempfile.TemporaryDirectory() as temporary:
                installer, target, _ = self.make_installer(Path(temporary))
                state_path = target / install_qw.INSTALL_STATE
                with mock.patch.object(installer, "installed_components", return_value=[]):
                    if present:
                        installer.write_install_state("none", [], known=[])
                        original = state_path.read_bytes()
                    else:
                        original = None
                    installer._create_stage(".state-parent-test.")
                    results = []
                    installer.write_install_state(
                        "none", [], known=list(installer.components),
                        mutation_results=results,
                    )
                    self.assertTrue(state_path.is_file())
                    installer.rollback_component_transactions(
                        results, install_qw.InstallerError("simulated parent failure"),
                    )
                if original is None:
                    self.assertFalse(state_path.exists())
                else:
                    self.assertEqual(original, state_path.read_bytes())
                installer.cleanup_stage()

    def test_install_state_loader_rejects_oversized_valid_json(self):
        """The manager facade must consume the runtime's bounded state reader."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            state_path = target / install_qw.INSTALL_STATE
            state_path.parent.mkdir(parents=True)
            document = {
                "format": 2,
                "project": "x86qw",
                "profile": "none",
                "requested_components": [],
                "recorded_components": [],
                "known_components": [],
                "capabilities": [],
                "component_fingerprint": install_qw.profile_fingerprint([]),
            }
            payload = json.dumps(document).encode("utf-8")
            state_path.write_bytes(
                payload + b" " * (runtime_state.MAX_INSTALL_STATE_BYTES + 1)
            )

            with self.assertRaisesRegex(install_qw.InstallerError, "Estado da instalação inválido"):
                installer.load_install_state(persist_migration=False)

    def test_install_state_loader_preserves_specific_validation_message(self):
        """Moving state parsing must not collapse an actionable field error."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            state_path = target / install_qw.INSTALL_STATE
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({
                "format": 2,
                "project": "x86qw",
                "profile": "custom",
                "requested_components": ["invalid component"],
                "recorded_components": [],
                "known_components": [],
                "capabilities": [],
                "component_fingerprint": install_qw.profile_fingerprint([]),
            }), encoding="utf-8")

            with self.assertRaises(install_qw.InstallerError) as raised:
                installer.load_install_state(persist_migration=False)

            self.assertEqual(
                str(raised.exception),
                f"Campo requested_components inválido no estado da instalação: {state_path}",
            )

    def test_cli_receipt_loader_rejects_oversized_valid_json(self):
        """CLI metadata must use the same bounded reader as state and TSV receipts."""

        self.assertTrue(
            hasattr(runtime_receipts, "MAX_RECEIPT_BYTES"),
            "runtime receipts must publish their persisted read limit",
        )
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            receipt = target / install_qw.CLI_RECEIPT
            receipt.parent.mkdir(parents=True)
            payload = json.dumps({
                "format": 1,
                "project": "x86qw",
                "version": "0.7.1",
            }).encode("utf-8")
            receipt.write_bytes(
                payload + b" " * (runtime_receipts.MAX_RECEIPT_BYTES + 1)
            )

            with self.assertRaisesRegex(install_qw.InstallerError, "Recibo da CLI"):
                installer.validate_cli_receipt(receipt)

    def test_cli_receipt_loader_preserves_invalid_version_message(self):
        """Typed CLI receipts must keep the historical actionable version error."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            receipt = target / install_qw.CLI_RECEIPT
            receipt.parent.mkdir(parents=True)
            receipt.write_text(json.dumps({
                "format": 1,
                "project": "x86qw",
                "version": "nightly",
            }), encoding="utf-8")

            with self.assertRaises(install_qw.InstallerError) as raised:
                installer.validate_cli_receipt(receipt)

            self.assertEqual(
                str(raised.exception),
                "Versão inválida no recibo da CLI x86QW: nightly",
            )

    def test_cli_receipt_writer_is_canonical_and_preserves_previous_on_fsync_failure(self):
        """CLI publication must use its runtime codec through the atomic writer."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            receipt = target / install_qw.CLI_RECEIPT
            receipt.parent.mkdir(parents=True)
            self.assertTrue(
                hasattr(installer, "write_cli_receipt_record"),
                "manager must expose one canonical CLI receipt writer",
            )
            installer.write_cli_receipt_record(receipt, {
                "format": 1,
                "project": "x86qw",
                "version": "0.7.1",
            })
            canonical = (
                b'{\n  "format": 1,\n  "project": "x86qw",\n'
                b'  "version": "0.7.1"\n}\n'
            )
            self.assertEqual(receipt.read_bytes(), canonical)

            with mock.patch.object(
                atomic_io.os,
                "fsync",
                side_effect=OSError("injected CLI receipt fsync failure"),
            ):
                with self.assertRaises(install_qw.InstallerError):
                    installer.write_cli_receipt_record(receipt, {
                        "format": 1,
                        "project": "x86qw",
                        "version": "0.7.2",
                    })

            self.assertEqual(receipt.read_bytes(), canonical)

    def test_ezquake_receipt_loader_rejects_oversized_valid_tsv(self):
        """A huge URL query must not turn a client receipt into unbounded input."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["linux"]
            receipt_path = target / spec.receipt("stable")
            receipt_path.parent.mkdir(parents=True)
            installer.write_ezquake_receipt_record(receipt_path, {
                "format": "1",
                "platform": "linux",
                "architecture": "x86_64",
                "channel": "stable",
                "selection": "3.6.9",
                "install_name": spec.runtime("stable"),
                "bundle_version": "3.6.9",
                "artifact_name": spec.stable_archive,
                "artifact_url": (
                    f"https://example.invalid/{spec.stable_archive}?padding="
                    + "x" * runtime_receipts.MAX_RECEIPT_BYTES
                ),
                "artifact_sha256": "a" * 64,
                "binary_sha256": "b" * 64,
            })

            with self.assertRaises(install_qw.InstallerError):
                installer.validate_ezquake_receipt(receipt_path, spec, "stable")

    def test_ezquake_receipt_loader_preserves_platform_error_message(self):
        """Typed ezQuake receipts must not hide which persisted identity diverged."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["linux"]
            receipt_path, _, _ = self.write_ezquake_fixture(
                installer, target, spec, "stable",
            )
            receipt_path.write_bytes(
                receipt_path.read_bytes().replace(
                    b"platform\tlinux\n", b"platform\twindows\n",
                )
            )

            with self.assertRaises(install_qw.InstallerError) as raised:
                installer.validate_ezquake_receipt(receipt_path, spec, "stable")

            self.assertEqual(
                str(raised.exception),
                f"invalid platform metadata in ezQuake receipt: {receipt_path}",
            )

    def test_component_receipt_loader_rejects_oversized_valid_tsv(self):
        """A component source field must not permit unbounded receipt reads."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            receipt, inventory = (
                target / relative
                for relative in installer.component_metadata("ktx")
            )
            receipt.parent.mkdir(parents=True)
            inventory.write_text(
                "qw/ktx.pk3\t" + "a" * 64 + "\n",
                encoding="utf-8",
            )
            installer.write_component_receipt(
                "ktx",
                "1.47+x86qw.18",
                "x" * runtime_receipts.MAX_RECEIPT_BYTES,
                inventory,
                receipt,
            )

            with self.assertRaises(install_qw.InstallerError):
                installer.validate_component_paths("ktx", receipt, inventory)

    def test_component_receipt_loader_preserves_selection_error_message(self):
        """Typed component receipts must retain the field-specific diagnosis."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            receipt, inventory = (
                target / relative
                for relative in installer.component_metadata("ktx")
            )
            receipt.parent.mkdir(parents=True)
            inventory.write_text(
                "qw/ktx.pk3\t" + "a" * 64 + "\n", encoding="utf-8",
            )
            receipt.write_text(
                "format\t1\n"
                "component\tktx\n"
                "selection\t\n"
                "source\thttps://example.invalid/ktx.zip\n"
                f"inventory_sha256\t{install_qw.file_hash(inventory)}\n",
                encoding="utf-8",
            )

            with self.assertRaises(install_qw.InstallerError) as raised:
                installer.validate_component_paths("ktx", receipt, inventory)

            self.assertEqual(
                str(raised.exception),
                "Seleção inválida no recibo do componente ktx.",
            )

    def test_inventory_loader_preserves_the_invalid_entry(self):
        """A malformed inventory must identify the offending persisted row."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            inventory = target / ".x86qw/components/ktx/inventory"
            inventory.parent.mkdir(parents=True)
            inventory.write_text("linha-sem-hash\n", encoding="utf-8")

            with self.assertRaises(install_qw.InstallerError) as raised:
                installer.validate_inventory(inventory)

            self.assertEqual(
                str(raised.exception),
                "invalid managed inventory entry: linha-sem-hash",
            )

    def test_legacy_nquake_receipt_preserves_format_error_message(self):
        """The one-way migration must keep its historical format diagnosis."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            receipt = target / ".x86qw/receipt"
            receipt.parent.mkdir(parents=True)
            receipt.write_text(
                "format\t2\n"
                f"distfiles_commit\t{'a' * 40}\n"
                f"inventory_sha256\t{'b' * 64}\n",
                encoding="utf-8",
            )

            with self.assertRaises(install_qw.InstallerError) as raised:
                installer.validate_nquake_receipt(receipt)

            self.assertEqual(str(raised.exception), "unsupported receipt format: 2")

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

    def test_repair_detects_missing_macos_nightly_preparation_without_replacing_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            _, receipt, runtime = self.write_ezquake_fixture(installer, target, spec, "nightly")
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

    @unittest.skipIf(os.name == "nt", "permissão executável usa bits POSIX")
    def test_repair_does_not_apply_local_changes_when_required_payload_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["linux"]
            receipt_path, receipt, runtime = self.write_ezquake_fixture(
                installer, target, spec, "stable", payload=b"runtime\n",
            )
            runtime.chmod(0o600)
            permission = install_qw.ClientRepairIssue(
                spec, "stable", receipt_path, receipt,
                "sem permissão de execução", "permission", None, "local-repair",
            )
            payload = install_qw.ClientRepairIssue(
                spec, "stable", receipt_path, receipt,
                "payload ausente", "payload", None, "payload-required",
            )
            assessment = install_qw.RepairAssessment(
                (), False, (), False, (permission, payload), (),
            )
            with mock.patch.object(installer, "repair_plan", return_value=assessment):
                with self.assertRaisesRegex(
                    install_qw.InstallerError, "reexecute o bootstrap",
                ):
                    installer.repair(
                        dry_run=False, plan_rows=[], allow_download=False,
                    )
            self.assertEqual(0o600, runtime.stat().st_mode & 0o777)

    @unittest.skipIf(os.name == "nt", "permissão executável usa bits POSIX")
    def test_repair_rolls_back_local_permission_when_state_commit_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            runtime = target / "mvdsv"
            runtime.write_bytes(b"runtime\n")
            runtime.chmod(0o600)
            assessment = install_qw.RepairAssessment(
                (), False, (runtime,), False, (), (),
            )
            state = {
                "profile": "none", "requested_components": [],
                "known_components": [], "capabilities": [],
            }
            with mock.patch.object(installer, "repair_plan", return_value=assessment):
                with mock.patch.object(installer, "load_install_state", return_value=state):
                    with mock.patch.object(
                        installer, "write_install_state",
                        side_effect=install_qw.PersistenceError(
                            "simulated state failure", committed=False,
                        ),
                    ):
                        with self.assertRaises(install_qw.PersistenceError):
                            installer.repair(
                                dry_run=False, plan_rows=[], allow_download=False,
                            )
            self.assertEqual(0o600, runtime.stat().st_mode & 0o777)

    @unittest.skipIf(os.name == "nt", "permissão executável usa bits POSIX")
    def test_repair_keeps_permission_and_state_inverses_through_final_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            runtime = target / "mvdsv"
            runtime.write_bytes(b"runtime\n")
            runtime.chmod(0o600)
            with mock.patch.object(installer, "installed_components", return_value=[]):
                installer.write_install_state("none", [], known=[])
            state_path = target / install_qw.INSTALL_STATE
            original_state = state_path.read_bytes()
            assessment = install_qw.RepairAssessment(
                (), False, (runtime,), False, (), (),
            )
            with mock.patch.object(installer, "repair_plan", return_value=assessment):
                with mock.patch.object(
                    installer, "verify_installation",
                    side_effect=install_qw.InstallerError(
                        "simulated final verification failure"
                    ),
                ):
                    with self.assertRaisesRegex(
                        install_qw.InstallerError, "verification failure",
                    ):
                        installer.repair(
                            dry_run=False, plan_rows=[], allow_download=False,
                        )
            self.assertEqual(0o600, runtime.stat().st_mode & 0o777)
            self.assertEqual(original_state, state_path.read_bytes())

    def test_offline_macos_nightly_preparation_repair_completes_without_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            spec = install_qw.PLATFORMS["macos"]
            receipt_path, receipt, runtime = self.write_ezquake_fixture(
                installer, target, spec, "nightly",
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
            prepare.assert_called_once_with(
                spec,
                "nightly",
                receipt_path,
                receipt,
                mutation_results=mock.ANY,
            )

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

    def test_repair_keeps_component_stage_until_state_outcome(self):
        for committed in (False, True):
            with self.subTest(committed=committed), tempfile.TemporaryDirectory() as temporary:
                installer, target, _ = self.make_installer(Path(temporary))
                installed: list[str] = []
                component_stages: list[Path] = []
                state_stages: list[Path] = []
                marker, install_components = self.stage_backed_component_install(
                    installer, target, installed, component_stages,
                )
                assessment = install_qw.RepairAssessment(
                    ("ktx",), False, (), False, (), (),
                )
                state = {
                    "format": 2,
                    "project": "x86qw",
                    "profile": "custom",
                    "requested_components": ["ktx"],
                    "recorded_components": [],
                    "known_components": list(installer.components),
                    "capabilities": [],
                    "component_fingerprint": install_qw.profile_fingerprint([]),
                }

                with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(
                    installer, "repair_plan", return_value=assessment,
                ), mock.patch.object(
                    installer, "validate_component_pair",
                    return_value=(True, [], {"selection": "old"}),
                ), mock.patch.object(
                    installer, "component_package_record", return_value={"version": "new"},
                ), mock.patch.object(
                    installer, "install_components", side_effect=install_components,
                ), mock.patch.object(
                    installer, "installed_components", side_effect=lambda: list(installed),
                ), mock.patch.object(
                    installer, "load_install_state", return_value=state,
                ), mock.patch.object(
                    installer, "write_install_state",
                    side_effect=self.state_failure_after_optional_commit(
                        installer, committed, state_stages,
                    ),
                ), mock.patch.object(
                    installer, "verify_installation",
                ):
                    with self.assertRaises(install_qw.PersistenceError):
                        installer.repair(dry_run=False, plan_rows=[])

                self.assertEqual([component_stages[0]], state_stages)
                self.assertFalse(component_stages[0].exists())
                self.assertIsNone(installer.stage)
                expected = "new\n" if committed else "old\n"
                self.assertEqual(expected, marker.read_text(encoding="utf-8"))
                self.assertEqual(
                    committed, (target / install_qw.INSTALL_STATE).is_file(),
                )
                self.assertEqual(["ktx"] if committed else [], installed)

    def test_repair_keeps_client_stage_until_state_outcome(self):
        for committed in (False, True):
            with self.subTest(committed=committed), tempfile.TemporaryDirectory() as temporary:
                installer, target, _ = self.make_installer(Path(temporary))
                spec = install_qw.PLATFORMS["linux"]
                receipt_path, old_receipt, runtime = self.write_ezquake_fixture(
                    installer, target, spec, "stable", payload=b"old-runtime\n",
                )
                old_receipt_bytes = receipt_path.read_bytes()
                selected = (
                    "3.6.10",
                    (f"https://example.invalid/{spec.stable_archive}",),
                    "c" * 64,
                )
                issue = install_qw.ClientRepairIssue(
                    spec, "stable", receipt_path, old_receipt,
                    "runtime divergente", "payload", selected,
                )
                assessment = install_qw.RepairAssessment(
                    (), False, (), False, (issue,), (),
                )
                state = {
                    "format": 2,
                    "project": "x86qw",
                    "profile": "none",
                    "requested_components": [],
                    "recorded_components": [],
                    "known_components": list(installer.components),
                    "capabilities": [],
                    "component_fingerprint": install_qw.profile_fingerprint([]),
                }
                state_stages: list[Path] = []

                def prepare_runtime(_archive):
                    self.assertIsNotNone(installer.stage)
                    assert installer.stage is not None
                    prepared = installer.stage / "prepared-runtime"
                    prepared.write_bytes(b"new-runtime\n")
                    installer.app_archive_sha256 = "c" * 64
                    installer.app_bundle_version = "3.6.10"
                    installer.app_binary_sha256 = install_qw.file_hash(prepared)
                    return prepared

                with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(
                    installer, "repair_plan", return_value=assessment,
                ), mock.patch.object(
                    installer, "ensure_macos_ezquake_closed",
                ), mock.patch.object(
                    installer, "prepare_cache",
                ), mock.patch.object(
                    installer, "ensure_archive", return_value=target / "archive",
                ), mock.patch.object(
                    installer, "prepare_runtime", side_effect=prepare_runtime,
                ), mock.patch.object(
                    installer, "installed_components", return_value=[],
                ), mock.patch.object(
                    installer, "load_install_state", return_value=state,
                ), mock.patch.object(
                    installer, "write_install_state",
                    side_effect=self.state_failure_after_optional_commit(
                        installer, committed, state_stages,
                    ),
                ), mock.patch.object(
                    installer, "verify_installation",
                ):
                    with self.assertRaises(install_qw.PersistenceError):
                        installer.repair(dry_run=False, plan_rows=[])

                self.assertEqual(1, len(state_stages))
                self.assertFalse(state_stages[0].exists())
                self.assertIsNone(installer.stage)
                if committed:
                    self.assertEqual(b"new-runtime\n", runtime.read_bytes())
                    persisted = installer.validate_ezquake_receipt(
                        receipt_path, spec, "stable",
                    )
                    self.assertEqual("3.6.10", persisted["selection"])
                    self.assertTrue((target / install_qw.INSTALL_STATE).is_file())
                else:
                    self.assertEqual(b"old-runtime\n", runtime.read_bytes())
                    self.assertEqual(old_receipt_bytes, receipt_path.read_bytes())
                    self.assertFalse((target / install_qw.INSTALL_STATE).exists())

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
                return ()

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

    def test_upgrade_rolls_back_updates_when_new_profile_component_fails(self):
        """A profile failure must not leave the preceding update published."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            marker = target / "qw/update-generation.txt"
            marker.parent.mkdir(parents=True)
            marker.write_bytes(b"old\n")
            state = {
                "format": 2,
                "project": "x86qw",
                "profile": "essential",
                "requested_components": [],
                "recorded_components": [],
                "known_components": list(installer.components),
                "capabilities": [],
                "component_fingerprint": install_qw.profile_fingerprint([]),
            }

            def update(*, mutation_results=None, **_options):
                old_payload = marker.read_bytes()
                plan = install_qw.MutationPlan(
                    identifier="test:upgrade-parent",
                    summary="publish an existing component update",
                    steps=(install_qw.MutationStep(
                        key="payload",
                        description="replace the existing payload",
                        observe=marker.read_bytes,
                        apply=lambda: marker.write_bytes(b"new\n"),
                        rollback=lambda _token: marker.write_bytes(old_payload),
                    ),),
                )
                result = install_qw.execute_mutation(
                    install_qw.prepare_mutation(plan),
                )
                if mutation_results is not None:
                    mutation_results.append(result)
                return True

            with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(
                installer, "update", side_effect=update,
            ), mock.patch.object(
                installer, "load_install_state", return_value=state,
            ), mock.patch.object(
                installer, "current_install_state", return_value=state,
            ), mock.patch.object(
                installer, "desired_components", return_value=["ktx"],
            ), mock.patch.object(
                installer, "installed_components", return_value=[],
            ), mock.patch.object(
                installer, "installed_legacy_component_replacements", return_value={},
            ), mock.patch.object(
                installer, "install_component_batch",
                side_effect=install_qw.InstallerError("profile component failed"),
            ):
                with self.assertRaisesRegex(
                    install_qw.InstallerError, "profile component failed",
                ):
                    installer.upgrade()

            self.assertEqual(b"old\n", marker.read_bytes())

    def test_upgrade_keeps_component_stage_until_state_outcome(self):
        for committed in (False, True):
            with self.subTest(committed=committed), tempfile.TemporaryDirectory() as temporary:
                installer, target, _ = self.make_installer(Path(temporary))
                installed: list[str] = []
                component_stages: list[Path] = []
                state_stages: list[Path] = []
                marker, install_components = self.stage_backed_component_install(
                    installer, target, installed, component_stages,
                )
                state = {
                    "format": 2,
                    "project": "x86qw",
                    "profile": "essential",
                    "requested_components": [],
                    "recorded_components": [],
                    "known_components": list(installer.components),
                    "capabilities": [],
                    "component_fingerprint": install_qw.profile_fingerprint([]),
                }

                with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(
                    installer, "update", return_value=False,
                ), mock.patch.object(
                    installer, "load_install_state", return_value=state,
                ), mock.patch.object(
                    installer, "current_install_state", return_value=state,
                ), mock.patch.object(
                    installer, "desired_components", return_value=["ktx"],
                ), mock.patch.object(
                    installer, "installed_components", side_effect=lambda: list(installed),
                ), mock.patch.object(
                    installer, "installed_legacy_component_replacements", return_value={},
                ), mock.patch.object(
                    installer, "install_components", side_effect=install_components,
                ), mock.patch.object(
                    installer, "write_install_state",
                    side_effect=self.state_failure_after_optional_commit(
                        installer, committed, state_stages,
                    ),
                ), mock.patch.object(
                    installer, "verify_installation",
                ):
                    with self.assertRaises(install_qw.PersistenceError):
                        installer.upgrade()

                self.assertEqual([component_stages[0]], state_stages)
                self.assertFalse(component_stages[0].exists())
                self.assertIsNone(installer.stage)
                expected = "new\n" if committed else "old\n"
                self.assertEqual(expected, marker.read_text(encoding="utf-8"))
                self.assertEqual(
                    committed, (target / install_qw.INSTALL_STATE).is_file(),
                )
                self.assertEqual(["ktx"] if committed else [], installed)

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
            before_target = [
                (
                    path.relative_to(target).as_posix(),
                    path.is_dir(),
                    b"" if path.is_dir() else path.read_bytes(),
                )
                for path in sorted(target.rglob("*"))
            ]
            archive = root / "x86qw-installer-1.0.5.zip"
            self.write_installer_bundle(archive, "1.0.5")
            record = {"version": "1.0.5"}
            completed = subprocess.CompletedProcess([], 0)
            observed_stages: list[Path] = []

            def provide_archive(_package):
                assert installer.stage is not None
                observed_stages.append(installer.stage)
                return archive

            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "installer_bundle_record", return_value=record):
                    with mock.patch.object(
                        installer, "download_component_package", side_effect=provide_archive,
                    ):
                        with mock.patch.object(install_qw.subprocess, "run", return_value=completed) as run:
                            self.assertTrue(installer.handoff_cli_update(
                                "upgrade", dry_run=False, assume_yes=True,
                            ))
            command = run.call_args.args[0]
            self.assertEqual(sys.executable, command[0])
            self.assertIn("--installed-cli", command)
            self.assertIn("--skip-cli-update", command)
            self.assertIn("--yes", command)
            self.assertEqual(["upgrade", str(target)], command[-2:])
            self.assertNotIn("shell", run.call_args.kwargs)
            self.assertTrue(all(target not in path.parents for path in observed_stages))
            self.assertEqual(before_target, [
                (
                    path.relative_to(target).as_posix(),
                    path.is_dir(),
                    b"" if path.is_dir() else path.read_bytes(),
                )
                for path in sorted(target.rglob("*"))
            ])
            self.assertFalse(any(target.glob(".x86qw-update.*")))

    def test_cli_update_fails_before_download_when_private_stage_cannot_be_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, target, _ = self.make_installer(root)
            with mock.patch.object(
                installer, "installer_bundle_record", return_value={"version": "1.0.5"},
            ), mock.patch.object(
                installer, "installed_cli_version", return_value="1.0.4",
            ), mock.patch.object(
                install_qw.private_fs, "private_mkdtemp",
                side_effect=install_qw.private_fs.PrivateFilesystemError("ACL indisponível"),
            ), mock.patch.object(
                installer, "download_component_package",
                side_effect=AssertionError("download iniciou sem stage privado"),
            ) as download, mock.patch.object(install_qw.subprocess, "run") as run:
                with self.assertRaisesRegex(install_qw.InstallerError, "área privada"):
                    installer.handoff_cli_update("update", dry_run=False)
            download.assert_not_called()
            run.assert_not_called()
            self.assertFalse((target / ".x86qw").exists())
            self.assertFalse(any(target.glob(".x86qw-update.*")))

    def test_cli_update_cleanup_preserves_a_replacement_of_the_stage_path(self):
        class StopHandoff(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, target, _ = self.make_installer(root)
            self.write_cli_receipt(target, "1.0.4")
            preserved_stage = target / "preserved-original-stage"
            personal_file: Path | None = None

            def replace_stage(_package):
                nonlocal personal_file
                assert installer.stage is not None
                lease = installer._stage_lease
                installer._stage_lease = None
                if lease is not None:
                    lease.close()
                installer.stage.rename(preserved_stage)
                installer.stage.mkdir()
                personal_file = installer.stage / "personal.txt"
                personal_file.write_text("preservar\n", encoding="utf-8")
                raise StopHandoff("interromper depois da troca")

            try:
                with mock.patch.object(
                    installer, "installer_bundle_record", return_value={"version": "1.0.5"},
                ), mock.patch.object(
                    installer, "download_component_package", side_effect=replace_stage,
                ):
                    with self.assertRaisesRegex(
                        install_qw.InstallerError, "mudou de identidade",
                    ):
                        installer.handoff_cli_update("update", dry_run=False)
                assert personal_file is not None
                self.assertTrue(personal_file.is_file())
                self.assertTrue(preserved_stage.is_dir())
            finally:
                if installer.stage is not None and install_qw.lexists(installer.stage):
                    install_qw.remove_path(installer.stage)
                if install_qw.lexists(preserved_stage):
                    install_qw.remove_path(preserved_stage)

    def test_cli_update_rejects_bundle_missing_required_member_without_handoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, target, _ = self.make_installer(root)
            self.write_cli_receipt(target, "1.0.4")
            archive = root / "x86qw-installer-1.0.5.zip"
            self.write_installer_bundle(archive, "1.0.5", omit="VERSION")
            record = {"version": "1.0.5"}
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "installer_bundle_record", return_value=record):
                    with mock.patch.object(installer, "download_component_package", return_value=archive):
                        with mock.patch.object(
                            install_qw.subprocess,
                            "run",
                            return_value=subprocess.CompletedProcess([], 0),
                        ) as run:
                            with self.assertRaises(install_qw.InstallerError):
                                installer.handoff_cli_update("upgrade", dry_run=False)
            run.assert_not_called()
            self.assertFalse(any(target.glob(".x86qw-update.*")))

    def test_cli_update_rejects_bundle_with_extra_member_without_handoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installer, target, _ = self.make_installer(root)
            self.write_cli_receipt(target, "1.0.4")
            archive = root / "x86qw-installer-1.0.5.zip"
            self.write_installer_bundle(archive, "1.0.5", extra_member="unexpected.txt")
            record = {"version": "1.0.5"}
            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(installer, "installer_bundle_record", return_value=record):
                    with mock.patch.object(installer, "download_component_package", return_value=archive):
                        with mock.patch.object(
                            install_qw.subprocess,
                            "run",
                            return_value=subprocess.CompletedProcess([], 0),
                        ) as run:
                            with self.assertRaises(install_qw.InstallerError):
                                installer.handoff_cli_update("upgrade", dry_run=False)
            run.assert_not_called()
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
                return bounded_downloader.errno.EINPROGRESS

            def getsockopt(self, level, option):
                del level, option
                return 0 if self.reachable else bounded_downloader.errno.EHOSTUNREACH

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
        with mock.patch.object(bounded_downloader, "_resolve_addresses", return_value=candidates):
            with mock.patch.object(bounded_downloader.socket, "socket", side_effect=sockets):
                with mock.patch.object(bounded_downloader.selectors, "DefaultSelector", FakeSelector):
                    connection = bounded_downloader.create_resilient_connection(
                        ("example.invalid", 443), timeout=2,
                    )
        self.assertIs(sockets[1], connection)
        self.assertTrue(sockets[0].closed)
        self.assertFalse(sockets[1].closed)
        self.assertGreater(sockets[1].timeout, 0)
        self.assertLessEqual(sockets[1].timeout, 2)

    def test_resilient_connection_limits_dns_resolution_time(self):
        started = time.monotonic()
        with mock.patch.object(
            bounded_downloader,
            "_resolve_addresses",
            side_effect=TimeoutError("Tempo esgotado ao resolver example.invalid."),
        ):
            with self.assertRaisesRegex(TimeoutError, "resolver example.invalid"):
                bounded_downloader.create_resilient_connection(
                    ("example.invalid", 443), timeout=0.01,
                )
        self.assertLess(time.monotonic() - started, 0.5)

    def test_public_catalog_falls_back_to_the_next_mirror(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            installer.online_only = True
            catalog = {"format": 1, "project": "x86qw", "packages": []}
            payload = json.dumps(catalog).encode()

            def fallback(contracts, **options):
                self.assertEqual(install_qw.CATALOG_URLS, tuple(item.url for item in contracts))
                options["on_mirror_failure"](
                    1, contracts[0], install_qw.DownloadError("timeout"),
                )
                return mock.Mock(data=payload)

            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(
                    installer.remote, "download_many", side_effect=fallback,
                ) as get:
                    self.assertEqual(catalog, installer.public_catalog("remote"))
            get.assert_called_once()

    def test_http_get_preserves_the_github_rate_limit_diagnostic(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            error = install_qw.DownloadHTTPError(
                403,
                "O servidor respondeu HTTP 403.",
                {"x-ratelimit-remaining": "0"},
            )
            with mock.patch.object(
                installer.remote, "download_one", side_effect=error,
            ), self.assertRaisesRegex(
                install_qw.InstallerError, "limite temporário de consultas do GitHub"
            ):
                installer.remote.get(
                    "https://github.com/example/catalog.json",
                    maximum_size=1024,
                    attempts=1,
                )

    def test_http_get_redacts_query_secrets_from_verbose_and_errors(self):
        sentinel = "X86QW_URL_SECRET_SENTINEL"
        url = f"https://example.invalid/catalog.json?token={sentinel}"
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            with mock.patch.object(
                installer.remote, "download_one",
                side_effect=install_qw.DownloadError("falha controlada"),
            ) as download, mock.patch.object(install_qw.console, "detail") as detail:
                with self.assertRaises(install_qw.InstallerError) as raised:
                    installer.remote.get(url, maximum_size=1024, attempts=1)

        self.assertEqual(url, download.call_args.args[0].url)
        diagnostics = [str(call.args[0]) for call in detail.call_args_list]
        self.assertTrue(any("/<redigido>" in message for message in diagnostics))
        self.assertNotIn(sentinel, "\n".join(diagnostics))
        self.assertNotIn(sentinel, str(raised.exception))

    def test_http_get_rejects_control_injected_url_before_detail_or_transport(self):
        sentinel = "X86QW_URL_SECRET_SENTINEL"
        url = f"https://example.invalid/catalog.json?token={sentinel}\n[ERRO] forged"
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            with mock.patch.object(installer.remote, "download_one") as download, mock.patch.object(
                install_qw.console, "detail",
            ) as detail, self.assertRaises(install_qw.InstallerError) as raised:
                installer.remote.get(url, maximum_size=1024, attempts=1)

        download.assert_not_called()
        detail.assert_not_called()
        self.assertNotIn(sentinel, str(raised.exception))
        self.assertNotIn("\n", str(raised.exception))

    def test_http_get_mirrors_validates_every_url_before_detail_or_transport(self):
        sentinel = "X86QW_URL_SECRET_SENTINEL"
        urls = (
            "https://first.example.invalid/catalog.json",
            f"https://second.example.invalid/catalog.json?token={sentinel}\nforged",
        )
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, _ = self.make_installer(Path(temporary))
            with mock.patch.object(
                installer.remote, "download_many",
            ) as download, mock.patch.object(
                install_qw.console, "detail",
            ) as detail, self.assertRaises(install_qw.InstallerError) as raised:
                installer.remote.get_mirrors(urls, maximum_size=1024, attempts=1)

        download.assert_not_called()
        detail.assert_not_called()
        self.assertNotIn(sentinel, str(raised.exception))

    def test_client_download_falls_back_to_the_next_mirror(self):
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
            installer.app_expected_size = len(payload)
            installer.app_urls = (
                f"https://first.invalid/{filename}",
                f"https://second.invalid/{filename}",
            )
            installer.app_url = installer.app_urls[0]

            def fallback(contracts, **options):
                self.assertEqual(installer.app_urls, tuple(item.url for item in contracts))
                self.assertTrue(all(item.expected_size == len(payload) for item in contracts))
                self.assertTrue(all(
                    item.expected_sha256 == installer.app_expected_checksum
                    for item in contracts
                ))
                self.assertTrue(all(
                    item.maximum_size == install_qw.MAX_ARTIFACT_BYTES
                    for item in contracts
                ))
                options["on_mirror_failure"](
                    1, contracts[0], install_qw.DownloadError("first mirror unavailable"),
                )
                contracts[1].destination.write_bytes(payload)
                return mock.Mock(data=None)

            with contextlib.redirect_stdout(io.StringIO()):
                with mock.patch.object(
                    installer.remote, "download_many", side_effect=fallback,
                ):
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
                with mock.patch.object(
                    installer.remote, "get_mirrors", side_effect=AssertionError("network used"),
                ):
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

    def test_cleanup_entrypoint_refuses_an_unowned_target(self):
        """A fixed cache-looking path is not evidence that x86QW owns a directory."""

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "foreign"
            victim = target / "ezquake/temp/personal.bin"
            victim.parent.mkdir(parents=True)
            victim.write_bytes(b"only copy")
            options = install_qw.parse_arguments(
                ["--online-only", "cleanup", str(target)], ROOT,
            )

            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    install_qw.InstallerError, "identidade gerenciada",
                ):
                    install_qw.execute_manager_action(options, ROOT)

            self.assertEqual(b"only copy", victim.read_bytes())

    def test_uninstall_entrypoint_refuses_a_launcher_without_receipt(self):
        """A personal script named x86qw.sh must never become managed by inference."""

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "foreign"
            target.mkdir()
            launcher = target / "x86qw.sh"
            launcher.write_bytes(b"personal launcher")
            options = install_qw.parse_arguments(
                ["--online-only", "uninstall", str(target)], ROOT,
            )

            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    install_qw.InstallerError, "identidade gerenciada",
                ):
                    install_qw.execute_manager_action(options, ROOT)

            self.assertEqual(b"personal launcher", launcher.read_bytes())

    def test_purge_entrypoint_refuses_metadata_created_only_by_its_lock(self):
        """The operation lock cannot manufacture authority to erase its target."""

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "foreign"
            target.mkdir()
            sentinel = target / "only-copy-of-personal-data.txt"
            sentinel.write_bytes(b"only copy")
            options = install_qw.parse_arguments(
                ["--online-only", "uninstall", str(target), "--purge"], ROOT,
            )

            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    install_qw.InstallerError, "identidade gerenciada",
                ):
                    install_qw.execute_manager_action(options, ROOT)

            self.assertEqual(b"only copy", sentinel.read_bytes())
            self.assertTrue(target.is_dir())

    def test_purge_entrypoint_refuses_malformed_identity_metadata(self):
        """Malformed state is uncertainty, never authority for destructive cleanup."""

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "foreign"
            state = target / install_qw.INSTALL_STATE
            state.parent.mkdir(parents=True)
            state.write_text("not-json\n", encoding="utf-8")
            sentinel = target / "personal.txt"
            sentinel.write_bytes(b"only copy")
            options = install_qw.parse_arguments(
                ["--online-only", "uninstall", str(target), "--purge"], ROOT,
            )

            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(install_qw.InstallerError):
                    install_qw.execute_manager_action(options, ROOT)

            self.assertEqual(b"only copy", sentinel.read_bytes())
            self.assertEqual("not-json\n", state.read_text(encoding="utf-8"))

    def test_purge_removes_the_entire_installation_and_owned_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, cache = self.make_installer(Path(temporary))
            (target / ".x86qw").mkdir()
            self.write_cli_receipt(target, "1.0.5")
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

    def test_purge_rolls_back_installation_when_a_later_domain_fails(self):
        """Target and external cache must form one reversible purge transaction."""

        quarantine = importlib.import_module("x86qw_runtime.io.quarantine")
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, cache = self.make_installer(Path(temporary))
            (target / ".x86qw").mkdir()
            self.write_cli_receipt(target, "1.0.5")
            personal = target / "personal.txt"
            personal.write_bytes(b"personal")
            installer.prepare_cache()
            cached = cache / "artifact"
            cached.write_bytes(b"cache")

            apply_quarantine = quarantine.apply_quarantine_removal
            attempts = 0

            def fail_second(path):
                nonlocal attempts
                attempts += 1
                if attempts == 2:
                    raise OSError("simulated cache quarantine failure")
                return apply_quarantine(path)

            with mock.patch.object(
                quarantine,
                "apply_quarantine_removal",
                side_effect=fail_second,
            ):
                with self.assertRaises(install_qw.InstallerError):
                    installer.purge()

            self.assertEqual(personal.read_bytes(), b"personal")
            self.assertEqual(cached.read_bytes(), b"cache")
            self.assertFalse(tuple(target.parent.glob(".x86qw-*-quarantine.*")))
            self.assertFalse(tuple(cache.parent.glob(".x86qw-*-quarantine.*")))

    def test_purge_keeps_operation_lock_until_final_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            (target / ".x86qw").mkdir()
            self.write_cli_receipt(target, "1.0.5")
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
                launcher = (target / "x86qw.cmd").read_text(encoding="utf-8")
                self.assertIn('"%X86QW_ACTION%"=="uninstall"', launcher)
                self.assertIn('if /I not "%~2"=="--help"', launcher)
                self.assertIn('del "%~f0"', launcher)
            else:
                self.assertFalse((target / "x86qw.cmd").exists())
            self.assertFalse((target / ".x86qw/cli").exists())
            self.assertEqual(b"preserve", (target / "id1/pak0.pak").read_bytes())

    def test_uninstall_rolls_back_cli_generation_when_a_later_removal_fails(self):
        """Uninstall must either remove its complete selection or restore all of it."""

        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            application = target / ".x86qw/cli/x86qw.pyz"
            application.parent.mkdir(parents=True)
            application.write_bytes(b"installed-cli")
            self.write_cli_receipt(target, "1.0.5")
            launcher = target / "x86qw.sh"
            launcher.write_bytes(b"#!/bin/sh\n")

            receipt = target / install_qw.CLI_RECEIPT
            before = {
                application: application.read_bytes(),
                receipt: receipt.read_bytes(),
                launcher: launcher.read_bytes(),
            }
            apply_removal = installer._apply_managed_path_removal
            attempts = 0

            def fail_second(destination, *, label):
                nonlocal attempts
                attempts += 1
                if attempts == 2:
                    raise OSError("simulated uninstall removal failure")
                return apply_removal(destination, label=label)

            with mock.patch.object(
                installer,
                "_apply_managed_path_removal",
                side_effect=fail_second,
            ):
                with self.assertRaises(install_qw.InstallerError):
                    installer.uninstall()

            for path, payload in before.items():
                self.assertEqual(payload, path.read_bytes(), path)
            self.assertFalse((target / install_qw.METADATA_DIR / "staging").exists())

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
            with self.assertRaisesRegex(
                install_qw.InstallerError, "sem identidade gerenciada x86QW",
            ):
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

    def test_cleanup_restores_owned_cache_when_runtime_cleanup_domain_fails(self):
        """Native cache and installed data must share one cleanup transaction."""

        self.assertTrue(
            hasattr(install_qw.Installer, "cleanup_data"),
            "cleanup needs one cross-filesystem transaction entrypoint",
        )
        quarantine = importlib.import_module("x86qw_runtime.io.quarantine")
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, cache = self.make_installer(Path(temporary))
            installer.prepare_cache()
            cached = cache / "artifact"
            cached.write_bytes(b"cache")
            runtime_cache = target / "ezquake/temp/download"
            runtime_cache.parent.mkdir(parents=True)
            runtime_cache.write_bytes(b"runtime")

            apply_quarantine = quarantine.apply_quarantine_removal
            attempts = 0

            def fail_second(path):
                nonlocal attempts
                attempts += 1
                if attempts == 2:
                    raise OSError("simulated runtime cleanup failure")
                return apply_quarantine(path)

            with mock.patch.object(
                quarantine,
                "apply_quarantine_removal",
                side_effect=fail_second,
            ):
                with self.assertRaises(install_qw.InstallerError):
                    installer.cleanup_data(downloads=False, personal_data=False)

            self.assertEqual(cached.read_bytes(), b"cache")
            self.assertEqual(runtime_cache.read_bytes(), b"runtime")

    def test_zip_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad.zip"
            destination = root / "output"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("../escape", b"bad")
            with self.assertRaisesRegex(install_qw.InstallerError, "Pacote ZIP inválido"):
                install_qw.safe_extract_zip(archive, destination)
            self.assertFalse((root / "escape").exists())
            self.assertFalse(destination.exists())

    def test_windows_drive_archive_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "bad-drive.zip"
            with zipfile.ZipFile(archive, "w") as package:
                package.writestr("C:/escape", b"bad")
            with self.assertRaisesRegex(install_qw.InstallerError, "Pacote ZIP inválido"):
                install_qw.safe_extract_zip(archive, root / "output")

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
