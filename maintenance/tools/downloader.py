"""Bounded, HTTPS-only download boundary shared by runtime and maintenance."""

from __future__ import annotations

import email.utils
import errno
import hashlib
import http.client
import io
import json
import math
import os
import random
import re
import selectors
import socket
import ssl
import string
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from x86qw_runtime.io import private_fs


CHUNK_SIZE = 1024 * 1024
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
TRANSIENT_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
TRANSIENT_ERRNOS = frozenset({
    errno.ECONNABORTED,
    errno.ECONNREFUSED,
    errno.ECONNRESET,
    errno.EHOSTUNREACH,
    errno.EINTR,
    errno.ENETDOWN,
    errno.ENETRESET,
    errno.ENETUNREACH,
    errno.ETIMEDOUT,
})
HEX64 = frozenset("0123456789abcdef")
ASCII_DECIMAL = re.compile(r"^[0-9]+$")
HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
SAFE_CROSS_ORIGIN_HEADERS = frozenset({"accept", "accept-encoding", "user-agent"})
DNS_MAX_CANDIDATES = 64
DNS_MAX_OUTPUT_BYTES = 64 * 1024
DNS_RESOLVER_SCRIPT = r"""
import json
import socket
import sys

if sys.stdin.buffer.read(1) != b"G":
    raise SystemExit(3)
host = sys.argv[1]
port = int(sys.argv[2])
try:
    discovered = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
except BaseException:
    raise SystemExit(2)
result = []
seen = set()
for family, socktype, proto, canonname, address in discovered:
    if family not in (socket.AF_INET, socket.AF_INET6) or socktype != socket.SOCK_STREAM:
        continue
    normalized = [str(address[0]), int(address[1])]
    if family == socket.AF_INET6:
        normalized.extend([int(address[2]), int(address[3])])
    key = (int(family), int(socktype), int(proto), tuple(normalized))
    if key in seen:
        continue
    seen.add(key)
    result.append([int(family), int(socktype), int(proto), str(canonname), normalized])
    if len(result) >= 64:
        break
sys.stdout.write(json.dumps(result, separators=(",", ":")))
"""
OPEN_CLEANUP_RESERVE_SECONDS = 0.5
MIN_OPEN_BUDGET_SECONDS = 0.5


class DownloadError(Exception):
    """Base error for the remote-byte boundary."""


class DownloadPolicyError(DownloadError):
    """The caller supplied an unsafe or incomplete contract."""


class DownloadRedirectError(DownloadError):
    """A redirect attempted to leave HTTPS."""


class DownloadProtocolError(DownloadError):
    """The peer returned malformed or contradictory metadata."""


class DownloadLimitError(DownloadError):
    """The response exceeded the declared byte boundary."""


class DownloadIntegrityError(DownloadError):
    """The response did not match its pinned size or digest."""


class DownloadDeadlineError(DownloadError):
    """The total monotonic deadline expired."""


class DownloadStorageError(DownloadError):
    """The local destination could not be written atomically."""


class DownloadTransportError(DownloadError):
    """A non-retryable remote transport failure occurred."""


class DownloadRetryBudgetError(DownloadTransportError):
    """The current mirror requested a retry that cannot fit in the budget."""


class DownloadHTTPError(DownloadTransportError):
    """A non-retryable HTTP response."""

    def __init__(self, status: int, message: str, headers: Mapping[str, str]) -> None:
        super().__init__(message)
        self.status = status
        self.headers = headers


