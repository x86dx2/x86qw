import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dist/installer/bin"))

import services  # noqa: E402
from x86qw_runtime import session_control  # noqa: E402
from x86qw_runtime.io import private_fs  # noqa: E402
from x86qw_runtime.supervisor import sessions  # noqa: E402


def _write_private(path: Path, payload: bytes) -> tuple[int, int]:
    descriptor = private_fs.create_private_file(path)
    identity = os.fstat(descriptor)
    with os.fdopen(descriptor, "wb") as output:
        output.write(payload)
    return int(identity.st_dev), int(identity.st_ino)


def _owner(target: Path, session_id: str) -> dict[str, object]:
    return {
        "format": 3,
        "project": "x86qw",
        "session_id": session_id,
        "operation_kind": "service",
        "command": "host",
        "controller_pid": 999999999,
        "controller_start_token": "dead-token",
        "controller_executable": str(target / "dead-controller"),
        "created_at": "2026-08-04T00:00:00+00:00",
        "installation": str(target),
        "private_filesystem": 1,
    }


class SupervisorOwnershipRaceTests(unittest.TestCase):
    def _run_background_race(self, race: str) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        target = Path(temporary.name) / "target"
        target.mkdir()
        private_fs.ensure_private_directories(target / ".x86qw", stop=target)
        script = r"""
import os
import sys
from pathlib import Path

from x86qw_runtime.io import private_fs
from x86qw_runtime.supervisor import sessions

target = Path(sys.argv[1])
race = sys.argv[2]
directory = target / '.x86qw/logs'
log = directory / 'service-fixture.log'
real_create_directory = private_fs.create_private_directory
real_ensure_directory = private_fs.ensure_private_directory
real_create_file = private_fs.create_private_file
real_open_append = private_fs.open_private_append
injected = False

def inject_directory(path):
    global injected
    if race == 'directory' and path == directory and not injected:
        injected = True
        real_create_directory(path)

def create_directory(path):
    inject_directory(path)
    return real_create_directory(path)

def ensure_directory(path):
    inject_directory(path)
    return real_ensure_directory(path)

def inject_log(path):
    global injected
    if race == 'log' and path == log and not injected:
        injected = True
        descriptor = real_create_file(path)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(b'concurrent log\n')

def create_file(path):
    inject_log(path)
    return real_create_file(path)

def open_append(path):
    inject_log(path)
    return real_open_append(path)

private_fs.create_private_directory = create_directory
private_fs.ensure_private_directory = ensure_directory
private_fs.create_private_file = create_file
private_fs.open_private_append = open_append
real_dup2 = os.dup2
calls = 0
def fail_first_dup2(source, destination, inheritable=True):
    global calls
    calls += 1
    if calls == 1:
        raise OSError('injected redirection failure')
    return real_dup2(source, destination, inheritable=inheritable)
sessions.os.dup2 = fail_first_dup2
try:
    sessions.activate_background_log(
        target, '.x86qw/logs/service-fixture.log',
    )
except OSError:
    pass
else:
    raise AssertionError('redirection failure was not propagated')
"""
        completed = subprocess.run(
            [sys.executable, "-c", script, str(target), race],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        return target

    def test_background_failure_preserves_concurrently_created_directory(self):
        target = self._run_background_race("directory")

        self.assertTrue((target / ".x86qw/logs").is_dir())

    def test_background_failure_preserves_concurrently_created_log(self):
        target = self._run_background_race("log")
        log = target / ".x86qw/logs/service-fixture.log"

        self.assertTrue(log.is_file())
        self.assertEqual(
            b"concurrent log\n",
            log.read_bytes(),
        )

    def test_consuming_stop_request_preserves_replacement_after_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            journal = sessions.SessionJournal(target, session_id="consume-race")
            request = journal.directory / "stop.request"
            payload = json.dumps({
                "format": 1,
                "project": "x86qw",
                "session_id": journal.session_id,
                "requested_at": "2026-08-04T00:00:00+00:00",
            }).encode("utf-8")
            _write_private(request, payload)
            real_read = private_fs.read_private_file
            replacement = b"concurrent request\n"

            def replace_after_read(path: Path, *, maximum_size: int) -> bytes:
                read = real_read(path, maximum_size=maximum_size)
                path.unlink()
                _write_private(path, replacement)
                return read

            with mock.patch.object(
                private_fs, "read_private_file", side_effect=replace_after_read,
            ), self.assertRaises(services.InstallerError):
                journal.consume_stop_request()

            self.assertEqual(replacement, request.read_bytes())

    def test_stop_cancellation_preserves_replacement_of_published_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            lock = services.SessionLock.acquire(target, "host")
            journal = sessions.SessionJournal(
                target, session_id=lock.session_id, controller=lock.owner,
            )
            replacement = b"concurrent cancellation request\n"
            real_publish = sessions.publish_stop_request

            def replace_after_publish(request: Path, payload: bytes):
                identity = real_publish(request, payload)
                request.unlink()
                _write_private(request, replacement)
                lock.release()
                return identity

            try:
                with mock.patch.object(
                    services, "publish_stop_request", side_effect=replace_after_publish,
                ), self.assertRaises(services.InstallerError):
                    services.request_service_stop(target, timeout=0.1)
                request = journal.directory / "stop.request"
                self.assertEqual(replacement, request.read_bytes())
            finally:
                request = journal.directory / "stop.request"
                if request.exists():
                    request.unlink()
                if lock.path.exists():
                    lock.release()

    def test_lock_release_preserves_same_session_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            lock = session_control.InstallationLock.acquire(target, "host", "service")
            replacement = (
                json.dumps(lock.owner, ensure_ascii=False, sort_keys=True) + "\n"
            ).encode("utf-8")
            lock.path.unlink()
            _write_private(lock.path, replacement)

            with self.assertRaises(OSError):
                lock.release()

            self.assertEqual(replacement, lock.path.read_bytes())
            lock.path.unlink()

    def test_confirm_recovery_preserves_reclaimed_path_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            sessions_root = target / ".x86qw/sessions"
            private_fs.ensure_private_directories(sessions_root, stop=target)
            active = sessions_root / "active.lock"
            _write_private(
                active,
                (json.dumps(_owner(target, "stale-session")) + "\n").encode("utf-8"),
            )
            lock = session_control.InstallationLock.acquire(target, "host", "service")
            self.assertIsNotNone(lock.reclaimed_path)
            reclaimed = lock.reclaimed_path
            assert reclaimed is not None
            reclaimed.unlink()
            replacement = b"concurrent reclaimed lock\n"
            _write_private(reclaimed, replacement)

            try:
                with self.assertRaises(OSError):
                    lock.confirm_recovery()
                self.assertEqual(replacement, reclaimed.read_bytes())
            finally:
                if reclaimed.exists():
                    reclaimed.unlink()
                lock.reclaimed_path = None
                lock.release()

    def test_reclaim_never_overwrites_concurrent_candidate_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            sessions_root = target / ".x86qw/sessions"
            private_fs.ensure_private_directories(sessions_root, stop=target)
            active = sessions_root / "active.lock"
            _write_private(
                active,
                (json.dumps(_owner(target, "stale-session")) + "\n").encode("utf-8"),
            )
            candidate = sessions_root / ".active.lock.reclaimed.new-session"
            concurrent = b"concurrent reclaim candidate\n"
            _write_private(candidate, concurrent)

            with mock.patch.object(
                session_control, "new_session_id", return_value="new-session",
            ), self.assertRaises(OSError):
                session_control.InstallationLock.acquire(target, "host", "service")

            self.assertEqual(concurrent, candidate.read_bytes())
            self.assertTrue(active.exists())

    def test_restore_reclaimed_lock_never_overwrites_concurrent_active_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            sessions_root = target / ".x86qw/sessions"
            private_fs.ensure_private_directories(sessions_root, stop=target)
            active = sessions_root / "active.lock"
            _write_private(
                active,
                (json.dumps(_owner(target, "stale-session")) + "\n").encode("utf-8"),
            )
            lock = session_control.InstallationLock.acquire(target, "host", "service")
            reclaimed = lock.reclaimed_path
            assert reclaimed is not None
            concurrent_owner = _owner(target, "concurrent-session")
            concurrent = (
                json.dumps(concurrent_owner, ensure_ascii=False, sort_keys=True) + "\n"
            ).encode("utf-8")
            real_replace = os.replace
            real_link = os.link
            injected = False

            def inject() -> None:
                nonlocal injected
                if not injected:
                    injected = True
                    _write_private(active, concurrent)

            def replace(source: Path, destination: Path) -> None:
                if Path(source) == reclaimed and Path(destination) == active:
                    inject()
                real_replace(source, destination)

            def link(source: Path, destination: Path) -> None:
                if Path(source) == reclaimed and Path(destination) == active:
                    inject()
                real_link(source, destination)

            try:
                with mock.patch.object(session_control.os, "replace", side_effect=replace), \
                     mock.patch.object(session_control.os, "link", side_effect=link):
                    try:
                        lock.release(restore_reclaimed=True)
                    except OSError:
                        pass
                self.assertEqual("concurrent-session", json.loads(active.read_text())["session_id"])
                self.assertTrue(reclaimed.exists())
            finally:
                if active.exists():
                    active.unlink()
                if reclaimed.exists():
                    reclaimed.unlink()

    def test_initial_journal_failure_preserves_replacement_session_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            replacement: Path | None = None

            def replace_directory(_journal: sessions.SessionJournal) -> None:
                nonlocal replacement
                replacement = _journal.directory
                replacement.rmdir()
                replacement.mkdir(mode=0o700)
                raise RuntimeError("injected initial write failure")

            with mock.patch.object(
                sessions.SessionJournal, "_write", autospec=True,
                side_effect=replace_directory,
            ), self.assertRaisesRegex(RuntimeError, "initial write failure"):
                sessions.SessionJournal(target, session_id="journal-init-race")

            assert replacement is not None
            self.assertTrue(replacement.is_dir())


if __name__ == "__main__":
    unittest.main()
