import argparse
import contextlib
import io
import json
import os
import signal
import socket
import stat
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
            (target / ".install").mkdir()
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

    def test_session_recovery_preserves_modified_materialized_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".install").mkdir()
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

    def test_session_recovery_preserves_modified_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".install").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(config_dir, "session-", ["hostname local"], journal)
            config.write_text("// configuração pessoalizada\n", encoding="utf-8")
            services.recover_sessions(target)
            self.assertTrue(config.exists())
            self.assertIn("pessoalizada", config.read_text(encoding="utf-8"))

    def test_session_journal_is_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".install").mkdir()
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
        ), mock.patch.object(services, "wait_http_readiness", side_effect=services.InstallerError("QTV falhou")):
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