class DownloadTransientError(DownloadTransportError):
    """A remote failure eligible for the configured retry policy."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    initial_backoff: float = 0.5
    maximum_backoff: float = 8.0
    jitter: float = 0.2
    transient_statuses: frozenset[int] = TRANSIENT_HTTP_STATUSES

    def __post_init__(self) -> None:
        if type(self.attempts) is not int or self.attempts < 1:
            raise DownloadPolicyError("A política de retry exige ao menos uma tentativa.")
        if (
            isinstance(self.initial_backoff, bool)
            or not isinstance(self.initial_backoff, (int, float))
            or not math.isfinite(self.initial_backoff)
            or isinstance(self.maximum_backoff, bool)
            or not isinstance(self.maximum_backoff, (int, float))
            or not math.isfinite(self.maximum_backoff)
            or self.initial_backoff < 0
            or self.maximum_backoff < 0
        ):
            raise DownloadPolicyError(
                "Os intervalos de retry devem ser números finitos não negativos."
            )
        if self.initial_backoff > self.maximum_backoff:
            raise DownloadPolicyError("O backoff inicial não pode exceder o máximo.")
        if (
            isinstance(self.jitter, bool)
            or not isinstance(self.jitter, (int, float))
            or not math.isfinite(self.jitter)
            or not 0 <= self.jitter <= 1
        ):
            raise DownloadPolicyError("O jitter de retry deve estar entre zero e um.")
        if (
            not isinstance(self.transient_statuses, frozenset)
            or not all(type(status) is int for status in self.transient_statuses)
            or not self.transient_statuses <= TRANSIENT_HTTP_STATUSES
        ):
            raise DownloadPolicyError("A política contém um status HTTP não transitório.")


@dataclass(frozen=True)
class PinnedArtifact:
    """Persistent artifact whose exact size and SHA-256 are known in advance."""

    url: str
    destination: Path
    expected_size: int
    expected_sha256: str
    maximum_size: int
    deadline_seconds: float
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    headers: Mapping[str, str] = field(default_factory=dict)
    label: str = "artefato"


@dataclass(frozen=True)
class BoundedMetadata:
    """Ephemeral dynamic response limited by bytes and a total deadline.

    This contract intentionally does not promote the response to a trusted
    artifact. TLS and the byte/deadline limits protect transport and resource
    usage; metadata authentication is a separate product contract.
    """

    url: str
    maximum_size: int
    deadline_seconds: float
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    headers: Mapping[str, str] = field(default_factory=dict)
    label: str = "metadados"
    method: str = "GET"


DownloadContract = PinnedArtifact | BoundedMetadata
ProgressCallback = Callable[[int, int | None, bool], None]
RetryCallback = Callable[[int, DownloadTransientError, float], None]
OpenCallback = Callable[[urllib.request.Request, float], object]
MirrorFailureCallback = Callable[[int, DownloadContract, DownloadError], None]


@dataclass(frozen=True)
class DownloadResult:
    url: str
    size: int
    sha256: str
    attempts: int
    headers: Mapping[str, str]
    data: bytes | None = None
    path: Path | None = None


class HTTPSOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject a redirect before urllib can issue a downgraded request."""

    def http_error_302(self, request, fp, code, message, headers):  # type: ignore[no-untyped-def]
        """Follow an HTTPS redirect without draining its untrusted body.

        The standard-library handler calls ``fp.read()`` without a size before
        following a redirect. That bypasses the byte boundary and can block
        after the response connection has left the deadline registry. Resolve
        the target and preserve urllib's loop policy, but close the intermediate
        response immediately instead.
        """

        if "location" in headers:
            new_url = headers["location"]
        elif "uri" in headers:
            new_url = headers["uri"]
        else:
            return None
        if not isinstance(new_url, str) or not new_url:
            _close_response_safely(fp)
            raise DownloadProtocolError("O servidor retornou um redirecionamento inválido.")

        try:
            url_parts = urllib.parse.urlparse(new_url)
            if not url_parts.path and url_parts.netloc:
                normalized_parts = list(url_parts)
                normalized_parts[2] = "/"
                new_url = urllib.parse.urlunparse(normalized_parts)
            try:
                new_url = urllib.parse.quote(
                    new_url,
                    encoding="iso-8859-1",
                    safe=string.punctuation,
                )
            except UnicodeEncodeError as error:
                raise DownloadProtocolError(
                    "O servidor retornou um redirecionamento inválido."
                ) from error
            new_url = urllib.parse.urljoin(request.full_url, new_url)
            redirected = self.redirect_request(
                request, fp, code, message, headers, new_url,
            )
            if redirected is None:
                return None

            visited = getattr(request, "redirect_dict", None)
            if visited is None:
                visited = {}
                request.redirect_dict = visited
            redirected.redirect_dict = visited
            if (
                visited.get(new_url, 0) >= self.max_repeats
                or len(visited) >= self.max_redirections
            ):
                raise DownloadRedirectError(
                    "O servidor excedeu o limite seguro de redirecionamentos."
                )
            visited[new_url] = visited.get(new_url, 0) + 1
        except BaseException:
            _close_response_safely(fp)
            raise

        _close_response_safely(fp)
        return self.parent.open(redirected, timeout=request.timeout)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302

    def redirect_request(self, request, fp, code, message, headers, new_url):  # type: ignore[no-untyped-def]
        source = validate_https_url(request.full_url, "URL de origem")
        target = validate_https_url(new_url, "redirecionamento")
        source_origin = (source.hostname.casefold(), source.port or 443)
        target_origin = (target.hostname.casefold(), target.port or 443)
        if source_origin != target_origin:
            forwarded = {name.casefold() for name in request.headers}
            internal = {name.casefold() for name in request.unredirected_hdrs}
            if (
                not forwarded <= SAFE_CROSS_ORIGIN_HEADERS
                or not internal <= (SAFE_CROSS_ORIGIN_HEADERS | {"host"})
            ):
                raise DownloadRedirectError(
                    "Um redirecionamento entre origens tentou encaminhar headers privados."
                )
        # Python 3.10 does not include 308 in HTTPRedirectHandler's accepted
        # status set. 307 has the same method-preserving redirect semantics for
        # this GET/HEAD-only transport, so use it only for the stdlib request
        # construction while retaining the original status in our loop policy.
        stdlib_code = 307 if code == 308 else code
        redirected = super().redirect_request(
            request, fp, stdlib_code, message, headers, new_url,
        )
        if redirected is not None and request.get_method() == "HEAD":
            redirected.method = "HEAD"
        return redirected


