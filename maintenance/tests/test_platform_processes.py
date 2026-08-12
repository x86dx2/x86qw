from __future__ import annotations

import ast
import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
INSTALLER_BIN = ROOT / "dist/installer/bin"
if str(INSTALLER_BIN) not in sys.path:
    sys.path.insert(0, str(INSTALLER_BIN))


class PlatformProcessBoundaryTests(unittest.TestCase):
    def test_session_control_reexports_canonical_process_contracts(self) -> None:
        """Locks and services must compare the same immutable identity types."""

        canonical = importlib.import_module("x86qw_runtime.platform.processes")
        legacy = importlib.import_module("session_control")

        for name in (
            "ProcessIdentity",
            "ProcessProbe",
            "process_identity",
            "probe_expected_process",
            "terminate_windows_process",
            "_windows_kernel32",
        ):
            with self.subTest(symbol=name):
                self.assertIs(getattr(legacy, name), getattr(canonical, name))

    def test_current_process_has_a_confirmed_native_identity(self) -> None:
        """The platform adapter must support the controller running the CLI."""

        processes = importlib.import_module("x86qw_runtime.platform.processes")
        probe = processes.process_identity(os.getpid())

        self.assertEqual("alive", probe.status, probe.detail)
        self.assertIsNotNone(probe.identity)
        assert probe.identity is not None
        self.assertEqual(os.getpid(), probe.identity.pid)
        self.assertTrue(probe.identity.creation_token)
        self.assertTrue(probe.identity.executable)

    @unittest.skipUnless(sys.platform == "darwin", "libproc é específico do macOS")
    def test_macos_identity_uses_libproc_when_ps_is_unavailable(self) -> None:
        """A sandbox denying the ps subprocess must not disable installation locks."""

        processes = importlib.import_module("x86qw_runtime.platform.processes")
        with mock.patch.object(processes.subprocess, "run", side_effect=PermissionError(1, "ps")):
            probe = processes._macos_process_identity(os.getpid())

        self.assertEqual("alive", probe.status, probe.detail)
        self.assertIsNotNone(probe.identity)

    def test_expected_process_rejects_pid_reuse_by_token_or_executable(self) -> None:
        """A matching PID alone never authorizes termination or recovery."""

        processes = importlib.import_module("x86qw_runtime.platform.processes")
        actual = processes.ProcessProbe(
            "alive",
            processes.ProcessIdentity(123, "new-token", "/runtime/new"),
        )
        with mock.patch.object(processes, "process_identity", return_value=actual):
            probe = processes.probe_expected_process(
                123, "old-token", "/runtime/old",
            )

        self.assertEqual("identity_mismatch", probe.status)
        self.assertIs(probe.identity, actual.identity)

    def test_macos_framework_exec_paths_share_one_owner_identity(self) -> None:
        """A framework launcher may exec without changing its live PID owner."""

        processes = importlib.import_module("x86qw_runtime.platform.processes")
        first = "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14"
        second = "/Library/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python"
        with mock.patch.object(processes.sys, "platform", "darwin"):
            self.assertEqual(
                processes._comparable_executable(first),
                processes._comparable_executable(second),
            )

    def test_session_control_contains_no_process_probe_implementation(self) -> None:
        """The compatibility facade must not become a second platform backend."""

        tree = ast.parse((INSTALLER_BIN / "session_control.py").read_text("utf-8"))
        definitions = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        self.assertTrue(
            {
                "ProcessIdentity",
                "ProcessProbe",
                "_linux_process_identity",
                "_macos_process_identity",
                "_windows_process_identity",
                "process_identity",
                "probe_expected_process",
                "terminate_windows_process",
                "_windows_kernel32",
            }.isdisjoint(definitions)
        )


if __name__ == "__main__":
    unittest.main()
