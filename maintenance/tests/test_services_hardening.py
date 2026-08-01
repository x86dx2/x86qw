import argparse
import contextlib
import io
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dist/installer/bin"))
import services  # noqa: E402


class ServiceHardeningTests(unittest.TestCase):
    def package(self, root: Path, members: list[tuple[str, bytes]]) -> tuple[Path, Path]:
        destination = root / "qw"
        destination.mkdir()
        package = destination / "ktx.pk3"
        with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, payload in members:
                archive.writestr(name, payload)
        return package, destination

    def assert_unsafe_archive(self, names: list[str]) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_names = [name.replace("\\", "/") for name in names]
            package, destination = self.package(
                Path(temporary), [(name, b"payload") for name in archive_names],
            )
            # ZipInfo sanitizes the host separator while writing on Windows.
            # Patch both equal-length name records so this remains a real ZIP
            # with the hostile spelling an external archive may contain.
            contents = package.read_bytes()
            for original, archive_name in zip(names, archive_names):
                if original != archive_name:
                    self.assertIn(archive_name.encode("utf-8"), contents)
                    contents = contents.replace(
                        archive_name.encode("utf-8"), original.encode("utf-8")
                    )
            package.write_bytes(contents)
            with self.assertRaises(services.InstallerError):
                services.materialize_dedicated_pk3(package, destination, "teste")

    def test_zip_rejects_traversal_drives_backslashes_and_empty_components(self):
        for names in (["../escape"], ["C:/drive.cfg"], ["a\\b.cfg"], ["a//b.cfg"], ["./a.cfg"]):
            with self.subTest(names=names):
                self.assert_unsafe_archive(names)

    def test_zip_rejects_windows_reserved_and_trailing_names(self):
        for name in ("CON", "con.cfg", "NUL.txt", "COM9.dat", "LPT1", "name. ", "name."):
            with self.subTest(name=name):
                self.assert_unsafe_archive([name])

    def test_zip_rejects_case_and_unicode_collisions(self):
        self.assert_unsafe_archive(["Config.cfg", "config.cfg"])
        self.assert_unsafe_archive(["caf\N{LATIN SMALL LETTER E WITH ACUTE}.cfg", "cafe\N{COMBINING ACUTE ACCENT}.cfg"])

    def test_zip_rejects_symlinks_and_abnormal_compression(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination = self.package(Path(temporary), [])
            info = zipfile.ZipInfo("link.cfg")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr(info, b"target")
            with self.assertRaises(services.InstallerError):
                services.materialize_dedicated_pk3(package, destination, "teste")
        with tempfile.TemporaryDirectory() as temporary:
            package, destination = self.package(
                Path(temporary), [("bomb.cfg", b"0" * (2 * 1024 * 1024))],
            )
            with self.assertRaises(services.InstallerError):
                services.materialize_dedicated_pk3(package, destination, "teste")

    def test_endpoint_parser_accepts_ipv4_ipv6_and_hostname(self):
        self.assertEqual("127.0.0.1:28501", services.parse_network_endpoint("127.0.0.1:28501"))
        self.assertEqual("[2001:db8::1]:28501", services.parse_network_endpoint("[2001:db8::1]:28501"))
        self.assertEqual("quake.example:28501", services.parse_network_endpoint("Quake.Example:28501"))
        for value in ("2001:db8::1:28501", "host", "host:0", "host:70000", "host:1;quit", "bad host:1"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                services.parse_network_endpoint(value)

    def test_password_prompt_and_private_file_do_not_echo_secret(self):
        options = SimpleNamespace(
            password="", prompt_password=True, password_file=None,
            spectator_password="", prompt_spectator_password=False, spectator_password_file=None,
            rcon_password="", prompt_rcon_password=False, rcon_password_file=None,
            qtv_password="", prompt_qtv_password=False, qtv_password_file=None,
        )
        output = io.StringIO()
        with mock.patch.object(services.getpass, "getpass", return_value="muito-secreta"), contextlib.redirect_stdout(output):
            services.resolve_passwords(options)
        self.assertEqual("muito-secreta", options.password)
        self.assertNotIn("muito-secreta", output.getvalue())
        with tempfile.TemporaryDirectory() as temporary:
            password_file = Path(temporary) / "secret"
            password_file.write_text("arquivo-secreto\n", encoding="utf-8")
            if os.name != "nt":
                password_file.chmod(0o600)
            self.assertEqual("arquivo-secreto", services.read_password_file(password_file, "senha"))

    def test_passwords_are_kept_out_of_child_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / "td2").mkdir()
            options = services.parse_arguments([
                "host", "td2", "--map", "dm6", "--password", "jogador-secreto",
                "--spectator-password", "espectador-secreto",
                "--rcon-password", "rcon-secreto", "--target", str(target),
            ], ROOT)
            game = next(game for game in services.gameplay.LOCAL_GAMES if game.key == "td2")
            selection = services.HostedGame(game, None, "dm6", frozenset(), options.ktx_options)
            with mock.patch.object(services, "runtime_binary", return_value=target / "mvdsv"), mock.patch.object(
                services, "materialize_hosted_game", return_value=None,
            ):
                spec = services.host_spec(SimpleNamespace(target=target), options, selection, [], [])
            command = " ".join(spec.arguments)
            self.assertNotIn("jogador-secreto", command)
            self.assertNotIn("espectador-secreto", command)
            self.assertNotIn("rcon-secreto", command)

    def test_qtv_readiness_confirms_http_and_upstream(self):
        process = mock.Mock()
        process.poll.return_value = None
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.recv.side_effect = [
            b"HTTP/1.0 200 OK\r\n\r\nstream 127.0.0.1:28501",
            b"",
        ]
        with mock.patch.object(services.socket, "create_connection", return_value=connection):
            services.wait_http_readiness(
                process,
                services.ServiceReadiness("http", "127.0.0.1", 28000, "127.0.0.1:28501"),
                timeout=0.1,
            )

    @unittest.skipIf(os.name == "nt", "ACLs do Windows não usam bits POSIX")
    def test_password_file_rejects_open_permissions_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            password_file = root / "secret"
            password_file.write_text("segredo", encoding="utf-8")
            password_file.chmod(0o644)
            with self.assertRaisesRegex(services.InstallerError, "Permissões inseguras") as raised:
                services.read_password_file(password_file, "senha")
            self.assertNotIn("segredo", str(raised.exception))
            password_file.chmod(0o600)
            link = root / "link"
            link.symlink_to(password_file)
            with self.assertRaises(services.InstallerError):
                services.read_password_file(link, "senha")

    def test_preflight_rejects_duplicate_and_occupied_ports_before_start(self):
        with self.assertRaisesRegex(services.InstallerError, "duplicada"):
            services.preflight_ports([
                ("MVDSV", "127.0.0.1", 28501, "udp"),
                ("QTV", "127.0.0.1", 28501, "tcp"),
            ])
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind(("127.0.0.1", 0))
            port = occupied.getsockname()[1]
            with self.assertRaisesRegex(services.InstallerError, "não está disponível"):
                services.preflight_ports([("QTV", "127.0.0.1", port, "tcp")])

    def test_session_recovery_removes_only_unchanged_created_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            created = target / "qw" / "created.cfg"
            created.parent.mkdir()
            created.write_text("managed", encoding="utf-8")
            journal = services.SessionJournal(target)
            journal.record_materialized(services.MaterializedFile(
                created, services.file_sha256(created), "fixture.pk3", True, False,
            ))
            services.recover_sessions(target)
            self.assertFalse(created.exists())
            recovered = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertEqual("clean", recovered["status"])

    def test_recovery_accepts_clean_legacy_journal_without_new_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            session = target / ".x86qw/sessions/legacy-clean"
            session.mkdir(parents=True)
            path = session / "session.json"
            legacy = {
                "format": 1,
                "project": "x86qw",
                "session_id": "legacy-clean",
                "created_at": "2026-07-31T18:57:49+00:00",
                "status": "clean",
                "processes": [{"label": "QTV", "pid": os.getpid()}],
                "temporary_files": [{
                    "path": "qtv/old-session.cfg",
                    "origin": "configuração efêmera",
                    "created_by_session": True,
                    "expected_hash": "a" * 64,
                }],
                "materialized_files": [],
                "created_directories": [],
            }
            path.write_text(json.dumps(legacy), encoding="utf-8")

            with mock.patch.object(
                services, "process_identity",
                side_effect=AssertionError("sessão limpa não deve consultar PID"),
            ):
                services.recover_sessions(target)

            self.assertEqual(legacy, json.loads(path.read_text(encoding="utf-8")))

    def test_recovery_treats_unclassified_legacy_temporary_as_sensitive(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            config = target / "qw/old-session.cfg"
            config.parent.mkdir(parents=True)
            secret = "segredo-legado"
            config.write_text(secret, encoding="utf-8")
            session = target / ".x86qw/sessions/legacy-interrupted"
            session.mkdir(parents=True)
            path = session / "session.json"
            path.write_text(json.dumps({
                "format": 1,
                "project": "x86qw",
                "session_id": "legacy-interrupted",
                "created_at": "2026-07-31T18:57:49+00:00",
                "status": "interrupted",
                "processes": [{"label": "QTV", "pid": 999999999}],
                "temporary_files": [{
                    "path": "qw/old-session.cfg",
                    "origin": "configuração efêmera",
                    "created_by_session": True,
                    "expected_hash": services.file_sha256(config),
                }],
                "materialized_files": [],
                "created_directories": [],
            }), encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                services.recover_sessions(target)

            self.assertFalse(config.exists())
            recovered = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("clean", recovered["status"])
            self.assertTrue(recovered["temporary_files"][0]["sensitive"])
            self.assertNotIn("expected_hash", recovered["temporary_files"][0])
            self.assertNotIn(secret, output.getvalue())

    def test_session_recovery_preserves_modified_materialized_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            created = target / "qw" / "created.cfg"
            created.parent.mkdir()
            created.write_text("managed", encoding="utf-8")
            journal = services.SessionJournal(target)
            journal.record_materialized(services.MaterializedFile(
                created, services.file_sha256(created), "fixture.pk3", True, False,
            ))
            created.write_text("personal", encoding="utf-8")
            services.recover_sessions(target)
            self.assertEqual("personal", created.read_text(encoding="utf-8"))
            recovered = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertTrue(recovered["materialized_files"][0]["modified_during_session"])

    def test_session_recovery_removes_modified_sensitive_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(config_dir, "session-", ["hostname local"], journal)
            secret = "segredo-que-nao-pode-vazar"
            config.write_text(f'password "{secret}"\n', encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                services.recover_sessions(target)
            self.assertFalse(config.exists())
            self.assertNotIn(secret, output.getvalue())
            self.assertNotIn(secret, journal.path.read_text(encoding="utf-8"))
            entry = json.loads(journal.path.read_text(encoding="utf-8"))["temporary_files"][0]
            self.assertNotIn("expected_hash", entry)

    def test_sensitive_temporary_replaced_by_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(config_dir, "session-", ["password secret"], journal)
            config.unlink()
            config.mkdir()
            personal = config / "personal.cfg"
            personal.write_text("preservar", encoding="utf-8")
            with self.assertRaisesRegex(services.InstallerError, "substituído por diretório"):
                services.recover_sessions(target)
            self.assertEqual("preservar", personal.read_text(encoding="utf-8"))

    def test_sensitive_temporary_symlink_is_unlinked_without_touching_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            personal = config_dir / "personal.cfg"
            personal.write_text("preservar", encoding="utf-8")
            journal = services.SessionJournal(target)
            config = services.temporary_config(config_dir, "session-", ["password secret"], journal)
            config.unlink()
            config.symlink_to(personal)
            services.recover_sessions(target)
            self.assertFalse(os.path.lexists(config))
            self.assertEqual("preservar", personal.read_text(encoding="utf-8"))

    @unittest.skipIf(os.name == "nt", "FIFO é uma fixture POSIX")
    def test_sensitive_temporary_special_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(config_dir, "session-", ["password secret"], journal)
            config.unlink()
            os.mkfifo(config)
            with self.assertRaisesRegex(services.InstallerError, "arquivo especial"):
                services.recover_sessions(target)
            self.assertTrue(stat.S_ISFIFO(config.lstat().st_mode))

    def test_session_recovery_preserves_modified_non_sensitive_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(
                config_dir, "session-", ["hostname local"], journal, sensitive=False,
            )
            config.write_text("// configuração pessoalizada\n", encoding="utf-8")
            services.recover_sessions(target)
            self.assertTrue(config.exists())
            self.assertIn("pessoalizada", config.read_text(encoding="utf-8"))

    def test_active_session_lock_blocks_recovery_and_preserves_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            first = services.SessionLock.acquire(target, "host")
            try:
                journal = services.SessionJournal(
                    target, session_id=first.session_id, controller=first.owner,
                )
                config_dir = target / "qw"
                config_dir.mkdir()
                config = services.temporary_config(
                    config_dir, "session-", ["hostname ativo"], journal,
                )
                with self.assertRaisesRegex(services.InstallerError, "operação x86QW ativa"):
                    services.SessionLock.acquire(target, "qtv")
                self.assertTrue(config.exists())
                self.assertEqual("starting", json.loads(journal.path.read_text(encoding="utf-8"))["status"])
            finally:
                first.release()

    def test_session_lock_acquisition_is_atomic_between_controllers(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            barrier = threading.Barrier(2)
            release = threading.Event()
            results: list[tuple[str, object]] = []
            results_lock = threading.Lock()

            def acquire(command: str) -> None:
                barrier.wait()
                try:
                    acquired = services.SessionLock.acquire(target, command)
                except services.InstallerError as error:
                    with results_lock:
                        results.append(("blocked", str(error)))
                    return
                with results_lock:
                    results.append(("acquired", acquired))
                release.wait(2)
                acquired.release()

            threads = [
                threading.Thread(target=acquire, args=(command,))
                for command in ("host", "proxy")
            ]
            for thread in threads:
                thread.start()
            deadline = time.monotonic() + 2
            while len(results) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            release.set()
            for thread in threads:
                thread.join(2)
            self.assertEqual(1, sum(kind == "acquired" for kind, _ in results))
            self.assertEqual(1, sum(kind == "blocked" for kind, _ in results))

    def test_maintenance_lock_blocks_all_service_entrypoints(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            (target / ".x86qw").mkdir()
            maintenance = services.session_control.InstallationLock.acquire(
                target, "update", "maintenance",
            )
            try:
                for command in ("host", "proxy", "qtv"):
                    with self.subTest(command=command):
                        with self.assertRaisesRegex(services.InstallerError, "operação x86QW ativa"):
                            services.SessionLock.acquire(target, command)
            finally:
                maintenance.release()

    def test_stale_controller_lock_is_reclaimed_and_journal_recovered(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            (target / ".x86qw").mkdir()
            old_session = "abandoned-session"
            journal = services.SessionJournal(target, session_id=old_session)
            lock_path = target / ".x86qw/sessions/active.lock"
            lock_path.write_text(json.dumps({
                "format": 1, "project": "x86qw", "session_id": old_session,
                "controller_pid": 999999999, "controller_start_token": "dead-token",
                "controller_executable": str(target / "dead-controller"),
                "created_at": "2026-07-31T00:00:00+00:00", "installation": str(target),
                "command": "host",
            }), encoding="utf-8")
            acquired = services.SessionLock.acquire(target, "proxy")
            try:
                services.recover_sessions(target)
                acquired.confirm_recovery()
                self.assertEqual("clean", json.loads(journal.path.read_text(encoding="utf-8"))["status"])
            finally:
                acquired.release()

    def test_missing_lock_does_not_recover_a_live_journal_controller(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            (target / ".x86qw").mkdir()
            first = services.SessionLock.acquire(target, "host")
            journal = services.SessionJournal(
                target, session_id=first.session_id, controller=first.owner,
            )
            config_dir = target / "qw"
            config_dir.mkdir()
            config = services.temporary_config(config_dir, "session-", ["hostname ativo"], journal)
            first.path.unlink()
            second = services.SessionLock.acquire(target, "proxy")
            try:
                with self.assertRaisesRegex(services.InstallerError, "controlador.*continua ativo"):
                    services.recover_sessions(target)
            finally:
                second.release()
            self.assertTrue(config.exists())
            self.assertEqual("starting", json.loads(journal.path.read_text(encoding="utf-8"))["status"])

    def test_inconclusive_controller_identity_preserves_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            sessions = target / ".x86qw/sessions"
            sessions.mkdir(parents=True)
            lock_path = sessions / "active.lock"
            lock_path.write_text(json.dumps({
                "format": 1, "project": "x86qw", "session_id": "unknown-session",
                "controller_pid": 424242, "controller_start_token": "unknown-token",
                "controller_executable": str(target / "controller"),
                "created_at": "2026-07-31T00:00:00+00:00", "installation": str(target),
                "command": "qtv",
            }), encoding="utf-8")
            with mock.patch.object(
                services.session_control, "probe_expected_process",
                return_value=services.ProcessProbe("inconclusive", detail="acesso negado"),
            ):
                with self.assertRaisesRegex(services.InstallerError, "Não foi possível confirmar"):
                    services.SessionLock.acquire(target, "host")
            self.assertTrue(lock_path.exists())

    def test_invalid_lock_is_preserved_and_never_recovered(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            sessions = target / ".x86qw/sessions"
            sessions.mkdir(parents=True)
            lock_path = sessions / "active.lock"
            lock_path.write_text("{invalid", encoding="utf-8")
            with self.assertRaisesRegex(services.InstallerError, "inválido"):
                services.SessionLock.acquire(target, "host")
            self.assertEqual("{invalid", lock_path.read_text(encoding="utf-8"))

    def test_lock_release_never_removes_another_session_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            (target / ".x86qw").mkdir()
            acquired = services.SessionLock.acquire(target, "host")
            other = dict(acquired.owner)
            other["session_id"] = "other-session"
            acquired.path.write_text(json.dumps(other), encoding="utf-8")
            acquired.release()
            self.assertTrue(acquired.path.exists())
            self.assertEqual(
                "other-session",
                json.loads(acquired.path.read_text(encoding="utf-8"))["session_id"],
            )

    def test_orphan_with_matching_identity_is_terminated_and_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            process = subprocess.Popen(
                (sys.executable, "-c", "import time; time.sleep(30)"),
                start_new_session=True,
            )
            journal = services.SessionJournal(target, controller={
                "controller_pid": 999999999,
                "controller_start_token": "dead-controller",
                "controller_executable": str(target / "dead-controller"),
                "command": "host",
            })
            spec = services.ProcessSpec("fixture", (sys.executable,), Path.cwd())
            try:
                journal.record_process(spec, process, process.pid)
                services.recover_sessions(target)
                process.wait(timeout=2)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            recovered = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertEqual("clean", recovered["status"])
            self.assertIn(recovered["recovery_actions"][0]["result"], {"terminated", "killed"})
            recorded = recovered["processes"][0]
            for field in ("runtime", "process_group", "executable", "creation_token", "address", "port"):
                self.assertIn(field, recorded)

    def test_reused_pid_is_not_terminated(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            journal = services.SessionJournal(target)
            processes = journal.data["processes"]
            self.assertIsInstance(processes, list)
            processes.append({
                "label": "MVDSV", "runtime": "mvdsv", "pid": 12345,
                "process_group": 12345, "executable": "/old/mvdsv",
                "creation_token": "old-token", "started_at": "2026-07-31T00:00:00+00:00",
                "address": "127.0.0.1", "port": 28501,
            })
            journal._write()
            mismatch = services.ProcessProbe(
                "identity_mismatch", services.ProcessIdentity(12345, "new-token", "/other/process"),
            )
            with mock.patch.object(services, "probe_expected_process", return_value=mismatch):
                with mock.patch.object(services, "signal_recorded_process") as terminate:
                    services.recover_sessions(target)
            terminate.assert_not_called()
            recovered = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertEqual("identity_mismatch", recovered["recovery_actions"][0]["result"])

    def test_inconclusive_orphan_preserves_journal_and_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            data = target / "qw/needed.cfg"
            data.parent.mkdir()
            data.write_text("needed", encoding="utf-8")
            journal = services.SessionJournal(target)
            journal.record_materialized(services.MaterializedFile(
                data, services.file_sha256(data), "fixture.pk3", True, False,
            ))
            processes = journal.data["processes"]
            self.assertIsInstance(processes, list)
            processes.append({"label": "QTV", "pid": 12345})
            journal._write()
            with mock.patch.object(
                services, "process_identity", return_value=services.ProcessProbe("inconclusive"),
            ):
                with self.assertRaisesRegex(services.InstallerError, "Não foi possível confirmar"):
                    services.recover_sessions(target)
            self.assertTrue(data.exists())
            self.assertEqual("starting", json.loads(journal.path.read_text(encoding="utf-8"))["status"])

    @unittest.skipIf(os.name == "nt", "grupos de processos POSIX não existem no Windows")
    def test_stop_processes_kills_descendant_after_leader_exits(self):
        with tempfile.TemporaryDirectory() as temporary:
            child_pid_path = Path(temporary) / "child.pid"
            script = (
                "import pathlib,signal,subprocess,sys,time\n"
                "child=subprocess.Popen([sys.executable,'-c',"
                "'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'])\n"
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
                "time.sleep(60)\n"
            )
            leader = subprocess.Popen(
                [sys.executable, "-c", script, str(child_pid_path)],
                start_new_session=True,
            )
            setattr(leader, "_x86qw_process_group", leader.pid)
            deadline = time.monotonic() + 3
            while not child_pid_path.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(child_pid_path.exists())
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
            try:
                services.stop_processes([leader])
            finally:
                if leader.poll() is None:
                    os.killpg(leader.pid, signal.SIGKILL)
                    leader.wait()
            self.assertIsNotNone(leader.poll())
            child_deadline = time.monotonic() + 2
            while time.monotonic() < child_deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail(f"descendente PID {child_pid} permaneceu ativo")

    @unittest.skipUnless(os.name == "nt", "Job Object é exercitado no runner Windows")
    def test_windows_job_object_kills_process_and_descendant(self):
        with tempfile.TemporaryDirectory() as temporary:
            child_pid_path = Path(temporary) / "child.pid"
            script = (
                "import pathlib,subprocess,sys,time\n"
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
                "time.sleep(60)\n"
            )
            job = services.WindowsJobObject()
            leader = subprocess.Popen(
                [sys.executable, "-c", script, str(child_pid_path)],
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            try:
                job.assign(leader)
                deadline = time.monotonic() + 5
                while not child_pid_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(child_pid_path.exists())
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))
                job.close()
                leader.wait(timeout=5)
                probe = services.process_identity(child_pid)
                self.assertEqual("dead", probe.status)
            finally:
                job.close()
                if leader.poll() is None:
                    leader.kill()
                    leader.wait()

    @unittest.skipUnless(os.name == "nt", "assinaturas Win32 são exercitadas no runner Windows")
    def test_windows_api_signatures_are_explicit(self):
        groups = (
            (services.session_control._windows_kernel32(), (
                "OpenProcess", "GetProcessTimes", "QueryFullProcessImageNameW",
                "TerminateProcess", "CloseHandle",
            )),
            (services._windows_job_kernel32(), (
                "CreateJobObjectW", "SetInformationJobObject",
                "AssignProcessToJobObject", "CloseHandle",
            )),
        )
        for kernel32, names in groups:
            for name in names:
                function = getattr(kernel32, name)
                self.assertIsNotNone(function.argtypes, name)
                self.assertIsNotNone(function.restype, name)

    def test_finalization_always_attempts_lock_release_after_cleanup_failures(self):
        for failing_step in ("journal", "session", "stage", "release"):
            with self.subTest(failing_step=failing_step):
                journal = mock.Mock()
                installer = mock.Mock()
                lock = mock.Mock()
                resources = services.ServiceResources([], [])
                resources.journal = journal
                resources.installer = installer
                resources.session_lock = lock
                if failing_step == "journal":
                    journal.set_status.side_effect = [RuntimeError("journal"), None]
                if failing_step == "stage":
                    installer.cleanup_stage.side_effect = RuntimeError("stage")
                if failing_step == "release":
                    lock.release.side_effect = RuntimeError("release")
                cleanup = (
                    mock.patch.object(
                        services, "cleanup_current_session", side_effect=RuntimeError("session"),
                    )
                    if failing_step == "session"
                    else mock.patch.object(services, "cleanup_current_session")
                )
                with cleanup:
                    with self.assertRaisesRegex(services.InstallerError, "finalização"):
                        with services.finalize_service_operation(resources):
                            pass
                lock.release.assert_called_once()

    def test_finalization_preserves_original_error_while_reporting_cleanup(self):
        resources = services.ServiceResources([], [])
        resources.session_lock = mock.Mock()
        with mock.patch.object(
            services, "cleanup_current_session", side_effect=RuntimeError("cleanup"),
        ):
            with self.assertRaisesRegex(ValueError, "original"):
                with services.finalize_service_operation(resources):
                    raise ValueError("original")
        resources.session_lock.release.assert_called_once()

    def test_host_qtv_upstream_uses_reachable_ipv4_and_ipv6_endpoint(self):
        expected = {
            "127.0.0.1": "127.0.0.1:28501",
            "0.0.0.0": "127.0.0.1:28501",
            "192.168.1.50": "192.168.1.50:28501",
            "::1": "[::1]:28501",
            "::": "[::1]:28501",
        }
        for address, endpoint in expected.items():
            with self.subTest(address=address):
                self.assertEqual(endpoint, services.host_qtv_upstream(address, 28501))

    def test_external_qtv_warning_is_independent_from_upstream_password(self):
        for password in ("", "upstream-secret"):
            options = SimpleNamespace(
                action="qtv", bind="0.0.0.0", upstream="127.0.0.1:28501",
                qtv_password=password,
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                services.warn_external_bind(options)
            self.assertIn("interface HTTP/QTV será exposta", output.getvalue())
            self.assertIn("não autentica o acesso HTTP", output.getvalue())

    def test_session_journal_is_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            journal = services.SessionJournal(target)
            if os.name != "nt":
                self.assertEqual(0o700, journal.directory.stat().st_mode & 0o777)
                self.assertEqual(0o600, journal.path.stat().st_mode & 0o777)

    def test_partial_startup_failure_stops_server_and_dependents(self):
        processes = [mock.Mock(pid=101), mock.Mock(pid=102)]
        for process in processes:
            process.poll.return_value = None
        with mock.patch.object(services.subprocess, "Popen", side_effect=processes), mock.patch.object(
            services, "apply_startup_rcon",
        ), mock.patch.object(
            services, "wait_http_readiness", side_effect=services.InstallerError("QTV falhou"),
        ), mock.patch.object(services, "WindowsJobObject"):
            specs = [
                services.ProcessSpec("MVDSV", ("mvdsv",), Path.cwd(), services.StartupRcon("127.0.0.1", 28501, "secret", "post.cfg", "dm6", "ktx")),
                services.ProcessSpec("QTV", ("qtv",), Path.cwd(), readiness=services.ServiceReadiness("http", "127.0.0.1", 28000)),
            ]
            with self.assertRaisesRegex(services.InstallerError, "QTV falhou"):
                services.run_processes(specs)
        for process in processes:
            process.terminate.assert_called_once()

    def test_mvdsv_readiness_checks_map_gamecode_and_applies_post_map(self):
        responses = [
            b"\xff\xff\xff\xffprint\n\\map\\dm6\\*gamedir\\qw",
            b"\xff\xff\xff\xffprint\n*game qw",
            b"\xff\xff\xff\xffprint\nexecing post.cfg",
        ]
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.recvfrom.side_effect = [(response, ("127.0.0.1", 28501)) for response in responses]
        with mock.patch.object(services.socket, "socket", return_value=connection):
            services.apply_startup_rcon(services.StartupRcon(
                "127.0.0.1", 28501, "bootstrap", "post.cfg", "dm6", "qw",
            ))
        sent = b"\n".join(call.args[0] for call in connection.sendto.call_args_list)
        self.assertIn(b"status", sent)
        self.assertIn(b"serverinfo", sent)
        self.assertIn(b"exec post.cfg", sent)

    @unittest.skipIf(os.name == "nt", "SIGTERM POSIX validado nos runners Unix; Windows usa terminate")
    def test_sigterm_stops_child_without_orphan(self):
        child_pid: list[int] = []
        original_popen = services.subprocess.Popen

        def capture(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            child_pid.append(process.pid)
            return process

        timer = threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGTERM))
        timer.start()
        try:
            with mock.patch.object(services.subprocess, "Popen", side_effect=capture):
                result = services.run_processes([
                    services.ProcessSpec("fixture", (sys.executable, "-c", "import time; time.sleep(30)"), Path.cwd()),
                ])
            self.assertEqual(128 + signal.SIGTERM, result)
            self.assertTrue(child_pid)
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid[0], 0)
        finally:
            timer.cancel()


if __name__ == "__main__":
    unittest.main()