class _TransportController:
    """Expose resolver and pending sockets to the deadline controller."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = False
        self._resolver: subprocess.Popen[bytes] | None = None
        self._sockets: set[socket.socket] = set()

    def attach_resolver(self, process: subprocess.Popen[bytes]) -> bool:
        with self._lock:
            if self._cancelled:
                cancelled = True
            else:
                self._resolver = process
                cancelled = False
        if cancelled:
            try:
                process.kill()
            except OSError:
                pass
            return False
        return True

    def detach_resolver(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            if self._resolver is process:
                self._resolver = None

    def attach_socket(self, connection: socket.socket) -> bool:
        with self._lock:
            if self._cancelled:
                cancelled = True
            else:
                self._sockets.add(connection)
                cancelled = False
        if cancelled:
            try:
                connection.close()
            except OSError:
                pass
            return False
        return True

    def detach_socket(self, connection: socket.socket) -> None:
        with self._lock:
            self._sockets.discard(connection)

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            resolver = self._resolver
            sockets = tuple(self._sockets)
        if resolver is not None:
            try:
                resolver.kill()
            except OSError:
                pass
        for connection in sockets:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass


def _resolve_addresses(
    host: str,
    port: int,
    timeout: float,
    transport_controller: _TransportController | None = None,
) -> list[tuple[int, int, int, str, tuple[object, ...]]]:
    """Resolve DNS in a killable child so the deadline leaves no DNS thread."""

    if not math.isfinite(timeout) or timeout <= 0:
        raise TimeoutError(f"Tempo esgotado ao resolver {host}.")
    deadline = time.monotonic() + timeout
    creationflags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    )
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", DNS_RESOLVER_SCRIPT, host, str(port)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    if transport_controller is not None and not transport_controller.attach_resolver(process):
        process.communicate()
        raise TimeoutError(f"Tempo esgotado ao resolver {host}.")
    try:
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout)
            output, _error = process.communicate(input=b"G", timeout=remaining)
        except subprocess.TimeoutExpired as error:
            try:
                process.kill()
            except OSError:
                pass
            process.communicate()
            raise TimeoutError(f"Tempo esgotado ao resolver {host}.") from error
        except BaseException:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.communicate()
            except BaseException:
                pass
            raise
    finally:
        if transport_controller is not None:
            transport_controller.detach_resolver(process)
    if process.returncode != 0:
        raise socket.gaierror(socket.EAI_FAIL, f"Falha ao resolver {host}.")
    if len(output) > DNS_MAX_OUTPUT_BYTES:
        raise OSError(f"A resolução de {host} excedeu o limite de saída.")
    try:
        document = json.loads(output)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OSError(f"A resolução de {host} retornou dados inválidos.") from error
    if not isinstance(document, list) or not document:
        raise OSError(f"Nenhum endereço foi encontrado para {host}.")
    candidates: list[tuple[int, int, int, str, tuple[object, ...]]] = []
    for item in document[:DNS_MAX_CANDIDATES]:
        if (
            not isinstance(item, list)
            or len(item) != 5
            or type(item[0]) is not int
            or item[0] not in {socket.AF_INET, socket.AF_INET6}
            or type(item[1]) is not int
            or item[1] != socket.SOCK_STREAM
            or type(item[2]) is not int
            or not isinstance(item[3], str)
            or not isinstance(item[4], list)
            or len(item[4]) not in {2, 4}
            or not isinstance(item[4][0], str)
            or type(item[4][1]) is not int
            or not 0 <= item[4][1] <= 65535
        ):
            raise OSError(f"A resolução de {host} retornou um endereço inválido.")
        if item[0] == socket.AF_INET and len(item[4]) != 2:
            raise OSError(f"A resolução de {host} retornou um IPv4 inválido.")
        if item[0] == socket.AF_INET6 and (
            len(item[4]) != 4
            or type(item[4][2]) is not int
            or type(item[4][3]) is not int
        ):
            raise OSError(f"A resolução de {host} retornou um IPv6 inválido.")
        candidates.append((item[0], item[1], item[2], item[3], tuple(item[4])))
    return candidates


def create_resilient_connection(
    address: tuple[str, int],
    timeout: float | object = socket._GLOBAL_DEFAULT_TIMEOUT,
    source_address: tuple[str, int] | None = None,
    *,
    transport_controller: _TransportController | None = None,
) -> socket.socket:
    """Resolve and connect every candidate under one absolute deadline."""

    host, port = address
    effective = socket.getdefaulttimeout() if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout
    if (
        effective is None
        or isinstance(effective, bool)
        or not isinstance(effective, (int, float))
        or not math.isfinite(effective)
        or effective <= 0
    ):
        raise TimeoutError(f"Tempo de conexão inválido para {host}:{port}.")
    started = time.monotonic()
    deadline = started + float(effective)
    candidates = _resolve_addresses(
        host, port, float(effective), transport_controller,
    )
    if time.monotonic() >= deadline:
        raise TimeoutError(f"Tempo esgotado ao resolver {host}.")
    pending: list[socket.socket] = []
    errors: list[OSError] = []
    selector = selectors.DefaultSelector()
    connected: socket.socket | None = None
    in_progress = {
        errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, errno.EINTR,
        *(value for name in ("WSAEINPROGRESS", "WSAEWOULDBLOCK", "WSAEALREADY")
          if (value := getattr(errno, name, None)) is not None),
    }
    try:
        for family, socktype, proto, _, sockaddr in candidates:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Tempo esgotado ao conectar a {host}:{port}.")
            connection = socket.socket(family, socktype, proto)
            if (
                transport_controller is not None
                and not transport_controller.attach_socket(connection)
            ):
                raise TimeoutError(f"Tempo esgotado ao conectar a {host}:{port}.")
            pending.append(connection)
            try:
                connection.setblocking(False)
                if source_address:
                    connection.bind(source_address)
                result = connection.connect_ex(sockaddr)
                if result in (0, errno.EISCONN):
                    connected = connection
                    break
                if result not in in_progress:
                    raise OSError(result, os.strerror(result))
                selector.register(connection, selectors.EVENT_WRITE)
            except OSError as error:
                errors.append(error)
                connection.close()
                pending.remove(connection)
                if transport_controller is not None:
                    transport_controller.detach_socket(connection)

        while connected is None and selector.get_map():
            remaining = max(0.0, deadline - time.monotonic())
            if remaining == 0 or not (events := selector.select(remaining)):
                raise TimeoutError(f"Tempo esgotado ao conectar a {host}:{port}.")
            for key, _ in events:
                connection = key.fileobj
                result = connection.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if result == 0:
                    connected = connection
                    break
                errors.append(OSError(result, os.strerror(result)))
                selector.unregister(connection)
                connection.close()
                pending.remove(connection)
                if transport_controller is not None:
                    transport_controller.detach_socket(connection)
        if connected is None:
            if errors:
                raise errors[-1]
            raise OSError(f"Não foi possível conectar a {host}:{port}.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"Tempo esgotado ao conectar a {host}:{port}.")
        connected.settimeout(remaining)
        return connected
    finally:
        selector.close()
        for connection in pending:
            if connection is not connected:
                connection.close()
                if transport_controller is not None:
                    transport_controller.detach_socket(connection)


class ResilientHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._transport_controller = _TransportController()

        def create_connection(
            address: tuple[str, int],
            timeout: float | object = socket._GLOBAL_DEFAULT_TIMEOUT,
            source_address: tuple[str, int] | None = None,
        ) -> socket.socket:
            return create_resilient_connection(
                address,
                timeout,
                source_address,
                transport_controller=self._transport_controller,
            )

        self._create_connection = create_connection

    def cancel_transport(self) -> None:
        self._transport_controller.cancel()


class _ConnectionRegistry:
    """Track the connection used while urllib opens and reads response headers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._connections: dict[int, http.client.HTTPSConnection] = {}
        self._cancelled: set[int] = set()

    def register(self, identity: int, connection: http.client.HTTPSConnection) -> None:
        with self._lock:
            if identity in self._cancelled:
                raise DownloadDeadlineError("A conexão foi cancelada pelo deadline total.")
            self._connections[identity] = connection

    def ensure_active(self, identity: int) -> None:
        with self._lock:
            if identity in self._cancelled:
                raise DownloadDeadlineError("A conexão foi cancelada pelo deadline total.")

    def unregister(self, identity: int) -> None:
        with self._lock:
            self._connections.pop(identity, None)

    def cancel(self, identity: int) -> None:
        with self._lock:
            self._cancelled.add(identity)
            connection = self._connections.get(identity)
        if connection is not None:
            cancel_transport = getattr(connection, "cancel_transport", None)
            if callable(cancel_transport):
                cancel_transport()
            active_socket = getattr(connection, "sock", None)
            if active_socket is not None:
                try:
                    active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            try:
                connection.close()
            except OSError:
                pass

    def clear(self, identity: int) -> None:
        with self._lock:
            self._connections.pop(identity, None)
            self._cancelled.discard(identity)


