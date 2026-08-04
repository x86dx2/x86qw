from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from x86qw_runtime import session_control
from x86qw_runtime.errors import InstallerError
from x86qw_runtime.supervisor.models import ProcessSpec
from x86qw_runtime.supervisor.sessions import (
    SessionJournal,
    journal_process_probe,
    load_session_journal,
)


class GuardianSessionJournalTests(unittest.TestCase):
    @staticmethod
    def _process(*, pending: bool) -> SimpleNamespace:
        process = SimpleNamespace(pid=os.getpid())
        if pending:
            process._x86qw_runtime_pending = True
        return process

    @staticmethod
    def _spec(root: Path) -> ProcessSpec:
        return ProcessSpec("MVDSV", (str(root / "mvdsv"),), root)

    def test_pending_record_keeps_guardian_identity_and_runtime_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = SessionJournal(root)
            process = self._process(pending=True)
            identity = session_control.process_identity(process.pid).identity
            self.assertIsNotNone(identity)

            journal.record_process(self._spec(root), process, process.pid)

            entry = json.loads(journal.path.read_text(encoding="utf-8"))["processes"][0]
            for field in ("runtime_executable", "runtime_pid", "state"):
                self.assertIn(field, entry)
            self.assertEqual(identity.executable, entry["executable"])
            self.assertEqual(identity.creation_token, entry["creation_token"])
            self.assertEqual(str(root / "mvdsv"), entry["runtime_executable"])
            self.assertIsNone(entry["runtime_pid"])
            self.assertEqual("pending", entry["state"])
            self.assertEqual("alive", journal_process_probe(entry).status)

    def test_direct_process_is_recorded_as_ready_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = SessionJournal(root)
            process = self._process(pending=False)

            journal.record_process(self._spec(root), process, process.pid)

            entry = json.loads(journal.path.read_text(encoding="utf-8"))["processes"][0]
            self.assertIn("runtime_pid", entry)
            self.assertIn("state", entry)
            self.assertEqual(process.pid, entry["runtime_pid"])
            self.assertEqual("ready", entry["state"])

    def test_started_runtime_promotes_only_the_matching_guardian_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = SessionJournal(root)
            process = self._process(pending=True)
            journal.record_process(self._spec(root), process, process.pid)

            self.assertTrue(callable(getattr(journal, "record_process_started", None)))
            journal.record_process_started(process, 4242)

            entry = json.loads(journal.path.read_text(encoding="utf-8"))["processes"][0]
            self.assertEqual(4242, entry["runtime_pid"])
            self.assertEqual("ready", entry["state"])

            entry["state"] = "pending"
            entry["runtime_pid"] = None
            entry["creation_token"] = "reused-pid"
            journal.data["processes"] = [entry]
            journal._write()
            with self.assertRaises(InstallerError):
                journal.record_process_started(process, 4343)
            self.assertEqual(
                "pending",
                json.loads(journal.path.read_text(encoding="utf-8"))["processes"][0]["state"],
            )

    def test_started_runtime_rejects_invalid_pid_and_missing_entry(self) -> None:
        for runtime_pid in (True, 0, -1):
            with self.subTest(runtime_pid=runtime_pid), tempfile.TemporaryDirectory() as temporary:
                journal = SessionJournal(Path(temporary))
                self.assertTrue(callable(getattr(journal, "record_process_started", None)))
                with self.assertRaises(InstallerError):
                    journal.record_process_started(self._process(pending=True), runtime_pid)

    def test_legacy_process_metadata_loads_as_ready_format_one(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = SessionJournal(root)
            journal.data["processes"] = [{
                "label": "MVDSV",
                "pid": 123,
                "executable": "/legacy/guardian",
                "creation_token": "legacy-token",
            }]
            journal._write()

            loaded = load_session_journal(journal.path)

            self.assertEqual(1, loaded["format"])
            entry = loaded["processes"][0]
            for field in ("runtime_executable", "runtime_pid", "state"):
                self.assertIn(field, entry)
            self.assertEqual("/legacy/guardian", entry["runtime_executable"])
            self.assertEqual(123, entry["runtime_pid"])
            self.assertEqual("ready", entry["state"])

    def test_process_metadata_schema_rejects_incoherent_state(self) -> None:
        invalid_entries = (
            {"runtime_executable": "/runtime", "runtime_pid": 123, "state": "pending"},
            {"runtime_executable": "/runtime", "runtime_pid": None, "state": "ready"},
            {"runtime_executable": 123, "runtime_pid": 123, "state": "ready"},
        )
        for index, invalid in enumerate(invalid_entries):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as temporary:
                journal = SessionJournal(Path(temporary), session_id=f"invalid-{index}")
                journal.data["processes"] = [{
                    "label": "MVDSV",
                    "pid": 123,
                    "executable": "/guardian",
                    "creation_token": "token",
                    **invalid,
                }]
                journal._write()
                with self.assertRaises(InstallerError):
                    load_session_journal(journal.path)

    def test_promotion_write_failure_restores_pending_memory_and_disk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = SessionJournal(root)
            process = self._process(pending=True)
            journal.record_process(self._spec(root), process, process.pid)

            self.assertTrue(callable(getattr(journal, "record_process_started", None)))
            with mock.patch.object(
                journal,
                "_write",
                side_effect=InstallerError("journal failed"),
            ), self.assertRaisesRegex(InstallerError, "journal failed"):
                journal.record_process_started(process, 4242)

            memory_entry = journal.data["processes"][0]
            disk_entry = json.loads(journal.path.read_text(encoding="utf-8"))["processes"][0]
            self.assertEqual("pending", memory_entry["state"])
            self.assertIsNone(memory_entry["runtime_pid"])
            self.assertEqual("pending", disk_entry["state"])
            self.assertIsNone(disk_entry["runtime_pid"])


if __name__ == "__main__":
    unittest.main()
