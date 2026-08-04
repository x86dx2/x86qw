from __future__ import annotations

import importlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from maintenance.tools.build_installer_bundle import zipapp_bytes


ROOT = Path(__file__).resolve().parents[2]
INSTALLER_BIN = ROOT / "dist/installer/bin"
if str(INSTALLER_BIN) not in sys.path:
    sys.path.insert(0, str(INSTALLER_BIN))


class RuntimeMenuBoundaryTests(unittest.TestCase):
    def test_entrypoints_share_the_canonical_menu_module(self) -> None:
        """Every interactive entrypoint must use one menu state and exception set."""

        canonical = importlib.import_module("x86qw_runtime.ui.menu")

        for module_name in ("manager", "gameplay", "services"):
            with self.subTest(module=module_name):
                entrypoint = importlib.import_module(module_name)
                self.assertIs(entrypoint.navigation, canonical)

    def test_legacy_menu_facade_preserves_public_symbol_identities(self) -> None:
        """Legacy imports must catch the same exceptions raised by the runtime UI."""

        legacy_spec = importlib.util.find_spec("menu")
        self.assertIsNotNone(
            legacy_spec,
            "dist/installer/bin/menu.py must remain as a compatibility facade",
        )
        canonical = importlib.import_module("x86qw_runtime.ui.menu")
        legacy = importlib.import_module("menu")

        for name in (
            "MenuCancelled",
            "MenuExit",
            "MenuOption",
            "configure",
            "confirm",
            "read_key",
            "select_many",
            "select_one",
            "supports_navigation",
        ):
            with self.subTest(symbol=name):
                self.assertIs(getattr(legacy, name), getattr(canonical, name))

    def test_installed_zipapp_contains_canonical_ui_and_the_legacy_facade(self) -> None:
        """The installed CLI must ship both its canonical import and compatibility path."""

        with zipfile.ZipFile(io.BytesIO(zipapp_bytes("9.9.9"))) as application:
            names = set(application.namelist())

        self.assertIn("x86qw_runtime/ui/__init__.py", names)
        self.assertIn("x86qw_runtime/ui/menu.py", names)
        self.assertIn("menu.py", names)


class RuntimeDownloaderBoundaryTests(unittest.TestCase):
    def test_maintenance_downloader_is_the_runtime_compatibility_module(self) -> None:
        """A second downloader implementation must not survive under maintenance."""

        runtime_spec = importlib.util.find_spec("x86qw_runtime.io.downloader")
        self.assertIsNotNone(
            runtime_spec,
            "the bounded downloader must be owned by x86qw_runtime",
        )
        runtime_downloader = importlib.import_module("x86qw_runtime.io.downloader")
        maintenance_downloader = importlib.import_module(
            "maintenance.tools.downloader",
        )

        self.assertIs(maintenance_downloader.download, runtime_downloader.download)
        self.assertIs(
            maintenance_downloader.DownloadContract,
            runtime_downloader.DownloadContract,
        )
        self.assertEqual(
            maintenance_downloader.PinnedArtifact.__module__,
            "x86qw_runtime.io.downloader",
        )

    def test_installed_zipapp_carries_only_the_runtime_downloader(self) -> None:
        """The public CLI must not ship the maintenance downloader facade."""

        with zipfile.ZipFile(io.BytesIO(zipapp_bytes("9.9.9"))) as application:
            names = set(application.namelist())

        self.assertIn("x86qw_runtime/io/downloader.py", names)
        self.assertNotIn("maintenance/tools/downloader.py", names)


class RuntimeErrorBoundaryTests(unittest.TestCase):
    def test_entrypoints_share_the_runtime_installer_error(self) -> None:
        """Entrypoints must not import manager merely to share an error type."""

        runtime_spec = importlib.util.find_spec("x86qw_runtime.errors")
        self.assertIsNotNone(
            runtime_spec,
            "typed CLI errors must be owned by x86qw_runtime",
        )
        runtime_errors = importlib.import_module("x86qw_runtime.errors")
        manager = importlib.import_module("manager")
        gameplay = importlib.import_module("gameplay")
        services = importlib.import_module("services")

        self.assertIs(manager.InstallerError, runtime_errors.InstallerError)
        self.assertIs(gameplay.InstallerError, runtime_errors.InstallerError)
        self.assertIs(services.InstallerError, runtime_errors.InstallerError)
        self.assertEqual(
            int(runtime_errors.InstallerError("falha").exit_code),
            1,
        )