class _DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(
        self,
        registry: _ConnectionRegistry,
        connection_class: type[http.client.HTTPSConnection],
    ) -> None:
        super().__init__()
        self._registry = registry
        base_class = connection_class

        class RegisteredConnection(base_class):  # type: ignore[valid-type,misc]
            def __init__(registered_self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, **kwargs)
                registry.register(threading.get_ident(), registered_self)

            def connect(registered_self) -> None:
                identity = threading.get_ident()
                registry.ensure_active(identity)
                try:
                    super().connect()
                except BaseException:
                    registry.ensure_active(identity)
                    raise
                try:
                    registry.ensure_active(identity)
                except DownloadDeadlineError:
                    registered_self.close()
                    raise

        self._connection_class = RegisteredConnection

    def https_open(self, request: urllib.request.Request):  # type: ignore[no-untyped-def]
        identity = threading.get_ident()
        self._registry.ensure_active(identity)
        try:
            return self.do_open(
                self._connection_class,
                request,
                context=self._context,
            )
        finally:
            self._registry.unregister(identity)


def _build_https_opener(
    *handlers: object,
    connection_class: type[http.client.HTTPSConnection] = ResilientHTTPSConnection,
) -> urllib.request.OpenerDirector:
    registry = _ConnectionRegistry()
    opener = urllib.request.build_opener(
        HTTPSOnlyRedirectHandler(),
        _DeadlineHTTPSHandler(registry, connection_class),
        *handlers,
    )
    setattr(opener, "_x86qw_connection_registry", registry)
    return opener


def validate_https_url(url: object, label: str = "URL") -> urllib.parse.SplitResult:
    if not isinstance(url, str) or not url:
        raise DownloadPolicyError(f"{label} não é uma URL válida.")
    if any(ord(character) <= 32 or ord(character) == 127 for character in url):
        raise DownloadPolicyError(f"{label} contém espaço ou caractere de controle.")
    try:
        parsed = urllib.parse.urlsplit(url)
        parsed.port
    except ValueError as error:
        raise DownloadPolicyError(f"{label} não é uma URL válida.") from error
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise DownloadPolicyError(f"{label} deve ser uma URL HTTPS absoluta.")
    return parsed


def safe_url_for_log(url: object) -> str:
    """Return a diagnostic origin without credentials, path, query or fragment.

    This helper is deliberately non-throwing so an invalid, attacker-controlled
    URL can still be mentioned generically after validation fails without
    echoing controls or secrets.  Callers must continue to validate the real
    URL with :func:`validate_https_url` before network I/O.
    """

    if (
        not isinstance(url, str)
        or not url
        or any(ord(character) <= 32 or ord(character) == 127 for character in url)
    ):
        return "<URL HTTPS inválida>"
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return "<URL HTTPS inválida>"
    hostname = parsed.hostname
    if (
        parsed.scheme.casefold() != "https"
        or not hostname
    ):
        return "<URL HTTPS inválida>"
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    authority = rendered_host if port in (None, 443) else f"{rendered_host}:{port}"
    return f"https://{authority}/<redigido>"


