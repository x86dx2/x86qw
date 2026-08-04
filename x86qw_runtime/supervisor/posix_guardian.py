"""POSIX launch gate whose stable leader owns one service process group."""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import time
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from x86qw_runtime.platform.host import (
    BoundLaunchTarget,
    LaunchPathIdentity,
    LaunchTarget,
    bound_launch_target,
)


_MAX_REQUEST_BYTES = 1024 * 1024
_MAX_ACK_BYTES = 64 * 1024
_RELEASE_BYTE = b"R"
_CHILD_BOOTSTRAP = (
    "import os,sys;"
    "from x86qw_runtime.supervisor.posix_guardian import _main;"
    "os._exit(_main(sys.argv[1:]) & 0xff)"
)


class GuardianError(RuntimeError):
    """A guardian lifecycle operation failed."""


class GuardianLaunchError(GuardianError):
    """The gated target could not be executed."""


class GuardianTimeoutError(GuardianError):
    """The guardian did not acknowledge the launch before its deadline."""


@dataclass(frozen=True)
class GuardianAck:
    pid: int
    process_group: int


def _close_fd(descriptor: int | None) -> None:
    if descriptor is None:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


def _write_all(descriptor: int, payload: bytes) -> None:
    pending = memoryview(payload)
    while pending:
        written = os.write(descriptor, pending)
        if written <= 0:
            raise OSError("guardian pipe write returned no progress")
        pending = pending[written:]


def _read_all(descriptor: int, maximum: int) -> bytes:
    payload = bytearray()
    while True:
        block = os.read(descriptor, min(65536, maximum - len(payload) + 1))
        if not block:
            return bytes(payload)
        payload.extend(block)
        if len(payload) > maximum:
            raise GuardianError("guardian control payload exceeded its bound")


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError as error:
        raise GuardianError(
            f"guardian process group {process_group} could not be inspected"
        ) from error
    return True


def _signal_process_group(process_group: int, requested_signal: int) -> bool:
    try:
        os.killpg(process_group, requested_signal)
    except ProcessLookupError:
        return False
    except PermissionError as error:
        raise GuardianError(
            f"guardian process group {process_group} could not be signalled"
        ) from error
    return True


def _wait_for_process_group_exit(
    process_group: int,
    process: subprocess.Popen[bytes],
    *,
    deadline: float,
) -> bool:
    inconclusive: GuardianError | None = None
    while True:
        process.poll()
        try:
            if not _process_group_exists(process_group):
                return True
            inconclusive = None
        except GuardianError as error:
            inconclusive = error
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if inconclusive is not None:
                raise inconclusive
            return False
        time.sleep(min(0.02, remaining))


def _inspect_process_group_until_conclusive(
    process_group: int,
    process: subprocess.Popen[bytes],
    *,
    deadline: float,
) -> bool:
    while True:
        process.poll()
        try:
            return _process_group_exists(process_group)
        except GuardianError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(0.02, remaining))


def _serialize_launch_target(target: LaunchTarget | None) -> dict[str, object] | None:
    if target is None:
        return None
    if not isinstance(target, LaunchTarget) or not target.paths:
        raise ValueError("guardian launch_target must be a non-empty LaunchTarget")
    return {
        "executable": os.fspath(target.executable),
        "expected_sha256": target.expected_sha256,
        "paths": [
            {
                "path": os.fspath(item.path),
                "directory": item.directory,
                "identity": list(item.identity),
                "size": item.size,
                "mtime_ns": item.mtime_ns,
            }
            for item in target.paths
        ],
    }


