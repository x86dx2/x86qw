from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from x86qw_runtime.platform import host
from x86qw_runtime.platform.host import executable_launch_target
from x86qw_runtime.supervisor.core import spawn_detached_client


@unittest.skipIf(os.name == "nt", "o executável adversarial usa shebang POSIX")
class LaunchTargetBindingTests(unittest.TestCase):
    @staticmethod
    def _portable_executable(destination: Path) -> None:
        """Use a self-contained POSIX binary, not a relocatable Python runtime."""

        source_name = shutil.which("sh")
        if source_name is None:
            raise unittest.SkipTest("um shell POSIX não está disponível")
        source = Path(source_name).resolve()
        shutil.copyfile(source, destination)
        shutil.copymode(source, destination)

    @staticmethod
    def _tree_snapshot(root: Path) -> tuple[tuple[str, str, bytes], ...]:
        return tuple(
            (
                str(path.relative_to(root)),
                "directory" if path.is_dir() else "file",
                b"" if path.is_dir() else path.read_bytes(),
            )
            for path in sorted(root.rglob("*"))
        )

    def test_detached_spawn_executes_the_validated_inode_after_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "client"
            marker = root / "executed.txt"
            executable.write_text(
                '#!/bin/sh\nprintf original > "$1"\n',
                encoding="utf-8",
            )
            executable.chmod(0o700)
            original = executable.read_bytes()
            target = executable_launch_target(
                executable,
                expected_sha256=hashlib.sha256(original).hexdigest(),
            )
            replacement = root / "replacement"
            replacement.symlink_to("/usr/bin/false")

            def replace_then_spawn(arguments: tuple[str, ...], **options: object):
                replacement.replace(executable)
                return subprocess.Popen(arguments, **options)

            process = spawn_detached_client(
                (
                    str(executable),
                    str(marker),
                ),
                root,
                launch_target=target,
                process_factory=replace_then_spawn,
                os_name="posix",
            )
            self.assertEqual(0, process.wait(timeout=5))
            self.assertEqual("original", marker.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 2
            while list(root.glob(".x86qw-launch-*")) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual([], list(root.glob(".x86qw-launch-*")))

    def test_detached_spawn_executes_validated_bytes_after_same_inode_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "client"
            marker = root / "executed.txt"
            original = b'#!/bin/sh\nprintf original > "$1"\n'
            executable.write_bytes(original)
            executable.chmod(0o755)
            target = executable_launch_target(
                executable,
                expected_sha256=hashlib.sha256(original).hexdigest(),
            )

            def overwrite_then_spawn(arguments: tuple[str, ...], **options: object):
                executable.write_bytes(b'#!/bin/sh\nprintf replacement > "$1"\n')
                executable.chmod(0o755)
                return subprocess.Popen(arguments, **options)

            process = spawn_detached_client(
                (str(executable), str(marker)),
                root,
                launch_target=target,
                process_factory=overwrite_then_spawn,
                os_name="posix",
            )
            self.assertEqual(0, process.wait(timeout=5))
            self.assertEqual("original", marker.read_text(encoding="utf-8"))

    def test_failed_spawn_removes_the_bound_launch_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "client"
            self._portable_executable(executable)
            payload = executable.read_bytes()
            target = executable_launch_target(
                executable,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )

            with self.assertRaisesRegex(OSError, "spawn refused"):
                spawn_detached_client(
                    (str(executable),),
                    root,
                    launch_target=target,
                    process_factory=lambda *_args, **_options: (_ for _ in ()).throw(
                        OSError("spawn refused")
                    ),
                    os_name="posix",
                )

            self.assertEqual([], list(root.glob(".x86qw-launch-*")))

    def test_darwin_binding_does_not_mutate_the_managed_runtime_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "client"
            self._portable_executable(executable)
            payload = executable.read_bytes()
            target = executable_launch_target(
                executable,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )
            baseline = self._tree_snapshot(root)
            release = threading.Event()

            class Process:
                pid = 42

                def wait(self, timeout: float | None = None) -> int:
                    if not release.wait(timeout):
                        raise subprocess.TimeoutExpired("client", timeout)
                    return 0

            with mock.patch.object(host.platform, "system", return_value="Darwin"):
                process = spawn_detached_client(
                    (str(executable),),
                    root,
                    launch_target=target,
                    process_factory=lambda *_args, **_options: Process(),
                    os_name="posix",
                )

            try:
                self.assertTrue(
                    baseline == self._tree_snapshot(root),
                    "a árvore gerenciada mudou enquanto o processo estava ativo",
                )
            finally:
                release.set()
                self.assertEqual(0, process.wait(timeout=2))

    def test_cleanup_does_not_mask_spawn_failure_if_runtime_parent_moves(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "runtime"
            moved = parent / "moved-runtime"
            root.mkdir()
            executable = root / "client"
            self._portable_executable(executable)
            payload = executable.read_bytes()
            target = executable_launch_target(
                executable,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )

            def move_then_fail(*_args: object, **_options: object) -> None:
                root.rename(moved)
                raise OSError("spawn refused")

            with mock.patch.object(host.platform, "system", return_value="Darwin"):
                with self.assertRaisesRegex(OSError, "spawn refused"):
                    spawn_detached_client(
                        (str(executable),),
                        root,
                        launch_target=target,
                        process_factory=move_then_fail,
                        os_name="posix",
                    )


class WindowsLaunchTargetBindingContractTests(unittest.TestCase):
    def test_windows_handle_guard_is_held_through_process_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "client.exe"
            payload = b"validated Windows executable"
            executable.write_bytes(payload)
            target = executable_launch_target(
                executable,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )

            class WindowsApi:
                GENERIC_READ = 0x80000000
                FILE_READ_ATTRIBUTES = 0x00000080
                OPEN_EXISTING = 3

                def __init__(self) -> None:
                    self.open = False

                def open_handle(self, path: Path, *, access: int, creation: int, directory: bool) -> int:
                    self.open = True
                    return 42

                def checked_identity(self, handle: int, *, directory: bool) -> tuple[int, int]:
                    return target.paths[-1].identity

                def size(self, handle: int) -> int:
                    return len(payload)

                def hash(self, handle: int, *, expected_size: int) -> str:
                    return hashlib.sha256(payload).hexdigest()

                def close(self, handle: int) -> None:
                    self.open = False

            api = WindowsApi()
            open_during_spawn = False

            def spawn(arguments: tuple[str, ...], **options: object):
                nonlocal open_during_spawn
                open_during_spawn = api.open
                return SimpleNamespace(pid=42)

            with mock.patch.object(
                host.managed_files, "_get_windows_file_api", return_value=api,
            ), mock.patch.object(host.platform, "system", return_value="Windows"):
                process = spawn_detached_client(
                    (str(executable),),
                    root,
                    launch_target=target,
                    process_factory=spawn,
                    os_name="nt",
                )

            self.assertEqual(42, process.pid)
            self.assertTrue(open_during_spawn)
            self.assertFalse(api.open)

    @unittest.skipUnless(os.name == "nt", "o compartilhamento Win32 exige runner Windows")
    def test_windows_guard_blocks_replacement_during_process_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = root / "client.exe"
            replacement = root / "replacement.exe"
            executable.write_bytes(b"validated executable")
            replacement.write_bytes(b"replacement executable")
            payload = executable.read_bytes()
            target = executable_launch_target(
                executable,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )
            replacement_blocked = False

            def replace_then_return(arguments: tuple[str, ...], **options: object):
                nonlocal replacement_blocked
                try:
                    os.replace(replacement, executable)
                except OSError:
                    replacement_blocked = True
                return SimpleNamespace(pid=42)

            process = spawn_detached_client(
                (str(executable),),
                root,
                launch_target=target,
                process_factory=replace_then_return,
                os_name="nt",
            )

            self.assertEqual(42, process.pid)
            self.assertTrue(replacement_blocked)
            self.assertEqual(payload, executable.read_bytes())


if __name__ == "__main__":
    unittest.main()