def _validate_contract(contract: DownloadContract) -> None:
    if (
        not isinstance(contract.label, str)
        or not contract.label
        or any(ord(character) < 32 or ord(character) == 127 for character in contract.label)
    ):
        raise DownloadPolicyError("O rótulo do download deve ser texto não vazio.")
    validate_https_url(contract.url, f"URL de {contract.label}")
    if type(contract.maximum_size) is not int or contract.maximum_size < 1:
        raise DownloadPolicyError(f"O limite de {contract.label} deve ser positivo.")
    if contract.maximum_size > MAX_ARTIFACT_BYTES:
        raise DownloadPolicyError(
            f"O limite de {contract.label} excede o máximo global suportado."
        )
    if (
        isinstance(contract.deadline_seconds, bool)
        or not isinstance(contract.deadline_seconds, (int, float))
        or not math.isfinite(contract.deadline_seconds)
        or contract.deadline_seconds <= 0
    ):
        raise DownloadPolicyError(f"O deadline de {contract.label} deve ser positivo.")
    if not isinstance(contract.retry, RetryPolicy):
        raise DownloadPolicyError("A política de retry é inválida.")
    if not isinstance(contract.headers, Mapping):
        raise DownloadPolicyError("Os headers de download devem formar um mapeamento.")
    for name, value in contract.headers.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise DownloadPolicyError("Os headers de download devem ser texto.")
        if HTTP_HEADER_NAME.fullmatch(name) is None:
            raise DownloadPolicyError("Um nome de header de download é inválido.")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise DownloadPolicyError("Um header de download contém caractere de controle.")
    if isinstance(contract, PinnedArtifact):
        if type(contract.expected_size) is not int or contract.expected_size < 0:
            raise DownloadPolicyError("O tamanho esperado não pode ser negativo.")
        if contract.expected_size > contract.maximum_size:
            raise DownloadPolicyError("O tamanho esperado excede o limite do download.")
    if isinstance(contract, PinnedArtifact):
        digest = contract.expected_sha256
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or digest.casefold() != digest
            or any(char not in HEX64 for char in digest)
        ):
            raise DownloadPolicyError("O SHA-256 esperado deve conter 64 dígitos hexadecimais minúsculos.")
        if not isinstance(contract.destination, Path):
            raise DownloadPolicyError("O destino do artefato deve ser um Path.")
    elif isinstance(contract, BoundedMetadata) and contract.method not in {"GET", "HEAD"}:
        raise DownloadPolicyError("Metadados remotos aceitam somente GET ou HEAD.")


def _remaining(deadline: float, clock: Callable[[], float]) -> float:
    remaining = deadline - clock()
    if remaining <= 0:
        raise DownloadDeadlineError("O deadline total do download foi excedido.")
    return remaining


def _retry_after(value: str | None, wall_clock: Callable[[], float]) -> float | None:
    if value is None:
        return None
    stripped = value.strip()
    if len(stripped) <= 20 and ASCII_DECIMAL.fullmatch(stripped):
        return float(stripped)
    try:
        parsed = email.utils.parsedate_to_datetime(stripped)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = datetime.fromtimestamp(wall_clock(), timezone.utc)
    return max(0.0, (parsed.astimezone(timezone.utc) - now).total_seconds())


def _header_value(headers: Mapping[str, str], expected_name: str) -> str | None:
    expected = expected_name.casefold()
    return next(
        (value for name, value in headers.items() if name.casefold() == expected),
        None,
    )


def _transient_os_error(error: BaseException) -> bool:
    if isinstance(error, socket.gaierror):
        return error.errno == socket.EAI_AGAIN
    if isinstance(error, (TimeoutError, socket.timeout, ConnectionError)):
        return True
    if isinstance(error, ssl.SSLError):
        return False
    if isinstance(error, OSError):
        return error.errno in TRANSIENT_ERRNOS
    return False


def _transport_error(error: BaseException, policy: RetryPolicy, wall_clock: Callable[[], float]) -> DownloadError:
    if isinstance(error, urllib.error.HTTPError):
        result: DownloadError
        try:
            message = f"O servidor respondeu HTTP {error.code}."
            headers = _response_headers(error)
            if error.code in policy.transient_statuses:
                result = DownloadTransientError(
                    message,
                    retry_after=_retry_after(_header_value(headers, "Retry-After"), wall_clock),
                )
            else:
                result = DownloadHTTPError(error.code, message, headers)
        finally:
            try:
                error.close()
            except OSError:
                pass
        return result
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        if _transient_os_error(reason):
            return DownloadTransientError(f"Falha temporária de rede: {reason}")
        return DownloadTransportError(f"Falha de rede não recuperável: {reason}")
    if isinstance(error, (http.client.IncompleteRead, http.client.RemoteDisconnected)):
        return DownloadTransientError(f"A conexão terminou antes do download: {error}")
    if _transient_os_error(error):
        return DownloadTransientError(f"Falha temporária de rede: {error}")
    return DownloadTransportError(f"Falha de transporte: {error}")


def _response_headers(response: object) -> dict[str, str]:
    raw = getattr(response, "headers", None)
    if raw is None:
        return {}
    get_all = getattr(raw, "get_all", None)
    if callable(get_all):
        content_lengths = get_all("Content-Length", [])
        if not isinstance(content_lengths, (list, tuple)):
            raise DownloadProtocolError("O servidor retornou Content-Length inválido.")
        if len(content_lengths) > 1:
            raise DownloadProtocolError("O servidor retornou Content-Length duplicado.")
    try:
        return {str(name): str(value) for name, value in raw.items()}
    except AttributeError as error:
        raise DownloadProtocolError("O servidor retornou headers inválidos.") from error


def _content_length(headers: Mapping[str, str]) -> int | None:
    value = _header_value(headers, "Content-Length")
    if value is None:
        return None
    if len(value) > 20 or ASCII_DECIMAL.fullmatch(value) is None:
        raise DownloadProtocolError("O servidor retornou Content-Length inválido.")
    return int(value)


def _response_socket(response: object) -> object | None:
    for attributes in (
        ("fp", "raw", "_sock"),
        ("fp", "_sock"),
        ("raw", "_sock"),
        ("_sock",),
    ):
        candidate = response
        for attribute in attributes:
            candidate = getattr(candidate, attribute, None)
            if candidate is None:
                break
        if candidate is not None and callable(getattr(candidate, "settimeout", None)):
            return candidate
    return None


def _set_read_timeout(response: object, remaining: float) -> None:
    connection = _response_socket(response)
    if connection is not None:
        try:
            connection.settimeout(max(0.001, remaining))
        except OSError as error:
            raise DownloadTransientError(
                f"Não foi possível aplicar o timeout de leitura: {error}"
            ) from error


def _close_response_safely(response: object | None) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except OSError:
            pass