def _deserialize_launch_target(payload: object) -> LaunchTarget | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValueError("invalid guardian launch target")
    executable = payload.get("executable")
    expected_sha256 = payload.get("expected_sha256")
    paths = payload.get("paths")
    if (
        not isinstance(executable, str)
        or not executable
        or not (
            expected_sha256 is None
            or isinstance(expected_sha256, str)
            and len(expected_sha256) == 64
            and all(character in "0123456789abcdef" for character in expected_sha256)
        )
        or not isinstance(paths, list)
        or not paths
    ):
        raise ValueError("invalid guardian launch target")
    identities: list[LaunchPathIdentity] = []
    for raw in paths:
        if not isinstance(raw, dict):
            raise ValueError("invalid guardian launch target path")
        path = raw.get("path")
        directory = raw.get("directory")
        identity = raw.get("identity")
        size = raw.get("size")
        mtime_ns = raw.get("mtime_ns")
        if (
            not isinstance(path, str)
            or not path
            or type(directory) is not bool
            or not isinstance(identity, list)
            or len(identity) != 2
            or not all(type(value) is int and value >= 0 for value in identity)
            or type(size) is not int
            or size < 0
            or type(mtime_ns) is not int
        ):
            raise ValueError("invalid guardian launch target path")
        identities.append(LaunchPathIdentity(
            Path(path),
            directory,
            (identity[0], identity[1]),
            size,
            mtime_ns,
        ))
    if identities[-1].directory or identities[-1].path != Path(executable):
        raise ValueError("invalid guardian launch target executable")
    return LaunchTarget(Path(executable), tuple(identities), expected_sha256)


def _import_root() -> str:
    relative = Path("x86qw_runtime/supervisor/posix_guardian.py")
    for raw in sys.path:
        candidate = Path(raw or os.curdir)
        if candidate.suffix == ".pyz" or (candidate / relative).is_file():
            return os.fspath(candidate.resolve())
    return os.fspath(Path(__file__).resolve().parents[2])


def _guardian_environment() -> dict[str, str]:
    environment = dict(os.environ)
    import_root = _import_root()
    current = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        import_root if not current else import_root + os.pathsep + current
    )
    return environment


class PosixGuardian:
    """Controller-side handle for a gated POSIX process-group leader."""

    def __init__(
        self,
        process: subprocess.Popen[bytes],
        gate_write: int,
        ack_read: int,
    ) -> None:
        self.process = process
        self._gate_write: int | None = gate_write
        self._ack_read: int | None = ack_read
        self._released = False

    @property
    def pid(self) -> int:
        return self.process.pid

    @property
    def process_group(self) -> int:
        return self.process.pid

    def _close_gate(self) -> None:
        descriptor, self._gate_write = self._gate_write, None
        _close_fd(descriptor)

    def _close_ack(self) -> None:
        descriptor, self._ack_read = self._ack_read, None
        _close_fd(descriptor)

    def release(self, *, timeout: float) -> GuardianAck:
        if self._released or self._gate_write is None:
            raise GuardianError("guardian launch gate is already closed")
        if timeout <= 0:
            raise ValueError("guardian release timeout must be positive")
        self._released = True
        try:
            try:
                _write_all(self._gate_write, _RELEASE_BYTE)
            finally:
                self._close_gate()
            descriptor = self._ack_read
            if descriptor is None:
                raise GuardianError("guardian acknowledgement channel is closed")
            selector = selectors.DefaultSelector()
            try:
                selector.register(descriptor, selectors.EVENT_READ)
                if not selector.select(timeout):
                    raise GuardianTimeoutError(
                        "guardian launch acknowledgement timed out"
                    )
                payload = _read_all(descriptor, _MAX_ACK_BYTES)
            finally:
                selector.close()
                self._close_ack()
            try:
                message = json.loads(payload.decode("utf-8"))
            except (UnicodeError, ValueError, json.JSONDecodeError) as error:
                raise GuardianLaunchError(
                    "guardian returned an invalid acknowledgement"
                ) from error
            if (
                not isinstance(message, dict)
                or message.get("status") not in {"started", "error"}
            ):
                raise GuardianLaunchError("guardian returned an invalid acknowledgement")
            if message["status"] == "error":
                detail = message.get("message")
                raise GuardianLaunchError(
                    str(detail)
                    if isinstance(detail, str)
                    else "guardian target exec failed"
                )
            pid = message.get("pid")
            process_group = message.get("process_group")
            if type(pid) is not int or pid <= 1 or process_group != self.pid:
                raise GuardianLaunchError("guardian returned an invalid target identity")
            return GuardianAck(pid, int(process_group))
        except BaseException as error:
            try:
                self.abort(timeout=min(timeout, 1.0))
            except BaseException as abort_error:
                raise error from abort_error
            raise

    def cancel(self, *, timeout: float) -> int:
        if self._released:
            raise GuardianError("guardian launch gate was already released")
        self._close_gate()
        self._close_ack()
        try:
            return self.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return self.abort(timeout=timeout)

    def wait(self, *, timeout: float | None = None) -> int:
        return self.process.wait(timeout=timeout)

    def abort(self, *, timeout: float) -> int:
        if timeout <= 0:
            raise ValueError("guardian abort timeout must be positive")
        self._close_gate()
        self._close_ack()
        process_group = self.process_group
        term_deadline = time.monotonic() + timeout
        if _inspect_process_group_until_conclusive(
            process_group,
            self.process,
            deadline=term_deadline,
        ):
            _signal_process_group(process_group, signal.SIGTERM)
        if not _wait_for_process_group_exit(
            process_group,
            self.process,
            deadline=term_deadline,
        ):
            _signal_process_group(process_group, signal.SIGKILL)
            kill_deadline = time.monotonic() + timeout
            if not _wait_for_process_group_exit(
                process_group,
                self.process,
                deadline=kill_deadline,
            ):
                raise GuardianTimeoutError(
                    f"guardian process group {process_group} survived SIGKILL"
                )
        remaining = max(0.001, term_deadline - time.monotonic())
        try:
            return self.process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as error:
            raise GuardianTimeoutError(
                f"guardian leader {self.pid} was not reaped"
            ) from error

    def close(self) -> None:
        self._close_gate()
        self._close_ack()


