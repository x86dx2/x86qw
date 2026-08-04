import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from x86qw_runtime.errors import InstallerError
from x86qw_runtime.supervisor.core import run_processes
from x86qw_runtime.supervisor.models import ProcessSpec


ROOT = Path(__file__).resolve().parents[2]


@unittest.skipIf(os.name == "nt", "o Windows usa Job Object com CREATE_SUSPENDED")
class SupervisorSpawnJournalAtomicityTests(unittest.TestCase):
    def test_guardian_identity_is_durable_before_runtime_is_marked_ready(self):
        events = []

        class Journal:
            def record_process(self, spec, process, process_group):
                events.append(("pending", process.pid, getattr(
                    process, "_x86qw_runtime_pending", False,
                )))

            def record_process_started(self, process, runtime_pid):
                events.append(("ready", process.pid, runtime_pid))

            def set_status(self, status):
                return None

            def consume_stop_request(self):
                return False

        with tempfile.TemporaryDirectory() as temporary:
            result = run_processes([
                ProcessSpec(
                    "fixture",
                    (sys.executable, "-c", "pass"),
                    Path(temporary),
                ),
            ], Journal())

        self.assertEqual(0, result)
        self.assertEqual("pending", events[0][0])
        self.assertIs(events[0][2], True)
        self.assertEqual("ready", events[1][0])
        self.assertEqual(events[0][1], events[1][1])
        self.assertNotEqual(events[1][1], events[1][2])

    def test_target_exec_failure_is_primary_after_durable_journal(self):
        class Journal:
            def record_process(self, spec, process, process_group):
                return None

            def set_status(self, status):
                return None

            def consume_stop_request(self):
                return False

        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing-runtime"

            with self.assertRaises(InstallerError) as raised:
                run_processes(
                    [ProcessSpec("fixture", (str(missing),), Path(temporary))],
                    Journal(),
                )

            self.assertIn("missing-runtime", str(raised.exception))
            self.assertNotIn("already released", str(raised.exception))

    def test_hard_crash_before_process_journal_never_starts_service(self):
        """Removing the launch gate before journal durability must start an orphan."""

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            service_pid = directory / "service.pid"
            controller_script = """
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from x86qw_runtime.supervisor.core import run_processes
from x86qw_runtime.supervisor.models import ProcessSpec

service_pid = Path(sys.argv[2])
service_code = (
    "import os,time;"
    "from pathlib import Path;"
    "time.sleep(0.15);"
    f"Path({str(service_pid)!r}).write_text(str(os.getpid()), encoding='ascii');"
    "time.sleep(60)"
)

class CrashBeforeDurableJournal:
    def record_process(self, spec, process, process_group):
        os._exit(91)

    def set_status(self, status):
        raise AssertionError("o controlador deve morrer antes de mudar o estado")

    def consume_stop_request(self):
        return False

run_processes(
    [ProcessSpec("fixture", (sys.executable, "-c", service_code), Path.cwd())],
    CrashBeforeDurableJournal(),
)
"""
            controller = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    controller_script,
                    str(ROOT),
                    str(service_pid),
                ],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.assertEqual(91, controller.wait(timeout=10))

            deadline = time.monotonic() + 2
            while not service_pid.exists() and time.monotonic() < deadline:
                time.sleep(0.02)

            orphan_pid = None
            if service_pid.exists():
                orphan_pid = int(service_pid.read_text(encoding="ascii"))
            try:
                self.assertFalse(
                    service_pid.exists(),
                    "o serviço executou antes de sua identidade ficar durável no journal",
                )
            finally:
                if orphan_pid is not None:
                    try:
                        os.killpg(orphan_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


if __name__ == "__main__":
    unittest.main()