def _open_with_deadline(
    open_url: OpenCallback,
    request: urllib.request.Request,
    deadline: float,
    clock: Callable[[], float],
    cancel_open: Callable[[int], None] | None,
    finish_open: Callable[[int], None] | None,
) -> object:
    """Bound connection, redirects and response headers by the total deadline.

    urllib's timeout is an inactivity timeout. A peer can otherwise drip header
    bytes or redirects forever. The operation therefore runs in one daemon
    worker while the controller enforces the monotonic wall budget. A late
    response is closed as soon as the underlying call returns.
    """

    initial_budget = _remaining(deadline, clock)
    if cancel_open is not None and initial_budget <= MIN_OPEN_BUDGET_SECONDS:
        raise DownloadDeadlineError(
            "O orçamento restante é insuficiente para iniciar uma conexão segura."
        )

    lock = threading.Lock()
    state: dict[str, object] = {}
    cancelled = False
    worker_identity: int | None = None

    def worker() -> None:
        nonlocal cancelled, worker_identity
        identity = threading.get_ident()
        with lock:
            worker_identity = identity
            cancelled_before_open = cancelled
        try:
            if cancelled_before_open and cancel_open is not None:
                cancel_open(identity)
            try:
                open_timeout = _remaining(deadline, clock)
                value: object = open_url(request, open_timeout)
                kind = "response"
            except BaseException as error:
                value = error
                kind = "error"
            close_late: object | None = None
            with lock:
                if cancelled:
                    if kind == "response":
                        close_late = value
                else:
                    state[kind] = value
            _close_response_safely(close_late)
        finally:
            if finish_open is not None:
                finish_open(identity)

    thread = threading.Thread(
        target=worker,
        name="x86qw-download-open",
        daemon=True,
    )
    thread.start()

    def cancel_and_collect() -> None:
        nonlocal cancelled
        with lock:
            cancelled = True
            response = state.pop("response", None)
            identity = worker_identity
        if identity is None:
            identity = thread.ident
        if identity is not None and cancel_open is not None:
            cancel_open(identity)
        _close_response_safely(response)
        if cancel_open is not None:
            cleanup_remaining = max(0.0, deadline - clock())
            if cleanup_remaining > 0:
                thread.join(cleanup_remaining)

    try:
        wait_budget = _remaining(deadline, clock)
        cleanup_reserve = (
            min(
                OPEN_CLEANUP_RESERVE_SECONDS,
                max(0.25, wait_budget / 10),
            )
            if cancel_open is not None
            else 0.0
        )
        thread.join(wait_budget - cleanup_reserve)
    except BaseException:
        cancel_and_collect()
        raise
    if thread.is_alive():
        cancel_and_collect()
        raise DownloadDeadlineError(
            "O deadline total expirou durante conexão, redirects ou headers."
        )
    with lock:
        response = state.get("response")
        error = state.get("error")
    if isinstance(error, BaseException):
        raise error
    if response is None:
        raise DownloadTransportError("O transporte terminou sem resposta.")
    _remaining(deadline, clock)
    return response


def _open_temporary(destination: Path) -> tuple[BinaryIO, Path]:
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        descriptor, temporary = private_fs.private_mkstemp(
            prefix=f".{destination.name}.", suffix=".download", directory=destination.parent,
        )
        output = os.fdopen(descriptor, "wb")
        descriptor = None
        return output, temporary
    except OSError as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        _unlink_temporary(temporary)
        raise DownloadStorageError(f"Não foi possível criar o temporário privado de {destination}.") from error


