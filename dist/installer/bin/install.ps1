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
import os
import random
import socket
import ssl
import string
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
        redirected = super().redirect_request(req, fp, code, msg, headers, target)
        if redirected is not None and req.get_method() == "HEAD":
            redirected.method = "HEAD"
        return redirected


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
            connection.close()

    def clear(self, identity):
        with self.lock:
            self.connections.pop(identity, None)
            self.cancelled.discard(identity)


class DeadlineHttpsHandler(urllib.request.HTTPSHandler):
    def __init__(self, registry):
        super().__init__()
        self.registry = registry

        class RegisteredConnection(http.client.HTTPSConnection):
            def __init__(registered_self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                registry.register(threading.get_ident(), registered_self)

            def connect(registered_self):
                identity = threading.get_ident()
                registry.ensure_active(identity)
                super().connect()
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
    deadline = min(total_deadline, attempt_deadline, connection_deadline)
    socket_timeout = max(0.001, deadline - started)
    lock = threading.Lock()
    state = {}
    cancelled = [False]

    def deadline_error():
        now = time.monotonic()
        if now >= total_deadline:
            return DownloadError("prazo total excedido durante conexao ou headers")
        if now >= attempt_deadline:
            return TransientError(
                "prazo da tentativa excedido durante conexao ou headers"
            )
        return TransientError("prazo de conexao ou headers excedido")

    def worker():
        try:
            try:
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
            registry.clear(threading.get_ident())

    thread = threading.Thread(target=worker, name="x86qw-bootstrap-open")
    thread.daemon = True
    thread.start()
    wait = remaining(deadline)
    try:
        thread.join(wait)
    except BaseException:
        with lock:
            cancelled[0] = True
            response = state.pop("response", None)
        if thread.ident is not None:
            registry.cancel(thread.ident)
        close_response(response)
        raise
    if thread.is_alive():
        with lock:
            cancelled[0] = True
            response = state.pop("response", None)
        if thread.ident is not None:
            registry.cancel(thread.ident)
        close_response(response)
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
    deadline = time.monotonic() + retry_max_time
    registry = ConnectionRegistry()
    opener = urllib.request.build_opener(
        HttpsOnlyRedirectHandler(), DeadlineHttpsHandler(registry),
    )
    opener.registry = registry
    last_error = None
    for url in urls:
        validate_https(url)
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
