import argparse
import contextlib
import hashlib
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
import gameplay  # noqa: E402
import manager  # noqa: E402
import services  # noqa: E402
from x86qw_runtime.io import atomic as atomic_io  # noqa: E402
from x86qw_runtime.io import managed_files  # noqa: E402
from x86qw_runtime.io import private_fs  # noqa: E402
from x86qw_runtime.platform import host as host_platform  # noqa: E402
from x86qw_runtime.supervisor import sessions as runtime_sessions  # noqa: E402
from x86qw_runtime.supervisor import core as supervisor_core  # noqa: E402

services.configure_context(
    manager.service_composition_context(services, gameplay),
)


class FakeWindowsFileApi:
    """Portable filesystem-backed stand-in for the narrow Win32 handle API."""

    GENERIC_READ = managed_files._WindowsFileApi.GENERIC_READ
    GENERIC_WRITE = managed_files._WindowsFileApi.GENERIC_WRITE
    DELETE = managed_files._WindowsFileApi.DELETE
    FILE_READ_ATTRIBUTES = managed_files._WindowsFileApi.FILE_READ_ATTRIBUTES
    CREATE_NEW = managed_files._WindowsFileApi.CREATE_NEW
    OPEN_EXISTING = managed_files._WindowsFileApi.OPEN_EXISTING

    def __init__(self):
        self.paths = {}
        self.moves = []
        self.deleted = []
        self.next_handle = 1

    def open_handle(self, path, *, access, creation, directory):
        path = Path(path)
        stream = None
        if directory:
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
                raise OSError("diretório inseguro")
        else:
            mode = "x+b" if creation == self.CREATE_NEW else "rb"
            stream = path.open(mode)
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                stream.close()
                raise OSError("arquivo inseguro")
        handle = self.next_handle
        self.next_handle += 1
        self.paths[handle] = {
            "path": path,
            "directory": directory,
            "stream": stream,
            "identity": (metadata.st_dev, metadata.st_ino),
            "delete": False,
        }
        return handle

    def close(self, handle):
        opened = self.paths.pop(handle)
        stream = opened["stream"]
        if stream is not None:
            stream.close()
        if opened["delete"]:
            path = opened["path"]
            metadata = path.lstat()
            if (metadata.st_dev, metadata.st_ino) != opened["identity"]:
                raise OSError("nome substituído")
            if opened["directory"]:
                path.rmdir()
            else:
                path.unlink()
            self.deleted.append(path)

    def checked_identity(self, handle, *, directory):
        opened = self.paths[handle]
        if opened["directory"] != directory:
            raise OSError("tipo incompatível")
        return opened["identity"]

    def write(self, handle, payload):
        self.paths[handle]["stream"].write(payload)

    def flush(self, handle):
        stream = self.paths[handle]["stream"]
        stream.flush()
        os.fsync(stream.fileno())

    def size(self, handle):
        return os.fstat(self.paths[handle]["stream"].fileno()).st_size

    def hash(self, handle, *, expected_size):
        stream = self.paths[handle]["stream"]
        limit = managed_files._assert_hashable_size(self.size(handle), expected_size)
        stream.seek(0)
        digest = hashlib.sha256()
        total = 0
        while True:
            block = stream.read(min(1024 * 1024, limit - total + 1))
            if not block:
                break
            total += len(block)
            if total > limit:
                raise OSError("arquivo excedeu o limite")
            digest.update(block)
        if expected_size is not None and total != expected_size:
            raise OSError("tamanho divergente")
        if self.size(handle) != total:
            raise OSError("arquivo mudou durante o hashing")
        return digest.hexdigest()

    def move_no_replace(self, source, destination):
        if os.path.lexists(destination):
            raise FileExistsError(destination)
        os.rename(source, destination)
        self.moves.append((Path(source), Path(destination)))

    def mark_delete(self, handle):
        opened = self.paths[handle]
        path = opened["path"]
        current = path.lstat()
        if opened["identity"] != (current.st_dev, current.st_ino):
            raise OSError("nome substituído")
        opened["delete"] = True


