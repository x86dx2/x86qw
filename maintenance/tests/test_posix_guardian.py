import importlib
import importlib.util
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipIf(os.name == "nt", "o Windows usa Job Object com CREATE_SUSPENDED")
class PosixGuardianTests(unittest.TestCase):
    def guardian(self):
        name = "x86qw_runtime.supervisor.posix_guardian"
        self.assertIsNotNone(
            importlib.util.find_spec(name),
            "a fronteira POSIX guardian ainda não existe",
        )
        return importlib.import_module(name)

    def wait_for(self, predicate, *, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail("condição de processo não foi observada antes do timeout")

    def process_exists(self, pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def test_eof_before_release_never_executes_target(self):
        guardian = self.guardian()
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "started"
            handle = guardian.spawn_guardian(
                (
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path(__import__('sys').argv[1]).write_text('yes')",
                    str(marker),
                ),
                cwd=Path(temporary),
            )

            self.assertEqual(0, handle.cancel(timeout=5))

            self.assertFalse(marker.exists())
            self.assertFalse(self.process_exists(handle.pid))

    def test_child_bootstrap_emits_no_runtime_warning(self):
        script = """
from pathlib import Path
from x86qw_runtime.supervisor.posix_guardian import spawn_guardian

handle = spawn_guardian(
    (__import__('sys').executable, '-c', 'raise SystemExit(0)'),
    cwd=Path.cwd(),
)
handle.release(timeout=5)
raise SystemExit(handle.wait(timeout=5))
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("", completed.stderr)

    def test_release_preserves_arguments_cwd_environment_fds_and_exit_code(self):
        guardian = self.guardian()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result = directory / "result.json"
            read_fd, write_fd = os.pipe()
            passed_fd = write_fd
            environment = dict(os.environ)
            environment["X86QW_GUARDIAN_VALUE"] = "valor unicode ç"
            code = """
import json
import os
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(json.dumps({
    "argv": sys.argv[2:],
    "cwd": os.getcwd(),
    "env": os.environ["X86QW_GUARDIAN_VALUE"],
}), encoding="utf-8")
os.write(int(sys.argv[4]), b"fd-herdado")
raise SystemExit(37)
"""
            try:
                handle = guardian.spawn_guardian(
                    (
                        sys.executable,
                        "-c",
                        code,
                        str(result),
                        "argumento com espaço",
                        "unicøde",
                        str(write_fd),
                    ),
                    cwd=directory,
                    env=environment,
                    pass_fds=(write_fd,),
                )
                os.close(write_fd)
                write_fd = -1

                ack = handle.release(timeout=5)
                self.assertEqual(handle.pid, ack.process_group)
                self.assertGreater(ack.pid, 1)
                self.assertEqual(37, handle.wait(timeout=5))
                self.assertEqual(b"fd-herdado", os.read(read_fd, 64))
                self.assertEqual({
                    "argv": ["argumento com espaço", "unicøde", str(passed_fd)],
                    "cwd": str(directory.resolve()),
                    "env": "valor unicode ç",
                }, json.loads(result.read_text(encoding="utf-8")))
            finally:
                os.close(read_fd)
                if write_fd >= 0:
                    os.close(write_fd)

    def test_executable_is_separate_from_literal_target_argv_zero(self):
        guardian = self.guardian()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            handle = guardian.spawn_guardian(
                ("x86qw-original-argv-zero", "60"),
                executable="/bin/sleep",
                cwd=directory,
            )
            ack = handle.release(timeout=5)
            if Path(f"/proc/{ack.pid}/cmdline").is_file():
                observed = Path(f"/proc/{ack.pid}/cmdline").read_bytes().split(b"\0", 1)[0]
                self.assertEqual(b"x86qw-original-argv-zero", observed)
            else:
                observed = subprocess.check_output(
                    ("ps", "-o", "command=", "-p", str(ack.pid)),
                    text=True,
                ).strip()
                self.assertTrue(observed.startswith("x86qw-original-argv-zero "), observed)
            handle.abort(timeout=5)

    def test_launch_target_is_bound_only_after_release_and_rejects_replacement(self):
        from x86qw_runtime.platform.host import executable_launch_target

        guardian = self.guardian()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "service"
            marker = directory / "executed"
            os.link(Path(sys.executable).resolve(), executable)
            payload = executable.read_bytes()
            target = executable_launch_target(
                executable,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )
            handle = guardian.spawn_guardian(
                (
                    str(executable),
                    "-c",
                    "from pathlib import Path; Path(__import__('sys').argv[1]).write_text('bad')",
                    str(marker),
                ),
                launch_target=target,
                cwd=directory,
            )
            replacement = directory / "replacement"
            replacement.symlink_to("/usr/bin/false")
            replacement.replace(executable)

            with self.assertRaisesRegex(
                guardian.GuardianLaunchError,
                "ausente ou inseguro|mudou",
            ):
                handle.release(timeout=5)

            self.assertFalse(marker.exists())
            self.assertIsNotNone(handle.process.returncode)
            self.assertEqual([], list(directory.glob(".x86qw-launch-*")))

    def test_launch_target_snapshot_is_owned_until_exit_and_then_removed(self):
        from x86qw_runtime.platform.host import executable_launch_target

        guardian = self.guardian()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "service"
            marker = directory / "executed"
            os.link(Path(sys.executable).resolve(), executable)
            payload = executable.read_bytes()
            target = executable_launch_target(
                executable,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )
            handle = guardian.spawn_guardian(
                (
                    str(executable),
                    "-c",
                    "from pathlib import Path; Path(__import__('sys').argv[1]).write_text('ok')",
                    str(marker),
                ),
                launch_target=target,
                cwd=directory,
            )

            handle.release(timeout=5)
            self.assertEqual(0, handle.wait(timeout=5))

            self.assertEqual("ok", marker.read_text(encoding="utf-8"))
            self.assertEqual([], list(directory.glob(".x86qw-launch-*")))

    def test_quiet_guardian_preserves_target_contract_and_cleans_snapshot(self):
        from x86qw_runtime.platform.host import executable_launch_target

        guardian = self.guardian()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executable = directory / "client"
            result = directory / "result.json"
            os.link(Path(sys.executable).resolve(), executable)
            payload = executable.read_bytes()
            target = executable_launch_target(
                executable,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )
            environment = dict(os.environ)
            environment["X86QW_QUIET_CONTRACT"] = "preservado"
            code = """
import json
import os
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(json.dumps({
    "argv": sys.argv[2:],
    "cwd": os.getcwd(),
    "env": os.environ["X86QW_QUIET_CONTRACT"],
}), encoding="utf-8")
print("stdout deve ser descartado")
print("stderr deve ser descartado", file=sys.stderr)
"""
            with mock.patch.object(
                guardian.subprocess,
                "Popen",
                wraps=subprocess.Popen,
            ) as popen:
                handle = guardian.spawn_guardian(
                    (
                        str(executable),
                        "-c",
                        code,
                        str(result),
                        "argumento com espaço",
                        "unicøde",
                    ),
                    launch_target=target,
                    cwd=directory,
                    env=environment,
                    quiet=True,
                )
            self.assertIs(subprocess.DEVNULL, popen.call_args.kwargs["stdout"])
            self.assertIs(subprocess.DEVNULL, popen.call_args.kwargs["stderr"])

            handle.release(timeout=5)
            self.assertEqual(0, handle.wait(timeout=5))

            self.assertEqual({
                "argv": ["argumento com espaço", "unicøde"],
                "cwd": str(directory.resolve()),
                "env": "preservado",
            }, json.loads(result.read_text(encoding="utf-8")))
            self.assertEqual([], list(directory.glob(".x86qw-launch-*")))

    def test_quiet_must_be_a_boolean(self):
        guardian = self.guardian()
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "quiet"):
                guardian.spawn_guardian(
                    ("/bin/true",),
                    cwd=Path(temporary),
                    quiet="yes",
                )

    def test_target_and_descendant_share_guardian_group_and_abort_leaves_no_orphan(self):
        guardian = self.guardian()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            child_pid_path = directory / "child.pid"
            code = """
import subprocess
import sys
import time
from pathlib import Path

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
Path(sys.argv[1]).write_text(str(child.pid), encoding="ascii")
time.sleep(60)
"""
            handle = guardian.spawn_guardian(
                (sys.executable, "-c", code, str(child_pid_path)),
                cwd=directory,
            )
            ack = handle.release(timeout=5)
            self.wait_for(child_pid_path.exists)
            child_pid = int(child_pid_path.read_text(encoding="ascii"))

            self.assertEqual(handle.pid, os.getpgid(handle.pid))
            self.assertEqual(handle.pid, os.getpgid(ack.pid))
            self.assertEqual(handle.pid, os.getpgid(child_pid))

            handle.abort(timeout=5)
            self.wait_for(lambda: not self.process_exists(handle.pid))
            self.wait_for(lambda: not self.process_exists(ack.pid))
            self.wait_for(lambda: not self.process_exists(child_pid))

    def test_abort_checks_group_after_guardian_leader_has_exited(self):
        guardian = self.guardian()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            child_pid_path = directory / "child.pid"
            term_marker = directory / "term-seen"
            ready_marker = directory / "child-ready"
            child_code = """
import signal
import sys
import time
from pathlib import Path

marker = Path(sys.argv[1])
ready = Path(sys.argv[2])
signal.signal(signal.SIGTERM, lambda *_: marker.write_text("yes", encoding="ascii"))
ready.write_text("yes", encoding="ascii")
while True:
    time.sleep(0.05)
"""
            target_code = """
import subprocess
import sys
import time
from pathlib import Path

child = subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[3], sys.argv[4]])
Path(sys.argv[2]).write_text(str(child.pid), encoding="ascii")
while not Path(sys.argv[4]).exists():
    time.sleep(0.01)