def spawn_guardian(
    arguments: Sequence[str | os.PathLike[str]],
    *,
    executable: str | os.PathLike[str] | None = None,
    launch_target: LaunchTarget | None = None,
    cwd: Path | str,
    env: Mapping[str, str] | None = None,
    pass_fds: Sequence[int] = (),
    quiet: bool = False,
) -> PosixGuardian:
    """Spawn a session leader that cannot execute ``arguments`` before release."""

    if os.name == "nt":
        raise GuardianError("POSIX guardian is unavailable on Windows")
    if type(quiet) is not bool:
        raise ValueError("guardian quiet must be a boolean")
    normalized_arguments = tuple(os.fspath(argument) for argument in arguments)
    if not normalized_arguments or any(not argument for argument in normalized_arguments):
        raise ValueError("guardian target arguments must not be empty")
    normalized_executable = None if executable is None else os.fspath(executable)
    if normalized_executable == "":
        raise ValueError("guardian target executable must not be empty")
    serialized_launch_target = _serialize_launch_target(launch_target)
    if launch_target is not None:
        if executable is not None:
            raise ValueError("guardian executable and launch_target are mutually exclusive")
        if normalized_arguments[0] != os.fspath(launch_target.executable):
            raise ValueError("guardian arguments diverge from launch_target")
    normalized_fds = tuple(int(descriptor) for descriptor in pass_fds)
    if len(set(normalized_fds)) != len(normalized_fds) or any(
        descriptor < 0 for descriptor in normalized_fds
    ):
        raise ValueError("guardian pass_fds must contain unique open descriptors")
    target_environment = dict(os.environ if env is None else env)
    request = json.dumps({
        "format": 1,
        "arguments": normalized_arguments,
        "executable": normalized_executable,
        "launch_target": serialized_launch_target,
        "cwd": os.fspath(cwd),
        "environment": target_environment,
        "pass_fds": normalized_fds,
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(request) > _MAX_REQUEST_BYTES:
        raise ValueError("guardian launch request exceeds its bound")

    gate_read, gate_write = os.pipe()
    ack_read, ack_write = os.pipe()
    request_read, request_write = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    try:
        inherited = tuple(sorted({gate_read, ack_write, request_read, *normalized_fds}))
        process = subprocess.Popen(
            (
                sys.executable,
                "-c",
                _CHILD_BOOTSTRAP,
                "--child",
                str(gate_read),
                str(ack_write),
                str(request_read),
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL if quiet else None,
            stderr=subprocess.DEVNULL if quiet else None,
            close_fds=True,
            pass_fds=inherited,
            start_new_session=True,
            env=_guardian_environment(),
        )
        _close_fd(gate_read)
        gate_read = -1
        _close_fd(ack_write)
        ack_write = -1
        _close_fd(request_read)
        request_read = -1
        _write_all(request_write, request)
        _close_fd(request_write)
        request_write = -1
        return PosixGuardian(process, gate_write, ack_read)
    except BaseException:
        for descriptor in (
            gate_read, gate_write, ack_read, ack_write, request_read, request_write,
        ):
            if descriptor >= 0:
                _close_fd(descriptor)
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        raise


def _write_ack(descriptor: int, message: dict[str, object]) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _write_all(descriptor, payload)


def _child_main(gate: int, ack: int, request: int) -> int:
    target: subprocess.Popen[bytes] | None = None
    target_fds: tuple[int, ...] = ()
    try:
        payload = _read_all(request, _MAX_REQUEST_BYTES)
        try:
            launch = json.loads(payload.decode("utf-8"))
        except (UnicodeError, ValueError, json.JSONDecodeError):
            return 126
        if (
            not isinstance(launch, dict)
            or launch.get("format") != 1
            or not isinstance(launch.get("arguments"), list)
            or not launch["arguments"]
            or not all(isinstance(item, str) and item for item in launch["arguments"])
            or not (
                launch.get("executable") is None
                or (
                    isinstance(launch.get("executable"), str)
                    and bool(launch["executable"])
                )
            )
            or not isinstance(launch.get("cwd"), str)
            or not isinstance(launch.get("environment"), dict)
            or not all(
                isinstance(name, str) and isinstance(value, str)
                for name, value in launch["environment"].items()
            )
            or not isinstance(launch.get("pass_fds"), list)
            or not all(type(item) is int and item >= 0 for item in launch["pass_fds"])
        ):
            return 126
        try:
            launch_target = _deserialize_launch_target(launch.get("launch_target"))
        except ValueError:
            return 126
        if launch_target is not None and launch.get("executable") is not None:
            return 126
        target_fds = tuple(launch["pass_fds"])
        if os.read(gate, 1) != _RELEASE_BYTE:
            return 0
        try:
            lease = (
                bound_launch_target(launch_target)
                if launch_target is not None
                else nullcontext(BoundLaunchTarget(
                    launch["executable"] or launch["arguments"][0]
                ))
            )
            with lease as bound:
                inherited_fds = tuple(sorted({*target_fds, *bound.pass_fds}))
                target = subprocess.Popen(
                    tuple(launch["arguments"]),
                    executable=bound.executable,
                    cwd=launch["cwd"],
                    env=launch["environment"],
                    close_fds=True,
                    pass_fds=inherited_fds,
                    start_new_session=False,
                )
                _write_ack(ack, {
                    "status": "started",
                    "pid": target.pid,
                    "process_group": os.getpgrp(),
                })
                _close_fd(ack)
                ack = -1
                for descriptor in target_fds:
                    _close_fd(descriptor)
                target_fds = ()
                returncode = target.wait()
        except Exception as error:
            _write_ack(ack, {
                "status": "error",
                "message": f"{launch['arguments'][0]}: {error}",
            })
            return 127
        return returncode if returncode >= 0 else 128 + abs(returncode)
    finally:
        for descriptor in (gate, ack, request, *target_fds):
            if descriptor >= 0:
                _close_fd(descriptor)


def _main(arguments: Sequence[str]) -> int:
    if len(arguments) != 4 or arguments[0] != "--child":
        return 2
    try:
        descriptors = tuple(int(value) for value in arguments[1:])
    except ValueError:
        return 2
    return _child_main(*descriptors)


if __name__ == "__main__":
    os._exit(_main(sys.argv[1:]) & 0xFF)


__all__ = (
    "GuardianAck",
    "GuardianError",
    "GuardianLaunchError",
    "GuardianTimeoutError",
    "PosixGuardian",
    "spawn_guardian",
)
