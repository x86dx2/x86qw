& {
param([object[]]$BootstrapArguments)

$ErrorActionPreference = "Stop"
$InstallerVersion = "0.7.1"
$InstallerFile = "x86qw-installer-$InstallerVersion.zip"
$InstallerSha256 = "a0946ffcc8a4e1181dbc55ea08caf54691b18b12e901d12069eb2064b38c0d80"
$InstallerSize = "157113"
$InstallerConnectTimeoutSeconds = 15
$InstallerTransferTimeoutSeconds = 120
$InstallerRetryMaxSeconds = 180
$InstallerRetries = 2
$InstallerUrls = @(
  "https://github.com/x86dx2/x86qw/releases/download/x86qw-installer-$InstallerVersion/$InstallerFile",
  "https://gitlab.com/api/v4/projects/84813414/packages/generic/x86qw-installer/$InstallerVersion/$InstallerFile"
)

$PreviousConsoleOutputEncoding = [Console]::OutputEncoding
$PreviousPowerShellOutputEncoding = $OutputEncoding
$Utf8Encoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $Utf8Encoding
$OutputEncoding = $Utf8Encoding
$InstallerExitCode = $null

try {
  $PythonRuntime = $null
  $PythonCandidates = @(
    [pscustomobject]@{ Command = "py"; Arguments = @("-3") },
    [pscustomobject]@{ Command = "python3"; Arguments = @() },
    [pscustomobject]@{ Command = "python"; Arguments = @() }
  )
  foreach ($Candidate in $PythonCandidates) {
    if (-not (Get-Command $Candidate.Command -ErrorAction SilentlyContinue)) {
      continue
    }
    try {
      $ProbeArguments = @($Candidate.Arguments) + @(
        "-c",
        "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
      )
      & $Candidate.Command @ProbeArguments *> $null
      $ProbeExitCode = $LASTEXITCODE
    } catch {
      continue
    }
    if ($ProbeExitCode -eq 0) {
      $PythonRuntime = $Candidate
      break
    }
  }

  if ($null -eq $PythonRuntime) {
    $PythonError = @(
      "x86QW: Python 3.10 ou mais recente nao foi encontrado.",
      "Instale com: winget install --id Python.Python.3.13 -e",
      "Depois abra um novo PowerShell e execute o instalador novamente.",
      "O alias da Microsoft Store, sozinho, nao e um Python utilizavel."
    ) -join [Environment]::NewLine
    throw $PythonError
  }

  $WorkDir = Join-Path ([System.IO.Path]::GetTempPath()) ("x86qw-installer-" + [guid]::NewGuid())
  New-Item -ItemType Directory -Path $WorkDir | Out-Null
  try {
    $Archive = Join-Path $WorkDir $InstallerFile
    $Downloader = Join-Path $WorkDir "x86qw-bootstrap-download.py"
    $DownloaderSource = @'
import email.utils
import errno
import hashlib
import http.client
import json
import math
import os
import random
import selectors
import socket
import ssl
import string
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


TRANSIENT_HTTP = frozenset({408, 425, 429, 500, 502, 503, 504})
TRANSIENT_ERRNOS = frozenset({
    errno.ECONNABORTED, errno.ECONNREFUSED, errno.ECONNRESET,
    errno.EHOSTUNREACH, errno.EINTR, errno.ENETDOWN, errno.ENETRESET,
    errno.ENETUNREACH, errno.ETIMEDOUT,
})
DNS_MAX_CANDIDATES = 64
DNS_MAX_OUTPUT_BYTES = 64 * 1024
OPEN_CLEANUP_RESERVE_SECONDS = 0.5
MIN_OPEN_BUDGET_SECONDS = 0.5
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


class DownloadError(Exception):
    pass


class PolicyError(DownloadError):
    pass


class IntegrityError(DownloadError):
    pass


class ProtocolError(DownloadError):
    pass


class StorageError(DownloadError):
    pass


class TransientError(DownloadError):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def validate_https(url):
    if not isinstance(url, str) or any(ord(character) <= 32 for character in url):
        raise PolicyError("URL HTTPS invalida")
    try:
        parsed = urllib.parse.urlsplit(url)
        parsed.port
    except ValueError as error:
        raise PolicyError("URL HTTPS invalida") from error
    if (parsed.scheme.lower() != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment):
        raise PolicyError("URL fora de HTTPS rejeitada")
    return parsed


class HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    @staticmethod
    def close_intermediate(fp):
        try:
            fp.close()
        except OSError:
            pass

    def http_error_302(self, req, fp, code, msg, headers):
        if "location" in headers:
            newurl = headers["location"]
        elif "uri" in headers:
            newurl = headers["uri"]
        else:
            self.close_intermediate(fp)
            return None
        try:
            parts = urllib.parse.urlparse(newurl)
            if parts.scheme not in ("http", "https", "ftp", ""):
                raise urllib.error.HTTPError(
                    newurl, code,
                    "{0} - redirecionamento para URL nao permitido".format(msg),
                    headers, fp,
                )
            if not parts.path and parts.netloc:
                parts = list(parts)
                parts[2] = "/"
            newurl = urllib.parse.urlunparse(parts)
            newurl = urllib.parse.quote(
                newurl, encoding="iso-8859-1", safe=string.punctuation,
            )
            newurl = urllib.parse.urljoin(req.full_url, newurl)
            redirected = self.redirect_request(req, fp, code, msg, headers, newurl)
            if redirected is None:
                self.close_intermediate(fp)
                return None
            if hasattr(req, "redirect_dict"):
                visited = redirected.redirect_dict = req.redirect_dict
                if (visited.get(newurl, 0) >= self.max_repeats
                        or len(visited) >= self.max_redirections):
                    raise urllib.error.HTTPError(
                        req.full_url, code, self.inf_msg + msg, headers, fp,
                    )
            else:
                visited = redirected.redirect_dict = req.redirect_dict = {}
            visited[newurl] = visited.get(newurl, 0) + 1
        except BaseException:
            self.close_intermediate(fp)
            raise
        self.close_intermediate(fp)
        return self.parent.open(redirected, timeout=req.timeout)

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        validate_https(req.full_url)
        validate_https(target)
        stdlib_code = 307 if code == 308 else code
        redirected = super().redirect_request(
            req, fp, stdlib_code, msg, headers, target,
        )
        if redirected is not None and req.get_method() == "HEAD":
            redirected.method = "HEAD"
        return redirected


class TransportController:
    def __init__(self):
        self.lock = threading.Lock()
        self.cancelled = False
        self.resolver = None
        self.sockets = set()

    def attach_resolver(self, process):
        with self.lock:
            if self.cancelled:
                cancelled = True
            else:
                self.resolver = process
                cancelled = False
        if cancelled:
            try:
                process.kill()
            except OSError:
                pass
            return False
        return True

    def detach_resolver(self, process):
        with self.lock:
            if self.resolver is process:
                self.resolver = None

    def attach_socket(self, connection):
        with self.lock:
            if self.cancelled:
                cancelled = True
            else:
                self.sockets.add(connection)
                cancelled = False
        if cancelled:
            try:
                connection.close()
            except OSError:
                pass
            return False
        return True

    def detach_socket(self, connection):
        with self.lock:
            self.sockets.discard(connection)

    def cancel(self):
        with self.lock:
            self.cancelled = True
            resolver = self.resolver
            sockets = tuple(self.sockets)
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


def resolve_addresses(host, port, timeout, transport_controller=None):
    if not math.isfinite(timeout) or timeout <= 0:
        raise TimeoutError("tempo esgotado durante resolucao DNS")
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
    if (transport_controller is not None
            and not transport_controller.attach_resolver(process)):
        process.communicate()
        raise TimeoutError("tempo esgotado durante resolucao DNS")
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
            raise TimeoutError("tempo esgotado durante resolucao DNS") from error
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
        raise socket.gaierror(socket.EAI_FAIL, "falha durante resolucao DNS")
    if len(output) > DNS_MAX_OUTPUT_BYTES:
        raise OSError("resolucao DNS excedeu o limite de saida")
    try:
        document = json.loads(output)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OSError("resolucao DNS retornou dados invalidos") from error
    if not isinstance(document, list) or not document:
        raise OSError("resolucao DNS nao retornou enderecos")
    candidates = []
    for item in document[:DNS_MAX_CANDIDATES]:
        if (
            not isinstance(item, list) or len(item) != 5
            or type(item[0]) is not int
            or item[0] not in {socket.AF_INET, socket.AF_INET6}
            or type(item[1]) is not int or item[1] != socket.SOCK_STREAM
            or type(item[2]) is not int or not isinstance(item[3], str)
            or not isinstance(item[4], list) or len(item[4]) not in {2, 4}
            or not isinstance(item[4][0], str) or type(item[4][1]) is not int
            or not 0 <= item[4][1] <= 65535
        ):
            raise OSError("resolucao DNS retornou endereco invalido")
        if item[0] == socket.AF_INET and len(item[4]) != 2:
            raise OSError("resolucao DNS retornou IPv4 invalido")
        if item[0] == socket.AF_INET6 and (
            len(item[4]) != 4
            or type(item[4][2]) is not int or type(item[4][3]) is not int
        ):
            raise OSError("resolucao DNS retornou IPv6 invalido")
        candidates.append((item[0], item[1], item[2], item[3], tuple(item[4])))
    return candidates


def create_resilient_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                                source_address=None, transport_controller=None):
    host, port = address
    effective = socket.getdefaulttimeout() if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout
    if (
        effective is None or isinstance(effective, bool)
        or not isinstance(effective, (int, float))
        or not math.isfinite(effective) or effective <= 0
    ):
        raise TimeoutError("tempo de conexao invalido")
    deadline = time.monotonic() + float(effective)
    candidates = resolve_addresses(
        host, port, float(effective), transport_controller,
    )
    if time.monotonic() >= deadline:
        raise TimeoutError("tempo esgotado durante resolucao DNS")
    pending = []
    errors = []
    selector = selectors.DefaultSelector()
    connected = None
    in_progress = {
        errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, errno.EINTR,
        *(value for name in ("WSAEINPROGRESS", "WSAEWOULDBLOCK", "WSAEALREADY")
          if (value := getattr(errno, name, None)) is not None),
    }
    try:
        for family, socktype, proto, _, sockaddr in candidates:
            if time.monotonic() >= deadline:
                raise TimeoutError("tempo esgotado durante conexao TCP")
            connection = socket.socket(family, socktype, proto)
            if (transport_controller is not None
                    and not transport_controller.attach_socket(connection)):
                raise TimeoutError("tempo esgotado durante conexao TCP")
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
                raise TimeoutError("tempo esgotado durante conexao TCP")
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
            raise OSError("nao foi possivel conectar")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("tempo esgotado durante conexao TCP")
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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.transport_controller = TransportController()

        def create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                              source_address=None):
            return create_resilient_connection(
                address, timeout, source_address, self.transport_controller,
            )

        self._create_connection = create_connection

    def cancel_transport(self):
        self.transport_controller.cancel()


class ConnectionRegistry:
    def __init__(self):
        self.lock = threading.Lock()
        self.connections = {}
        self.cancelled = set()

    def register(self, identity, connection):
        with self.lock:
            if identity in self.cancelled:
                raise DownloadError("conexao cancelada pelo prazo total")
            self.connections[identity] = connection

    def ensure_active(self, identity):
        with self.lock:
            if identity in self.cancelled:
                raise DownloadError("conexao cancelada pelo prazo total")

    def unregister(self, identity):
        with self.lock:
            self.connections.pop(identity, None)

    def cancel(self, identity):
        with self.lock:
            self.cancelled.add(identity)
            connection = self.connections.get(identity)
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

    def clear(self, identity):
        with self.lock:
            self.connections.pop(identity, None)
            self.cancelled.discard(identity)


class DeadlineHttpsHandler(urllib.request.HTTPSHandler):
    def __init__(self, registry):
        super().__init__()
        self.registry = registry

        class RegisteredConnection(ResilientHTTPSConnection):
            def __init__(registered_self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                registry.register(threading.get_ident(), registered_self)

            def connect(registered_self):
                identity = threading.get_ident()
                registry.ensure_active(identity)
                try:
                    super().connect()
                except BaseException:
                    registry.ensure_active(identity)
                    raise
                try:
                    registry.ensure_active(identity)
                except DownloadError:
                    registered_self.close()
                    raise

        self.connection_class = RegisteredConnection

    def https_open(self, request):
        identity = threading.get_ident()
        self.registry.ensure_active(identity)
        try:
            return self.do_open(
                self.connection_class, request, context=self._context,
            )
        finally:
            self.registry.unregister(identity)


def retry_after(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if len(value) <= 20 and value and all("0" <= char <= "9" for char in value):
        return float(value)
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = datetime.fromtimestamp(time.time(), timezone.utc)
    return max(0.0, (parsed.astimezone(timezone.utc) - now).total_seconds())


def transient_os_error(error):
    if isinstance(error, socket.gaierror):
        return error.errno == socket.EAI_AGAIN
    if isinstance(error, (TimeoutError, socket.timeout, ConnectionError)):
        return True
    if isinstance(error, ssl.SSLError):
        return False
    return isinstance(error, OSError) and error.errno in TRANSIENT_ERRNOS


def transport_error(error):
    if isinstance(error, urllib.error.HTTPError):
        try:
            message = "resposta HTTP {0}".format(error.code)
            if error.code in TRANSIENT_HTTP:
                headers = error.headers or {}
                return TransientError(message, retry_after(headers.get("Retry-After")))
            return DownloadError(message)
        finally:
            try:
                error.close()
            except OSError:
                pass
    if isinstance(error, urllib.error.URLError):
        if transient_os_error(error.reason):
            return TransientError("falha temporaria de rede: {0}".format(error.reason))
        return DownloadError("falha de rede: {0}".format(error.reason))
    if isinstance(error, (http.client.IncompleteRead, http.client.RemoteDisconnected)):
        return TransientError("conexao terminou antes do download: {0}".format(error))
    if transient_os_error(error):
        return TransientError("falha temporaria de rede: {0}".format(error))
    return DownloadError("falha de transporte: {0}".format(error))


def response_socket(response):
    for attributes in (("fp", "raw", "_sock"), ("fp", "_sock"), ("_sock",)):
        candidate = response
        for attribute in attributes:
            candidate = getattr(candidate, attribute, None)
            if candidate is None:
                break
        if candidate is not None and callable(getattr(candidate, "settimeout", None)):
            return candidate
    return None


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise DownloadError("prazo total do download excedido")
    return value


def set_read_timeout(response, total_deadline, attempt_deadline):
    timeout = min(remaining(total_deadline), attempt_deadline - time.monotonic())
    if timeout <= 0:
        raise TransientError("prazo da tentativa de download excedido")
    connection = response_socket(response)
    if connection is not None:
        try:
            connection.settimeout(max(0.001, timeout))
        except OSError as error:
            raise TransientError("falha ao aplicar timeout de leitura") from error


def close_response(response):
    close = getattr(response, "close", None)
    if callable(close):
        try:
            close()
        except OSError:
            pass


def open_with_deadline(opener, request, connect_timeout, total_deadline,
                       attempt_deadline, registry):
    started = time.monotonic()
    if started >= total_deadline:
        raise DownloadError("prazo total excedido durante conexao ou headers")
    if started >= attempt_deadline:
        raise TransientError("prazo da tentativa excedido durante conexao ou headers")
    connection_deadline = started + connect_timeout
    if total_deadline <= attempt_deadline and total_deadline <= connection_deadline:
        deadline = total_deadline
        timeout_error = DownloadError("prazo total excedido durante conexao ou headers")
    elif attempt_deadline <= connection_deadline:
        deadline = attempt_deadline
        timeout_error = TransientError(
            "prazo da tentativa excedido durante conexao ou headers"
        )
    else:
        deadline = connection_deadline
        timeout_error = TransientError("prazo de conexao ou headers excedido")
    if deadline - time.monotonic() <= MIN_OPEN_BUDGET_SECONDS:
        raise timeout_error
    lock = threading.Lock()
    state = {}
    cancelled = [False]
    worker_identity = [None]

    def deadline_error():
        return timeout_error

    def worker():
        identity = threading.get_ident()
        with lock:
            worker_identity[0] = identity
            cancelled_before_open = cancelled[0]
        try:
            if cancelled_before_open:
                registry.cancel(identity)
            try:
                socket_timeout = deadline - time.monotonic()
                if socket_timeout <= 0:
                    raise deadline_error()
                value = opener.open(request, timeout=socket_timeout)
                kind = "response"
            except BaseException as error:
                value = error
                kind = "error"
            late = None
            with lock:
                if cancelled[0]:
                    if kind == "response":
                        late = value
                else:
                    state[kind] = value
            close_response(late)
        finally:
            registry.clear(identity)

    thread = threading.Thread(target=worker, name="x86qw-bootstrap-open")
    thread.daemon = True
    thread.start()

    def cancel_and_collect():
        with lock:
            cancelled[0] = True
            response = state.pop("response", None)
            identity = worker_identity[0]
        if identity is None:
            identity = thread.ident
        if identity is not None:
            registry.cancel(identity)
        close_response(response)
        cleanup_remaining = max(0.0, total_deadline - time.monotonic())
        if cleanup_remaining > 0:
            thread.join(min(OPEN_CLEANUP_RESERVE_SECONDS, cleanup_remaining))

    try:
        wait = deadline - time.monotonic()
        if wait <= 0:
            raise deadline_error()
        cleanup_budget = min(
            OPEN_CLEANUP_RESERVE_SECONDS, max(0.25, wait / 10),
        )
        cleanup_after_limit = max(0.0, total_deadline - deadline)
        cleanup_reserve = max(0.0, cleanup_budget - cleanup_after_limit)
        thread.join(wait - cleanup_reserve)
    except BaseException:
        cancel_and_collect()
        raise
    if thread.is_alive():
        cancel_and_collect()
        raise deadline_error()
    with lock:
        response = state.get("response")
        error = state.get("error")
    if isinstance(error, BaseException):
        raise error
    if response is None:
        raise DownloadError("transporte terminou sem resposta")
    if time.monotonic() >= deadline:
        close_response(response)
        raise deadline_error()
    return response


def content_length(headers):
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        values = get_all("Content-Length", [])
    else:
        value = headers.get("Content-Length")
        values = [] if value is None else [value]
    if len(values) > 1:
        raise ProtocolError("Content-Length duplicado")
    if not values:
        return None
    value = str(values[0])
    if len(value) > 20 or not value or not all("0" <= char <= "9" for char in value):
        raise ProtocolError("Content-Length invalido")
    return int(value)


def unlink_if_present(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def download_attempt(url, destination, expected_size, expected_sha256,
                     connect_timeout, total_deadline, transfer_timeout, opener):
    attempt_deadline = min(total_deadline, time.monotonic() + transfer_timeout)
    descriptor, part = tempfile.mkstemp(
        prefix=".x86qw-bootstrap-", dir=os.path.dirname(destination),
    )
    if os.name != "nt":
        os.fchmod(descriptor, 0o600)
    output = os.fdopen(descriptor, "wb")
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "x86QW-bootstrap", "Accept-Encoding": "identity"},
        )
        try:
            response_context = open_with_deadline(
                opener,
                request,
                connect_timeout,
                total_deadline,
                attempt_deadline,
                opener.registry,
            )
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit, DownloadError)):
                raise
            raise transport_error(error) from error
        with response_context as response:
            validate_https(response.geturl())
            if getattr(response, "status", 200) != 200:
                raise ProtocolError("resposta HTTP inesperada")
            declared = content_length(response.headers)
            if declared is not None and declared != expected_size:
                raise IntegrityError("Content-Length do instalador divergente")

            digest = hashlib.sha256()
            total = 0
            while True:
                set_read_timeout(response, total_deadline, attempt_deadline)
                reader = getattr(response, "read1", response.read)
                try:
                    block = reader(min(64 * 1024, expected_size - total + 1))
                except BaseException as error:
                    if isinstance(error, (KeyboardInterrupt, SystemExit, DownloadError)):
                        raise
                    raise transport_error(error) from error
                remaining(total_deadline)
                if not block:
                    break
                if not isinstance(block, bytes):
                    raise ProtocolError("resposta remota nao contem bytes")
                total += len(block)
                if total > expected_size:
                    raise IntegrityError("resposta maior que o limite permitido")
                digest.update(block)
                try:
                    written = output.write(block)
                except OSError as error:
                    raise StorageError("falha ao gravar o instalador") from error
                if written != len(block):
                    raise StorageError("gravacao parcial do instalador")

        if total != expected_size:
            raise TransientError("transferencia parcial do instalador")
        if digest.hexdigest() != expected_sha256:
            raise IntegrityError("SHA-256 do instalador divergente")
        try:
            output.flush()
            os.fsync(output.fileno())
            output.close()
            remaining(total_deadline)
            os.replace(part, destination)
            part = None
        except OSError as error:
            raise StorageError("falha ao promover o instalador") from error
    finally:
        if not output.closed:
            try:
                output.close()
            except OSError:
                pass
        if part is not None:
            unlink_if_present(part)


