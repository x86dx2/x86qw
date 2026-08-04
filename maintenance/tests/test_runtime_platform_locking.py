from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import io
import zipfile
from pathlib import Path
from unittest import mock

from maintenance.tools.build_installer_bundle import zipapp_bytes


ROOT = Path(__file__).resolve().parents[2]
INSTALLER_BIN = ROOT / "dist/installer/bin"
if str(INSTALLER_BIN) not in sys.path:
    sys.path.insert(0, str(INSTALLER_BIN))


def runtime_locking():
    try:
        return importlib.import_module("x86qw_runtime.platform.locking")
    except ModuleNotFoundError as error:
        raise AssertionError(
            "o mutex da instalação ainda não pertence ao runtime"
        ) from error


class RuntimePlatformLockingTests(unittest.TestCase):
    def test_session_control_facade_is_the_canonical_runtime_module(self) -> None:
        """Lock schema and ownership live in runtime, not in an entrypoint copy."""

        canonical = importlib.import_module("x86qw_runtime.session_control")
        legacy = importlib.import_module("session_control")
        self.assertIs(legacy, canonical)
        self.assertIs(legacy.InstallationLock, canonical.InstallationLock)

    def test_zipapp_contains_only_canonical_session_control(self) -> None:
        with zipfile.ZipFile(io.BytesIO(zipapp_bytes("9.9.9"))) as application:
            names = set(application.namelist())

        self.assertIn("x86qw_runtime/session_control.py", names)
        self.assertNotIn("session_control.py", names)

    def test_session_control_reexports_canonical_locking_contracts(self) -> None:
        """The compatibility facade must not grow a second native mutex backend."""

        canonical = runtime_locking()
        legacy = importlib.import_module("session_control")

        for legacy_name, canonical_name in (
            ("SessionControlError", "SessionControlError"),
            ("_windows_acquisition_mutex_name", "windows_acquisition_mutex_name"),
            ("_windows_acquisition_mutex", "windows_acquisition_mutex"),
            ("_installation_acquisition_mutex", "installation_acquisition_mutex"),
        ):
            with self.subTest(symbol=legacy_name):
                self.assertIs(
                    getattr(legacy, legacy_name), getattr(canonical, canonical_name),
                )

    def test_runtime_locking_import_is_independent_of_installer_entrypoints(self) -> None:
        """Importing the reusable adapter must not load an installed CLI facade."""

        script = """
import sys
from pathlib import Path

sys.path[:] = [sys.argv[1]] + [item for item in sys.path if item != sys.argv[2]]
import x86qw_runtime.platform.locking
for forbidden in ("manager", "services", "session_control", "gameplay"):
    if forbidden in sys.modules:
        raise SystemExit("runtime imported entrypoint: " + forbidden)
"""
        completed = subprocess.run(
            [sys.executable, "-c", script, str(ROOT), str(INSTALLER_BIN)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )

        self.assertEqual(0, completed.returncode, completed.stderr or completed.stdout)

    def test_mutex_serializes_two_independent_controllers(self) -> None:
        """A second process must not enter the reclaim critical section early."""

        runtime_locking()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target = root / "quake-world"
            sessions = target / ".x86qw/sessions"
            sessions.mkdir(parents=True)
            coordination = root / "coordination"
            coordination.mkdir()
            script = """
import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from x86qw_runtime.platform.locking import installation_acquisition_mutex

target = Path(sys.argv[2])
sessions = Path(sys.argv[3])
coordination = Path(sys.argv[4])
role = sys.argv[5]
if role == "waiter":
    (coordination / "waiter-ready").touch()
with installation_acquisition_mutex(target, sessions):
    (coordination / (role + "-entered")).touch()
    if role == "holder":
        deadline = time.monotonic() + 5
        while not (coordination / "release-holder").exists() and time.monotonic() < deadline:
            time.sleep(0.005)
"""

            def start(role: str) -> subprocess.Popen[str]:
                return subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        script,
                        str(ROOT),
                        str(target),
                        str(sessions),
                        str(coordination),
                        role,
                    ],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

            holder = start("holder")
            waiter: subprocess.Popen[str] | None = None
            try:
                deadline = time.monotonic() + 3
                while (
                    not (coordination / "holder-entered").exists()
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.005)
                self.assertTrue((coordination / "holder-entered").exists())

                waiter = start("waiter")
                deadline = time.monotonic() + 3
                while (
                    not (coordination / "waiter-ready").exists()
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.005)
                self.assertTrue((coordination / "waiter-ready").exists())
                time.sleep(0.1)
                self.assertFalse((coordination / "waiter-entered").exists())

                (coordination / "release-holder").touch()
                completed = [holder.communicate(timeout=5), waiter.communicate(timeout=5)]
                self.assertEqual(
                    [0, 0], [holder.returncode, waiter.returncode], completed,
                )
                self.assertTrue((coordination / "waiter-entered").exists())
            finally:
                (coordination / "release-holder").touch(exist_ok=True)
                for process in (holder, waiter):
                    if process is not None and process.poll() is None:
                        process.terminate()
                        process.communicate(timeout=5)

    @unittest.skipIf(os.name == "nt", "O_NOFOLLOW/flock são contratos POSIX")
    def test_posix_mutex_rejects_a_symlinked_sessions_directory(self) -> None:
        """A hostile path replacement must never redirect the mutex elsewhere."""

        locking = runtime_locking()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target = root / "quake-world"
            target.mkdir()
            outside = root / "outside"
            outside.mkdir()
            sessions = target / "sessions"
            sessions.symlink_to(outside, target_is_directory=True)

            with self.assertRaises(OSError):
                with locking.installation_acquisition_mutex(target, sessions):
                    self.fail("diretório de sessões redirecionado foi aceito")

    def test_inconclusive_controller_preserves_the_exact_active_lock(self) -> None:
        """Uncertain process identity must block instead of reclaiming evidence."""

        runtime_locking()
        legacy = importlib.import_module("session_control")
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            sessions = target / ".x86qw/sessions"
            sessions.mkdir(parents=True)
            legacy.private_fs.ensure_private_directory(target / ".x86qw")
            legacy.private_fs.ensure_private_directory(sessions)
            lock_path = sessions / "active.lock"
            payload = (
                json.dumps(
                    {
                        "format": 3,
                        "project": "x86qw",
                        "session_id": "uncertain-session",
                        "operation_kind": "service",
                        "command": "host",
                        "controller_pid": 424242,
                        "controller_start_token": "uncertain-token",
                        "controller_executable": str(target / "controller"),
                        "created_at": "2026-08-04T00:00:00+00:00",
                        "installation": str(target),
                        "private_filesystem": 1,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            lock_path.write_bytes(payload)
            legacy.private_fs.protect_private_file(lock_path)

            with mock.patch.object(
                legacy,
                "probe_expected_process",
                return_value=legacy.ProcessProbe("inconclusive", detail="access denied"),
            ):
                with self.assertRaisesRegex(
                    legacy.SessionControlError, "(?i)não foi possível confirmar",
                ):
                    legacy.InstallationLock.acquire(target, "proxy")

            self.assertEqual(payload, lock_path.read_bytes())
            self.assertEqual([], list(sessions.glob(".active.lock.reclaimed.*")))

    def test_dead_owner_can_be_reclaimed_then_restored_without_data_loss(self) -> None:
        """A failed recovery must be able to put the original owner back exactly."""

        runtime_locking()
        legacy = importlib.import_module("session_control")
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            sessions = target / ".x86qw/sessions"
            sessions.mkdir(parents=True)
            legacy.private_fs.ensure_private_directory(target / ".x86qw")
            legacy.private_fs.ensure_private_directory(sessions)
            lock_path = sessions / "active.lock"
            payload = (
                json.dumps(
                    {
                        "format": 3,
                        "project": "x86qw",
                        "session_id": "dead-session",
                        "operation_kind": "service",
                        "command": "host",
                        "controller_pid": 424242,
                        "controller_start_token": "dead-token",
                        "controller_executable": str(target / "dead-controller"),
                        "created_at": "2026-08-04T00:00:00+00:00",
                        "installation": str(target),
                        "private_filesystem": 1,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            lock_path.write_bytes(payload)
            legacy.private_fs.protect_private_file(lock_path)

            with mock.patch.object(
                legacy,
                "probe_expected_process",
                return_value=legacy.ProcessProbe("dead"),
            ):
                acquired = legacy.InstallationLock.acquire(target, "proxy")
            self.assertIsNotNone(acquired.reclaimed_path)
            self.assertNotEqual(payload, lock_path.read_bytes())

            acquired.release(restore_reclaimed=True)

            self.assertEqual(payload, lock_path.read_bytes())
            self.assertEqual([], list(sessions.glob(".active.lock.reclaimed.*")))

    @unittest.skipUnless(os.name == "nt", "requer CreateMutexW e DACL nativos")
    def test_windows_mutex_is_acquirable_with_private_dacl(self) -> None:
        """The Windows adapter must create a usable mutex under its private ACL."""

        locking = runtime_locking()
        with tempfile.TemporaryDirectory() as temporary:
            with locking.windows_acquisition_mutex(Path(temporary)):
                self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