class RuntimeVersionBoundaryTests(unittest.TestCase):
    def test_manager_and_builder_use_the_runtime_version_contract(self) -> None:
        """Version syntax must not be reimplemented by each entrypoint."""

        runtime_spec = importlib.util.find_spec("x86qw_runtime.versioning")
        self.assertIsNotNone(
            runtime_spec,
            "the current version rules must be owned by x86qw_runtime",
        )
        versioning = importlib.import_module("x86qw_runtime.versioning")
        manager = importlib.import_module("manager")
        builder = importlib.import_module(
            "maintenance.tools.build_installer_bundle",
        )

        self.assertIs(manager.STABLE_VERSION, versioning.STABLE_VERSION)
        self.assertIs(manager.NIGHTLY_VERSION, versioning.NIGHTLY_VERSION)
        self.assertIs(manager.COMPONENT_VERSION, versioning.COMPONENT_VERSION)
        self.assertIs(builder.version_key, versioning.version_key)
        self.assertEqual(versioning.version_key("0.7.1"), (0, 7, 1))
        for invalid in ("0.7", "0.7.1-rc.1", "v0.7.1", "1.2.3.4"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    versioning.version_key(invalid)


class LazyCatalogBoundaryTests(unittest.TestCase):
    def test_help_and_version_do_not_read_runtime_catalogs(self) -> None:
        """A malformed catalog must not break a command that does not need it."""

        payload = zipapp_bytes("9.9.9")
        corrupted = io.BytesIO()
        catalog_members = {
            "_x86qw/capabilities.json",
            "_x86qw/runtimes.json",
            "_x86qw/games.json",
            "_x86qw/compatibility.json",
            "_x86qw/components.json",
        }
        with zipfile.ZipFile(io.BytesIO(payload)) as source:
            with zipfile.ZipFile(corrupted, "w") as destination:
                for info in source.infolist():
                    data = source.read(info.filename)
                    if info.filename in catalog_members:
                        data = b"{catalogo-indisponivel"
                    destination.writestr(info, data)

        with tempfile.TemporaryDirectory() as temporary_directory:
            application = Path(temporary_directory) / "x86qw.pyz"
            application.write_bytes(corrupted.getvalue())
            environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            for arguments in (
                ("--help",),
                ("--version",),
                ("version",),
                ("play", "--help"),
                ("host", "--help"),
            ):
                with self.subTest(arguments=arguments):
                    result = subprocess.run(
                        [sys.executable, os.fspath(application), *arguments],
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=False,
                        timeout=10,
                        env=environment,
                    )
                    self.assertEqual(
                        result.returncode,
                        0,
                        result.stdout + result.stderr,
                    )


class RuntimeCatalogOwnershipTests(unittest.TestCase):
    def test_installed_catalog_loaders_are_owned_by_the_runtime(self) -> None:
        """Installed catalog consumers must not execute maintenance modules."""

        runtime_spec = importlib.util.find_spec("x86qw_runtime.catalogs")
        self.assertIsNotNone(
            runtime_spec,
            "runtime catalog models must be owned by x86qw_runtime",
        )
        catalogs = importlib.import_module("x86qw_runtime.catalogs")
        manager = importlib.import_module("manager")
        gameplay = importlib.import_module("gameplay")
        maintenance_components = importlib.import_module(
            "maintenance.tools.components",
        )
        maintenance_runtime = importlib.import_module(
            "maintenance.tools.runtime_catalog",
        )

        self.assertIs(manager.load_runtime_catalog, catalogs.load_component_catalog)
        self.assertIs(manager.components_by_id, catalogs.components_by_id)
        self.assertIs(manager.resolve_dependencies, catalogs.resolve_dependencies)
        self.assertIs(gameplay.load_games, catalogs.load_games)
        self.assertIs(gameplay.games_by_id, catalogs.games_by_id)
        self.assertIs(
            maintenance_components.load_runtime_catalog,
            catalogs.load_component_catalog,
        )
        self.assertIs(maintenance_runtime.load_games, catalogs.load_games)
        self.assertIs(maintenance_runtime.runtimes_by_id, catalogs.runtimes_by_id)

    def test_installed_zipapp_contains_no_maintenance_modules(self) -> None:
        """The public runtime artifact must not embed repository tooling."""

        with zipfile.ZipFile(io.BytesIO(zipapp_bytes("9.9.9"))) as application:
            names = set(application.namelist())

        self.assertIn("x86qw_runtime/catalogs.py", names)
        self.assertFalse(
            any(name == "maintenance" or name.startswith("maintenance/") for name in names),
            sorted(name for name in names if name.startswith("maintenance")),
        )

    def test_installed_zipapp_contains_the_atomic_runtime_boundary(self) -> None:
        """State and journal consumers in the public CLI need the canonical writer."""

        with zipfile.ZipFile(io.BytesIO(zipapp_bytes("9.9.9"))) as application:
            names = set(application.namelist())

        self.assertIn("x86qw_runtime/io/atomic.py", names)


class RuntimeStateOwnershipTests(unittest.TestCase):
    def test_manager_uses_the_runtime_state_parser_migration_and_codec(self) -> None:
        """State logic duplicated in manager would let persisted contracts drift."""

        manager = importlib.import_module("manager")
        state = importlib.import_module("x86qw_runtime.state")
        migrations = importlib.import_module("x86qw_runtime.migrations")

        self.assertIs(manager.parse_install_state, state.parse_install_state)
        self.assertIs(manager.read_install_state, state.read_install_state)
        self.assertIs(
            getattr(manager, "serialize_install_state", None),
            state.serialize_install_state,
        )
        self.assertIs(manager.migrate_install_state, migrations.migrate_install_state)

    def test_installed_zipapp_contains_state_and_migration_contracts(self) -> None:
        """The installed manager must not fall back to repository-only state code."""

        with zipfile.ZipFile(io.BytesIO(zipapp_bytes("9.9.9"))) as application:
            names = set(application.namelist())

        self.assertIn("x86qw_runtime/state.py", names)
        self.assertIn("x86qw_runtime/migrations.py", names)


class RuntimeReceiptOwnershipTests(unittest.TestCase):
    def test_installed_zipapp_contains_receipt_codecs_and_bounded_metadata_io(self) -> None:
        """Installed receipt parsing must not depend on unshipped manager duplicates."""

        with zipfile.ZipFile(io.BytesIO(zipapp_bytes("9.9.9"))) as application:
            names = set(application.namelist())

        self.assertIn("x86qw_runtime/receipts.py", names)
        self.assertIn("x86qw_runtime/io/metadata.py", names)


class RuntimeTransactionOwnershipTests(unittest.TestCase):
    def test_installed_zipapp_contains_the_transaction_contract(self) -> None:
        """Installed mutations must not fall back to manager-only orchestration."""

        with zipfile.ZipFile(io.BytesIO(zipapp_bytes("9.9.9"))) as application:
            names = set(application.namelist())

        self.assertIn("x86qw_runtime/transaction.py", names)


class RuntimeSupervisorOwnershipTests(unittest.TestCase):
    def test_services_use_runtime_path_primitives_without_manager(self) -> None:
        """Session recovery must not import the installer for basic filesystem I/O."""

        services = importlib.import_module("services")
        paths = importlib.import_module("x86qw_runtime.io.paths")
        arguments = importlib.import_module("x86qw_runtime.ui.arguments")

        self.assertIs(services.lexists, paths.lexists)
        self.assertIs(services.remove_path, paths.remove_path)
        self.assertIs(services.FriendlyArgumentParser, arguments.FriendlyArgumentParser)

    def test_services_reexports_the_runtime_supervisor_contract(self) -> None:
        """The installed services facade must not retain a second supervisor."""

        services = importlib.import_module("services")
        supervisor = importlib.import_module("x86qw_runtime.supervisor.core")

        for name in (
            "ServiceSignal",
            "WindowsJobObject",
            "posix_process_group_status",
            "run_processes",
            "stop_processes",
        ):
            with self.subTest(symbol=name):
                self.assertIs(getattr(services, name), getattr(supervisor, name))

    def test_installed_zipapp_contains_and_executes_the_runtime_supervisor(self) -> None:
        """Service commands must use the canonical supervisor in the public artifact."""

        payload = zipapp_bytes("9.9.9")
        with zipfile.ZipFile(io.BytesIO(payload)) as application:
            names = set(application.namelist())
        self.assertIn("x86qw_runtime/supervisor/core.py", names)
        self.assertIn("x86qw_runtime/io/paths.py", names)
        self.assertIn("x86qw_runtime/platform/locking.py", names)
        self.assertIn("x86qw_runtime/ui/arguments.py", names)
        self.assertIn("x86qw_runtime/ui/console.py", names)

        with tempfile.TemporaryDirectory() as temporary_directory:
            application = Path(temporary_directory) / "x86qw.pyz"
            application.write_bytes(payload)
            environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            for arguments in (("host", "--help"), ("status", "--help")):
                with self.subTest(arguments=arguments):
                    result = subprocess.run(
                        [sys.executable, os.fspath(application), *arguments],
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=False,
                        timeout=10,
                        env=environment,
                    )
                    self.assertEqual(
                        result.returncode,
                        0,
                        result.stdout + result.stderr,
                    )


if __name__ == "__main__":
    unittest.main()