def download_mirrors(urls, destination, expected_size, expected_sha256,
                     connect_timeout, transfer_timeout, retries, retry_max_time):
    if not isinstance(urls, (list, tuple)) or not urls:
        raise PolicyError("ao menos um mirror HTTPS e obrigatorio")
    validated_urls = []
    for url in urls:
        validate_https(url)
        validated_urls.append(url)
    deadline = time.monotonic() + retry_max_time
    registry = ConnectionRegistry()
    opener = urllib.request.build_opener(
        HttpsOnlyRedirectHandler(), DeadlineHttpsHandler(registry),
    )
    opener.registry = registry
    last_error = None
    for url in validated_urls:
        for attempt in range(retries + 1):
            try:
                remaining(deadline)
                download_attempt(
                    url, destination, expected_size, expected_sha256,
                    connect_timeout, deadline, transfer_timeout, opener,
                )
                return url
            except TransientError as error:
                last_error = error
                if attempt >= retries:
                    break
                pause = error.retry_after
                if pause is None:
                    base = min(8.0, 0.5 * (2 ** attempt))
                    pause = base * (0.8 + (0.4 * random.random()))
                if pause >= remaining(deadline):
                    break
                time.sleep(pause)
            except StorageError:
                raise
            except DownloadError as error:
                last_error = error
                break
        print(
            "x86QW: mirror rejeitado por indisponibilidade ou integridade: " + url,
            file=sys.stderr,
        )
    raise DownloadError(str(last_error or "download indisponivel"))


