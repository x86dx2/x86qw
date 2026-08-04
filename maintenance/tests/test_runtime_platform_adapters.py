from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from x86qw_runtime.io import managed_files
from x86qw_runtime.platform import host, python_runtime


class PythonHandoffAdapterTests(unittest.TestCase):
    def test_handoff_uses_validated_python_without_shell_and_returns_exit_code(self) -> None:
        application = Path("diretório unicode ç") / "x86qw.pyz"
        arguments = ("--skip-cli-update", "upgrade", "destino com espaço")
        completed = subprocess.CompletedProcess([], 37)

        with mock.patch.object(
            subprocess,
            "run",
            return_value=completed,
        ) as run:
            result = python_runtime.run_handoff(application, arguments)

        self.assertEqual(37, result)
        self.assertEqual(
            [sys.executable, os.fspath(application), *arguments],
            run.call_args.args[0],
        )
        self.assertEqual({"check": False}, run.call_args.kwargs)


class PermissionAdapterTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "bits de execução são POSIX")
    def test_posix_mode_and_executable_repair_are_owned_by_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime"
            path.write_bytes(b"runtime")
            path.chmod(0o600)

            self.assertTrue(host.executable_permission_missing(path))
            previous = host.add_owner_execute(path)

            self.assertEqual(0o600, previous)
            self.assertEqual(0o700, stat.S_IMODE(path.stat().st_mode))
            self.assertFalse(host.executable_permission_missing(path))
            host.apply_mode(path, previous)
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))

    def test_windows_mode_operations_are_noops(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.exe"
            path.write_bytes(b"runtime")
            original = stat.S_IMODE(path.stat().st_mode)

            self.assertFalse(host.supports_posix_permissions(os_name="nt"))
            self.assertFalse(
                host.executable_permission_missing(path, os_name="nt")
            )
            self.assertEqual(
                original,
                host.add_owner_execute(path, os_name="nt"),
            )
            host.apply_mode(path, 0o755, os_name="nt")

            self.assertEqual(original, stat.S_IMODE(path.stat().st_mode))

    def test_launcher_removal_names_preserve_the_active_windows_batch(self) -> None:
        self.assertEqual(
            ("x86qw.sh",),
            host.cli_launcher_names_for_removal(os_name="nt"),
        )
        self.assertEqual(
            ("x86qw.sh", "x86qw.cmd"),
            host.cli_launcher_names_for_removal(os_name="posix"),
        )

    @unittest.skipIf(os.name == "nt", "identidade POSIX exercitada localmente")
    def test_identity_bound_unlink_preserves_a_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "default.cfg"
            path.write_bytes(b"original")
            metadata = path.stat()
            original_identity = int(metadata.st_dev), int(metadata.st_ino)
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(b"personal")
            replacement.replace(path)

            with self.assertRaises(host.HostPlatformError):
                host.unlink_identity_bound_file(path, original_identity)

            self.assertEqual(b"personal", path.read_bytes())

    @unittest.skipIf(os.name == "nt", "rename descriptor-relative é POSIX")
    def test_identity_bound_unlink_preserves_swap_at_atomic_move(self) -> None:
        rename_api = managed_files._get_posix_rename_api()
        if rename_api is None:
            self.skipTest("rename exclusivo POSIX indisponível")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "default.cfg"
            path.write_bytes(b"original")
            metadata = path.stat()
            original_identity = int(metadata.st_dev), int(metadata.st_ino)
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(b"personal")
            real_move = rename_api.move_no_replace
            swapped = False

            def swap_then_move(*arguments: object, **kwargs: object) -> None:
                nonlocal swapped
                if not swapped:
                    swapped = True
                    replacement.replace(path)
                real_move(*arguments, **kwargs)

            with mock.patch.object(
                rename_api,
                "move_no_replace",
                side_effect=swap_then_move,
            ), self.assertRaises(host.HostPlatformError):
                host.unlink_identity_bound_file(path, original_identity)

            self.assertTrue(swapped)
            self.assertEqual(b"personal", path.read_bytes())
            self.assertEqual([], list(path.parent.glob(".x86qw-unlink-*")))


if __name__ == "__main__":
    unittest.main()
