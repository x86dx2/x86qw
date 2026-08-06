import contextlib
import importlib.util
import os
import shutil
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from x86qw_runtime.trust import deserialize_trusted_versions


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "install_qw_installed_trust",
    ROOT / "dist/installer/bin/manager.py",
)
install_qw = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = install_qw
SPEC.loader.exec_module(install_qw)


class InstalledTrustTests(unittest.TestCase):
    NOW = datetime(2026, 8, 4, tzinfo=timezone.utc)
    TRUST_ENV = (
        "X86_QW_CATALOG_URL",
        "X86_QW_TRUST_METADATA_URL",
        "X86_QW_TRUST_METADATA_REQUIRED",
        "X86_QW_REQUIRE_TRUST_METADATA",
        "X86_QW_TRUST_ROOT_URL",
        "X86_QW_TRUST_CURRENT_URL",
        "X86_QW_TRUST_SNAPSHOT_URL",
        "X86_QW_TRUST_METADATA_ROOT_URL",
        "X86_QW_TRUST_METADATA_CURRENT_URL",
        "X86_QW_TRUST_METADATA_SNAPSHOT_URL",
    )

    def setUp(self):
        install_qw.console.configure(verbose=False, no_color=True)

    @contextlib.contextmanager
    def clean_trust_environment(self):
        saved = {name: os.environ.get(name) for name in self.TRUST_ENV}
        try:
            for name in self.TRUST_ENV:
                os.environ.pop(name, None)
            yield
        finally:
            for name in self.TRUST_ENV:
                os.environ.pop(name, None)
                if saved[name] is not None:
                    os.environ[name] = saved[name]

    def _fixture(self, role: str, *, generation: int = 1) -> bytes:
        if generation == 1:
            path = ROOT / "maintenance/inventory/trust" / f"{role}.json"
        else:
            path = ROOT / "maintenance/tests/fixtures/trust" / f"{role}-v{generation}.json"
        return path.read_bytes()

    def _project(self, root: Path, *, generation: int | None = None) -> tuple[Path, Path, bytes]:
        project = root / "project"
        target = root / "target"
        (project / install_qw.PUBLIC_CATALOG).parent.mkdir(parents=True)
        (project / install_qw.PUBLIC_CATALOG).write_bytes(
            (ROOT / install_qw.PUBLIC_CATALOG).read_bytes()
        )
        if generation is not None:
            trust = project / install_qw.TRUST_METADATA_DIR
            trust.mkdir(parents=True)
            for role in install_qw.TRUST_ROLE_NAMES:
                (trust / f"{role}.json").write_bytes(
                    self._fixture(role, generation=generation)
                )
        target.mkdir()
        return project, target, (project / install_qw.PUBLIC_CATALOG).read_bytes()

    def _installer(self, project: Path, target: Path):
        return install_qw.Installer(project, target)

    def test_local_signed_catalog_persists_private_trusted_versions(self):
        with self.clean_trust_environment(), tempfile.TemporaryDirectory() as temporary:
            project, target, _ = self._project(Path(temporary), generation=1)
            installer = self._installer(project, target)
            with mock.patch.object(install_qw, "trust_now", return_value=self.NOW):
                catalog = installer.public_catalog("local")
            self.assertEqual("x86qw", catalog["project"])
            state = target / ".x86qw/trust/versions.json"
            self.assertTrue(state.is_file())
            self.assertEqual(0o600, stat.S_IMODE(state.stat().st_mode))
            versions = deserialize_trusted_versions(state.read_bytes())
            self.assertEqual((1, 1, 1), versions.as_tuple())
            self.assertIsNone(versions.evidence_version)
            self.assertIsNone(versions.evidence_digest)
            self.assertNotIn(b"PRIVATE", state.read_bytes().upper())

    def test_local_metadata_tamper_fails_closed_before_catalog_use(self):
        with self.clean_trust_environment(), tempfile.TemporaryDirectory() as temporary:
            project, target, _ = self._project(Path(temporary), generation=1)
            current = project / install_qw.TRUST_METADATA_DIR / "current.json"
            payload = current.read_bytes()
            current.write_bytes(payload[:-2] + (b"A" if payload[-2:-1] != b"A" else b"B") + payload[-1:])
            installer = self._installer(project, target)
            with mock.patch.object(install_qw, "trust_now", return_value=self.NOW):
                with self.assertRaisesRegex(install_qw.InstallerError, "confiança"):
                    installer.public_catalog("local")
            self.assertFalse((target / ".x86qw/trust/versions.json").exists())

    def test_legacy_local_catalog_remains_available_without_metadata(self):
        with self.clean_trust_environment(), tempfile.TemporaryDirectory() as temporary:
            project, target, _ = self._project(Path(temporary))
            installer = self._installer(project, target)
            with mock.patch.object(install_qw, "trust_now", return_value=self.NOW):
                catalog = installer.public_catalog("legacy")
            self.assertEqual("x86qw", catalog["project"])
            self.assertFalse((target / ".x86qw/trust/versions.json").exists())

    def test_required_metadata_without_source_stops_before_remote_catalog(self):
        with self.clean_trust_environment(), tempfile.TemporaryDirectory() as temporary:
            project, target, _ = self._project(Path(temporary))
            installer = self._installer(project, target)
            installer.online_only = True
            os.environ["X86_QW_TRUST_METADATA_REQUIRED"] = "1"
            with mock.patch.object(installer.remote, "get_mirrors") as get_mirrors:
                with self.assertRaisesRegex(install_qw.InstallerError, "obrigatórios"):
                    installer.public_catalog("remote")
            get_mirrors.assert_not_called()

    def test_one_point_zero_cli_requires_trust_even_without_environment_override(self):
        with self.clean_trust_environment(), mock.patch.object(
            install_qw, "application_version", return_value="1.0.0",
        ):
            installer = self._installer(Path("/tmp/project"), Path("/tmp/target"))
            self.assertTrue(installer._trust_metadata_required())

    def test_one_point_zero_cli_cannot_disable_trust_with_false_override(self):
        with self.clean_trust_environment(), mock.patch.object(
            install_qw, "application_version", return_value="1.0.0",
        ):
            os.environ["X86_QW_TRUST_METADATA_REQUIRED"] = "0"
            installer = self._installer(Path("/tmp/project"), Path("/tmp/target"))
            self.assertTrue(installer._trust_metadata_required())

    def test_remote_roles_are_preflighted_and_state_is_persisted(self):
        with self.clean_trust_environment(), tempfile.TemporaryDirectory() as temporary:
            project, target, catalog_payload = self._project(Path(temporary))
            installer = self._installer(project, target)
            installer.online_only = True
            os.environ["X86_QW_TRUST_METADATA_URL"] = "https://trust.example.invalid/x86qw"
            roles = {role: self._fixture(role) for role in install_qw.TRUST_ROLE_NAMES}
            with mock.patch.object(
                installer.remote,
                "get_metadata_roles",
                return_value=roles,
            ) as get_roles, mock.patch.object(
                installer.remote,
                "get_mirrors",
                return_value=(catalog_payload, install_qw.CATALOG_URL),
            ) as get_catalog, mock.patch.object(
                install_qw, "trust_now", return_value=self.NOW,
            ):
                catalog = installer.public_catalog("remote")
            self.assertEqual("x86qw", catalog["project"])
            get_roles.assert_called_once_with(
                {
                    "root": "https://trust.example.invalid/x86qw/root.json",
                    "current": "https://trust.example.invalid/x86qw/current.json",
                    "snapshot": "https://trust.example.invalid/x86qw/snapshot.json",
                },
                maximum_size=install_qw.TRUST_METADATA_MAX_BYTES,
                timeout=install_qw.CATALOG_TIMEOUT,
                attempts=2,
            )
            get_catalog.assert_called_once()
            self.assertTrue((target / ".x86qw/trust/versions.json").is_file())

    def test_remote_role_tamper_is_rejected_before_catalog_request(self):
        with self.clean_trust_environment(), tempfile.TemporaryDirectory() as temporary:
            project, target, _ = self._project(Path(temporary))
            installer = self._installer(project, target)
            installer.online_only = True
            os.environ["X86_QW_TRUST_METADATA_URL"] = "https://trust.example.invalid/x86qw"
            roles = {role: self._fixture(role) for role in install_qw.TRUST_ROLE_NAMES}
            payload = roles["snapshot"]
            roles["snapshot"] = payload[:-2] + b"A" + payload[-1:]
            with mock.patch.object(
                installer.remote, "get_metadata_roles", return_value=roles,
            ), mock.patch.object(installer.remote, "get_mirrors") as get_catalog, mock.patch.object(
                install_qw, "trust_now", return_value=self.NOW,
            ):
                with self.assertRaisesRegex(install_qw.InstallerError, "confiança"):
                    installer.public_catalog("remote")
            get_catalog.assert_not_called()

    def test_persisted_state_blocks_explicit_catalog_without_roles(self):
        with self.clean_trust_environment(), tempfile.TemporaryDirectory() as temporary:
            project, target, _ = self._project(Path(temporary), generation=1)
            with mock.patch.object(install_qw, "trust_now", return_value=self.NOW):
                self._installer(project, target).public_catalog("seed")
            installer = self._installer(project, target)
            installer.online_only = True
            os.environ["X86_QW_CATALOG_URL"] = "https://catalog.example.invalid/catalog.json"
            with mock.patch.object(installer.remote, "get") as get_catalog:
                with self.assertRaisesRegex(install_qw.InstallerError, "obrigatórios"):
                    installer.public_catalog("explicit")
            get_catalog.assert_not_called()

    def test_persisted_root_rotation_rejects_old_metadata_afterward(self):
        with self.clean_trust_environment(), tempfile.TemporaryDirectory() as temporary:
            project, target, _ = self._project(Path(temporary), generation=1)
            installer = self._installer(project, target)
            with mock.patch.object(install_qw, "trust_now", return_value=self.NOW):
                installer.public_catalog("v1")
            trust = project / install_qw.TRUST_METADATA_DIR
            for role in install_qw.TRUST_ROLE_NAMES:
                (trust / f"{role}.json").write_bytes(self._fixture(role, generation=2))
            rotated = self._installer(project, target)
            with mock.patch.object(install_qw, "trust_now", return_value=self.NOW):
                rotated.public_catalog("v2")
            self.assertEqual(
                (2, 2, 2),
                deserialize_trusted_versions(
                    (target / ".x86qw/trust/versions.json").read_bytes()
                ).as_tuple(),
            )
            for role in install_qw.TRUST_ROLE_NAMES:
                (trust / f"{role}.json").write_bytes(self._fixture(role, generation=1))
            rollback = self._installer(project, target)
            with mock.patch.object(install_qw, "trust_now", return_value=self.NOW):
                with self.assertRaisesRegex(install_qw.InstallerError, "confiança"):
                    rollback.public_catalog("rollback")


if __name__ == "__main__":
    unittest.main()