"""
            handle = guardian.spawn_guardian(
                (
                    sys.executable,
                    "-c",
                    target_code,
                    child_code,
                    str(child_pid_path),
                    str(term_marker),
                    str(ready_marker),
                ),
                cwd=directory,
            )
            handle.release(timeout=5)
            self.wait_for(
                lambda: child_pid_path.is_file()
                and bool(child_pid_path.read_text(encoding="ascii").strip())
            )
            child_pid = int(child_pid_path.read_text(encoding="ascii"))
            self.assertEqual(0, handle.wait(timeout=5))
            self.assertTrue(self.process_exists(child_pid))

            self.assertEqual(0, handle.abort(timeout=0.2))

            self.assertTrue(term_marker.exists(), "o descendente não recebeu SIGTERM")
            self.wait_for(lambda: not self.process_exists(child_pid))
            with self.assertRaises(ProcessLookupError):
                os.killpg(handle.process_group, 0)

    def test_invalid_ack_aborts_group_closes_pipes_and_reaps_guardian(self):
        guardian = self.guardian()
        with tempfile.TemporaryDirectory() as temporary:
            handle = guardian.spawn_guardian(
                (sys.executable, "-c", "import time; time.sleep(60)"),
                cwd=Path(temporary),
            )

            with mock.patch.object(guardian, "_read_all", return_value=b"invalid-json"):
                with mock.patch.object(handle, "abort", wraps=handle.abort) as abort:
                    with self.assertRaisesRegex(
                        guardian.GuardianLaunchError,
                        "invalid acknowledgement",
                    ):
                        handle.release(timeout=5)

            abort.assert_called_once()
            self.assertIsNone(handle._gate_write)
            self.assertIsNone(handle._ack_read)
            self.assertIsNotNone(handle.process.returncode)
            self.assertFalse(self.process_exists(handle.pid))
            with self.assertRaises(ProcessLookupError):
                os.killpg(handle.process_group, 0)

    def test_ack_eof_aborts_group_and_reaps_guardian(self):
        guardian = self.guardian()
        with tempfile.TemporaryDirectory() as temporary:
            handle = guardian.spawn_guardian(
                (sys.executable, "-c", "import time; time.sleep(60)"),
                cwd=Path(temporary),
            )

            with mock.patch.object(guardian, "_read_all", return_value=b""):
                with mock.patch.object(handle, "abort", wraps=handle.abort) as abort:
                    with self.assertRaises(guardian.GuardianLaunchError):
                        handle.release(timeout=5)

            abort.assert_called_once()
            self.assertIsNotNone(handle.process.returncode)
            self.assertFalse(self.process_exists(handle.pid))

    def test_ack_error_remains_primary_when_abort_also_fails(self):
        guardian = self.guardian()
        with tempfile.TemporaryDirectory() as temporary:
            handle = guardian.spawn_guardian(
                (sys.executable, "-c", "import time; time.sleep(60)"),
                cwd=Path(temporary),
            )
            try:
                with mock.patch.object(guardian, "_read_all", return_value=b"invalid"):
                    with mock.patch.object(
                        handle,
                        "abort",
                        side_effect=guardian.GuardianError("cleanup failed"),
                    ):
                        with self.assertRaises(guardian.GuardianLaunchError) as raised:
                            handle.release(timeout=5)
                self.assertIsInstance(raised.exception.__cause__, guardian.GuardianError)
                self.assertEqual("cleanup failed", str(raised.exception.__cause__))
            finally:
                handle.abort(timeout=5)

    def test_launch_target_rejects_arbitrary_objects(self):
        guardian = self.guardian()
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "launch_target"):
                guardian.spawn_guardian(
                    ("/bin/true",),
                    launch_target=object(),
                    cwd=Path(temporary),
                )

    def test_exec_failure_is_reported_and_group_is_reaped(self):
        guardian = self.guardian()
        with tempfile.TemporaryDirectory() as temporary:
            handle = guardian.spawn_guardian(
                (str(Path(temporary) / "missing-runtime"),),
                cwd=Path(temporary),
            )

            with self.assertRaisesRegex(guardian.GuardianLaunchError, "missing-runtime"):
                handle.release(timeout=5)

            self.assertEqual(127, handle.wait(timeout=5))
            self.assertFalse(self.process_exists(handle.pid))

    def test_release_timeout_aborts_guardian_before_target_executes(self):
        guardian = self.guardian()
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "started"
            handle = guardian.spawn_guardian(
                (
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path(__import__('sys').argv[1]).write_text('yes')",
                    str(marker),
                ),
                cwd=Path(temporary),
            )
            os.kill(handle.pid, signal.SIGSTOP)

            with self.assertRaises(guardian.GuardianTimeoutError):
                handle.release(timeout=0.1)

            self.assertFalse(marker.exists())
            self.assertFalse(self.process_exists(handle.pid))


if __name__ == "__main__":
    unittest.main()
