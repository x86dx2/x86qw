import ctypes
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dist/installer/bin"))

import services  # noqa: E402
from maintenance.tools import downloader  # noqa: E402
from x86qw_runtime.io import private_fs  # noqa: E402
from x86qw_runtime.platform import windows_acl  # noqa: E402


class PrivateFilesystemContractTests(unittest.TestCase):
    def test_private_sddl_has_only_current_user_and_system(self):
        user = "S-1-5-21-100-200-300-400"
        self.assertEqual(
            f"O:{user}D:P(A;;FA;;;{user})(A;;FA;;;S-1-5-18)",
            windows_acl.private_sddl(user, directory=False),
        )
        self.assertEqual(
            f"O:{user}D:P(A;OICI;FA;;;{user})(A;OICI;FA;;;S-1-5-18)",
            windows_acl.private_sddl(user, directory=True),
        )
        for forbidden in ("WD", "BU", "BA", "AU"):
            self.assertNotIn(f";;;{forbidden})", windows_acl.private_sddl(user, directory=True))

    def test_private_sddl_rejects_an_invalid_sid(self):
        for value in (
            "", "user", "S-1-5-18 injected", " S-1-5-18",
            "S-1-5-21)(A;;FA;;;WD",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                windows_acl.private_sddl(value, directory=False)

    def test_win32_file_disposition_uses_the_one_byte_boolean_abi(self):
        self.assertEqual(1, ctypes.sizeof(windows_acl._FileDispositionInfo))

    def test_managed_reads_allow_atomic_metadata_mutation_but_password_reads_do_not(self):
        managed = windows_acl._private_read_share_mode(exact=True)
        external = windows_acl._private_read_share_mode(exact=False)
        self.assertEqual(
            windows_acl._WindowsApi.FILE_SHARE_READ
            | windows_acl._WindowsApi.FILE_SHARE_WRITE
            | windows_acl._WindowsApi.FILE_SHARE_DELETE,
            managed,
        )
        self.assertEqual(windows_acl._WindowsApi.FILE_SHARE_READ, external)

    @unittest.skipIf(os.name == "nt", "modos POSIX são validados nos runners Unix")
    def test_posix_private_creation_is_owner_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = root / "private"
            private_fs.ensure_private_directory(private)
            descriptor, path = private_fs.private_mkstemp(
                directory=root, prefix="secret-", suffix=".cfg",
            )
            os.close(descriptor)
            self.assertEqual(0o700, stat.S_IMODE(private.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            private_fs.validate_private_directory(private)
            private_fs.validate_private_file(path)

    def test_sensitive_config_fails_before_writing_when_private_creation_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with mock.patch.object(
                services.private_fs,
                "private_mkstemp",
                side_effect=private_fs.PrivateFilesystemError("ACL indisponível"),
            ):
                with self.assertRaisesRegex(
                    services.InstallerError, "acesso privado",
                ):
                    services.temporary_config(
                        directory, "secret-", ["rcon_password segredo"], sensitive=True,
                    )
            self.assertEqual([], list(directory.iterdir()))


@unittest.skipUnless(os.name == "nt", "DACL nativa é exercitada somente no runner Windows")
class WindowsPrivateAclTests(unittest.TestCase):
    def _apply_sddl(self, path: Path, sddl: str, *, directory: bool) -> None:
        api = windows_acl._api()
        handle = windows_acl._open_path(path, directory=directory, writable_dacl=True)
        try:
            with windows_acl._descriptor_from_sddl(sddl) as (_, dacl):
                result = api.advapi32.SetSecurityInfo(
                    handle,
                    api.SE_FILE_OBJECT,
                    api.DACL_SECURITY_INFORMATION | api.PROTECTED_DACL_SECURITY_INFORMATION,
                    None,
                    None,
                    dacl,
                    None,
                )
                if result:
                    raise api.error("SetSecurityInfo test fixture failed", int(result))
        finally:
            api.kernel32.CloseHandle(handle)

    def _make_broad_parent(self, root: Path) -> str:
        user = windows_acl.current_user_sid()
        self._apply_sddl(
            root,
            (
                f"O:{user}D:P"
                f"(A;OICI;FA;;;{user})"
                "(A;OICI;GR;;;WD)"
                "(A;OICI;GR;;;BU)"
            ),
            directory=True,
        )
        return user

    def _assert_canonical(self, path: Path, *, directory: bool) -> None:
        acl = windows_acl.validate_private_path(path, directory=directory)
        self.assertTrue(acl.protected)
        self.assertEqual(
            {windows_acl.current_user_sid(), windows_acl.SYSTEM_SID},
            set(acl.principals),
        )
        self.assertEqual(2, len(acl.principals))

    def test_native_api_signatures_are_explicit(self):
        for function in windows_acl.api_functions():
            self.assertIsNotNone(function.argtypes, function)
            self.assertIsNotNone(function.restype, function)

    def test_precreated_broad_installation_mutex_is_rejected(self):
        from ctypes import wintypes

        class SecurityAttributes(ctypes.Structure):
            _fields_ = (
                ("length", wintypes.DWORD),
                ("security_descriptor", ctypes.c_void_p),
                ("inherit_handle", wintypes.BOOL),
            )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            ctypes.POINTER(SecurityAttributes), wintypes.BOOL, wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            name = services.session_control._windows_acquisition_mutex_name(target)
            user = windows_acl.current_user_sid()
            with windows_acl._descriptor_from_sddl(
                f"O:{user}D:P(A;;GA;;;{user})(A;;GA;;;WD)",
            ) as (descriptor, _):
                attributes = SecurityAttributes(
                    ctypes.sizeof(SecurityAttributes), descriptor, False,
                )
                hostile = kernel32.CreateMutexW(ctypes.byref(attributes), False, name)
            self.assertTrue(hostile)
            try:
                with self.assertRaisesRegex(
                    services.session_control.SessionControlError, "mutex.*privad",
                ):
                    with services.session_control._windows_acquisition_mutex(target):
                        self.fail("mutex hostil foi aceito")
            finally:
                kernel32.CloseHandle(hostile)

    def test_generated_objects_override_everyone_and_users_inheritance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._make_broad_parent(root)
            target = root / "quake-world"
            target.mkdir()
            (target / "qw").mkdir()

            lock = services.SessionLock.acquire(target, "host")
            journal = services.SessionJournal(
                target, session_id=lock.session_id, controller=lock.owner,
            )
            sensitive = services.temporary_config(
                target / "qw", "x86qw-sensitive-", ["rcon_password segredo"],
                journal, sensitive=True,
            )
            stop = journal.directory / "stop.request"
            services.publish_stop_request(stop, b'{"format":1}\n')
            log = target / ".x86qw/logs/service-native.log"
            with mock.patch.object(services.os, "dup2"):
                services.activate_background_log(target, ".x86qw/logs/service-native.log")
            ktx = services.gameplay.write_ktx_runtime_config(
                target, (("k_fb_name_0", "\\x10Luffy"),),
            )
            try:
                for directory in (
                    target / ".x86qw",
                    target / ".x86qw/sessions",
                    journal.directory,
                    target / ".x86qw/logs",
                ):
                    self._assert_canonical(directory, directory=True)
                for path in (lock.path, journal.path, sensitive, stop, log, ktx.path):
                    self._assert_canonical(path, directory=False)
            finally:
                services.unlink_stop_request(stop)
                journal.release_sensitive_temporary(sensitive)
                services.unlink_sensitive_temporary(sensitive)
                services.gameplay.remove_ktx_runtime_config(ktx)
                lock.release()

    def test_downloader_temporary_is_private_before_first_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._make_broad_parent(root)
            output, path = downloader._open_temporary(root / "installer.zip")
            metadata = os.fstat(output.fileno())
            identity = (int(metadata.st_dev), int(metadata.st_ino))
            try:
                self._assert_canonical(path, directory=False)
                self.assertEqual(0, metadata.st_size)
            finally:
                output.close()
                private_fs.unlink_private_file(path, expected_identity=identity)

    def test_update_handoff_stage_overrides_broad_parent_before_download(self):
        class StopAfterAclCheck(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._make_broad_parent(root)
            target = root / "quake-world"
            target.mkdir()
            installer = services.core.Installer(ROOT, target, root / "cache")
            observed: list[Path] = []

            def inspect_stage(_package):
                assert installer.stage is not None
                self.assertNotEqual(target, installer.stage)
                self.assertNotIn(target, installer.stage.parents)
                self._assert_canonical(installer.stage, directory=True)
                observed.append(installer.stage)
                raise StopAfterAclCheck("stage validado antes do download")

            with mock.patch.object(
                installer, "installer_bundle_record", return_value={"version": "1.0.5"},
            ), mock.patch.object(
                installer, "installed_cli_version", return_value="1.0.4",
            ), mock.patch.object(
                installer, "download_component_package", side_effect=inspect_stage,
            ):
                with self.assertRaisesRegex(StopAfterAclCheck, "stage validado"):
                    installer.handoff_cli_update("update", dry_run=False)

            self.assertEqual(1, len(observed))
            self.assertFalse(observed[0].exists())
            self.assertFalse((target / ".x86qw/staging").exists())
            self.assertFalse((target / ".x86qw").exists())
            self.assertFalse(any(target.glob(".x86qw-update.*")))

    def test_private_creation_rejects_a_junction_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside"
            outside.mkdir()
            junction = root / "junction"
            completed = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", "junction", "outside"],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            try:
                with self.assertRaises(windows_acl.WindowsAclError):
                    private_fs.private_mkstemp(
                        directory=junction, prefix="must-not-escape-",
                    )
                self.assertEqual([], list(outside.iterdir()))
            finally:
                junction.rmdir()

    def test_broad_password_file_is_rejected_without_disclosing_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            user = self._make_broad_parent(root)
            password = root / "password.txt"
            password.write_text("segredo-nao-vazar\n", encoding="utf-8")
            with self.assertRaises(services.InstallerError) as raised:
                services.read_password_file(password, "senha RCON")
            self.assertNotIn("segredo-nao-vazar", str(raised.exception))

            self._apply_sddl(
                password, f"O:{user}D:P(A;;GR;;;{user})", directory=False,
            )
            self.assertEqual(
                "segredo-nao-vazar",
                services.read_password_file(password, "senha RCON"),
            )

    def test_broadened_lock_is_never_trusted(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            target.mkdir()
            lock = services.SessionLock.acquire(target, "host")
            try:
                user = windows_acl.current_user_sid()
                self._apply_sddl(
                    lock.path,
                    f"O:{user}D:P(A;;FA;;;{user})(A;;GR;;;WD)",
                    directory=False,
                )
                for _attempt in range(2):
                    with self.assertRaisesRegex(
                        services.session_control.SessionControlError, "inválido",
                    ):
                        services.session_control.read_lock_owner(lock.path)
                self.assertTrue(lock.path.exists())
            finally:
                # Restore only this test fixture so TemporaryDirectory can be
                # removed without teaching release() to trust the broad lock.
                windows_acl.protect_private_path(lock.path, directory=False)
                lock.release()

    def test_legacy_071_lock_without_private_marker_is_hardened_then_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            target.mkdir()
            lock = services.SessionLock.acquire(target, "host")
            legacy = dict(lock.owner)
            legacy["format"] = 2
            legacy.pop("private_filesystem")
            lock.path.write_text(json.dumps(legacy), encoding="utf-8")
            user = windows_acl.current_user_sid()
            self._apply_sddl(
                lock.path,
                f"O:{user}D:P(A;;FA;;;{user})(A;;GR;;;WD)",
                directory=False,
            )
            try:
                migrated = services.session_control.read_lock_owner(lock.path)
                self.assertTrue(migrated.pop("_x86qw_legacy_acl_migrated"))
                self.assertEqual(legacy, migrated)
                self._assert_canonical(lock.path, directory=False)
                read_again = services.session_control.read_lock_owner(lock.path)
                self.assertTrue(read_again.pop("_x86qw_legacy_acl_migrated"))
                self.assertEqual(legacy, read_again)
            finally:
                lock.release()

    def test_legacy_lock_migration_rejects_an_existing_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            target.mkdir()
            lock = services.SessionLock.acquire(target, "host")
            legacy = dict(lock.owner)
            legacy["format"] = 2
            legacy.pop("private_filesystem")
            lock.path.write_text(json.dumps(legacy), encoding="utf-8")
            user = windows_acl.current_user_sid()
            self._apply_sddl(
                lock.path,
                f"O:{user}D:P(A;;FA;;;{user})(A;;GR;;;WD)",
                directory=False,
            )
            try:
                with lock.path.open("r+b") as writer:
                    writer.seek(0, os.SEEK_END)
                    with self.assertRaises(services.session_control.SessionControlError):
                        services.session_control.read_lock_owner(lock.path)
                self.assertNotIn("_x86qw_legacy_acl_migrated", legacy)
            finally:
                windows_acl.protect_private_path(lock.path, directory=False)
                lock.release()

    def test_broadened_current_journal_never_becomes_legacy(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            target.mkdir()
            journal = services.SessionJournal(target, session_id="current-broadened")
            user = windows_acl.current_user_sid()
            self._apply_sddl(
                journal.path,
                f"O:{user}D:P(A;;FA;;;{user})(A;;GR;;;WD)",
                directory=False,
            )
            try:
                for _attempt in range(2):
                    with self.assertRaisesRegex(
                        services.InstallerError, "Journal de sessão inválido",
                    ):
                        services.load_session_journal(journal.path)
            finally:
                windows_acl.protect_private_path(journal.path, directory=False)

    def test_legacy_071_clean_inert_session_is_hardened_without_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            target.mkdir()
            journal = services.SessionJournal(target, session_id="legacy-clean")
            journal.data.pop("private_filesystem")
            journal.data["status"] = "clean"
            journal._write()
            user = windows_acl.current_user_sid()
            self._apply_sddl(
                journal.directory,
                f"O:{user}D:P(A;OICI;FA;;;{user})(A;OICI;GR;;;WD)",
                directory=True,
            )
            self._apply_sddl(
                journal.path,
                f"O:{user}D:P(A;;FA;;;{user})(A;;GR;;;WD)",
                directory=False,
            )

            services.recover_sessions(target)

            self._assert_canonical(journal.directory, directory=True)
            self._assert_canonical(journal.path, directory=False)
            self.assertEqual(
                "clean",
                json.loads(journal.path.read_text(encoding="utf-8"))["status"],
            )

    def test_legacy_071_interrupted_session_cannot_kill_or_unlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            game = target / "qw"
            game.mkdir(parents=True)
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
            )
            config = game / "legacy-sensitive.cfg"
            config.write_text("rcon_password nao-remover\n", encoding="utf-8")
            journal = services.SessionJournal(target, session_id="legacy-interrupted")
            identity = services.process_identity(child.pid).identity
            self.assertIsNotNone(identity)
            assert identity is not None
            journal.data.pop("private_filesystem")
            journal.data["status"] = "interrupted"
            journal.data["controller"] = {
                "pid": 2_147_483_647,
                "creation_token": "controlador-encerrado",
                "executable": str(target / "controller.exe"),
                "command": "host",
            }
            journal.data["processes"] = [{
                "label": "MVDSV",
                "runtime": "mvdsv",
                "pid": child.pid,
                "process_group": child.pid,
                "executable": identity.executable,
                "creation_token": identity.creation_token,
                "started_at": "2026-08-03T00:00:00+00:00",
                "address": "127.0.0.1",
                "port": 28501,
                "parameters": {},
            }]
            journal.data["temporary_files"] = [{
                "path": "qw/legacy-sensitive.cfg",
                "origin": "configuração efêmera legada",
                "created_by_session": True,
                "type": "temporary-config",
                "sensitive": True,
            }]
            journal._write()
            user = windows_acl.current_user_sid()
            self._apply_sddl(
                journal.directory,
                f"O:{user}D:P(A;OICI;FA;;;{user})(A;OICI;GR;;;WD)",
                directory=True,
            )
            self._apply_sddl(
                journal.path,
                f"O:{user}D:P(A;;FA;;;{user})(A;;GR;;;WD)",
                directory=False,
            )
            try:
                with self.assertRaisesRegex(
                    services.InstallerError, "histórico.*não pode autorizar",
                ):
                    services.recover_sessions(target)
                self.assertIsNone(child.poll())
                self.assertTrue(config.is_file())
                self.assertEqual(
                    "interrupted",
                    json.loads(journal.path.read_text(encoding="utf-8"))["status"],
                )
            finally:
                child.terminate()
                child.wait(timeout=5)

    def test_status_style_reader_does_not_block_lock_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "quake-world"
            target.mkdir()
            lock = services.SessionLock.acquire(target, "host")
            api = windows_acl._api()
            handle = api.kernel32.CreateFileW(
                str(lock.path),
                api.FILE_READ_DATA | api.READ_CONTROL | api.FILE_READ_ATTRIBUTES,
                windows_acl._private_read_share_mode(exact=True),
                None,
                api.OPEN_EXISTING,
                api.FILE_ATTRIBUTE_NORMAL | api.FILE_FLAG_OPEN_REPARSE_POINT,
                None,
            )
            self.assertNotEqual(api.INVALID_HANDLE_VALUE, handle)
            released = False
            try:
                lock.release()
                released = True
            finally:
                api.kernel32.CloseHandle(handle)
            self.assertTrue(released)
            self.assertFalse(lock.path.exists())


if __name__ == "__main__":
    unittest.main()