try:
    download_mirrors(
        sys.argv[8:],
        sys.argv[1],
        int(sys.argv[2]),
        sys.argv[3],
        int(sys.argv[4]),
        int(sys.argv[5]),
        int(sys.argv[6]),
        int(sys.argv[7]),
    )
except Exception as error:
    print("x86QW: " + str(error), file=sys.stderr)
    raise SystemExit(1)
'@
    [System.IO.File]::WriteAllText(
      $Downloader,
      $DownloaderSource,
      (New-Object System.Text.UTF8Encoding($false))
    )
    Remove-Item -LiteralPath $Archive -Force -ErrorAction SilentlyContinue
    Write-Host "x86QW: baixando instalador $InstallerVersion..."
    $DownloadArguments = @($PythonRuntime.Arguments) + @(
      $Downloader,
      $Archive,
      [string]$InstallerSize,
      $InstallerSha256,
      [string]$InstallerConnectTimeoutSeconds,
      [string]$InstallerTransferTimeoutSeconds,
      [string]$InstallerRetries,
      [string]$InstallerRetryMaxSeconds
    ) + @($InstallerUrls)
    try {
      & $PythonRuntime.Command @DownloadArguments
      $DownloadExitCode = $LASTEXITCODE
    } catch {
      $DownloadExitCode = 1
    }
    if ($DownloadExitCode -ne 0 -or -not (Test-Path -LiteralPath $Archive -PathType Leaf)) {
      throw "x86QW: nenhum mirror entregou um instalador integro."
    }
    $ArchiveSize = (Get-Item -LiteralPath $Archive).Length
    $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash.ToLowerInvariant()
    if ($ArchiveSize -ne $InstallerSize -or $Actual -ne $InstallerSha256) {
      throw "x86QW: o downloader retornou um instalador divergente."
    }
    Expand-Archive -Path $Archive -DestinationPath $WorkDir
    $Root = Join-Path $WorkDir "x86qw-installer-$InstallerVersion"
    $InstallerArguments = @($PythonRuntime.Arguments) + @((Join-Path $Root "x86qw.pyz"), "--online-only") + @($BootstrapArguments)
    & $PythonRuntime.Command @InstallerArguments
    $InstallerExitCode = $LASTEXITCODE
  } finally {
    Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue
  }

} finally {
  [Console]::OutputEncoding = $PreviousConsoleOutputEncoding
  $OutputEncoding = $PreviousPowerShellOutputEncoding
}

if ($null -ne $InstallerExitCode) {
  if ($InstallerExitCode -ne 0) {
    Write-Error "x86QW: o instalador terminou com codigo $InstallerExitCode." -ErrorAction Continue
  }
  $global:LASTEXITCODE = $InstallerExitCode
}
} @($args)