def _unlink_temporary(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        # Never recurse: an unexpected directory or special entry is preserved
        # and reported to the caller.
        raise DownloadStorageError(
            f"Não foi possível remover o temporário privado {path}."
        ) from error


def _read_response(
    response: object,
    contract: DownloadContract,
    output: BinaryIO,
    digest: "hashlib._Hash",
    deadline: float,
    clock: Callable[[], float],
    wall_clock: Callable[[], float],
    progress: ProgressCallback | None,
) -> tuple[int, dict[str, str]]:
    headers = _response_headers(response)
    declared = _content_length(headers)
    expected = (
        contract.expected_size if isinstance(contract, PinnedArtifact) else None
    )
    if declared is not None:
        if declared > contract.maximum_size:
            raise DownloadLimitError(
                f"Content-Length de {contract.label} excede o limite de {contract.maximum_size} bytes."
            )
        if expected is not None and declared != expected:
            raise DownloadIntegrityError(
                f"Content-Length de {contract.label} diverge do tamanho esperado."
            )
    if isinstance(contract, BoundedMetadata) and contract.method == "HEAD":
        return 0, headers
    received = 0
    total = expected if expected is not None else declared
    # HTTPResponse.read() may perform several socket reads internally (notably
    # for chunked bodies). A slow peer could therefore keep one call alive by
    # dripping chunks before each socket timeout. read1() performs at most one
    # buffered/socket read, returning control so the monotonic deadline is
    # checked again between chunks.
    read = getattr(response, "read1", None)
    if not callable(read):
        read = getattr(response, "read", None)
    if not callable(read):
        raise DownloadProtocolError("A resposta remota não pode ser lida.")
    while True:
        remaining = _remaining(deadline, clock)
        _set_read_timeout(response, remaining)
        allowance = contract.maximum_size - received
        if expected is not None:
            allowance = min(allowance, expected - received)
        try:
            block = read(min(CHUNK_SIZE, allowance + 1))
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise _transport_error(error, contract.retry, wall_clock) from error
        _remaining(deadline, clock)
        if not block:
            break
        if not isinstance(block, bytes):
            raise DownloadProtocolError("A resposta remota não contém bytes.")
        received += len(block)
        if received > contract.maximum_size:
            raise DownloadLimitError(
                f"O download de {contract.label} excedeu o limite de {contract.maximum_size} bytes."
            )
        if expected is not None and received > expected:
            raise DownloadIntegrityError(
                f"O download de {contract.label} excedeu o tamanho esperado de {expected} bytes."
            )
        digest.update(block)
        try:
            written = output.write(block)
        except OSError as error:
            raise DownloadStorageError(f"Falha ao gravar {contract.label} no destino.") from error
        if written != len(block):
            raise DownloadStorageError(
                f"A gravação de {contract.label} terminou antes de persistir o bloco completo."
            )
        if progress is not None:
            progress(received, total, False)
    if expected is not None and received != expected:
        raise DownloadTransientError(
            f"A conexão de {contract.label} terminou com {received} bytes; "
            f"eram esperados {expected}."
        )
    if declared is not None and received != declared:
        raise DownloadTransientError(
            f"A conexão de {contract.label} terminou antes do Content-Length declarado."
        )
    if progress is not None:
        progress(received, total, True)
    return received, headers


def _attempt(
    contract: DownloadContract,
    attempt: int,
    deadline: float,
    *,
    open_url: OpenCallback,
    cancel_open: Callable[[int], None] | None,
    finish_open: Callable[[int], None] | None,
    clock: Callable[[], float],
    wall_clock: Callable[[], float],
    progress: ProgressCallback | None,
) -> DownloadResult:
    request_headers = {
        "User-Agent": "x86qw-downloader/1",
        "Accept-Encoding": "identity",
        **dict(contract.headers),
    }
    request = urllib.request.Request(
        contract.url,
        headers=request_headers,
        method=contract.method if isinstance(contract, BoundedMetadata) else "GET",
    )
    output: BinaryIO
    temporary: Path | None = None
    memory: io.BytesIO | None = None
    if isinstance(contract, PinnedArtifact):
        output, temporary = _open_temporary(contract.destination)
    else:
        memory = io.BytesIO()
        output = memory
    digest = hashlib.sha256()
    failure: BaseException | None = None
    try:
        try:
            response_context = _open_with_deadline(
                open_url,
                request,
                deadline,
                clock,
                cancel_open,
                finish_open,
            )
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit, DownloadError)):
                raise
            raise _transport_error(error, contract.retry, wall_clock) from error
        with response_context as response:  # type: ignore[union-attr]
            final_url = getattr(response, "geturl", lambda: contract.url)()
            try:
                validate_https_url(final_url, "URL final do download")
            except DownloadPolicyError as error:
                raise DownloadRedirectError(str(error)) from error
            status = getattr(response, "status", None)
            if status is None and callable(getattr(response, "getcode", None)):
                status = response.getcode()
            if status is not None and status != 200:
                raise DownloadProtocolError(
                    f"O servidor respondeu HTTP {status} sem um corpo completo esperado."
                )
            size, headers = _read_response(
                response, contract, output, digest, deadline, clock, wall_clock, progress,
            )
        actual_sha256 = digest.hexdigest()
        if isinstance(contract, PinnedArtifact):
            if actual_sha256 != contract.expected_sha256:
                raise DownloadIntegrityError(f"O SHA-256 de {contract.label} não corresponde ao esperado.")
        if isinstance(contract, PinnedArtifact):
            try:
                output.flush()
                os.fsync(output.fileno())
                if os.name == "nt":
                    _remaining(deadline, clock)
                    private_fs.replace_open_private_file(
                        output.fileno(), temporary, contract.destination,
                    )
                    temporary = None
                    output.close()
                else:
                    output.close()
                    _remaining(deadline, clock)
                    os.replace(temporary, contract.destination)
                    temporary = None
            except OSError as error:
                raise DownloadStorageError(
                    f"Não foi possível promover {contract.label} atomicamente."
                ) from error
            return DownloadResult(
                final_url, size, actual_sha256, attempt, headers, path=contract.destination,
            )
        assert memory is not None
        return DownloadResult(
            final_url, size, actual_sha256, attempt, headers, data=memory.getvalue(),
        )
    except BaseException as error:
        failure = error
        raise
    finally:
        if not output.closed:
            try:
                output.close()
            except OSError:
                # Preserve the primary error. A close failure on the success path
                # was already translated to DownloadStorageError above.
                pass
        try:
            _unlink_temporary(temporary)
        except DownloadStorageError:
            if failure is None:
                raise
            raise DownloadStorageError(
                f"{failure} A limpeza do temporário também falhou."
            ) from failure


def _retry_delay(
    policy: RetryPolicy,
    attempt: int,
    retry_after: float | None,
    random_value: Callable[[], float],
) -> float:
    if retry_after is not None:
        return retry_after
    base = min(policy.maximum_backoff, policy.initial_backoff * (2 ** (attempt - 1)))
    if not base or not policy.jitter:
        return base
    return max(0.0, base * (1 + policy.jitter * ((2 * random_value()) - 1)))


def _select_transport() -> tuple[
    OpenCallback,
    Callable[[int], None] | None,
    Callable[[int], None] | None,
]:
    # Build a fresh transport for every public operation. Keeping a module-level
    # opener would let an otherwise read-only caller mutate ``open`` and replace
    # the bytes received by a later production download.
    selected_opener = _build_https_opener()
    registry = getattr(selected_opener, "_x86qw_connection_registry", None)
    if not isinstance(registry, _ConnectionRegistry):
        raise DownloadPolicyError("O opener HTTPS selado perdeu seu registro interno.")

    def selected_open(request: urllib.request.Request, timeout: float) -> object:
        return selected_opener.open(request, timeout=timeout)

    return selected_open, registry.cancel, registry.clear


