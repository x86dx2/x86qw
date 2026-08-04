import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "install_qw_purge_transaction",
    ROOT / "dist/installer/bin/manager.py",
)
install_qw = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = install_qw
SPEC.loader.exec_module(install_qw)


class InstallerPurgeTransactionTests(unittest.TestCase):
    def setUp(self):
        install_qw.console.configure(verbose=False, no_color=True)

    def make_installer(self, root: Path):
        project = root / "project"
        target = project / "quake-world"
        cache = root / "cache" / "x86qw"
        target.mkdir(parents=True)
        cache.parent.mkdir()
        installer = install_qw.Installer(project, target, cache)
        receipt = target / install_qw.CLI_RECEIPT
        receipt.parent.mkdir(parents=True)
        receipt.write_text(
            json.dumps({
                "format": 1,
                "project": "x86qw",
                "version": "0.7.1",
            }) + "\n",
            encoding="utf-8",
        )
        return installer, target, cache

    def test_purge_preserves_installation_replaced_after_plan_revalidation(self):
        """Purge authority must stay bound to the observed installation inode."""

        quarantine = install_qw.quarantine
        with tempfile.TemporaryDirectory() as temporary:
            installer, target, _ = self.make_installer(Path(temporary))
            (target / "managed").write_bytes(b"managed")
            parked = target.parent / "owned-before-race"
            concurrent = b"personal concurrent installation"
            real_apply = quarantine.apply_quarantine_removal
            raced = False

            def replace_before_apply(path: Path, **kwargs):
                nonlocal raced
                if path == target and not raced:
                    raced = True
                    path.rename(parked)
                    path.mkdir()
                    (path / "personal").write_bytes(concurrent)
                return real_apply(path, **kwargs)

            with mock.patch.object(
                quarantine,
                "apply_quarantine_removal",
                side_effect=replace_before_apply,
            ), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(install_qw.InstallerError):
                    installer.purge()

            self.assertTrue(raced)
            self.assertTrue((target / "personal").is_file())
            self.assertEqual(concurrent, (target / "personal").read_bytes())
            self.assertTrue((parked / "managed").is_file())

    def test_cleanup_preserves_cache_replaced_after_plan_revalidation(self):
        """Cache ownership must stay bound to the directory validated by its marker."""

        quarantine = install_qw.quarantine
        with tempfile.TemporaryDirectory() as temporary:
            installer, _, cache = self.make_installer(Path(temporary))
            installer.prepare_cache()
            (cache / "managed").write_bytes(b"managed")
            cache = cache.resolve(strict=True)
            parked = cache.parent / "owned-cache-before-race"
            concurrent = b"personal concurrent cache"
            real_apply = quarantine.apply_quarantine_removal
            raced = False

            def replace_before_apply(path: Path, **kwargs):
                nonlocal raced
                if path == cache and not raced:
                    raced = True
                    path.rename(parked)
                    path.mkdir()
                    (path / "personal").write_bytes(concurrent)
                return real_apply(path, **kwargs)

            with mock.patch.object(
                quarantine,
                "apply_quarantine_removal",
                side_effect=replace_before_apply,
            ), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(install_qw.InstallerError):
                    installer.cleanup_cache()

            self.assertTrue(raced)
            self.assertTrue((cache / "personal").is_file())
            self.assertEqual(concurrent, (cache / "personal").read_bytes())
            self.assertTrue((parked / "managed").is_file())

    def test_final_purge_failure_restores_removed_empty_topology(self):
        """A failed final rmdir must not leave a half-removed installation root."""

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "project" / "quake-world"
            receipt = target / install_qw.CLI_RECEIPT
            receipt.parent.mkdir(parents=True)
            receipt.write_text(
                json.dumps({
                    "format": 1,
                    "project": "x86qw",
                    "version": "0.7.1",
                }) + "\n",
                encoding="utf-8",
            )
            (target / "personal.txt").write_bytes(b"remove")
            options = install_qw.parse_arguments(
                ["--online-only", "uninstall", str(target), "--purge"], ROOT,
            )
            normalized_target = target.resolve()
            real_rmdir = Path.rmdir
            failed = False

            def fail_target(path: Path) -> None:
                nonlocal failed
                if path == normalized_target and not failed:
                    failed = True
                    raise OSError("simulated final target removal failure")
                real_rmdir(path)

            with mock.patch.object(
                Path, "rmdir", autospec=True, side_effect=fail_target,
            ), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    install_qw.InstallerError, "finalização",
                ):
                    install_qw.execute_manager_action(options, ROOT)

            self.assertTrue(failed)
            self.assertTrue(
                (normalized_target / install_qw.METADATA_DIR).is_dir()
            )


if __name__ == "__main__":
    unittest.main()
