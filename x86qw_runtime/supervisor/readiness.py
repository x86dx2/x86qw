"""Deterministic readiness probes for supervised x86QW services."""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Callable
from typing import Protocol

from x86qw_runtime.errors import InstallerError

from .models import ServiceReadiness, StartupRcon


class PollableProcess(Protocol):
    def poll(self) -> int | None: ...


def _endpoint(address: str, port: int) -> str:
    return f"[{address}]:{port}" if ":" in address else f"{address}:{port}"


def preflight_ports(
    requests: list[tuple[str, str, int, str]],
    *,
    socket_factory: Callable[..., object] = socket.socket,
    os_name: str | None = None,
) -> None:
    seen: dict[int, str] = {}
    active_os_name = os.name if os_name is None else os_name
    for label, address, port, kind in requests:
        if port in seen:
            raise InstallerError(
                f"Porta local duplicada: {port} foi solicitada por {seen[port]} e {label}."
            )
        seen[port] = label
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        socket_type = socket.SOCK_STREAM if kind == "tcp" else socket.SOCK_DGRAM
        try:
            with socket_factory(family, socket_type) as listener:  # type: ignore[attr-defined]
                if active_os_name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                listener.bind((address, port))
        except OSError as error:
            raise InstallerError(
                f"A porta {_endpoint(address, port)} de {label} não está disponível."
            ) from error


def qtv_http_response_ready(response: bytes, upstream: str | None) -> bool:
    status_line, _, _ = response.partition(b"\r\n")
    fields = status_line.split()
    if (
        len(fields) < 2
        or not fields[0].startswith(b"HTTP/")
        or not fields[1].isdigit()
        or not 200 <= int(fields[1]) < 300
    ):
        return False
    if upstream is None:
        return True
    _, separator, body = response.partition(b"\r\n\r\n")
    if not separator:
        return False
    return upstream.casefold().encode("utf-8") in body.lower()


def wait_http_readiness(
    process: PollableProcess,
    readiness: ServiceReadiness,
    timeout: float = 8.0,
    *,
    connection_factory: Callable[..., object] = socket.create_connection,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    deadline = monotonic() + timeout
    last_response = b""
    while monotonic() < deadline:
        if process.poll() is not None:
            raise InstallerError("QTV encerrou antes de ficar pronto.")
        try:
            with connection_factory(
                (readiness.address, readiness.port), timeout=0.4,
            ) as connection:  # type: ignore[attr-defined]
                connection.sendall(b"GET /nowplaying/ HTTP/1.0\r\nHost: x86qw.local\r\n\r\n")
                chunks: list[bytes] = []
                while sum(map(len, chunks)) < 1024 * 1024:
                    block = connection.recv(65535)
                    if not block:
                        break
                    chunks.append(block)
                last_response = b"".join(chunks)
                if qtv_http_response_ready(last_response, readiness.upstream):
                    return
        except OSError:
            pass
        sleep(0.1)
    if readiness.upstream is not None and qtv_http_response_ready(last_response, None):
        raise InstallerError("QTV respondeu por HTTP, mas não registrou o upstream solicitado.")
    raise InstallerError(
        f"QTV não respondeu em http://{_endpoint(readiness.address, readiness.port)}/."
    )


def wait_udp_readiness(
    process: PollableProcess,
    readiness: ServiceReadiness,
    timeout: float = 1.0,
    *,
    socket_factory: Callable[..., object] = socket.socket,
    os_name: str | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if process.poll() is not None:
            raise InstallerError("QWFWD encerrou durante a inicialização.")
        sleep(0.05)
    family = socket.AF_INET6 if ":" in readiness.address else socket.AF_INET
    active_os_name = os.name if os_name is None else os_name
    try:
        with socket_factory(family, socket.SOCK_DGRAM) as probe:  # type: ignore[attr-defined]
            if active_os_name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind((readiness.address, readiness.port))
    except OSError:
        return
    raise InstallerError("QWFWD permaneceu vivo, mas não ocupou a porta UDP solicitada.")


def apply_startup_rcon(
    startup: StartupRcon,
    timeout: float = 8.0,
    *,
    socket_factory: Callable[..., object] = socket.socket,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    family = socket.AF_INET6 if ":" in startup.address else socket.AF_INET
    destination = (startup.address, startup.port)
    deadline = monotonic() + timeout
    with socket_factory(family, socket.SOCK_DGRAM) as connection:  # type: ignore[attr-defined]
        connection.settimeout(0.25)
        while monotonic() < deadline:
            connection.sendto(b"\xff\xff\xff\xffstatus\n", destination)
            try:
                response, _ = connection.recvfrom(65535)
            except TimeoutError:
                continue
            if response.startswith(b"\xff\xff\xff\xff"):
                break
        else:
            raise InstallerError(
                f"MVDSV não respondeu em {_endpoint(startup.address, startup.port)}."
            )

        decoded_status = response.decode("latin-1", errors="replace").casefold()
        if startup.expected_map.casefold() not in decoded_status:
            raise InstallerError(
                f"MVDSV respondeu, mas não confirmou o mapa {startup.expected_map}."
            )
        serverinfo_command = f"rcon {startup.password} serverinfo\n".encode("ascii")
        connection.sendto(b"\xff\xff\xff\xff" + serverinfo_command, destination)
        try:
            serverinfo, _ = connection.recvfrom(65535)
        except TimeoutError as error:
            raise InstallerError("MVDSV não confirmou o gamecode carregado.") from error
        if b"Bad rcon_password" in serverinfo:
            raise InstallerError("MVDSV rejeitou o preflight RCON local.")
        combined = (response + b"\n" + serverinfo).decode(
            "latin-1", errors="replace",
        ).casefold()
        if startup.expected_gamedir.casefold() not in combined:
            raise InstallerError(
                f"MVDSV não confirmou o gamecode {startup.expected_gamedir}."
            )

        # KTX's sv_rconlim counts both password checks performed for each packet.
        sleep(1.05)

        command = f"rcon {startup.password} exec {startup.config_name}\n".encode("ascii")
        connection.settimeout(2.0)
        connection.sendto(b"\xff\xff\xff\xff" + command, destination)
        try:
            response, _ = connection.recvfrom(65535)
        except TimeoutError as error:
            raise InstallerError("MVDSV não confirmou a configuração dedicada.") from error
        if b"Bad rcon_password" in response:
            raise InstallerError("MVDSV rejeitou a configuração dedicada por RCON local.")


__all__ = (
    "apply_startup_rcon",
    "preflight_ports",
    "qtv_http_response_ready",
    "wait_http_readiness",
    "wait_udp_readiness",
)