class ServiceHardeningTests(unittest.TestCase):
    @staticmethod
    def _service_installer(target, component, binary, payload):
        return SimpleNamespace(
            target=target,
            validate_component_pair=lambda selected: (
                True,
                [(binary.relative_to(target).as_posix(), hashlib.sha256(payload).hexdigest())],
                {"component": component},
            ) if selected == component else (False, [], None),
        )

    def test_host_spec_carries_the_inventory_bound_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / "td2").mkdir()
            binary = target / "mvdsv"
            payload = b"managed mvdsv\n"
            binary.write_bytes(payload)
            binary.chmod(0o755)
            installer = self._service_installer(target, "mvdsv", binary, payload)
            options = services.parse_arguments([
                "host", "td2", "--map", "dm6", "--target", str(target),
            ], ROOT)
            game = next(game for game in services.gameplay.LOCAL_GAMES if game.key == "td2")
            selection = services.HostedGame(
                game, None, "dm6", frozenset(), options.ktx_options,
            )
            with mock.patch.object(
                services, "runtime_binary", return_value=binary,
            ), mock.patch.object(services, "materialize_hosted_game", return_value=None):
                spec = services.host_spec(installer, options, selection, [], [])
            self.assertIsNotNone(spec.launch_target)
            self.assertEqual(binary, spec.launch_target.executable)

    def test_proxy_spec_carries_the_inventory_bound_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            directory = target / "qwfwd"
            directory.mkdir()
            (directory / "qwfwd.cfg").write_text("// fixture\n", encoding="utf-8")
            binary = directory / "qwfwd"
            payload = b"managed qwfwd\n"
            binary.write_bytes(payload)
            binary.chmod(0o755)
            installer = self._service_installer(target, "qwfwd", binary, payload)
            options = services.parse_arguments([
                "proxy", "--target", str(target),
            ], ROOT)
            with mock.patch.object(services, "runtime_binary", return_value=binary):
                spec = services.proxy_spec(installer, options)
            self.assertIsNotNone(spec.launch_target)
            self.assertEqual(binary, spec.launch_target.executable)

    def test_qtv_spec_carries_the_inventory_bound_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            directory = target / "qtv"
            directory.mkdir()
            (directory / "qtv.cfg").write_text("// fixture\n", encoding="utf-8")
            binary = directory / "qtv"
            payload = b"managed qtv\n"
            binary.write_bytes(payload)
            binary.chmod(0o755)
            installer = self._service_installer(target, "qtv", binary, payload)
            with mock.patch.object(services, "runtime_binary", return_value=binary):
                spec = services.qtv_spec(
                    installer,
                    bind="127.0.0.1",
                    port=28000,
                    hostname="x86QW",
                    upstream=None,
                    password="",
                    session_paths=[],
                )
            self.assertIsNotNone(spec.launch_target)
            self.assertEqual(binary, spec.launch_target.executable)

    def test_supervisor_revalidates_executable_identity_before_spawn(self):
        """A replacement after preflight must never reach Popen."""

        self.assertIsNotNone(
            getattr(host_platform, "executable_launch_target", None),
            "the canonical executable launch contract is missing",
        )
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "mvdsv"
            payload = b"verified service\n"
            executable.write_bytes(payload)
            executable.chmod(0o755)
            target = host_platform.executable_launch_target(
                executable,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )
            replacement = executable.with_name("replacement")
            replacement.write_bytes(b"hostile service!\n")
            replacement.chmod(0o755)
            replacement.replace(executable)

            spawned = []
            spec = services.ProcessSpec(
                "MVDSV", (str(executable),), Path(temporary),
                launch_target=target,
            )
            with self.assertRaisesRegex(manager.InstallerError, "mudou"):
                services.run_processes(
                    [spec],
                    process_factory=lambda *args, **options: spawned.append(
                        (args, options),
                    ),
                    signal_setter=lambda _signum, _handler: signal.SIG_DFL,
                    os_name="posix",
                )
            self.assertEqual([], spawned)

    def test_detached_client_revalidates_executable_identity_before_spawn(self):
        self.assertIsNotNone(
            getattr(host_platform, "executable_launch_target", None),
            "the canonical executable launch contract is missing",
        )
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "ezquake"
            payload = b"verified client\n"
            executable.write_bytes(payload)
            executable.chmod(0o755)
            target = host_platform.executable_launch_target(
                executable,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )
            executable.write_bytes(b"modified client\n")
            spawned = []
            with self.assertRaisesRegex(manager.InstallerError, "mudou"):
                supervisor_core.spawn_detached_client(
                    (str(executable),), Path(temporary),
                    launch_target=target,
                    process_factory=lambda *args, **options: spawned.append(
                        (args, options),
                    ),
                    os_name="posix",
                )
            self.assertEqual([], spawned)

    def test_detached_client_spawn_owns_platform_creation_flags(self):
        calls = []

        def spawn(arguments, **options):
            calls.append((arguments, options))
            return SimpleNamespace(pid=42)

        for os_name, expected_session in (("posix", True), ("nt", False)):
            with self.subTest(os_name=os_name):
                calls.clear()
                process = supervisor_core.spawn_detached_client(
                    ("ezquake", "+connect", "127.0.0.1:28501"),
                    Path("/Games/x86qw"),
                    process_factory=spawn,
                    os_name=os_name,
                )
                self.assertEqual(42, process.pid)
                arguments, options = calls[0]
                self.assertEqual(
                    ("ezquake", "+connect", "127.0.0.1:28501"), arguments,
                )
                self.assertEqual(Path("/Games/x86qw"), options["cwd"])
                self.assertIs(subprocess.DEVNULL, options["stdin"])
                self.assertIs(subprocess.DEVNULL, options["stdout"])
                self.assertIs(subprocess.DEVNULL, options["stderr"])
                self.assertEqual(
                    expected_session, options.get("start_new_session", False),
                )

    def test_background_controller_spawn_forwards_private_request_off_argv(self):
        class Pipe:
            def __init__(self):
                self.payload = b""
                self.closed = False

            def write(self, payload):
                self.payload += payload

            def close(self):
                self.closed = True

        calls = []
        pipe = Pipe()

        def spawn(arguments, **options):
            calls.append((arguments, options))
            return SimpleNamespace(pid=42, stdin=pipe)

        request = b'{"format":1,"secrets":{"rcon":"hidden"}}\n'
        process = supervisor_core.spawn_background_controller(
            ("python", "services.py", "host", "--background-child"),
            Path("/project"),
            request,
            process_factory=spawn,
            os_name="nt",
        )

        self.assertEqual(42, process.pid)
        self.assertEqual(request, pipe.payload)
        self.assertTrue(pipe.closed)
        arguments, options = calls[0]
        self.assertNotIn("hidden", " ".join(arguments))
        self.assertIs(subprocess.PIPE, options["stdin"])
        self.assertEqual(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008),
            options["creationflags"],
        )

    def test_background_controller_pipe_failure_stops_the_spawned_process(self):
        """A failed private handoff must not leave its detached controller alive."""

        class BrokenPipe:
            closed = False

            def write(self, _payload):
                raise BrokenPipeError("simulated private handoff failure")

            def close(self):
                self.closed = True

        class Process:
            pid = 42
            returncode = None

            def __init__(self):
                self.stdin = BrokenPipe()

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = -15

            def wait(self, _timeout=None):
                return self.returncode

        process = Process()

        with self.assertRaisesRegex(BrokenPipeError, "private handoff"):
            supervisor_core.spawn_background_controller(
                ("python", "services.py", "host", "--background-child"),
                Path("/project"),
                b'{"format":1}\n',
                process_factory=lambda *_args, **_options: process,
                os_name="nt",
            )

        self.assertEqual(-15, process.returncode)
        self.assertTrue(process.stdin.closed)

    def test_background_controller_reports_handoff_and_cleanup_failures(self):
        """Cleanup diagnostics must not replace the error that triggered cleanup."""

        class BrokenPipe:
            def write(self, _payload):
                raise BrokenPipeError("private handoff failed")

            def close(self):
                pass

        process = SimpleNamespace(pid=42, stdin=BrokenPipe())
        with mock.patch.object(
            supervisor_core,
            "stop_processes",
            side_effect=OSError("process cleanup failed"),
        ), self.assertRaises(supervisor_core.InstallerError) as raised:
            supervisor_core.spawn_background_controller(
                ("python", "services.py", "host", "--background-child"),
                Path("/project"),
                b'{"format":1}\n',
                process_factory=lambda *_args, **_options: process,
                os_name="nt",
            )

        message = str(raised.exception)
        self.assertIn("private handoff failed", message)
        self.assertIn("process cleanup failed", message)

    def test_supervisor_models_are_canonical_service_types(self):
        from x86qw_runtime.supervisor import models

        self.assertIs(services.ProcessSpec, models.ProcessSpec)
        self.assertIs(services.StartupRcon, models.StartupRcon)
        self.assertIs(services.ServiceReadiness, models.ServiceReadiness)

    def test_supervisor_reexports_canonical_platform_process_types(self):
        from x86qw_runtime import supervisor
        from x86qw_runtime.platform import processes

        self.assertIs(
            getattr(supervisor, "ProcessIdentity", None), processes.ProcessIdentity,
        )
        self.assertIs(getattr(supervisor, "ProcessProbe", None), processes.ProcessProbe)

    def test_supervisor_readiness_functions_are_canonical_service_api(self):
        from x86qw_runtime.supervisor import readiness

        self.assertIs(services.preflight_ports, readiness.preflight_ports)
        self.assertIs(services.qtv_http_response_ready, readiness.qtv_http_response_ready)
        self.assertIs(services.wait_http_readiness, readiness.wait_http_readiness)
        self.assertIs(services.wait_udp_readiness, readiness.wait_udp_readiness)
        self.assertIs(services.apply_startup_rcon, readiness.apply_startup_rcon)

    def test_supervisor_core_owns_process_tree_backends(self):
        from x86qw_runtime.supervisor import core

        self.assertIs(services.stop_processes, core.stop_processes)
        self.assertIs(services.posix_process_group_status, core.posix_process_group_status)
        self.assertIs(services.WindowsJobObject, core.WindowsJobObject)
        self.assertIs(services.ServiceSignal, core.ServiceSignal)
        self.assertIs(services.run_processes, core.run_processes)

    def test_supervisor_runner_preserves_coordinated_stop_order(self):
        events = []

        class Reporter:
            def detail(self, message):
                events.append(("detail", message))

            def info(self, message):
                events.append(("info", message))

            def warning(self, message):
                events.append(("warning", message))

        class Journal:
            def record_process(self, spec, process, process_group):
                events.append(("record", spec.label, process.pid, process_group))

            def set_status(self, status):
                events.append(("status", status))

            def consume_stop_request(self):
                events.append(("stop-request",))
                return True

        process = SimpleNamespace(pid=4242, poll=lambda: None)

        def spawn(arguments, **options):
            events.append((
                "spawn", arguments, options["cwd"], options["start_new_session"],
            ))
            return process

        def set_signal(signum, handler):
            phase = "install" if callable(handler) else f"restore:{handler}"
            events.append(("signal", int(signum), phase))
            return f"old-{int(signum)}"

        result = services.run_processes(
            [services.ProcessSpec("fixture", ("fixture", "--flag"), Path("/tmp"))],
            Journal(),
            reporter=Reporter(),
            process_factory=spawn,
            signal_setter=set_signal,
            stopper=lambda processes: events.append(
                ("stop", tuple(process.pid for process in processes)),
            ),
            os_name="posix",
            sleep=lambda _delay: self.fail("stop coordenado não deve aguardar"),
        )

        self.assertEqual(0, result)
        self.assertEqual([
            ("signal", 2, "install"),
            ("signal", 15, "install"),
            ("detail", "Iniciando fixture: fixture"),
            ("spawn", ("fixture", "--flag"), Path("/tmp"), True),
            ("record", "fixture", 4242, 4242),
            ("status", "running"),
            ("stop-request",),
            ("info", "Encerramento solicitado pelo gerenciador x86QW…"),
            ("stop", (4242,)),
            ("signal", 2, "restore:old-2"),
            ("signal", 15, "restore:old-15"),
        ], events)

    def test_supervisor_runner_preserves_signal_exit_codes(self):
        for signum, expected in ((signal.SIGINT, 130), (signal.SIGTERM, 143)):
            with self.subTest(signum=signum):
                events = []

                class Reporter:
                    detail = lambda _self, _message: None
                    warning = lambda _self, _message: None

                    def info(self, message):
                        events.append(("info", message))

                class Journal:
                    record_process = lambda _self, _spec, _process, _group: None

                    def set_status(self, status):
                        events.append(("status", status))

                    def consume_stop_request(self):
                        return False

                process = SimpleNamespace(pid=5151, poll=lambda: None)

                def interrupt(_delay):
                    raise services.ServiceSignal(signum)

                result = services.run_processes(
                    [services.ProcessSpec("fixture", ("fixture",), Path("/tmp"))],
                    Journal(),
                    reporter=Reporter(),
                    process_factory=lambda *_args, **_options: process,
                    signal_setter=lambda _signum, _handler: signal.SIG_DFL,
                    stopper=lambda processes: events.append(
                        ("stop", tuple(item.pid for item in processes)),
                    ),
                    os_name="posix",
                    sleep=interrupt,
                )

                self.assertEqual(expected, result)
                self.assertEqual([
                    ("status", "running"),
                    ("info", "Encerrando serviços x86QW…"),
                    ("status", "interrupted"),
                    ("stop", (5151,)),
                ], events)

    def test_supervisor_runner_uses_windows_job_backend(self):
        events = []
        process = SimpleNamespace(pid=6262, poll=lambda: None)

        class Job:
            def start_process(self, arguments, cwd):
                events.append(("job-start", arguments, cwd))
                return process

            def close(self):
                events.append(("job-close",))

        class Journal:
            def record_process(self, spec, candidate, process_group):
                events.append(("record", spec.label, candidate.pid, process_group))

            def set_status(self, status):
                events.append(("status", status))

            def consume_stop_request(self):
                return True

        result = services.run_processes(
            [services.ProcessSpec("fixture", ("fixture",), Path("C:/x86qw"))],
            Journal(),
            reporter=mock.Mock(),
            process_factory=lambda *_args, **_options: self.fail(
                "Windows deve iniciar pelo Job Object"
            ),
            windows_job_factory=lambda _reporter: Job(),
            signal_setter=lambda _signum, _handler: signal.SIG_DFL,
            stopper=lambda processes: events.append(
                ("stop", tuple(item.pid for item in processes)),
            ),
            os_name="nt",
        )

        self.assertEqual(0, result)
        self.assertEqual([
            ("job-start", ("fixture",), Path("C:/x86qw")),
            ("record", "fixture", 6262, 6262),
            ("status", "running"),
            ("stop", (6262,)),
            ("job-close",),
        ], events)

    def test_supervisor_runner_attempts_every_finalizer_after_cleanup_failure(self):
        events = []
        process = SimpleNamespace(pid=7373, poll=lambda: 0)

        class Reporter:
            detail = lambda _self, _message: None
            info = lambda _self, _message: None

            def warning(self, message):
                events.append(("warning", message))

        class Job:
            def start_process(self, _arguments, _cwd):
                return process

            def close(self):
                events.append(("job-close",))
                raise RuntimeError("job")

        def set_signal(signum, handler):
            events.append((
                "signal", int(signum), "install" if callable(handler) else "restore",
            ))
            return f"old-{int(signum)}"

        def fail_stop(_processes):
            events.append(("stop",))
            raise RuntimeError("stop")

        with self.assertRaisesRegex(
            services.InstallerError, "Falha ao finalizar a árvore de processos",
        ):
            services.run_processes(
                [services.ProcessSpec("fixture", ("fixture",), Path("C:/x86qw"))],
                reporter=Reporter(),
                windows_job_factory=lambda _reporter: Job(),
                signal_setter=set_signal,
                stopper=fail_stop,
                os_name="nt",
            )

        self.assertEqual([
            ("signal", 2, "install"),
            ("signal", 15, "install"),
            ("stop",),
            ("job-close",),
            ("signal", 2, "restore"),
            ("signal", 15, "restore"),
            ("warning", "Falha ao finalizar árvore de processos: stop"),
            ("warning", "Falha ao finalizar árvore de processos: job"),
        ], events)

    def test_preflight_accepts_an_injected_socket_factory(self):
        class OccupiedSocket:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def bind(self, _address):
                raise OSError("segredo-do-backend")

        with self.assertRaises(services.InstallerError) as raised:
            services.preflight_ports(
                [("QTV", "127.0.0.1", 28000, "tcp")],
                socket_factory=lambda *_args: OccupiedSocket(),
                os_name="posix",
            )
        self.assertEqual(
            "A porta 127.0.0.1:28000 de QTV não está disponível.",
            str(raised.exception),
        )
        self.assertNotIn("segredo-do-backend", str(raised.exception))

    def test_http_readiness_accepts_injected_transport_and_clock(self):
        class HttpConnection:
            def __init__(self):
                self.sent = []
                self.responses = [
                    b"HTTP/1.0 200 OK\r\n\r\n127.0.0.1:28501",
                    b"",
                ]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def sendall(self, payload):
                self.sent.append(payload)

            def recv(self, _maximum):
                return self.responses.pop(0)

        connection = HttpConnection()
        opened = []

        def connect(address, *, timeout):
            opened.append((address, timeout))
            return connection

        ticks = iter((10.0, 10.0))
        services.wait_http_readiness(
            SimpleNamespace(poll=lambda: None),
            services.ServiceReadiness(
                "http", "127.0.0.1", 28000, "127.0.0.1:28501",
            ),
            timeout=0.5,
            connection_factory=connect,
            monotonic=lambda: next(ticks),
            sleep=lambda _delay: self.fail("probe pronto não deve aguardar"),
        )

        self.assertEqual([(("127.0.0.1", 28000), 0.4)], opened)
        self.assertEqual(
            [b"GET /nowplaying/ HTTP/1.0\r\nHost: x86qw.local\r\n\r\n"],
            connection.sent,
        )

    def test_udp_readiness_accepts_injected_socket_and_clock(self):
        class OccupiedUdpSocket:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def bind(self, _address):
                raise OSError("porta ocupada pelo serviço")

        opened = []

        def open_socket(family, socket_type):
            opened.append((family, socket_type))
            return OccupiedUdpSocket()

        ticks = iter((20.0, 21.0))
        services.wait_udp_readiness(
            SimpleNamespace(poll=lambda: None),
            services.ServiceReadiness("udp", "127.0.0.1", 30000),
            timeout=0.5,
            socket_factory=open_socket,
            os_name="posix",
            monotonic=lambda: next(ticks),
            sleep=lambda _delay: self.fail("prazo já encerrado não deve aguardar"),
        )

        self.assertEqual([(socket.AF_INET, socket.SOCK_DGRAM)], opened)

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

    def test_pk3_materialization_and_cleanup_are_reversible(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"

            materialized = services.materialize_dedicated_pk3(
                package, destination_root, "teste",
            )
            self.assertEqual(b"managed", destination.read_bytes())
            self.assertTrue(materialized.files[0].created_by_session)

            services.cleanup_dedicated_ktx(materialized)
            self.assertFalse(destination.exists())
            self.assertFalse(destination.parent.exists())
            self.assertTrue(package.exists())

    def test_pk3_journal_records_created_directory_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, destination_root = self.package(
                root, [("configs/server.cfg", b"managed")],
            )
            journal = services.SessionJournal(root)

            materialized = services.materialize_dedicated_pk3(
                package, destination_root, "teste", journal,
            )

            recorded = journal.data["created_directories"]
            self.assertEqual(1, len(recorded))
            self.assertEqual("qw/configs", recorded[0]["path"])
            self.assertIsInstance(recorded[0]["device"], int)
            self.assertIsInstance(recorded[0]["inode"], int)
            self.assertEqual(
                materialized.directories[0].identity,
                (recorded[0]["device"], recorded[0]["inode"]),
            )
            recorded_file = journal.data["materialized_files"][0]
            self.assertEqual(len(b"managed"), recorded_file["expected_size"])
            services.cleanup_dedicated_ktx(materialized)

    def test_pk3_persists_recovery_intent_before_completing_promoted_member(self):
        class ControllerKilledDuringCompletion(services.SessionJournal):
            def record_materialized(self, entry):
                raise SystemExit("controlador encerrado")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, destination_root = self.package(
                root, [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            journal = ControllerKilledDuringCompletion(root)

            with self.assertRaisesRegex(SystemExit, "controlador encerrado"):
                services.materialize_dedicated_pk3(
                    package, destination_root, "teste", journal,
                )

            self.assertEqual(b"managed", destination.read_bytes())
            persisted = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(persisted["materialized_files"]))
            self.assertEqual("pending", persisted["materialized_files"][0]["state"])

            services.recover_sessions(root)

            self.assertFalse(destination.exists())
            recovered = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertEqual("clean", recovered["status"])

    def test_managed_hashing_rejects_oversize_before_reading_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "oversize.cfg"
            with path.open("wb") as output:
                output.truncate(8 * 1024 * 1024)
            descriptor = os.open(path, os.O_RDONLY)
            try:
                with mock.patch.object(services.os, "read", wraps=os.read) as read:
                    with self.assertRaises(OSError):
                        managed_files._hash_open_file(descriptor, expected_size=7)
                read.assert_not_called()
            finally:
                os.close(descriptor)
            with self.assertRaises(OSError):
                services.file_sha256(path, expected_size=7)

    def test_managed_hashing_caps_legacy_files_without_expected_size(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "oversize.cfg"
            with path.open("wb") as output:
                output.truncate(services._MAX_MANAGED_FILE_SIZE + 1)
            with self.assertRaises(OSError):
                services.file_sha256(path)

    @unittest.skipUnless(
        managed_files._secure_archive_dir_fd_supported() or os.name == "nt",
        "recuperação ancorada requer handles POSIX ou Win32",
    )
    def test_pk3_recovery_uses_recorded_file_and_directory_identities(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, destination_root = self.package(
                root, [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            journal = services.SessionJournal(root)
            services.materialize_dedicated_pk3(
                package, destination_root, "teste", journal,
            )

            services.recover_sessions(root)

            self.assertFalse(destination.exists())
            self.assertFalse(destination.parent.exists())
            self.assertEqual("clean", json.loads(
                journal.path.read_text(encoding="utf-8"),
            )["status"])

    @unittest.skipUnless(
        managed_files._secure_archive_dir_fd_supported(),
        "corrida requer operações POSIX relativas a descritor",
    )
    def test_pk3_never_overwrites_personal_file_created_before_promotion(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            real_link = os.link

            def create_personal_then_link(source, target, **kwargs):
                descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=kwargs["dst_dir_fd"],
                )
                with os.fdopen(descriptor, "wb") as output:
                    output.write(b"personal")
                return real_link(source, target, **kwargs)

            with mock.patch.object(services.os, "link", side_effect=create_personal_then_link):
                with self.assertRaisesRegex(
                    services.InstallerError, "surgiu durante a preparação",
                ):
                    services.materialize_dedicated_pk3(
                        package, destination_root, "teste",
                    )

            self.assertEqual(b"personal", destination.read_bytes())
            self.assertEqual([], list(destination.parent.glob(".x86qw_ktx_*")))

    @unittest.skipUnless(
        managed_files._secure_archive_dir_fd_supported(),
        "corrida requer operações POSIX relativas a descritor",
    )
    def test_pk3_parent_swapped_to_symlink_causes_zero_external_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, destination_root = self.package(
                root, [("configs/server.cfg", b"managed")],
            )
            parent = destination_root / "configs"
            parent.mkdir()
            original_parent = destination_root / "configs-original"
            external = root / "external"
            external.mkdir()
            marker = external / "personal.txt"
            marker.write_text("personal", encoding="utf-8")
            real_open_parent = managed_files._secure_archive_parent
            calls = 0

            def swap_before_second_pass(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    parent.rename(original_parent)
                    parent.symlink_to(external, target_is_directory=True)
                return real_open_parent(*args, **kwargs)

            with mock.patch.object(
                managed_files, "_secure_archive_parent", side_effect=swap_before_second_pass,
            ):
                with self.assertRaisesRegex(services.InstallerError, "Diretório inseguro"):
                    services.materialize_dedicated_pk3(
                        package, destination_root, "teste",
                    )

            self.assertEqual([marker], list(external.iterdir()))
            self.assertEqual("personal", marker.read_text(encoding="utf-8"))
            self.assertEqual([], list(original_parent.iterdir()))

    @unittest.skipUnless(
        managed_files._secure_archive_dir_fd_supported(),
        "corrida requer operações POSIX relativas a descritor",
    )
    def test_pk3_detects_destination_replaced_after_atomic_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            real_link = os.link

            def replace_after_link(source, target, **kwargs):
                result = real_link(source, target, **kwargs)
                os.unlink(target, dir_fd=kwargs["dst_dir_fd"])
                descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=kwargs["dst_dir_fd"],
                )
                with os.fdopen(descriptor, "wb") as output:
                    output.write(b"personal")
                return result

            with mock.patch.object(services.os, "link", side_effect=replace_after_link):
                with self.assertRaisesRegex(
                    services.InstallerError, "substituído durante a preparação",
                ):
                    services.materialize_dedicated_pk3(
                        package, destination_root, "teste",
                    )

            self.assertEqual(b"personal", destination.read_bytes())

    @unittest.skipUnless(
        managed_files._secure_archive_dir_fd_supported(),
        "rollback requer operações POSIX relativas a descritor",
    )
    def test_pk3_posix_journal_failure_preserves_modified_promoted_inode(self):
        class FailingJournal:
            def record_directory(self, entry):
                return None

            def record_materialized_intent(self, entry):
                return None

            def record_materialized(self, entry):
                entry.path.write_bytes(b"personal-concurrent-data")
                raise RuntimeError("journal indisponível")

        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            output = io.StringIO()

            with contextlib.redirect_stdout(output), self.assertRaisesRegex(
                services.InstallerError, "journal indisponível",
            ):
                services.materialize_dedicated_pk3(
                    package, destination_root, "teste", FailingJournal(),
                )

            self.assertEqual(b"personal-concurrent-data", destination.read_bytes())
            self.assertIn("foi preservado", output.getvalue())

    def test_pk3_fallback_detects_destination_replaced_after_atomic_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination_root = root / "qw"
            destination_root.mkdir()
            source = root / "source.cfg"
            source.write_bytes(b"managed")
            destination = destination_root / "server.cfg"
            member = SimpleNamespace(
                path=services.PurePosixPath("server.cfg"),
                size=len(b"managed"),
                sha256=hashlib.sha256(b"managed").hexdigest(),
            )
            real_link = os.link

            def replace_after_link(source_path, target_path, **kwargs):
                result = real_link(source_path, target_path, **kwargs)
                Path(target_path).unlink()
                Path(target_path).write_bytes(b"personal")
                return result

            with mock.patch.object(services.os, "link", side_effect=replace_after_link):
                with self.assertRaisesRegex(
                    services.InstallerError, "substituído durante a preparação",
                ):
                    managed_files._fallback_materialize_member(
                        source, destination, member, "teste", destination_root,
                    )

            self.assertEqual(b"personal", destination.read_bytes())
            self.assertEqual([], list(destination_root.glob(".x86qw_ktx_*")))

    @unittest.skipUnless(
        managed_files._secure_archive_dir_fd_supported(),
        "corrida requer operações POSIX relativas a descritor",
    )
    def test_pk3_cleanup_preserves_file_replaced_after_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            materialized = services.materialize_dedicated_pk3(
                package, destination_root, "teste",
            )
            destination = destination_root / "configs/server.cfg"
            real_hash = managed_files._hash_open_file
            replaced = False

            def replace_after_hash(descriptor, **kwargs):
                nonlocal replaced
                digest = real_hash(descriptor, **kwargs)
                if not replaced:
                    replaced = True
                    destination.unlink()
                    destination.write_bytes(b"personal")
                return digest

            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), mock.patch.object(
                managed_files, "_hash_open_file", side_effect=replace_after_hash,
            ):
                services.cleanup_dedicated_ktx(materialized)

            self.assertEqual(b"personal", destination.read_bytes())
            self.assertEqual([], list(destination.parent.glob(".x86qw_cleanup_*")))
            self.assertIn("foi preservado", output.getvalue())

    @unittest.skipUnless(
        managed_files._secure_archive_dir_fd_supported(),
        "quarentena requer operações POSIX relativas a descritor",
    )
    def test_pk3_cleanup_preserves_same_inode_modified_after_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            materialized = services.materialize_dedicated_pk3(
                package, destination_root, "teste",
            )
            destination = destination_root / "configs/server.cfg"
            original_identity = destination.stat().st_ino
            real_hash = managed_files._hash_open_file
            calls = 0

            def modify_same_inode_after_hash(descriptor, **kwargs):
                nonlocal calls
                digest = real_hash(descriptor, **kwargs)
                calls += 1
                if calls == 1:
                    destination.write_bytes(b"personal-concurrent-data")
                    self.assertEqual(original_identity, destination.stat().st_ino)
                return digest

            output = io.StringIO()
            with contextlib.redirect_stdout(output), mock.patch.object(
                managed_files, "_hash_open_file", side_effect=modify_same_inode_after_hash,
            ):
                services.cleanup_dedicated_ktx(materialized)

            self.assertEqual(b"personal-concurrent-data", destination.read_bytes())
            self.assertEqual([], list(destination.parent.glob(".x86qw_cleanup_*")))
            self.assertIn("foi preservado", output.getvalue())

    @unittest.skipUnless(
        managed_files._secure_archive_dir_fd_supported()
        and managed_files._get_posix_rename_api() is not None,
        "rename exclusivo requer Linux ou macOS compatível",
    )
    def test_posix_exclusive_rename_never_replaces_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            destination = root / "destination"
            source.write_text("managed", encoding="utf-8")
            destination.write_text("personal", encoding="utf-8")
            descriptor = os.open(root, managed_files._directory_open_flags())
            api = managed_files._get_posix_rename_api()
            self.assertIsNotNone(api)
            try:
                with self.assertRaises(FileExistsError):
                    api.move_no_replace(descriptor, source.name, descriptor, destination.name)
                self.assertEqual("managed", source.read_text(encoding="utf-8"))
                self.assertEqual("personal", destination.read_text(encoding="utf-8"))
                destination.unlink()
                api.move_no_replace(descriptor, source.name, descriptor, destination.name)
            finally:
                os.close(descriptor)
            self.assertFalse(source.exists())
            self.assertEqual("managed", destination.read_text(encoding="utf-8"))

    @unittest.skipUnless(
        managed_files._secure_archive_dir_fd_supported()
        and managed_files._get_posix_rename_api() is not None,
        "rename exclusivo requer Linux ou macOS compatível",
    )
    def test_pk3_cleanup_preserves_public_replacement_at_atomic_move(self):
        for timing in ("before", "after"):
            with self.subTest(timing=timing), tempfile.TemporaryDirectory() as temporary:
                package, destination_root = self.package(
                    Path(temporary), [("configs/server.cfg", b"managed")],
                )
                materialized = services.materialize_dedicated_pk3(
                    package, destination_root, "teste",
                )
                destination = destination_root / "configs/server.cfg"
                api = managed_files._get_posix_rename_api()
                self.assertIsNotNone(api)
                real_move = api.move_no_replace
                triggered = False

                def race_public_name(source_directory, source_name, destination_directory, destination_name):
                    nonlocal triggered
                    if not triggered and source_name == destination.name:
                        triggered = True
                        if timing == "before":
                            os.unlink(source_name, dir_fd=source_directory)
                            descriptor = os.open(
                                source_name,
                                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                0o600,
                                dir_fd=source_directory,
                            )
                            with os.fdopen(descriptor, "wb") as output:
                                output.write(b"personal-concurrent-data")
                            return real_move(
                                source_directory,
                                source_name,
                                destination_directory,
                                destination_name,
                            )
                        result = real_move(
                            source_directory,
                            source_name,
                            destination_directory,
                            destination_name,
                        )
                        descriptor = os.open(
                            source_name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                            0o600,
                            dir_fd=source_directory,
                        )
                        with os.fdopen(descriptor, "wb") as output:
                            output.write(b"personal-concurrent-data")
                        return result
                    return real_move(
                        source_directory,
                        source_name,
                        destination_directory,
                        destination_name,
                    )

                with mock.patch.object(api, "move_no_replace", side_effect=race_public_name):
                    services.cleanup_dedicated_ktx(materialized)

                self.assertTrue(triggered)
                self.assertEqual(b"personal-concurrent-data", destination.read_bytes())
                self.assertEqual([], list(destination.parent.glob(".x86qw_cleanup_*")))

    @unittest.skipUnless(
        managed_files._secure_archive_dir_fd_supported(),
        "fail-closed requer operações POSIX relativas a descritor",
    )
    def test_pk3_cleanup_preserves_when_exclusive_rename_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            materialized = services.materialize_dedicated_pk3(
                package, destination_root, "teste",
            )
            destination = destination_root / "configs/server.cfg"
            output = io.StringIO()
            with contextlib.redirect_stdout(output), mock.patch.object(
                managed_files, "_get_posix_rename_api", return_value=None,
            ):
                services.cleanup_dedicated_ktx(materialized)
            self.assertEqual(b"managed", destination.read_bytes())
            self.assertIn("foi preservado", output.getvalue())

    def test_pk3_fallback_cleanup_never_unlinks_a_replacement_by_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "server.cfg"
            destination.write_bytes(b"managed")
            metadata = destination.lstat()
            entry = services.MaterializedFile(
                destination,
                hashlib.sha256(b"managed").hexdigest(),
                "fixture.pk3",
                True,
                False,
                root,
                (metadata.st_dev, metadata.st_ino),
            )
            destination.unlink()
            destination.write_bytes(b"personal")
            output = io.StringIO()

            with contextlib.redirect_stdout(output), mock.patch.object(
                managed_files, "_secure_archive_dir_fd_supported", return_value=False,
            ), mock.patch.object(
                Path, "unlink", side_effect=AssertionError("unlink por caminho proibido"),
            ):
                services.cleanup_dedicated_ktx(
                    services.MaterializedKtx((entry,), (), root),
                )

            self.assertEqual(b"personal", destination.read_bytes())
            self.assertIn("foi preservado", output.getvalue())

    def test_pk3_windows_backend_promotes_without_hardlink_and_cleans_normally(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            api = FakeWindowsFileApi()

            with mock.patch.object(managed_files, "_WINDOWS_FILE_API", api), mock.patch.object(
                managed_files,
                "_fallback_materialize_member",
                side_effect=AssertionError("fallback hardlink não deve ser usado"),
            ):
                materialized = services.materialize_dedicated_pk3(
                    package, destination_root, "teste",
                )
                self.assertEqual(b"managed", destination.read_bytes())
                self.assertEqual(1, len(api.moves))
                self.assertEqual([], list(destination.parent.glob(".x86qw_ktx_*")))

                services.cleanup_dedicated_ktx(materialized)

            self.assertFalse(destination.exists())
            self.assertFalse(destination.parent.exists())

    def test_pk3_windows_backend_journal_failure_does_not_orphan_promotion(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            api = FakeWindowsFileApi()
            journal = mock.Mock()
            journal.record_materialized.side_effect = RuntimeError("journal indisponível")

            with mock.patch.object(managed_files, "_WINDOWS_FILE_API", api):
                with self.assertRaisesRegex(services.InstallerError, "journal indisponível"):
                    services.materialize_dedicated_pk3(
                        package, destination_root, "teste", journal,
                    )

            self.assertFalse(destination.exists())
            self.assertFalse(destination.parent.exists())
            self.assertEqual([], list(destination_root.rglob(".x86qw_ktx_*")))
            journal.record_materialized.assert_called_once()

    def test_pk3_windows_backend_intent_failure_removes_private_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            api = FakeWindowsFileApi()
            journal = mock.Mock()
            journal.record_materialized_intent.side_effect = RuntimeError(
                "journal indisponível"
            )

            with mock.patch.object(managed_files, "_WINDOWS_FILE_API", api):
                with self.assertRaisesRegex(services.InstallerError, "journal indisponível"):
                    services.materialize_dedicated_pk3(
                        package, destination_root, "teste", journal,
                    )

            self.assertFalse(destination.exists())
            self.assertEqual([], api.moves)
            self.assertEqual([], list(destination_root.rglob(".x86qw_ktx_*")))
            journal.record_materialized.assert_not_called()

    def test_pk3_windows_backend_journal_failure_preserves_modified_same_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            api = FakeWindowsFileApi()
            journal = mock.Mock()

            def modify_then_fail(entry):
                entry.path.write_bytes(b"personal-concurrent-data")
                raise RuntimeError("journal indisponível")

            journal.record_materialized.side_effect = modify_then_fail

            with mock.patch.object(managed_files, "_WINDOWS_FILE_API", api):
                with self.assertRaisesRegex(services.InstallerError, "journal indisponível"):
                    services.materialize_dedicated_pk3(
                        package, destination_root, "teste", journal,
                    )

            self.assertEqual(b"personal-concurrent-data", destination.read_bytes())
            journal.record_materialized.assert_called_once()

    def test_pk3_windows_backend_no_replace_preserves_file_appearing_at_promotion(self):
        class AppearingDestinationApi(FakeWindowsFileApi):
            def move_no_replace(self, source, destination):
                Path(destination).write_bytes(b"personal")
                return super().move_no_replace(source, destination)

        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            api = AppearingDestinationApi()

            with mock.patch.object(managed_files, "_WINDOWS_FILE_API", api):
                with self.assertRaisesRegex(services.InstallerError, "surgiu durante"):
                    services.materialize_dedicated_pk3(
                        package, destination_root, "teste",
                    )

            self.assertEqual(b"personal", destination.read_bytes())
            self.assertEqual([], list(destination.parent.glob(".x86qw_ktx_*")))

    def test_pk3_windows_backend_inconclusive_open_preserves_modified_same_identity(self):
        class ModifiedBeforeConfirmationApi(FakeWindowsFileApi):
            fail_path = None

            def move_no_replace(self, source, destination):
                super().move_no_replace(source, destination)
                destination = Path(destination)
                destination.write_bytes(b"personal-concurrent-data")
                self.fail_path = destination

            def open_handle(self, path, *, access, creation, directory):
                if self.fail_path is not None and Path(path) == self.fail_path:
                    self.fail_path = None
                    raise OSError("confirmação pós-promoção inconclusiva")
                return super().open_handle(
                    path, access=access, creation=creation, directory=directory,
                )

        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            api = ModifiedBeforeConfirmationApi()

            with mock.patch.object(managed_files, "_WINDOWS_FILE_API", api):
                with self.assertRaisesRegex(
                    services.InstallerError, "alterado foi preservado",
                ):
                    services.materialize_dedicated_pk3(
                        package, destination_root, "teste",
                    )

            self.assertEqual(b"personal-concurrent-data", destination.read_bytes())
            self.assertEqual([], list(destination.parent.glob(".x86qw_ktx_*")))

    def test_pk3_windows_backend_cleanup_preserves_replacement_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            api = FakeWindowsFileApi()

            with mock.patch.object(managed_files, "_WINDOWS_FILE_API", api):
                materialized = services.materialize_dedicated_pk3(
                    package, destination_root, "teste",
                )
                destination.unlink()
                destination.write_bytes(b"personal")
                services.cleanup_dedicated_ktx(materialized)

            self.assertEqual(b"personal", destination.read_bytes())

    def test_pk3_windows_backend_rejects_reparse_parent_without_external_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, destination_root = self.package(
                root, [("configs/server.cfg", b"managed")],
            )
            external = root / "external"
            external.mkdir()
            marker = external / "personal.txt"
            marker.write_text("personal", encoding="utf-8")
            link = destination_root / "configs"
            try:
                link.symlink_to(external, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"symlink de diretório indisponível: {error}")

            with mock.patch.object(managed_files, "_WINDOWS_FILE_API", FakeWindowsFileApi()):
                with self.assertRaisesRegex(services.InstallerError, "Diretório inseguro"):
                    services.materialize_dedicated_pk3(
                        package, destination_root, "teste",
                    )

            self.assertEqual([marker], list(external.iterdir()))

    @unittest.skipUnless(os.name == "nt", "handles de arquivo são exercitados no runner Windows")
    def test_pk3_windows_native_materialization_identity_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            api = managed_files._get_windows_file_api()
            self.assertIsNotNone(api)

            materialized = services.materialize_dedicated_pk3(
                package, destination_root, "teste",
            )
            handle = api.open_handle(
                destination,
                access=api.GENERIC_READ,
                creation=api.OPEN_EXISTING,
                directory=False,
            )
            try:
                self.assertEqual(
                    materialized.files[0].identity,
                    api.checked_identity(handle, directory=False),
                )
            finally:
                api.close(handle)
            self.assertEqual([], list(destination.parent.glob(".x86qw_ktx_*")))

            services.cleanup_dedicated_ktx(materialized)

            self.assertFalse(destination.exists())
            self.assertFalse(destination.parent.exists())

    @unittest.skipUnless(os.name == "nt", "replacement é exercitado no runner Windows")
    def test_pk3_windows_native_cleanup_preserves_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            destination = destination_root / "configs/server.cfg"
            materialized = services.materialize_dedicated_pk3(
                package, destination_root, "teste",
            )
            destination.unlink()
            destination.write_bytes(b"personal")

            services.cleanup_dedicated_ktx(materialized)

            self.assertEqual(b"personal", destination.read_bytes())

    @unittest.skipUnless(os.name == "nt", "reparse point é exercitado no runner Windows")
    def test_pk3_windows_native_reparse_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, destination_root = self.package(
                root, [("configs/server.cfg", b"managed")],
            )
            external = root / "external"
            external.mkdir()
            marker = external / "personal.txt"
            marker.write_text("personal", encoding="utf-8")
            link = destination_root / "configs"
            try:
                link.symlink_to(external, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"privilégio para symlink indisponível: {error}")

            with self.assertRaisesRegex(services.InstallerError, "Diretório inseguro"):
                services.materialize_dedicated_pk3(
                    package, destination_root, "teste",
                )

            self.assertEqual([marker], list(external.iterdir()))

    @unittest.skipUnless(
        managed_files._secure_archive_dir_fd_supported() or os.name == "nt",
        "identidade de diretório requer handles POSIX ou Win32",
    )
    def test_pk3_cleanup_preserves_replacement_directory_with_new_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, destination_root = self.package(
                Path(temporary), [("configs/server.cfg", b"managed")],
            )
            materialized = services.materialize_dedicated_pk3(
                package, destination_root, "teste",
            )
            directory_entry = materialized.directories[0]
            self.assertTrue(managed_files.cleanup_materialized_file(materialized.files[0]))
            directory_entry.path.rmdir()
            directory_entry.path.mkdir()
            replacement_identity = managed_files._file_identity(directory_entry.path.lstat())
            self.assertNotEqual(directory_entry.identity, replacement_identity)

            self.assertFalse(managed_files.cleanup_materialized_directory(directory_entry))
            self.assertTrue(directory_entry.path.is_dir())

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
            if os.name == "nt":
                services.private_fs.protect_private_file(password_file)
            else:
                password_file.chmod(0o600)
            self.assertEqual("arquivo-secreto", services.read_password_file(password_file, "senha"))

    def test_private_password_permission_guidance_is_actionable_on_each_host(self):
        self.assertEqual(
            "use chmod 600",
            services.private_fs.private_file_permission_guidance(os_name="posix"),
        )
        self.assertEqual(
            "proteja a DACL para o usuário atual e LOCAL SYSTEM",
            services.private_fs.private_file_permission_guidance(os_name="nt"),
        )

    def test_password_error_uses_runtime_guidance_without_disclosing_the_secret(self):
        with tempfile.TemporaryDirectory() as temporary:
            password_file = Path(temporary) / "secret"
            password_file.write_text("segredo-nao-vazar", encoding="utf-8")
            with mock.patch.object(
                services.private_fs,
                "read_private_user_file",
                side_effect=OSError("segredo-nao-vazar"),
            ), mock.patch.object(
                services.private_fs,
                "private_file_permission_guidance",
                return_value="orientação fornecida pelo runtime",
                create=True,
            ), self.assertRaises(services.InstallerError) as raised:
                services.read_password_file(password_file, "senha")

        message = str(raised.exception)
        self.assertIn("orientação fornecida pelo runtime", message)
        self.assertNotIn("segredo-nao-vazar", message)

    def test_help_fallback_uses_the_canonical_host_adapter(self):
        original = services._service_context
        try:
            services._service_context = None
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                services.main(["--help"])
            self.assertIs(services.host_adapter, services._context().host_platform)
        finally:
            assert original is not None
            services.configure_context(original)

    def test_passwords_are_kept_out_of_child_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / "td2").mkdir()
            binary = target / "mvdsv"
            binary.write_bytes(b"fixture\n")
            binary.chmod(0o755)
            launch_target = host_platform.executable_launch_target(binary)
            options = services.parse_arguments([
                "host", "td2", "--map", "dm6", "--password", "jogador-secreto",
                "--spectator-password", "espectador-secreto",
                "--rcon-password", "rcon-secreto", "--target", str(target),
            ], ROOT)
            game = next(game for game in services.gameplay.LOCAL_GAMES if game.key == "td2")
            selection = services.HostedGame(game, None, "dm6", frozenset(), options.ktx_options)
            with mock.patch.object(
                services, "runtime_launch_target", return_value=launch_target,
            ), mock.patch.object(
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
            b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n"
            b'<td class="adr">127.0.0.1:28501</td>',
            b"",
        ]
        services.wait_http_readiness(
            process,
            services.ServiceReadiness("http", "127.0.0.1", 28000, "127.0.0.1:28501"),
            timeout=0.1,
            connection_factory=lambda *_args, **_kwargs: connection,
        )
        connection.sendall.assert_called_once_with(
            b"GET /nowplaying/ HTTP/1.0\r\nHost: x86qw.local\r\n\r\n",
        )

    def test_qtv_http_readiness_requires_success_and_the_complete_upstream(self):
        page = (
            b"HTTP/1.0 200 OK\r\nContent-Type: text/html\r\n\r\n"
            b'<td class="adr">[::1]:28501</td>'
        )
        self.assertTrue(services.qtv_http_response_ready(page, "[::1]:28501"))
        self.assertFalse(services.qtv_http_response_ready(page, "[::1]:28502"))
        self.assertFalse(services.qtv_http_response_ready(
            b"HTTP/1.0 301 Moved Permanently\r\nLocation: /nowplaying/\r\n\r\n",
            "[::1]:28501",
        ))

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

    def test_session_journal_initial_write_failure_removes_empty_session_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)

            with mock.patch.object(
                runtime_sessions,
                "atomic_write_json",
                side_effect=atomic_io.AtomicWriteError("disco indisponível"),
            ), self.assertRaisesRegex(
                services.InstallerError, "journal privado.*não pôde ser gravado",
            ):
                services.SessionJournal(target, session_id="first-write-failure")

            sessions = target / ".x86qw/sessions"
            self.assertTrue(sessions.is_dir())
            self.assertEqual([], list(sessions.iterdir()))

    def test_recovery_removes_empty_session_left_by_hard_controller_exit(self):
        script = """
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from x86qw_runtime.supervisor import sessions

sessions.SessionJournal._write = lambda self: os._exit(91)
sessions.SessionJournal(Path(sys.argv[2]), session_id=sys.argv[3])
"""
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            session_id = runtime_sessions.session_control.new_session_id()
            completed = subprocess.run(
                [sys.executable, "-c", script, str(ROOT), str(target), session_id],
                check=False,
                timeout=10,
            )
            orphan = target / ".x86qw/sessions" / session_id

            self.assertEqual(91, completed.returncode)
            self.assertTrue(orphan.is_dir())
            self.assertEqual([], list(orphan.iterdir()))

            services.recover_sessions(target)

            self.assertFalse(orphan.exists())

    def test_recovery_removes_empty_atomic_staging_left_by_hard_exit(self):
        script = """
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from x86qw_runtime.supervisor import sessions

real_mkstemp = sessions.private_fs.private_mkstemp
def exit_after_staging(**kwargs):
    real_mkstemp(**kwargs)
    os._exit(92)

sessions.private_fs.private_mkstemp = exit_after_staging
sessions.SessionJournal(Path(sys.argv[2]), session_id=sys.argv[3])
"""
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            session_id = runtime_sessions.session_control.new_session_id()
            completed = subprocess.run(
                [sys.executable, "-c", script, str(ROOT), str(target), session_id],
                check=False,
                timeout=10,
            )
            orphan = target / ".x86qw/sessions" / session_id

            self.assertEqual(92, completed.returncode)
            staging = list(orphan.glob(".session.json.*.tmp"))
            self.assertEqual(1, len(staging))
            self.assertEqual(0, staging[0].stat().st_size)

            services.recover_sessions(target)

            self.assertFalse(orphan.exists())

    def test_recovery_preserves_unknown_content_without_initial_journal(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            metadata = target / ".x86qw"
            sessions = metadata / "sessions"
            session = sessions / runtime_sessions.session_control.new_session_id()
            services.private_fs.ensure_private_directory(metadata)
            services.private_fs.ensure_private_directory(sessions)
            services.private_fs.create_private_directory(session)
            personal = session / "personal.txt"
            descriptor = services.private_fs.create_private_file(personal)
            with os.fdopen(descriptor, "wb") as output:
                output.write(b"personal")

            with self.assertRaisesRegex(
                services.InstallerError, "Journal de sessão inválido",
            ):
                services.recover_sessions(target)

            self.assertEqual(b"personal", personal.read_bytes())

    @unittest.skipIf(os.name == "nt", "journals pre-DACL are quarantined on Windows")
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
            services.ensure_private_directory(target / ".x86qw")
            services.ensure_private_directory(target / ".x86qw/sessions")
            services.ensure_private_directory(session)
            services.private_fs.protect_private_file(path)

            with mock.patch.object(
                services, "process_identity",
                side_effect=AssertionError("sessão limpa não deve consultar PID"),
            ):
                services.recover_sessions(target)

            self.assertEqual(legacy, json.loads(path.read_text(encoding="utf-8")))

    def test_unfinished_legacy_journal_never_authorizes_recovery_actions(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            journal = services.SessionJournal(target, session_id="legacy-untrusted")
            journal.data.pop("private_filesystem")
            journal.data["status"] = "interrupted"
            journal._write()

            with mock.patch.object(
                services.private_fs,
                "read_private_file_with_legacy_windows_migration",
                return_value=(journal.path.read_bytes(), True),
            ), mock.patch.object(
                services, "journal_controller_probe",
            ) as controller_probe, mock.patch.object(
                services, "journal_process_probe",
            ) as process_probe, self.assertRaisesRegex(
                services.InstallerError, "conteúdo histórico.*não pode autorizar",
            ):
                services.assert_recovery_processes_confirmable(target)

            controller_probe.assert_not_called()
            process_probe.assert_not_called()
            self.assertEqual(
                "interrupted",
                json.loads(journal.path.read_text(encoding="utf-8"))["status"],
            )

    @unittest.skipIf(os.name == "nt", "journals pre-DACL are quarantined on Windows")
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
            services.ensure_private_directory(target / ".x86qw")
            services.ensure_private_directory(target / ".x86qw/sessions")
            services.ensure_private_directory(session)
            services.private_fs.protect_private_file(path)

            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(
                output,
            ), self.assertRaisesRegex(
                services.InstallerError, "identidade.*preservado",
            ):
                services.recover_sessions(target)

            self.assertEqual(secret, config.read_text(encoding="utf-8"))
            recovered = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("interrupted", recovered["status"])
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

    def test_session_recovery_preserves_legacy_materialized_file_without_size(self):
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
            entry = journal.data["materialized_files"][0]
            entry.pop("expected_size")
            journal._write()

            services.recover_sessions(target)

            self.assertEqual("managed", created.read_text(encoding="utf-8"))
            recovered = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertTrue(recovered["materialized_files"][0]["modified_during_session"])

    def test_session_journal_rejects_boolean_or_oversize_expected_size(self):
        for invalid_size in (True, services._MAX_MANAGED_FILE_SIZE + 1):
            with self.subTest(expected_size=invalid_size), tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary)
                (target / ".x86qw").mkdir()
                created = target / "qw" / "created.cfg"
                created.parent.mkdir()
                created.write_text("managed", encoding="utf-8")
                journal = services.SessionJournal(target)
                journal.record_materialized(services.MaterializedFile(
                    created, services.file_sha256(created), "fixture.pk3", True, False,
                ))
                journal.data["materialized_files"][0]["expected_size"] = invalid_size
                journal._write()

                with self.assertRaisesRegex(
                    services.InstallerError, "Journal de sessão inválido",
                ):
                    services.load_session_journal(journal.path)

    def test_session_journal_rejects_unknown_file_intent_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            created = target / "qw/created.cfg"
            created.parent.mkdir(parents=True)
            created.write_text("managed", encoding="utf-8")
            journal = services.SessionJournal(target)
            journal.record_materialized(services.MaterializedFile(
                created, services.file_sha256(created), "fixture.pk3", True, False,
            ))
            journal.data["materialized_files"][0]["state"] = "unknown"
            journal._write()

            with self.assertRaisesRegex(
                services.InstallerError, "Journal de sessão inválido",
            ):
                services.load_session_journal(journal.path)

    def test_session_recovery_removes_modified_sensitive_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(config_dir, "session-", ["hostname local"], journal)
            journal.release_all_sensitive_temporaries()
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
            self.assertNotIn("expected_size", entry)

    def test_sensitive_temporary_ready_record_retains_creation_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)

            config = services.temporary_config(
                config_dir, "session-", ["password secret"], journal,
            )

            identity = managed_files.persistent_path_identity(
                config, directory=False,
            )
            entry = json.loads(journal.path.read_text(encoding="utf-8"))[
                "temporary_files"
            ][0]
            self.assertEqual(identity[0], entry["device"])
            self.assertEqual(identity[1], entry["inode"])
            self.assertEqual("ready", entry["state"])
            journal.release_all_sensitive_temporaries()
            config.unlink()

    def test_sensitive_temporary_regular_replacement_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(
                config_dir, "session-", ["password secret"], journal,
            )
            journal.release_all_sensitive_temporaries()
            config.unlink()
            config.write_text("personal", encoding="utf-8")
            output = io.StringIO()

            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(
                output,
            ), self.assertRaisesRegex(
                services.InstallerError, "identidade.*preservado",
            ):
                services.recover_sessions(target)

            self.assertEqual("personal", config.read_text(encoding="utf-8"))
            self.assertIn("substituído", output.getvalue())

    def test_current_session_cleanup_rejects_sensitive_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(
                config_dir, "session-", ["password secret"], journal,
            )
            journal.release_all_sensitive_temporaries()
            config.unlink()
            config.write_text("personal", encoding="utf-8")

            with self.assertRaisesRegex(
                services.InstallerError, "identidade.*preservado",
            ):
                services.cleanup_current_session(journal, [config], [])

            self.assertEqual("personal", config.read_text(encoding="utf-8"))

    def test_sensitive_temporary_intent_is_durable_before_completion(self):
        class CompletionFailureJournal(services.SessionJournal):
            def record_temporary(self, path, origin, *, sensitive, tracked=None):
                self.persisted_before_completion = json.loads(
                    self.path.read_text(encoding="utf-8")
                )
                raise RuntimeError("journal indisponível")

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = CompletionFailureJournal(target)
            secret = "segredo-antes-da-conclusão"

            with self.assertRaisesRegex(RuntimeError, "journal indisponível"):
                services.temporary_config(
                    config_dir,
                    "session-",
                    [f'password "{secret}"'],
                    journal,
                )

            persisted = journal.persisted_before_completion
            self.assertEqual(1, len(persisted["temporary_files"]))
            entry = persisted["temporary_files"][0]
            self.assertEqual("pending", entry["state"])
            self.assertTrue(entry["sensitive"])
            self.assertNotIn("expected_hash", entry)
            self.assertNotIn("expected_size", entry)
            self.assertNotIn(secret, journal.path.read_text(encoding="utf-8"))

    def test_non_sensitive_temporary_intent_failure_removes_empty_reservation(self):
        class IntentFailureJournal(services.SessionJournal):
            def record_temporary_intent(self, *args, **kwargs):
                raise RuntimeError("journal indisponível")

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = IntentFailureJournal(target)

            with self.assertRaisesRegex(RuntimeError, "journal indisponível"):
                services.temporary_config(
                    config_dir,
                    "session-",
                    ["hostname local"],
                    journal,
                    sensitive=False,
                )

            self.assertEqual([], list(config_dir.iterdir()))

    def test_sensitive_temporary_replaced_by_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(config_dir, "session-", ["password secret"], journal)
            journal.release_all_sensitive_temporaries()
            config.unlink()
            config.mkdir()
            personal = config / "personal.cfg"
            personal.write_text("preservar", encoding="utf-8")
            with self.assertRaisesRegex(
                services.InstallerError, "identidade.*preservado",
            ):
                services.recover_sessions(target)
            self.assertEqual("preservar", personal.read_text(encoding="utf-8"))

    def test_sensitive_temporary_symlink_replacement_is_preserved_without_touching_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            personal = config_dir / "personal.cfg"
            personal.write_text("preservar", encoding="utf-8")
            journal = services.SessionJournal(target)
            config = services.temporary_config(config_dir, "session-", ["password secret"], journal)
            journal.release_all_sensitive_temporaries()
            config.unlink()
            config.symlink_to(personal)
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(
                output,
            ), self.assertRaisesRegex(
                services.InstallerError, "identidade.*preservado",
            ):
                services.recover_sessions(target)
            self.assertTrue(config.is_symlink())
            self.assertEqual("preservar", personal.read_text(encoding="utf-8"))
            self.assertIn("substituído", output.getvalue())

    @unittest.skipIf(os.name == "nt", "FIFO é uma fixture POSIX")
    def test_sensitive_temporary_special_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(config_dir, "session-", ["password secret"], journal)
            journal.release_all_sensitive_temporaries()
            config.unlink()
            os.mkfifo(config)
            with self.assertRaisesRegex(
                services.InstallerError, "identidade.*preservado",
            ):
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
            recorded = journal.data["temporary_files"][0]
            self.assertEqual(config.stat().st_size, recorded["expected_size"])
            self.assertIsInstance(recorded["device"], int)
            self.assertIsInstance(recorded["inode"], int)
            config.write_text("// configuração pessoalizada\n", encoding="utf-8")
            services.recover_sessions(target)
            self.assertTrue(config.exists())
            self.assertIn("pessoalizada", config.read_text(encoding="utf-8"))

    def test_session_recovery_removes_unchanged_non_sensitive_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(
                config_dir, "session-", ["hostname local"], journal, sensitive=False,
            )

            services.recover_sessions(target)

            self.assertFalse(config.exists())
            self.assertEqual("clean", json.loads(
                journal.path.read_text(encoding="utf-8"),
            )["status"])

    @unittest.skipUnless(
        managed_files._secure_archive_dir_fd_supported(),
        "corrida requer operações POSIX relativas a descritor",
    )
    def test_non_sensitive_temporary_recovery_preserves_replacement_after_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            config = services.temporary_config(
                config_dir, "session-", ["hostname local"], journal, sensitive=False,
            )
            real_hash = managed_files._hash_open_file
            replaced = False

            def replace_after_hash(descriptor, **kwargs):
                nonlocal replaced
                digest = real_hash(descriptor, **kwargs)
                if not replaced:
                    replaced = True
                    config.unlink()
                    config.write_text("personal-concurrent-data", encoding="utf-8")
                return digest

            with mock.patch.object(
                managed_files, "_hash_open_file", side_effect=replace_after_hash,
            ):
                services.recover_sessions(target)

            self.assertEqual("personal-concurrent-data", config.read_text(encoding="utf-8"))
            self.assertEqual([], list(config_dir.glob(".x86qw_cleanup_*")))
            recovered = json.loads(journal.path.read_text(encoding="utf-8"))
            self.assertTrue(recovered["temporary_files"][0]["modified_during_session"])

    def test_non_sensitive_temporary_creation_failure_preserves_replacement_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            config_dir = target / "qw"
            config_dir.mkdir()
            journal = services.SessionJournal(target)
            replaced: Path | None = None

            def replace_with_directory(path, _origin, **_kwargs):
                nonlocal replaced
                replaced = Path(path)
                replaced.unlink()
                replaced.mkdir()
                (replaced / "personal.txt").write_text("personal", encoding="utf-8")
                raise RuntimeError("journal indisponível")

            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(
                output,
            ), mock.patch.object(
                journal, "record_temporary", side_effect=replace_with_directory,
            ), self.assertRaisesRegex(RuntimeError, "journal indisponível"):
                services.temporary_config(
                    config_dir,
                    "session-",
                    ["hostname local"],
                    journal,
                    sensitive=False,
                )

            self.assertIsNotNone(replaced)
            assert replaced is not None
            self.assertEqual("personal", (replaced / "personal.txt").read_text(encoding="utf-8"))
            self.assertIn("foi preservado", output.getvalue())

    def test_active_session_lock_blocks_recovery_and_preserves_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            first = services.SessionLock.acquire(target, "host")
            journal = None
            config = None
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
                if journal is not None:
                    journal.release_all_sensitive_temporaries()
                if config is not None:
                    services.unlink_sensitive_temporary(config)
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
                "format": 3, "project": "x86qw", "session_id": old_session,
                "operation_kind": "service", "private_filesystem": 1,
                "controller_pid": 999999999, "controller_start_token": "dead-token",
                "controller_executable": str(target / "dead-controller"),
                "created_at": "2026-07-31T00:00:00+00:00", "installation": str(target),
                "command": "host",
            }), encoding="utf-8")
            services.private_fs.protect_private_file(lock_path)
            acquired = services.SessionLock.acquire(target, "proxy")
            try:
                services.recover_sessions(target)
                acquired.confirm_recovery()
                self.assertEqual("clean", json.loads(journal.path.read_text(encoding="utf-8"))["status"])
            finally:
                acquired.release()

    def test_stale_lock_reclaim_is_atomic_between_controllers(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            sessions = target / ".x86qw/sessions"
            sessions.mkdir(parents=True)
            services.ensure_private_directory(target / ".x86qw")
            services.ensure_private_directory(sessions)
            lock_path = sessions / "active.lock"
            stale_token = "stale-controller-token"
            lock_path.write_text(json.dumps({
                "format": 3, "project": "x86qw", "session_id": "stale-session",
                "operation_kind": "service", "private_filesystem": 1,
                "controller_pid": 999999999, "controller_start_token": stale_token,
                "controller_executable": str(target / "dead-controller"),
                "created_at": "2026-07-31T00:00:00+00:00", "installation": str(target),
                "command": "host",
            }), encoding="utf-8")
            services.private_fs.protect_private_file(lock_path)

            stale_observed = threading.Barrier(2)
            replacement_created = threading.Event()
            release = threading.Event()
            results: list[tuple[str, object]] = []
            results_lock = threading.Lock()
            replace_lock = threading.Lock()
            replace_calls = 0
            real_replace = services.session_control.os.replace
            real_create = services.session_control.private_fs.create_private_file

            def probe(pid: int, creation_token: str, executable: str):
                del pid, executable
                if creation_token == stale_token:
                    try:
                        stale_observed.wait(0.25)
                    except threading.BrokenBarrierError:
                        pass
                    return services.ProcessProbe("dead")
                return services.ProcessProbe(
                    "alive",
                    services.ProcessIdentity(os.getpid(), creation_token, sys.executable),
                )

            def ordered_replace(source: Path, destination: Path):
                nonlocal replace_calls
                with replace_lock:
                    replace_calls += 1
                    ordinal = replace_calls
                if ordinal == 2:
                    self.assertTrue(replacement_created.wait(2))
                return real_replace(source, destination)

            def observed_create(path: Path):
                descriptor = real_create(path)
                if path == lock_path:
                    replacement_created.set()
                return descriptor

            def acquire(command: str) -> None:
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

            with mock.patch.object(
                services.session_control, "probe_expected_process", side_effect=probe,
            ), mock.patch.object(
                services.session_control.os, "replace", side_effect=ordered_replace,
            ), mock.patch.object(
                services.session_control.private_fs, "create_private_file",
                side_effect=observed_create,
            ):
                threads = [
                    threading.Thread(target=acquire, args=(command,))
                    for command in ("host", "proxy")
                ]
                for thread in threads:
                    thread.start()
                deadline = time.monotonic() + 3
                while len(results) < 2 and time.monotonic() < deadline:
                    time.sleep(0.01)
                release.set()
                for thread in threads:
                    thread.join(2)

            self.assertEqual(1, sum(kind == "acquired" for kind, _ in results), results)
            self.assertEqual(1, sum(kind == "blocked" for kind, _ in results), results)

    def test_stale_lock_reclaim_is_atomic_between_cli_processes(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            sessions = target / ".x86qw/sessions"
            sessions.mkdir(parents=True)
            services.ensure_private_directory(target / ".x86qw")
            services.ensure_private_directory(sessions)
            stale_token = "stale-cli-token"
            lock_path = sessions / "active.lock"
            lock_path.write_text(json.dumps({
                "format": 3, "project": "x86qw", "session_id": "stale-cli-session",
                "operation_kind": "service", "private_filesystem": 1,
                "controller_pid": 999999999, "controller_start_token": stale_token,
                "controller_executable": str(target / "dead-controller"),
                "created_at": "2026-07-31T00:00:00+00:00", "installation": str(target),
                "command": "host",
            }), encoding="utf-8")
            services.private_fs.protect_private_file(lock_path)
            coordination = target / "coordination"
            coordination.mkdir()
            start = coordination / "start"
            release = coordination / "release"
            script = """
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import session_control

target = Path(sys.argv[2])
coordination = Path(sys.argv[3])
command = sys.argv[4]
stale_token = "stale-cli-token"
real_replace = session_control.os.replace
real_create = session_control.private_fs.create_private_file

while not (coordination / "start").exists():
    time.sleep(0.005)

def probe(pid, creation_token, executable):
    if creation_token == stale_token:
        (coordination / ("observed-" + str(os.getpid()))).touch()
        deadline = time.monotonic() + 0.75
        while len(list(coordination.glob("observed-*"))) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        return session_control.ProcessProbe("dead")
    return session_control.ProcessProbe(
        "alive", session_control.ProcessIdentity(pid, creation_token, executable),
    )

def ordered_replace(source, destination):
    first = coordination / "first-replacer"
    try:
        marker = os.open(first, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        deadline = time.monotonic() + 2
        while not (coordination / "replacement-created").exists() and time.monotonic() < deadline:
            time.sleep(0.005)
    else:
        os.close(marker)
    return real_replace(source, destination)

def observed_create(path):
    descriptor = real_create(path)
    if path.name == "active.lock":
        (coordination / "replacement-created").touch()
    return descriptor

session_control.probe_expected_process = probe
session_control.os.replace = ordered_replace
session_control.private_fs.create_private_file = observed_create
try:
    lock = session_control.InstallationLock.acquire(target, command, "service")
except session_control.SessionControlError as error:
    (coordination / ("blocked-" + str(os.getpid()))).write_text(str(error), encoding="utf-8")
else:
    (coordination / ("acquired-" + str(os.getpid()))).touch()
    deadline = time.monotonic() + 3
    while not (coordination / "release").exists() and time.monotonic() < deadline:
        time.sleep(0.005)
    lock.release()
"""
            commands = ("host", "proxy")
            processes = [
                subprocess.Popen(
                    [
                        sys.executable, "-c", script,
                        str(ROOT / "dist/installer/bin"), str(target),
                        str(coordination), command,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for command in commands
            ]
            start.touch()
            deadline = time.monotonic() + 5
            while (
                len(list(coordination.glob("acquired-*")))
                + len(list(coordination.glob("blocked-*"))) < 2
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            release.touch()
            completed = [process.communicate(timeout=5) for process in processes]
            self.assertEqual(
                [0, 0], [process.returncode for process in processes], completed,
            )
            self.assertEqual(1, len(list(coordination.glob("acquired-*"))), completed)
            blocked = list(coordination.glob("blocked-*"))
            self.assertEqual(1, len(blocked), completed)
            self.assertIn(
                "operação x86QW ativa", blocked[0].read_text(encoding="utf-8"),
            )
            self.assertFalse(lock_path.exists())

    def test_installation_mutex_serializes_distinct_cli_processes(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            sessions = target / ".x86qw/sessions"
            sessions.mkdir(parents=True)
            services.ensure_private_directory(target / ".x86qw")
            services.ensure_private_directory(sessions)
            coordination = target / "mutex-coordination"
            coordination.mkdir()
            script = """
import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import session_control

target = Path(sys.argv[2])
sessions = Path(sys.argv[3])
coordination = Path(sys.argv[4])
role = sys.argv[5]
if role == "waiter":
    (coordination / "waiter-ready").touch()
with session_control._installation_acquisition_mutex(target, sessions):
    (coordination / (role + "-entered")).touch()
    if role == "holder":
        deadline = time.monotonic() + 3
        while not (coordination / "release-holder").exists() and time.monotonic() < deadline:
            time.sleep(0.005)
"""

            def start(role: str):
                return subprocess.Popen(
                    [
                        sys.executable, "-c", script,
                        str(ROOT / "dist/installer/bin"), str(target),
                        str(sessions), str(coordination), role,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

            holder = start("holder")
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
            self.assertEqual([0, 0], [holder.returncode, waiter.returncode], completed)
            self.assertTrue((coordination / "waiter-entered").exists())

    def test_windows_installation_mutex_uses_the_global_namespace(self):
        name = services.session_control._windows_acquisition_mutex_name(
            Path("C:/x86QW/quake-world"),
        )
        self.assertTrue(name.startswith("Global\\x86QW-install-"), name)

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
            try:
                if os.name == "nt":
                    with self.assertRaisesRegex(
                        services.InstallerError, "raiz privada.*protegida",
                    ):
                        services.SessionLock.acquire(target, "proxy")
                    self.assertFalse(first.path.exists())
                else:
                    second = services.SessionLock.acquire(target, "proxy")
                    try:
                        with self.assertRaisesRegex(
                            services.InstallerError, "controlador.*continua ativo",
                        ):
                            services.recover_sessions(target)
                    finally:
                        second.release()
                self.assertTrue(config.exists())
                self.assertEqual(
                    "starting",
                    json.loads(journal.path.read_text(encoding="utf-8"))["status"],
                )
            finally:
                journal.release_sensitive_temporary(config)
                services.unlink_sensitive_temporary(config)
                first.release()

    def test_inconclusive_controller_identity_preserves_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            sessions = target / ".x86qw/sessions"
            sessions.mkdir(parents=True)
            lock_path = sessions / "active.lock"
            lock_path.write_text(json.dumps({
                "format": 3, "project": "x86qw", "session_id": "unknown-session",
                "operation_kind": "service", "private_filesystem": 1,
                "controller_pid": 424242, "controller_start_token": "unknown-token",
                "controller_executable": str(target / "controller"),
                "created_at": "2026-07-31T00:00:00+00:00", "installation": str(target),
                "command": "qtv",
            }), encoding="utf-8")
            services.ensure_private_directory(target / ".x86qw")
            services.ensure_private_directory(sessions)
            services.private_fs.protect_private_file(lock_path)
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
            services.ensure_private_directory(target / ".x86qw")
            services.ensure_private_directory(sessions)
            services.private_fs.protect_private_file(lock_path)
            with self.assertRaisesRegex(services.InstallerError, "inválido"):
                services.SessionLock.acquire(target, "host")
            self.assertEqual("{invalid", lock_path.read_text(encoding="utf-8"))

    def test_lock_owner_rejects_boolean_integer_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            sessions = target / ".x86qw/sessions"
            sessions.mkdir(parents=True)
            services.ensure_private_directory(target / ".x86qw")
            services.ensure_private_directory(sessions)
            lock_path = sessions / "active.lock"
            owner = {
                "format": 1, "project": "x86qw", "session_id": "session",
                "controller_pid": 999999999, "controller_start_token": "token",
                "controller_executable": str(target / "controller"),
                "created_at": "2026-07-31T00:00:00+00:00", "installation": str(target),
                "command": "host",
            }
            for field in ("format", "controller_pid"):
                with self.subTest(field=field):
                    invalid = dict(owner)
                    invalid[field] = True
                    lock_path.write_text(json.dumps(invalid), encoding="utf-8")
                    services.private_fs.protect_private_file(lock_path)
                    with self.assertRaisesRegex(
                        services.session_control.SessionControlError, "inválido",
                    ):
                        services.session_control.read_lock_owner(lock_path)

    def test_current_lock_rejects_boolean_private_filesystem_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            lock = services.SessionLock.acquire(target, "host")
            invalid = dict(lock.owner)
            invalid["private_filesystem"] = True
            lock.path.write_text(json.dumps(invalid), encoding="utf-8")
            services.private_fs.protect_private_file(lock.path)
            with self.assertRaisesRegex(
                services.session_control.SessionControlError, "inválido",
            ):
                services.session_control.read_lock_owner(lock.path)
            # Restore the fixture to let the owner release its exact lock.
            lock.path.write_text(json.dumps(lock.owner), encoding="utf-8")
            services.private_fs.protect_private_file(lock.path)
            lock.release()

    def test_lock_creation_never_unlinks_when_identity_cannot_be_proved(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            real_fstat = services.session_control.os.fstat
            real_create = services.session_control.private_fs.create_private_file
            lock_descriptors: list[int] = []

            def reject_lock_identity(descriptor: int):
                if descriptor in lock_descriptors:
                    raise OSError("identity unavailable")
                return real_fstat(descriptor)

            def capture_lock_descriptor(path: Path):
                descriptor = real_create(path)
                lock_descriptors.append(descriptor)
                return descriptor

            with mock.patch.object(
                services.session_control.os, "fstat", side_effect=reject_lock_identity,
            ), mock.patch.object(
                services.session_control.private_fs,
                "create_private_file",
                side_effect=capture_lock_descriptor,
            ), mock.patch.object(
                services.session_control.private_fs,
                "unlink_private_file",
            ) as unlink:
                with self.assertRaisesRegex(
                    services.InstallerError, "identidade não pôde ser comprovada",
                ):
                    services.SessionLock.acquire(target, "host")

            unlink.assert_not_called()
            lock_path = target / ".x86qw/sessions/active.lock"
            self.assertTrue(lock_path.exists())
            self.assertEqual(b"", lock_path.read_bytes())

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

    def test_status_lists_every_active_service_and_only_safe_parameters(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            lock = services.SessionLock.acquire(target, "host")
            try:
                journal = services.SessionJournal(
                    target, session_id=lock.session_id, controller=lock.owner,
                )
                journal.data["status"] = "running"
                processes = journal.data["processes"]
                self.assertIsInstance(processes, list)
                for index, (label, runtime, port, parameters) in enumerate((
                    ("MVDSV", "mvdsv", 28501, {
                        "game": "KTX", "mode": "Duel", "map": "dm6",
                        "bots": "1", "bot_skill": "random",
                        "secrets": "RCON", "password": "raw-password",
                    }),
                    ("QTV", "qtv", 28000, {
                        "http": "http://127.0.0.1:28000/",
                        "upstream": "127.0.0.1:28501",
                        "upstream_secret": "configurado",
                    }),
                    ("QWFWD", "qwfwd", 30000, {
                        "bind": "127.0.0.1", "protocol": "UDP QuakeWorld",
                    }),
                ), 1):
                    processes.append({
                        "label": label,
                        "runtime": runtime,
                        "pid": 9000 + index,
                        "process_group": 9000 + index,
                        "executable": str(target / runtime),
                        "creation_token": f"token-{index}",
                        "started_at": "2026-08-02T00:00:00+00:00",
                        "address": "127.0.0.1",
                        "port": port,
                        "parameters": parameters,
                    })
                journal._write()
                alive = services.ProcessProbe(
                    "alive", services.ProcessIdentity(1, "token", sys.executable),
                )
                output = io.StringIO()
                with mock.patch.object(
                    services, "probe_expected_process", return_value=alive,
                ), contextlib.redirect_stdout(output):
                    services.show_service_status(target)
            finally:
                lock.release()
            rendered = output.getvalue()
            for value in (
                "MVDSV", "QTV", "QWFWD", "Duel", "dm6", "random",
                "127.0.0.1:28501", "http://127.0.0.1:28000/", "UDP QuakeWorld",
                "Serviços › Encerrar serviços ativos", "status --stop",
            ):
                self.assertIn(value, rendered)
            self.assertRegex(rendered, r"Segredos\s+\| RCON")
            self.assertNotIn("raw-password", rendered)

    def test_status_without_a_stack_is_read_only_and_successful(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                services.show_service_status(target)
            self.assertIn("Nenhum serviço x86QW está ativo", output.getvalue())
            self.assertFalse((target / ".x86qw").exists())

    def test_background_controller_arguments_and_request_keep_secrets_off_argv(self):
        options = services.parse_arguments([
            "qtv", "--target", "/tmp/x86qw-test", "--bind", "127.0.0.1",
            "--upstream", "127.0.0.1:28501", "--qtv-password", "segredo",
            "--background",
        ], ROOT)
        arguments = services.background_controller_arguments(
            options, None, ".x86qw/logs/service-1234.log",
        )
        self.assertIn("--background-child", arguments)
        self.assertIn("--background-log", arguments)
        self.assertNotIn("--background", arguments)
        self.assertNotIn("--prompt-qtv-password", arguments)
        self.assertNotIn("segredo", arguments)

        request = {
            "format": 1,
            "project": "x86qw",
            "secrets": {
                "password": "jogador", "spectator_password": "espectador",
                "rcon_password": "rcon", "qtv_password": "qtv",
            },
        }
        stream = SimpleNamespace(
            buffer=io.BytesIO((json.dumps(request) + "\n").encode("utf-8")),
        )
        with mock.patch.object(services.sys, "stdin", stream):
            services.read_background_request(options)
        self.assertEqual("qtv", options.qtv_password)
        self.assertTrue(options.background)
        self.assertFalse(options.prompt_qtv_password)

    def test_status_stop_request_performs_coordinated_shutdown_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve()
            lock = services.SessionLock.acquire(target, "proxy")
            journal = services.SessionJournal(
                target, session_id=lock.session_id, controller=lock.owner,
                background=True,
                background_log=".x86qw/logs/service-test.log",
            )
            resources = services.ServiceResources([], [])
            resources.session_lock = lock
            resources.recovery_confirmed = True
            resources.journal = journal
            result: list[int] = []

            def controller() -> None:
                with services.finalize_service_operation(resources):
                    result.append(services.run_processes([
                        services.ProcessSpec(
                            "fixture",
                            (sys.executable, "-c", "import time; time.sleep(60)"),
                            target,
                        ),
                    ], journal))

            thread = threading.Thread(target=controller)
            thread.start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                state = services.load_session_journal(journal.path)
                if state["status"] == "running":
                    break
                time.sleep(0.05)
            else:
                self.fail("a fixture de serviço não ficou pronta")
            services.request_service_stop(target, timeout=5)
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual([0], result)
            self.assertFalse(lock.path.exists())
            self.assertFalse((journal.directory / "stop.request").exists())
            final = services.load_session_journal(journal.path)
            self.assertEqual("clean", final["status"])
            self.assertTrue(final["background"])
            self.assertEqual(".x86qw/logs/service-test.log", final["background_log"])

    def test_stop_request_is_published_only_after_the_writer_is_flushed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            request = directory / "stop.request"
            payload = b'{"format": 1, "project": "x86qw"}\n'
            original_fsync = services.os.fsync
            observations: list[bool] = []

            def assert_private_until_fsync(descriptor: int) -> None:
                observations.append(not request.exists())
                original_fsync(descriptor)

            with mock.patch.object(
                services.os, "fsync", side_effect=assert_private_until_fsync,
            ):
                services.publish_stop_request(request, payload)

            self.assertEqual([True], observations)
            self.assertEqual(payload, request.read_bytes())
            self.assertEqual([], list(directory.glob(".stop-*.request")))
            services.unlink_stop_request(request)
            self.assertFalse(request.exists())

    def test_runtime_stop_request_fsync_failure_leaves_no_transaction_residue(self):
        publisher = getattr(runtime_sessions, "publish_stop_request", None)
        self.assertIsNotNone(publisher, "stop request publication must be runtime-owned")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            request = directory / "stop.request"

            with mock.patch.object(
                runtime_sessions.os,
                "fsync",
                side_effect=OSError("injected stop request fsync failure"),
            ), self.assertRaises(OSError):
                publisher(request, b"transactional stop\n")

            self.assertFalse(request.exists())
            self.assertEqual([], list(directory.iterdir()))

    def test_runtime_stop_request_preserves_a_concurrent_request(self):
        publisher = getattr(runtime_sessions, "publish_stop_request", None)
        self.assertIsNotNone(publisher, "stop request publication must be runtime-owned")
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            request = directory / "stop.request"
            descriptor = private_fs.create_private_file(request)
            with os.fdopen(descriptor, "wb") as output:
                output.write(b"concurrent winner\n")

            with self.assertRaisesRegex(services.InstallerError, "Já existe"):
                publisher(request, b"losing request\n")

            self.assertEqual(b"concurrent winner\n", request.read_bytes())
            self.assertEqual([request], list(directory.iterdir()))

    def test_runtime_background_log_is_private_and_append_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            target.mkdir()
            log_directory = target / ".x86qw" / "logs"
            private_fs.ensure_private_directories(log_directory, stop=target)
            log = log_directory / "service-fixture.log"
            descriptor = private_fs.create_private_file(log)
            with os.fdopen(descriptor, "wb") as output:
                output.write(b"existing diagnostics\n")
            script = (
                "import sys\n"
                "from pathlib import Path\n"
                "from x86qw_runtime.supervisor import sessions\n"
                "sessions.activate_background_log("
                "Path(sys.argv[1]), '.x86qw/logs/service-fixture.log')\n"
                "print('new diagnostics', flush=True)\n"
            )

            result = subprocess.run(
                [sys.executable, "-c", script, str(target)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            private_fs.validate_private_file(log)
            self.assertEqual(
                b"existing diagnostics\nnew diagnostics" + os.linesep.encode(),
                log.read_bytes(),
            )

    def _assert_background_log_failure(
        self, target: Path, *, fail_at: int, replace_log: bool = False,
    ) -> None:
        script = """
import os
import sys
from pathlib import Path
from x86qw_runtime.io import private_fs
from x86qw_runtime.supervisor import sessions

target = Path(sys.argv[1])
fail_at = int(sys.argv[2])
replace_log = sys.argv[3] == 'replace'
log = target / '.x86qw/logs/service-fixture.log'
real_dup2 = os.dup2
calls = 0
def fail_redirection(source, destination, inheritable=True):
    global calls
    calls += 1
    if calls == fail_at:
        if replace_log:
            log.unlink()
            descriptor = private_fs.create_private_file(log)
            with os.fdopen(descriptor, 'wb') as output:
                output.write(b'concurrent replacement\\n')
        raise OSError(f'injected dup2 failure {fail_at}')
    return real_dup2(source, destination, inheritable=inheritable)

os.set_inheritable(1, False)
os.set_inheritable(2, True)
before = tuple(
    (os.fstat(fd).st_dev, os.fstat(fd).st_ino, os.get_inheritable(fd))
    for fd in (1, 2)
)
sessions.os.dup2 = fail_redirection
try:
    sessions.activate_background_log(
        target, '.x86qw/logs/service-fixture.log',
    )
except OSError:
    pass
else:
    raise AssertionError('dup2 failure was not propagated')
after = tuple(
    (os.fstat(fd).st_dev, os.fstat(fd).st_ino, os.get_inheritable(fd))
    for fd in (1, 2)
)
assert after == before, (before, after)
os.write(1, b'stdout-restored\\n')
os.write(2, b'stderr-restored\\n')
"""
        result = subprocess.run(
            [
                sys.executable, "-c", script, str(target), str(fail_at),
                "replace" if replace_log else "preserve",
            ],
            cwd=ROOT,
            capture_output=True,
            timeout=30,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(b"stdout-restored\n", result.stdout)
        self.assertEqual(b"stderr-restored\n", result.stderr)

    def test_background_log_first_dup2_failure_restores_fds_and_created_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            target.mkdir()
            metadata = target / ".x86qw"
            private_fs.ensure_private_directories(metadata, stop=target)

            self._assert_background_log_failure(target, fail_at=1)

            self.assertEqual([], list(metadata.iterdir()))

    def test_background_log_second_dup2_failure_restores_fds_and_preserves_existing_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            target.mkdir()
            log = target / ".x86qw/logs/service-fixture.log"
            private_fs.ensure_private_directories(log.parent, stop=target)
            descriptor = private_fs.create_private_file(log)
            with os.fdopen(descriptor, "wb") as output:
                output.write(b"existing diagnostics\n")

            self._assert_background_log_failure(target, fail_at=2)

            self.assertEqual(b"existing diagnostics\n", log.read_bytes())

    def test_background_log_failure_preserves_a_replacement_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            target.mkdir()
            private_fs.ensure_private_directories(target / ".x86qw", stop=target)
            log = target / ".x86qw/logs/service-fixture.log"

            self._assert_background_log_failure(
                target, fail_at=2, replace_log=True,
            )

            if os.name == "nt":
                # The append handle intentionally denies DELETE sharing.  The
                # attempted replacement is rejected and the unchanged log
                # created by this activation is then removed during rollback.
                self.assertFalse(log.exists())
                return
            self.assertEqual(b"concurrent replacement\n", log.read_bytes())

    def test_session_journal_fsync_failure_preserves_previous_bytes(self):
        """A journal update must use the same durable atomic boundary as state."""

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            journal = services.SessionJournal(target)
            previous = journal.path.read_bytes()
            journal.data["status"] = "running"

            with mock.patch.object(
                atomic_io.os,
                "fsync",
                side_effect=OSError("injected journal fsync failure"),
            ):
                with self.assertRaises(services.InstallerError):
                    journal._write()

            self.assertEqual(journal.path.read_bytes(), previous)
            self.assertEqual(list(journal.directory.glob(".session.json.*.tmp")), [])

    def test_invalid_journal_json_fails_before_private_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            (target / ".x86qw").mkdir()
            journal = services.SessionJournal(target)
            journal.data["not_json"] = object()
            with mock.patch.object(
                services.private_fs,
                "private_mkstemp",
                side_effect=AssertionError("staging must not begin"),
            ):
                with self.assertRaises(TypeError) as raised:
                    journal._write()
            self.assertIn("not JSON serializable", str(raised.exception))
            self.assertEqual(list(journal.directory.glob("*.tmp")), [])

    def test_sensitive_config_cleanup_never_masks_the_operational_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            journal = mock.Mock()
            journal.record_temporary.side_effect = services.InstallerError("journal-failed")
            output = io.StringIO()
            with mock.patch.object(
                services,
                "cleanup_sensitive_temporary",
                side_effect=services.InstallerError("cleanup-failed"),
            ), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                with self.assertRaisesRegex(services.InstallerError, "journal-failed"):
                    services.temporary_config(
                        directory, "secret-", ["rcon_password redigido"], journal,
                    )
            self.assertIn("cleanup-failed", output.getvalue())

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
            for field in (
                "runtime", "process_group", "executable", "creation_token",
                "address", "port", "parameters",
            ):
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
            with mock.patch.object(
                runtime_sessions.session_control,
                "probe_expected_process",
                return_value=mismatch,
            ):
                with mock.patch.object(runtime_sessions, "signal_recorded_process") as terminate:
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
                runtime_sessions.session_control,
                "process_identity",
                return_value=services.ProcessProbe("inconclusive"),
            ):
                with self.assertRaisesRegex(services.InstallerError, "Não foi possível confirmar"):
                    services.recover_sessions(target)
            self.assertTrue(data.exists())
            self.assertEqual("starting", json.loads(journal.path.read_text(encoding="utf-8"))["status"])

    @unittest.skipIf(os.name == "nt", "grupos de processos POSIX não existem no Windows")
    def test_stop_processes_retries_transient_group_probe_after_leader_exit(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            start_new_session=True,
        )
        process.wait(timeout=5)
        with mock.patch.object(
            supervisor_core,
            "posix_process_group_status",
            side_effect=("alive", "inconclusive", "dead"),
        ), mock.patch.object(supervisor_core.os, "killpg") as killpg:
            services.stop_processes([process])
        killpg.assert_called_once_with(process.pid, signal.SIGTERM)

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

    def test_windows_job_starts_process_suspended_then_assigns_and_resumes(self):
        events: list[str] = []
        process = SimpleNamespace(pid=4312, _handle=8765)
        job = object.__new__(services.WindowsJobObject)
        job.handle = 99
        job.assign = lambda candidate: events.append(f"assign:{candidate.pid}")
        job.resume = lambda candidate: events.append(f"resume:{candidate.pid}")

        def spawn(*arguments, **options):
            events.append("spawn")
            self.assertEqual((("fixture", "--flag"),), arguments)
            self.assertEqual(Path("C:/x86qw"), options["cwd"])
            self.assertIs(subprocess.DEVNULL, options["stdin"])
            flags = options["creationflags"]
            self.assertEqual(0x00000004, flags & 0x00000004)
            self.assertEqual(0x00000200, flags & 0x00000200)
            return process

        with mock.patch.object(supervisor_core.subprocess, "Popen", side_effect=spawn):
            started = job.start_process(("fixture", "--flag"), Path("C:/x86qw"))

        self.assertIs(process, started)
        self.assertEqual(["spawn", "assign:4312", "resume:4312"], events)

    def test_windows_job_rolls_back_suspended_leader_when_assignment_fails(self):
        events: list[tuple[str, int, int]] = []

        class Kernel:
            def AssignProcessToJobObject(self, job_handle, process_handle):
                events.append(("assign", job_handle, process_handle))
                return False

            def TerminateProcess(self, process_handle, exit_code):
                events.append(("terminate-process", process_handle, exit_code))
                return True

            def WaitForSingleObject(self, process_handle, timeout_ms):
                events.append(("wait-process", process_handle, timeout_ms))
                return 0

        process = SimpleNamespace(pid=4313, _handle=8766)
        job = object.__new__(services.WindowsJobObject)
        job.handle = 99
        job.kernel32 = Kernel()

        with mock.patch.object(
            supervisor_core.ctypes, "get_last_error", return_value=5, create=True,
        ), mock.patch.object(supervisor_core.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(services.InstallerError, "associar PID 4313"):
                job.start_process(("fixture",), Path("C:/x86qw"))

        self.assertEqual(("assign", 99, 8766), events[0])
        self.assertIn(("terminate-process", 8766, 1), events)
        self.assertIn(("wait-process", 8766, 4000), events)

    def test_windows_job_rolls_back_assigned_tree_when_resume_fails(self):
        events: list[tuple[str, int, int]] = []

        class Kernel:
            def AssignProcessToJobObject(self, job_handle, process_handle):
                events.append(("assign", job_handle, process_handle))
                return True

            def TerminateJobObject(self, job_handle, exit_code):
                events.append(("terminate-job", job_handle, exit_code))
                return True

            def WaitForSingleObject(self, process_handle, timeout_ms):
                events.append(("wait-process", process_handle, timeout_ms))
                return 0

        process = SimpleNamespace(pid=4314, _handle=8767)
        job = object.__new__(services.WindowsJobObject)
        job.handle = 99
        job.kernel32 = Kernel()
        job.resume = mock.Mock(side_effect=services.InstallerError("resume recusado"))

        with mock.patch.object(supervisor_core.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(services.InstallerError, "resume recusado"):
                job.start_process(("fixture",), Path("C:/x86qw"))

        self.assertEqual(("assign", 99, 8767), events[0])
        self.assertIn(("terminate-job", 99, 1), events)
        self.assertIn(("wait-process", 8767, 4000), events)

    def test_windows_job_reports_failed_rollback(self):
        warnings = []

        class Kernel:
            def AssignProcessToJobObject(self, _job_handle, _process_handle):
                return False

            def TerminateProcess(self, _process_handle, _exit_code):
                return False

        process = SimpleNamespace(pid=4315, _handle=8768)
        job = object.__new__(services.WindowsJobObject)
        job.handle = 99
        job.kernel32 = Kernel()
        job.reporter = SimpleNamespace(warning=warnings.append)

        with mock.patch.object(
            supervisor_core.ctypes, "get_last_error", return_value=5, create=True,
        ), mock.patch.object(supervisor_core.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(
                services.InstallerError, "reversão segura também falhou",
            ):
                job.start_process(("fixture",), Path("C:/x86qw"))

        self.assertEqual([
            "Falha ao reverter PID 4315 após startup recusado: "
            "Não foi possível reverter o PID suspenso 4315 (5).",
        ], warnings)

    def test_windows_job_close_terminates_tree_and_retains_failed_handle_for_retry(self):
        events: list[tuple[str, int, int] | tuple[str, int]] = []

        class Kernel:
            close_results = iter((False, True))

            def TerminateJobObject(self, job_handle, exit_code):
                events.append(("terminate-job", job_handle, exit_code))
                return True

            def CloseHandle(self, job_handle):
                events.append(("close", job_handle))
                return next(self.close_results)

        job = object.__new__(services.WindowsJobObject)
        job.handle = 99
        job.kernel32 = Kernel()

        with mock.patch.object(
            supervisor_core.ctypes, "get_last_error", return_value=6, create=True,
        ):
            with self.assertRaisesRegex(services.InstallerError, "fechar o Job Object"):
                job.close()
            self.assertEqual(99, job.handle)
            job.close()

        self.assertIsNone(job.handle)
        self.assertEqual([
            ("terminate-job", 99, 1), ("close", 99),
            ("terminate-job", 99, 1), ("close", 99),
        ], events)

    def test_windows_job_close_reports_termination_failure_after_handle_closes(self):
        events: list[tuple[str, int, int] | tuple[str, int]] = []

        class Kernel:
            def TerminateJobObject(self, job_handle, exit_code):
                events.append(("terminate-job", job_handle, exit_code))
                return False

            def CloseHandle(self, job_handle):
                events.append(("close", job_handle))
                return True

        job = object.__new__(services.WindowsJobObject)
        job.handle = 99
        job.kernel32 = Kernel()

        with mock.patch.object(
            supervisor_core.ctypes, "get_last_error", return_value=5, create=True,
        ), self.assertRaisesRegex(
            services.InstallerError, "encerramento explícito da árvore falhou",
        ):
            job.close()

        self.assertIsNone(job.handle)
        self.assertEqual([
            ("terminate-job", 99, 1), ("close", 99),
        ], events)

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
            original_assign = job.assign

            def assign_before_user_code(process):
                self.assertFalse(
                    child_pid_path.exists(),
                    "o processo executou código antes de entrar no Job Object",
                )
                original_assign(process)

            job.assign = assign_before_user_code
            leader = job.start_process(
                (sys.executable, "-c", script, str(child_pid_path)),
                Path.cwd(),
            )
            try:
                deadline = time.monotonic() + 5
                while not child_pid_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(child_pid_path.exists())
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))
                job.close()
                leader.wait(timeout=5)
                child_deadline = time.monotonic() + 5
                probe = services.process_identity(child_pid)
                while probe.status != "dead" and time.monotonic() < child_deadline:
                    time.sleep(0.02)
                    probe = services.process_identity(child_pid)
                self.assertEqual("dead", probe.status)
            finally:
                job.close()
                if leader.poll() is None:
                    leader.kill()
                    leader.wait()

    @unittest.skipUnless(os.name == "nt", "startup suspenso é exercitado no runner Windows")
    def test_windows_rejected_job_assignment_never_executes_child_code(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "executed.txt"
            job = services.WindowsJobObject()
            spawned: list[subprocess.Popen[bytes]] = []
            original_popen = supervisor_core.subprocess.Popen

            def capture(*arguments, **options):
                process = original_popen(*arguments, **options)
                spawned.append(process)
                return process

            def reject(_process):
                raise services.InstallerError("associação recusada para teste")

            job.assign = reject
            try:
                with mock.patch.object(
                    supervisor_core.subprocess, "Popen", side_effect=capture,
                ):
                    with self.assertRaisesRegex(
                        services.InstallerError, "associação recusada",
                    ):
                        job.start_process(
                            (
                                sys.executable, "-c",
                                "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text('ran')",
                                str(marker),
                            ),
                            Path.cwd(),
                        )
                self.assertEqual(1, len(spawned))
                spawned[0].wait(timeout=5)
                self.assertIsNotNone(spawned[0].returncode)
                self.assertFalse(marker.exists())
            finally:
                job.close()
                for process in spawned:
                    if process.poll() is None:
                        process.kill()
                        process.wait()

    @unittest.skipUnless(os.name == "nt", "assinaturas Win32 são exercitadas no runner Windows")
    def test_windows_api_signatures_are_explicit(self):
        groups = (
            (services.session_control._windows_kernel32(), (
                "OpenProcess", "GetProcessTimes", "QueryFullProcessImageNameW",
                "TerminateProcess", "CloseHandle",
            )),
            (managed_files._get_windows_file_api().kernel32, (
                "CreateFileW", "GetFileInformationByHandleEx",
                "SetFileInformationByHandle", "MoveFileExW", "ReadFile",
                "WriteFile", "SetFilePointerEx", "GetFileSizeEx",
                "FlushFileBuffers", "CloseHandle",
            )),
            (supervisor_core._windows_job_kernel32(), (
                "CreateJobObjectW", "SetInformationJobObject",
                "AssignProcessToJobObject", "CreateToolhelp32Snapshot",
                "Thread32First", "Thread32Next", "OpenThread", "ResumeThread",
                "TerminateProcess", "TerminateJobObject", "WaitForSingleObject",
                "CloseHandle",
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
                if failing_step == "session":
                    journal.set_status.assert_any_call("interrupted")

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
            self.assertTrue(journal.path.is_file())
            self.assertFalse(journal.path.is_symlink())
            if os.name != "nt":
                self.assertEqual(0o700, journal.directory.stat().st_mode & 0o777)
                self.assertEqual(0o600, journal.path.stat().st_mode & 0o777)

    def test_temporary_config_preserves_raw_quake_name_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            raw_name = b'set k_fb_name_0 "/\xa0\xd9\xe1\xed\xe1\xf4\xef"'
            config = services.temporary_config(directory, "host-", [raw_name])
            try:
                self.assertIn(raw_name + b"\n", config.read_bytes())
            finally:
                services.unlink_sensitive_temporary(config)

    def test_partial_startup_failure_stops_server_and_dependents(self):
        processes = [mock.Mock(pid=101), mock.Mock(pid=102)]
        for process in processes:
            process.poll.return_value = None
        spawn = mock.Mock(side_effect=processes)
        specs = [
            services.ProcessSpec("MVDSV", ("mvdsv",), Path.cwd(), services.StartupRcon("127.0.0.1", 28501, "secret", "post.cfg", "dm6", "ktx")),
            services.ProcessSpec("QTV", ("qtv",), Path.cwd(), readiness=services.ServiceReadiness("http", "127.0.0.1", 28000)),
        ]

        def fail_http(_process, _readiness):
            raise services.InstallerError("QTV falhou")

        with self.assertRaisesRegex(services.InstallerError, "QTV falhou"):
            services.run_processes(
                specs,
                reporter=mock.Mock(),
                process_factory=spawn,
                signal_setter=lambda _signum, _handler: signal.SIG_DFL,
                os_name="posix",
                apply_rcon=lambda _startup: None,
                http_readiness=fail_http,
            )
        for process in processes:
            process.terminate.assert_called_once()
        for call in spawn.call_args_list:
            self.assertIs(call.kwargs["stdin"], subprocess.DEVNULL)

    def test_mvdsv_readiness_checks_map_gamecode_and_applies_post_map(self):
        responses = [
            b"\xff\xff\xff\xffprint\n\\map\\dm6\\*gamedir\\qw",
            b"\xff\xff\xff\xffprint\n*game qw",
            b"\xff\xff\xff\xffprint\nexecing post.cfg",
        ]
        connection = mock.MagicMock()
        connection.__enter__.return_value = connection
        connection.recvfrom.side_effect = [(response, ("127.0.0.1", 28501)) for response in responses]
        sleep = mock.Mock()
        ticks = iter((1.0, 1.0))
        services.apply_startup_rcon(
            services.StartupRcon(
                "127.0.0.1", 28501, "bootstrap", "post.cfg", "dm6", "qw",
            ),
            socket_factory=lambda *_args: connection,
            monotonic=lambda: next(ticks),
            sleep=sleep,
        )
        sent = b"\n".join(call.args[0] for call in connection.sendto.call_args_list)
        self.assertIn(b"status", sent)
        self.assertIn(b"serverinfo", sent)
        self.assertIn(b"exec post.cfg", sent)
        sleep.assert_called_once_with(1.05)

    @unittest.skipIf(os.name == "nt", "SIGTERM POSIX validado nos runners Unix; Windows usa terminate")
    def test_sigterm_stops_child_without_orphan(self):
        child_pid: list[int] = []
        original_popen = supervisor_core.subprocess.Popen

        def capture(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            child_pid.append(process.pid)
            return process

        timer = threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGTERM))
        timer.start()
        try:
            with mock.patch.object(supervisor_core.subprocess, "Popen", side_effect=capture):
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
