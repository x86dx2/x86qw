from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

from x86qw_runtime import migrations


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "x86qw_migration_manager_test", ROOT / "dist/installer/bin/manager.py",
)
manager = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = manager
SPEC.loader.exec_module(manager)


def _snapshot(root: Path) -> tuple[tuple[str, str, int, bytes | str], ...]:
    entries: list[tuple[str, str, int, bytes | str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if path.is_symlink():
            entries.append((relative, "symlink", 0, path.readlink().as_posix()))
        elif path.is_dir():
            entries.append((relative, "directory", 0, ""))
        elif path.is_file():
            entries.append((relative, "file", metadata.st_mode, path.read_bytes()))
        else:
            entries.append((relative, "special", metadata.st_mode, ""))
    return tuple(entries)


class MigrationManagerBoundaryTests(unittest.TestCase):
    def test_migrate_dry_run_is_zero_write_and_does_not_create_a_lock(self) -> None:
        fixture = ROOT / "maintenance/tests/fixtures/migrations/0.7.3"
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "installation"
            shutil.copytree(fixture, target)
            before = _snapshot(target)
            output = io.StringIO()
            errors = io.StringIO()
            manager.console.configure(verbose=False, no_color=True)
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                result = manager.main(["migrate", "--dry-run", str(target)])

            self.assertEqual(0, result, errors.getvalue())
            self.assertIn("Simulação concluída", output.getvalue())
            self.assertEqual(before, _snapshot(target))
            self.assertFalse((target / ".x86qw/sessions/active.lock").exists())

    def test_migrate_recovers_a_pending_journal_only_on_non_dry_run(self) -> None:
        fixture = ROOT / "maintenance/tests/fixtures/migrations/0.7.3"
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "installation"
            shutil.copytree(fixture, target)
            script = """
import os
from pathlib import Path
from x86qw_runtime import migrations

root = Path(os.environ["MIGRATION_ROOT"])
plan = migrations.plan_migration(root, source_version="0.7.3")
persist = migrations._persist_migration_journal

def crash(path, document):
    persist(path, document)
    if document.get("phase") == "commit" and any(
        operation.get("status") == "committed"
        for operation in document.get("operations", [])
    ):
        os._exit(73)

migrations._persist_migration_journal = crash
migrations.execute_migration(plan)
"""
            environment = os.environ.copy()
            environment["MIGRATION_ROOT"] = str(target)
            crashed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=ROOT,
                env=environment,
                check=False,
            )
            self.assertEqual(73, crashed.returncode)
            self.assertIsNotNone(migrations.inspect_pending_migration(target))

            dry_output = io.StringIO()
            dry_errors = io.StringIO()
            with contextlib.redirect_stdout(dry_output), contextlib.redirect_stderr(dry_errors):
                dry_result = manager.main(["--no-color", "migrate", "--dry-run", str(target)])
            self.assertEqual(1, dry_result)
            self.assertIsNotNone(migrations.inspect_pending_migration(target))

            output = io.StringIO()
            errors = io.StringIO()
            manager.console.configure(verbose=False, no_color=True)
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                result = manager.main(["--no-color", "migrate", str(target)])

            self.assertEqual(0, result, errors.getvalue())
            self.assertIn("Migração concluída", output.getvalue())
            self.assertIsNone(migrations.inspect_pending_migration(target))
            self.assertTrue((target / ".x86qw/components/ktx/receipt").is_file())

    def test_migrate_is_an_exclusive_maintenance_lock_command(self) -> None:
        self.assertIn("migrate", manager.session_control.LOCK_COMMANDS)
        self.assertIn("migrate", manager.session_control.MAINTENANCE_COMMANDS)


if __name__ == "__main__":
    unittest.main()