def _download_until_deadline(
    contract: DownloadContract,
    deadline: float,
    *,
    open_url: OpenCallback,
    cancel_open: Callable[[int], None] | None,
    finish_open: Callable[[int], None] | None,
    clock: Callable[[], float],
    wall_clock: Callable[[], float],
    sleep: Callable[[float], None],
    random_value: Callable[[], float],
    progress: ProgressCallback | None,
    on_retry: RetryCallback | None,
) -> DownloadResult:
    """Execute one validated contract inside an existing absolute deadline."""

    last_error: DownloadTransientError | None = None
    for attempt in range(1, contract.retry.attempts + 1):
        _remaining(deadline, clock)
        try:
            return _attempt(
                contract,
                attempt,
                deadline,
                open_url=open_url,
                cancel_open=cancel_open,
                finish_open=finish_open,
                clock=clock,
                wall_clock=wall_clock,
                progress=progress,
            )
        except DownloadTransientError as error:
            last_error = error
            if attempt >= contract.retry.attempts:
                break
            delay = _retry_delay(
                contract.retry, attempt, error.retry_after, random_value,
            )
            remaining = _remaining(deadline, clock)
            if delay >= remaining:
                raise DownloadRetryBudgetError(
                    "A espera da próxima tentativa não cabe no deadline restante."
                ) from error
            if on_retry is not None:
                on_retry(attempt + 1, error, delay)
            sleep(delay)
    assert last_error is not None
    raise last_error


def _validate_mirror_contracts(
    contracts: tuple[DownloadContract, ...],
) -> DownloadContract:
    if not contracts:
        raise DownloadPolicyError("Informe ao menos um mirror para download.")
    for contract in contracts:
        if not isinstance(contract, (PinnedArtifact, BoundedMetadata)):
            raise DownloadPolicyError("Um contrato de mirror é inválido.")
        _validate_contract(contract)

    first = contracts[0]
    first_type = type(first)
    if any(type(contract) is not first_type for contract in contracts[1:]):
        raise DownloadPolicyError("Todos os mirrors devem usar o mesmo tipo de contrato.")
    if any(
        contract.deadline_seconds != first.deadline_seconds
        for contract in contracts[1:]
    ):
        raise DownloadPolicyError("Todos os mirrors devem compartilhar o mesmo deadline total.")

    if isinstance(first, PinnedArtifact):
        identity = (
            first.destination,
            first.expected_size,
            first.expected_sha256,
            first.maximum_size,
        )
        for contract in contracts[1:]:
            assert isinstance(contract, PinnedArtifact)
            if (
                contract.destination,
                contract.expected_size,
                contract.expected_sha256,
                contract.maximum_size,
            ) != identity:
                raise DownloadPolicyError(
                    "Mirrors fixados devem apontar para o mesmo destino e identidade."
                )
    else:
        assert isinstance(first, BoundedMetadata)
        identity = (first.maximum_size, first.method)
        for contract in contracts[1:]:
            assert isinstance(contract, BoundedMetadata)
            if (contract.maximum_size, contract.method) != identity:
                raise DownloadPolicyError(
                    "Mirrors de metadados devem compartilhar limite e método."
                )
    return first


def _download_impl(
    contract: DownloadContract,
    *,
    clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    random_value: Callable[[], float] = random.random,
    progress: ProgressCallback | None = None,
    on_retry: RetryCallback | None = None,
) -> DownloadResult:
    """Internal dependency-injected implementation used by focused tests."""

    _validate_contract(contract)
    selected_open, cancel_open, finish_open = _select_transport()
    deadline = clock() + contract.deadline_seconds
    return _download_until_deadline(
        contract,
        deadline,
        open_url=selected_open,
        cancel_open=cancel_open,
        finish_open=finish_open,
        clock=clock,
        wall_clock=wall_clock,
        sleep=sleep,
        random_value=random_value,
        progress=progress,
        on_retry=on_retry,
    )


def download(
    contract: DownloadContract,
    *,
    progress: ProgressCallback | None = None,
    on_retry: RetryCallback | None = None,
) -> DownloadResult:
    """Download one bounded response through the sealed production transport."""

    return _download_impl(
        contract,
        progress=progress,
        on_retry=on_retry,
    )


def _download_mirrors_impl(
    contracts: tuple[DownloadContract, ...],
    *,
    clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    random_value: Callable[[], float] = random.random,
    progress: ProgressCallback | None = None,
    on_retry: RetryCallback | None = None,
    on_mirror_failure: MirrorFailureCallback | None = None,
) -> DownloadResult:
    """Internal dependency-injected mirror implementation used by tests.

    All contracts are validated before network I/O. Storage, policy, deadline,
    protocol and limit failures are local or terminal and therefore never
    advance to another mirror. Only transport and integrity failures may fall
    back.
    """

    first = _validate_mirror_contracts(contracts)
    selected_open, cancel_open, finish_open = _select_transport()
    deadline = clock() + first.deadline_seconds
    last_error: DownloadError | None = None
    for index, contract in enumerate(contracts, start=1):
        try:
            return _download_until_deadline(
                contract,
                deadline,
                open_url=selected_open,
                cancel_open=cancel_open,
                finish_open=finish_open,
                clock=clock,
                wall_clock=wall_clock,
                sleep=sleep,
                random_value=random_value,
                progress=progress,
                on_retry=on_retry,
            )
        except (DownloadTransportError, DownloadIntegrityError) as error:
            last_error = error
            if on_mirror_failure is not None:
                on_mirror_failure(index, contract, error)
    assert last_error is not None
    raise last_error


def download_mirrors(
    contracts: tuple[DownloadContract, ...],
    *,
    progress: ProgressCallback | None = None,
    on_retry: RetryCallback | None = None,
    on_mirror_failure: MirrorFailureCallback | None = None,
) -> DownloadResult:
    """Try equivalent mirrors through the sealed production transport."""

    return _download_mirrors_impl(
        contracts,
        progress=progress,
        on_retry=on_retry,
        on_mirror_failure=on_mirror_failure,
    )
