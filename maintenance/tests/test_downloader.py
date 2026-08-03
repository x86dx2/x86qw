from __future__ import annotations

import ast
import email.utils
import errno
import hashlib
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import ExitStack
from email.message import Message
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "maintenance/tools"))

import downloader  # noqa: E402


class FakeResponse:
    def __init__(
        self,
        body: bytes = b"",
        *,
        headers: dict[str, str] | None = None,
        url: str = "https://downloads.example.invalid/artifact.zip",
    ) -> None:
        self._body = body
        self._offset = 0
        self.headers = headers or {}
        self._url = url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, size: int) -> bytes:
        if self._offset >= len(self._body):
            return b""
        block = self._body[self._offset : self._offset + size]
        self._offset += len(block)
        return block


class EndlessResponse(FakeResponse):
    def __init__(self) -> None:
        super().__init__()
        self.read_calls = 0

    def read(self, size: int) -> bytes:
        self.read_calls += 1
        return b"x" * size


class RecordingEndlessResponse(EndlessResponse):
    def __init__(self) -> None:
        super().__init__()
        self.requested_sizes: list[int] = []

    def read(self, size: int) -> bytes:
        self.requested_sizes.append(size)
        return super().read(size)


class ReadOneResponse(FakeResponse):
    def __init__(self, body: bytes, clock: "AdvancingClock") -> None:
        super().__init__(body)
        self.clock = clock
        self.read_calls = 0
        self.read1_calls = 0

    def read(self, _size: int) -> bytes:
        self.read_calls += 1
        raise AssertionError("read() não deve agregar várias leituras de socket")

    def read1(self, size: int) -> bytes:
        self.read1_calls += 1
        self.clock.advance(0.6)
        return super().read(min(size, 1))


class AdvancingClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class OutputProxy:
    def __init__(
        self,
        handle: object,
        *,
        write_error: OSError | None = None,
        flush_error: OSError | None = None,
        short_write: bool = False,
        close_error: OSError | None = None,
    ) -> None:
        self._handle = handle
        self._write_error = write_error
        self._flush_error = flush_error
        self._short_write = short_write
        self._close_error = close_error

    @property
    def closed(self) -> bool:
        return bool(getattr(self._handle, "closed"))

    def write(self, data: bytes) -> int:
        if self._write_error is not None:
            raise self._write_error
        written = int(getattr(self._handle, "write")(data))
        return written - 1 if self._short_write and written else written

    def flush(self) -> None:
        if self._flush_error is not None:
            raise self._flush_error
        getattr(self._handle, "flush")()

    def fileno(self) -> int:
        return int(getattr(self._handle, "fileno")())

    def close(self) -> None:
        if self._close_error is not None:
            error = self._close_error
            self._close_error = None
            getattr(self._handle, "close")()
            raise error
        getattr(self._handle, "close")()


class DownloaderTests(unittest.TestCase):
    PAYLOAD = b"x86QW bounded downloader\n"
    URL = "https://downloads.example.invalid/artifact.zip"

    def test_no_python_consumer_bypasses_the_shared_remote_byte_boundary(self) -> None:
        consumers = [ROOT / "maintenance/manage.py"]
        consumers.extend((ROOT / "maintenance/tools").glob("*.py"))
        consumers.extend((ROOT / "dist/installer/bin").glob("*.py"))
        downloader_path = ROOT / "maintenance/tools/downloader.py"
        manager_path = ROOT / "dist/installer/bin/manager.py"
        allowed_module_imports = {
            downloader_path: {"http.client", "urllib.request"},
            manager_path: {"http.client"},
        }
        allowed_external_network = {
            "curl": {ROOT / "maintenance/tools/publish_gitlab_packages.py"},
            "gh": {ROOT / "maintenance/manage.py"},
        }

        def dotted_name(node: ast.AST) -> str | None:
            parts: list[str] = []
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            if not isinstance(node, ast.Name):
                return None
            parts.append(node.id)
            return ".".join(reversed(parts))

        for path in consumers:
            with self.subTest(path=path.relative_to(ROOT)):
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=os.fspath(path))
                aliases: dict[str, str] = {}
                permitted_modules = allowed_module_imports.get(path, set())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for imported in node.names:
                            if imported.name in {"urllib.request", "http.client"}:
                                self.assertIn(imported.name, permitted_modules)
                            if imported.asname:
                                aliases[imported.asname] = imported.name
                    elif isinstance(node, ast.ImportFrom):
                        module = node.module or ""
                        imported_modules = {
                            f"{module}.{item.name}" if module else item.name
                            for item in node.names
                        }
                        if {"urllib.request", "http.client"} & imported_modules:
                            self.assertTrue(
                                ({"urllib.request", "http.client"} & imported_modules)
                                <= permitted_modules
                            )
                        if module in {"urllib.request", "http.client"}:
                            self.assertIn(module, permitted_modules)
                            if path != downloader_path and module == "urllib.request":
                                forbidden = {
                                    "build_opener", "OpenerDirector", "urlopen", "urlretrieve",
                                }
                                self.assertTrue(
                                    forbidden.isdisjoint({item.name for item in node.names})
                                )
                        for imported in node.names:
                            local_name = imported.asname or imported.name
                            aliases[local_name] = f"{module}.{imported.name}" if module else imported.name
                    elif isinstance(node, ast.Call):
                        name = dotted_name(node.func)
                        if name:
                            prefix, separator, suffix = name.partition(".")
                            if prefix in aliases:
                                name = aliases[prefix] + (separator + suffix if separator else "")
                        forbidden_calls = {
                            "urllib.request.urlopen",
                            "urllib.request.urlretrieve",
                            "urllib.request.build_opener",
                        }
                        if path != downloader_path:
                            self.assertNotIn(name, forbidden_calls)
                            if (
                                isinstance(node.func, ast.Attribute)
                                and node.func.attr == "open"
                                and isinstance(node.func.value, ast.Call)
                            ):
                                constructor = dotted_name(node.func.value.func)
                                if constructor:
                                    prefix, separator, suffix = constructor.partition(".")
                                    if prefix in aliases:
                                        constructor = aliases[prefix] + (
                                            separator + suffix if separator else ""
                                        )
                                    self.assertNotEqual(
                                        "urllib.request.OpenerDirector", constructor
                                    )
                        if name in {"importlib.import_module", "__import__"} and node.args:
                            module = node.args[0]
                            if isinstance(module, ast.Constant) and module.value in {
                                "urllib.request", "http.client",
                            }:
                                self.fail(f"import dinâmico de rede fora da allowlist: {path}")
                        for argument in node.args:
                            if not isinstance(argument, (ast.List, ast.Tuple)) or not argument.elts:
                                continue
                            executable = argument.elts[0]
                            if (
                                isinstance(executable, ast.Constant)
                                and isinstance(executable.value, str)
                                and executable.value in allowed_external_network
                            ):
                                self.assertIn(path, allowed_external_network[executable.value])
                    elif isinstance(node, ast.Attribute) and path != downloader_path:
                        name = dotted_name(node)
                        if name:
                            prefix, separator, suffix = name.partition(".")
                            if prefix in aliases:
                                name = aliases[prefix] + (separator + suffix if separator else "")
                            self.assertNotIn(
                                name,
                                {
                                    "urllib.request.urlopen",
                                    "urllib.request.urlretrieve",
                                    "urllib.request.build_opener",
                                    "urllib.request.OpenerDirector",
                                },
                            )

    def pinned(
        self,
        destination: Path,
        *,
        url: str | None = None,
        payload: bytes | None = None,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
        maximum_size: int | None = None,
        deadline_seconds: float = 10,
        attempts: int = 1,
        initial_backoff: float = 0,
        maximum_backoff: float = 10,
    ) -> downloader.PinnedArtifact:
        body = self.PAYLOAD if payload is None else payload
        return downloader.PinnedArtifact(
            url=self.URL if url is None else url,
            destination=destination,
            expected_size=len(body) if expected_size is None else expected_size,
            expected_sha256=(
                hashlib.sha256(body).hexdigest()
                if expected_sha256 is None
                else expected_sha256
            ),
            maximum_size=(
                max(1, len(body)) if maximum_size is None else maximum_size
            ),
            deadline_seconds=deadline_seconds,
            retry=downloader.RetryPolicy(
                attempts=attempts,
                initial_backoff=initial_backoff,
                maximum_backoff=maximum_backoff,
                jitter=0,
            ),
            label="fixture",
        )

    @staticmethod
    def mirror_url(index: int) -> str:
        return f"https://mirror-{index}.example.invalid/artifact.zip"

    def test_safe_url_for_log_redacts_credentials_query_and_fragment(self) -> None:
        sentinel = "X86QW_URL_SECRET_SENTINEL"
        rendered = downloader.safe_url_for_log(
            f"https://operator:{sentinel}@example.invalid:8443/{sentinel}.zip"
            f"?token={sentinel}#{sentinel}"
        )

        self.assertEqual("https://example.invalid:8443/<redigido>", rendered)
        self.assertNotIn(sentinel, rendered)
        self.assertNotIn("operator", rendered)

    def test_safe_url_for_log_replaces_a_control_injected_url(self) -> None:
        sentinel = "X86QW_URL_SECRET_SENTINEL"
        rendered = downloader.safe_url_for_log(
            f"https://example.invalid/file.zip?token={sentinel}\n[ERRO] forged"
        )

        self.assertEqual("<URL HTTPS inválida>", rendered)
        self.assertNotIn(sentinel, rendered)
        self.assertNotIn("\n", rendered)

    @staticmethod
    def response_opener(response: FakeResponse):
        def open_url(_request: object, _timeout: float) -> FakeResponse:
            return response

        return open_url

    @staticmethod
    def assert_no_download_temporaries(test: unittest.TestCase, directory: Path) -> None:
        test.assertEqual([], list(directory.glob(".*.download")))

    def test_pinned_download_promotes_atomically_after_complete_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "artifact.zip"
            destination.write_bytes(b"installed version")
            response = FakeResponse(
                self.PAYLOAD,
                headers={"Content-Length": str(len(self.PAYLOAD))},
            )
            original_replace = os.replace
            observed: dict[str, object] = {}

            def replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                observed["destination_before_replace"] = destination.read_bytes()
                observed["temporary_payload"] = Path(source).read_bytes()
                observed["source"] = Path(source)
                original_replace(source, target)

            with mock.patch.object(downloader.os, "replace", side_effect=replace) as replace_mock:
                result = downloader.download(
                    self.pinned(destination),
                    open_url=self.response_opener(response),
                )

            self.assertEqual(b"installed version", observed["destination_before_replace"])
            self.assertEqual(self.PAYLOAD, observed["temporary_payload"])
            self.assertEqual(self.PAYLOAD, destination.read_bytes())
            self.assertEqual(destination, result.path)
            self.assertEqual(len(self.PAYLOAD), result.size)
            self.assertEqual(hashlib.sha256(self.PAYLOAD).hexdigest(), result.sha256)
            self.assertEqual(1, result.attempts)
            replace_mock.assert_called_once()
            self.assertFalse(Path(observed["source"]).exists())
            self.assert_no_download_temporaries(self, root)

    def test_mirrors_share_one_deadline_across_retries_and_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            first_url = self.mirror_url(1)
            second_url = self.mirror_url(2)
            clock = AdvancingClock()
            timeouts: list[tuple[str, float]] = []

            def open_url(request: object, timeout: float) -> FakeResponse:
                url = str(getattr(request, "full_url"))
                timeouts.append((url, timeout))
                if url == first_url:
                    clock.advance(1)
                    raise downloader.DownloadTransientError("mirror indisponível")
                return FakeResponse(self.PAYLOAD, url=url)

            contracts = (
                self.pinned(
                    destination,
                    url=first_url,
                    deadline_seconds=5,
                    attempts=2,
                    initial_backoff=1,
                    maximum_backoff=1,
                ),
                self.pinned(
                    destination,
                    url=second_url,
                    deadline_seconds=5,
                    attempts=1,
                ),
            )
            result = downloader.download_mirrors(
                contracts,
                open_url=open_url,
                clock=clock,
                sleep=clock.advance,
            )

            self.assertEqual(second_url, result.url)
            self.assertEqual(self.PAYLOAD, destination.read_bytes())
            self.assertEqual(
                [(first_url, 5), (first_url, 3), (second_url, 2)],
                timeouts,
            )

    def test_mirrors_fall_back_after_transport_and_integrity_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            urls = tuple(self.mirror_url(index) for index in range(1, 4))
            failures: list[tuple[int, str, type[downloader.DownloadError]]] = []

            def open_url(request: object, _timeout: float) -> FakeResponse:
                url = str(getattr(request, "full_url"))
                if url == urls[0]:
                    raise downloader.DownloadTransportError("transporte encerrado")
                if url == urls[1]:
                    return FakeResponse(b"x" * len(self.PAYLOAD), url=url)
                return FakeResponse(self.PAYLOAD, url=url)

            result = downloader.download_mirrors(
                tuple(self.pinned(destination, url=url) for url in urls),
                open_url=open_url,
                on_mirror_failure=lambda index, contract, error: failures.append(
                    (index, contract.url, type(error))
                ),
            )

            self.assertEqual(urls[2], result.url)
            self.assertEqual(
                [
                    (1, urls[0], downloader.DownloadTransportError),
                    (2, urls[1], downloader.DownloadIntegrityError),
                ],
                failures,
            )

    def test_mirror_retry_after_larger_than_budget_advances_to_next_origin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            urls = (self.mirror_url(1), self.mirror_url(2))
            opened: list[str] = []
            sleeps: list[float] = []

            def busy_then_healthy(request: object, _timeout: float) -> FakeResponse:
                url = str(getattr(request, "full_url"))
                opened.append(url)
                if url == urls[0]:
                    raise urllib.error.HTTPError(
                        url,
                        503,
                        "Service Unavailable",
                        {"Retry-After": "30"},
                        None,
                    )
                return FakeResponse(self.PAYLOAD, url=url)

            result = downloader.download_mirrors(
                tuple(
                    self.pinned(
                        destination,
                        url=url,
                        deadline_seconds=2,
                        attempts=2,
                    )
                    for url in urls
                ),
                open_url=busy_then_healthy,
                clock=lambda: 0,
                sleep=sleeps.append,
            )

            self.assertEqual(urls[1], result.url)
            self.assertEqual([urls[0], urls[1]], opened)
            self.assertEqual([], sleeps)
            self.assertEqual(self.PAYLOAD, destination.read_bytes())

    def test_mirrors_never_fall_back_after_local_or_contract_failure(self) -> None:
        terminal_failures = (
            downloader.DownloadStorageError("sem espaço"),
            downloader.DownloadPolicyError("política recusada"),
            downloader.DownloadDeadlineError("deadline encerrado"),
            downloader.DownloadProtocolError("headers contraditórios"),
            downloader.DownloadLimitError("resposta acima do limite"),
            downloader.DownloadRedirectError("redirect inseguro"),
        )
        for terminal_error in terminal_failures:
            with (
                self.subTest(error=type(terminal_error).__name__),
                tempfile.TemporaryDirectory() as temporary,
            ):
                destination = Path(temporary) / "artifact.zip"
                urls = (self.mirror_url(1), self.mirror_url(2))
                opened: list[str] = []
                callbacks: list[object] = []

                def open_url(request: object, _timeout: float) -> FakeResponse:
                    opened.append(str(getattr(request, "full_url")))
                    raise terminal_error

                with self.assertRaises(type(terminal_error)):
                    downloader.download_mirrors(
                        tuple(self.pinned(destination, url=url) for url in urls),
                        open_url=open_url,
                        on_mirror_failure=lambda *_arguments: callbacks.append(_arguments),
                    )

                self.assertEqual([urls[0]], opened)
                self.assertEqual([], callbacks)
                self.assertFalse(destination.exists())
                self.assert_no_download_temporaries(self, destination.parent)

    def test_mirrors_do_not_fall_back_when_local_storage_cannot_be_opened(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            contracts = tuple(
                self.pinned(destination, url=self.mirror_url(index))
                for index in (1, 2)
            )
            open_url = mock.Mock()

            with mock.patch.object(
                downloader,
                "_open_temporary",
                side_effect=downloader.DownloadStorageError("destino indisponível"),
            ), self.assertRaises(downloader.DownloadStorageError):
                downloader.download_mirrors(contracts, open_url=open_url)

            open_url.assert_not_called()

    def test_mirrors_validate_every_https_url_before_first_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            contracts = (
                self.pinned(destination, url=self.mirror_url(1)),
                self.pinned(destination, url="http://mirror-2.example.invalid/artifact.zip"),
            )
            open_url = mock.Mock()

            with self.assertRaises(downloader.DownloadPolicyError):
                downloader.download_mirrors(contracts, open_url=open_url)

            open_url.assert_not_called()
            self.assertFalse(destination.exists())

    def test_unpinned_mirrors_must_share_the_exact_expected_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "discovered.bin"
            contracts = (
                downloader.BoundedPayload(
                    url=self.mirror_url(1),
                    destination=destination,
                    expected_size=len(self.PAYLOAD),
                    maximum_size=len(self.PAYLOAD) + 1,
                    deadline_seconds=10,
                ),
                downloader.BoundedPayload(
                    url=self.mirror_url(2),
                    destination=destination,
                    expected_size=len(self.PAYLOAD) + 1,
                    maximum_size=len(self.PAYLOAD) + 1,
                    deadline_seconds=10,
                ),
            )
            open_url = mock.Mock()

            with self.assertRaisesRegex(
                downloader.DownloadPolicyError, "destino, tamanho e limite"
            ):
                downloader.download_mirrors(contracts, open_url=open_url)

            open_url.assert_not_called()
            self.assertFalse(destination.exists())

    def test_unpinned_maintenance_payload_is_bounded_and_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "discovered.bin"
            result = downloader.download(
                downloader.BoundedPayload(
                    url=self.URL,
                    destination=destination,
                    expected_size=len(self.PAYLOAD),
                    maximum_size=len(self.PAYLOAD),
                    deadline_seconds=10,
                    retry=downloader.RetryPolicy(attempts=1),
                ),
                open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
            )
            self.assertEqual(self.PAYLOAD, destination.read_bytes())
            self.assertEqual(hashlib.sha256(self.PAYLOAD).hexdigest(), result.sha256)

    def test_unpinned_payload_requires_a_positive_expected_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "discovered.bin"
            for invalid in (0, -1):
                with self.subTest(expected_size=invalid), self.assertRaises(
                    downloader.DownloadPolicyError
                ):
                    downloader.download(
                        downloader.BoundedPayload(
                            url=self.URL,
                            destination=destination,
                            expected_size=invalid,
                            maximum_size=len(self.PAYLOAD),
                            deadline_seconds=10,
                            retry=downloader.RetryPolicy(attempts=1),
                        ),
                        open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                    )

    def test_unpinned_payload_rejects_divergent_content_length(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "discovered.bin"
            response = FakeResponse(
                self.PAYLOAD,
                headers={"Content-Length": str(len(self.PAYLOAD) - 1)},
            )
            with self.assertRaises(downloader.DownloadIntegrityError):
                downloader.download(
                    downloader.BoundedPayload(
                        url=self.URL,
                        destination=destination,
                        expected_size=len(self.PAYLOAD),
                        maximum_size=len(self.PAYLOAD),
                        deadline_seconds=10,
                        retry=downloader.RetryPolicy(attempts=1),
                    ),
                    open_url=self.response_opener(response),
                )
            self.assertFalse(destination.exists())

    def test_unpinned_payload_rejects_short_body_without_content_length(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "discovered.bin"
            with self.assertRaises(downloader.DownloadTransientError):
                downloader.download(
                    downloader.BoundedPayload(
                        url=self.URL,
                        destination=destination,
                        expected_size=len(self.PAYLOAD),
                        maximum_size=len(self.PAYLOAD),
                        deadline_seconds=10,
                        retry=downloader.RetryPolicy(attempts=1),
                    ),
                    open_url=self.response_opener(FakeResponse(self.PAYLOAD[:-1])),
                )
            self.assertFalse(destination.exists())

    def test_unpinned_payload_stops_one_byte_past_expected_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "discovered.bin"
            response = RecordingEndlessResponse()
            with self.assertRaises(downloader.DownloadIntegrityError):
                downloader.download(
                    downloader.BoundedPayload(
                        url=self.URL,
                        destination=destination,
                        expected_size=1,
                        maximum_size=1024,
                        deadline_seconds=10,
                        retry=downloader.RetryPolicy(attempts=1),
                    ),
                    open_url=self.response_opener(response),
                )
            self.assertEqual([2], response.requested_sizes)
            self.assertFalse(destination.exists())

    @unittest.skipIf(os.name == "nt", "mode POSIX 0600 não se aplica ao Windows")
    def test_temporary_is_private_before_atomic_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "artifact.zip"
            original_replace = os.replace
            observed_modes: list[int] = []

            def replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                observed_modes.append(stat.S_IMODE(Path(source).stat().st_mode))
                original_replace(source, target)

            with mock.patch.object(downloader.os, "replace", side_effect=replace):
                downloader.download(
                    self.pinned(destination),
                    open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                )

            self.assertEqual([0o600], observed_modes)

    @unittest.skipIf(os.name == "nt", "fchmod não se aplica ao Windows")
    def test_fchmod_failure_closes_and_removes_the_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            real_close = os.close
            closed: list[int] = []

            def close(descriptor: int) -> None:
                closed.append(descriptor)
                real_close(descriptor)

            with mock.patch.object(
                downloader.os, "fchmod", side_effect=OSError(errno.EPERM, "denied")
            ), mock.patch.object(downloader.os, "close", side_effect=close):
                with self.assertRaises(downloader.DownloadStorageError):
                    downloader.download(
                        self.pinned(destination),
                        open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                    )

            self.assertEqual(1, len(closed))
            self.assert_no_download_temporaries(self, destination.parent)

    def test_fdopen_failure_closes_and_removes_the_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            real_close = os.close
            closed: list[int] = []

            def close(descriptor: int) -> None:
                closed.append(descriptor)
                real_close(descriptor)

            with mock.patch.object(
                downloader.os, "fdopen", side_effect=OSError(errno.EIO, "fdopen failed")
            ), mock.patch.object(downloader.os, "close", side_effect=close):
                with self.assertRaises(downloader.DownloadStorageError):
                    downloader.download(
                        self.pinned(destination),
                        open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                    )

            self.assertEqual(1, len(closed))
            self.assert_no_download_temporaries(self, destination.parent)

    def test_content_length_above_maximum_is_rejected_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            response = FakeResponse(
                b"x",
                headers={"Content-Length": "100"},
            )
            contract = self.pinned(
                destination,
                payload=b"x",
                expected_size=1,
                maximum_size=1,
            )

            with self.assertRaises(downloader.DownloadLimitError):
                downloader.download(contract, open_url=self.response_opener(response))

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_oversized_metadata_content_length_is_rejected_without_reading(self) -> None:
        response = FakeResponse(
            b"ignored",
            headers={"Content-Length": "1048577"},
        )
        with self.assertRaises(downloader.DownloadLimitError):
            downloader.download(
                downloader.BoundedMetadata(
                    url=self.URL,
                    maximum_size=1024 * 1024,
                    deadline_seconds=10,
                    retry=downloader.RetryPolicy(attempts=1),
                    label="catálogo",
                ),
                open_url=self.response_opener(response),
            )
        self.assertEqual(0, response._offset)

    def test_content_length_must_match_pinned_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            response = FakeResponse(
                self.PAYLOAD,
                headers={"Content-Length": str(len(self.PAYLOAD) - 1)},
            )

            with self.assertRaises(downloader.DownloadIntegrityError):
                downloader.download(
                    self.pinned(destination),
                    open_url=self.response_opener(response),
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_invalid_content_length_is_protocol_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            response = FakeResponse(
                self.PAYLOAD,
                headers={"Content-Length": "12x"},
            )

            with self.assertRaises(downloader.DownloadProtocolError):
                downloader.download(
                    self.pinned(destination),
                    open_url=self.response_opener(response),
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_duplicate_content_length_is_protocol_error(self) -> None:
        expected_length = str(len(self.PAYLOAD))

        class DuplicateHeaders(dict[str, str]):
            def get_all(self, name: str, default: list[str]) -> list[str]:
                if name.casefold() == "content-length":
                    return [expected_length, expected_length]
                return default

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            response = FakeResponse(self.PAYLOAD)
            response.headers = DuplicateHeaders()

            with self.assertRaisesRegex(
                downloader.DownloadProtocolError, "Content-Length duplicado"
            ):
                downloader.download(
                    self.pinned(destination),
                    open_url=self.response_opener(response),
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_unicode_content_length_is_protocol_error(self) -> None:
        response = FakeResponse(self.PAYLOAD, headers={"Content-Length": "²"})
        with self.assertRaises(downloader.DownloadProtocolError):
            downloader.download(
                downloader.BoundedMetadata(
                    url=self.URL,
                    maximum_size=1024,
                    deadline_seconds=10,
                    retry=downloader.RetryPolicy(attempts=1),
                ),
                open_url=self.response_opener(response),
            )

    def test_stream_without_content_length_cannot_exceed_maximum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            body = b"four"
            contract = self.pinned(
                destination,
                payload=body,
                expected_size=3,
                maximum_size=3,
            )

            with self.assertRaises(downloader.DownloadLimitError):
                downloader.download(
                    contract,
                    open_url=self.response_opener(FakeResponse(body)),
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_pinned_stream_reads_only_one_byte_beyond_expected_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            response = RecordingEndlessResponse()
            with self.assertRaises(downloader.DownloadIntegrityError):
                downloader.download(
                    self.pinned(
                        destination,
                        payload=b"x",
                        expected_size=1,
                        maximum_size=1024 * 1024,
                    ),
                    open_url=self.response_opener(response),
                )
            self.assertEqual([2], response.requested_sizes)

    def test_unbounded_metadata_stream_stops_immediately_after_limit(self) -> None:
        response = EndlessResponse()
        with self.assertRaises(downloader.DownloadLimitError):
            downloader.download(
                downloader.BoundedMetadata(
                    url=self.URL,
                    maximum_size=32,
                    deadline_seconds=10,
                    retry=downloader.RetryPolicy(attempts=1),
                    label="catálogo",
                ),
                open_url=self.response_opener(response),
            )
        self.assertEqual(1, response.read_calls)

    def test_partial_metadata_body_must_match_declared_content_length(self) -> None:
        response = FakeResponse(
            b"short",
            headers={"Content-Length": "20"},
        )
        with self.assertRaises(downloader.DownloadTransientError):
            downloader.download(
                downloader.BoundedMetadata(
                    url=self.URL,
                    maximum_size=1024,
                    deadline_seconds=10,
                    retry=downloader.RetryPolicy(attempts=1),
                    label="catálogo",
                ),
                open_url=self.response_opener(response),
            )

    def test_short_partial_response_is_rejected_and_destination_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            response = FakeResponse(
                self.PAYLOAD[:-3],
                headers={"Content-Length": str(len(self.PAYLOAD))},
            )

            with self.assertRaises(downloader.DownloadTransientError):
                downloader.download(
                    self.pinned(destination),
                    open_url=self.response_opener(response),
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_partial_response_is_retried_with_a_fresh_temporary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            opens = 0

            def partial_then_complete(_request: object, _timeout: float) -> FakeResponse:
                nonlocal opens
                opens += 1
                body = self.PAYLOAD[:-3] if opens == 1 else self.PAYLOAD
                return FakeResponse(
                    body,
                    headers={"Content-Length": str(len(self.PAYLOAD))},
                )

            result = downloader.download(
                self.pinned(
                    destination,
                    attempts=2,
                    initial_backoff=0,
                    maximum_backoff=0,
                ),
                open_url=partial_then_complete,
                sleep=lambda _delay: None,
            )

            self.assertEqual(2, result.attempts)
            self.assertEqual(self.PAYLOAD, destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_wrong_sha256_is_rejected_and_destination_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            contract = self.pinned(destination, expected_sha256="0" * 64)

            with self.assertRaises(downloader.DownloadIntegrityError):
                downloader.download(
                    contract,
                    open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_temporary_cleanup_failure_is_reported_without_changing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            contract = self.pinned(destination, expected_sha256="0" * 64)

            with mock.patch.object(
                downloader.Path,
                "unlink",
                side_effect=OSError(errno.EPERM, "unlink denied"),
            ):
                with self.assertRaisesRegex(
                    downloader.DownloadStorageError, "limpeza do temporário também falhou"
                ):
                    downloader.download(
                        contract,
                        open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                    )

            self.assertEqual(b"preserve", destination.read_bytes())

    def test_total_deadline_expires_during_response_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            calls = 0

            def clock() -> float:
                nonlocal calls
                calls += 1
                return 0.0 if calls <= 3 else 2.0

            with self.assertRaises(downloader.DownloadDeadlineError):
                downloader.download(
                    self.pinned(destination, deadline_seconds=1),
                    open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                    clock=clock,
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_deadline_is_rechecked_after_fsync_before_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            clock = AdvancingClock()
            original_fsync = os.fsync

            def slow_fsync(descriptor: int) -> None:
                original_fsync(descriptor)
                clock.advance(2)

            with mock.patch.object(
                downloader.os, "fsync", side_effect=slow_fsync,
            ), self.assertRaises(downloader.DownloadDeadlineError):
                downloader.download(
                    self.pinned(destination, deadline_seconds=1),
                    open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                    clock=clock,
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_opener_receives_remaining_deadline_as_timeout_keyword(self) -> None:
        opener = downloader.build_https_opener()
        with mock.patch.object(opener, "open", return_value=FakeResponse(b"metadata")) as opened:
            result = downloader.download(
                downloader.BoundedMetadata(
                    url=self.URL,
                    maximum_size=32,
                    deadline_seconds=7,
                    retry=downloader.RetryPolicy(attempts=1),
                ),
                opener=opener,
                clock=lambda: 10,
            )
        self.assertEqual(b"metadata", result.data)
        request = opened.call_args.args[0]
        self.assertEqual("identity", request.get_header("Accept-encoding"))
        self.assertEqual({"timeout": 7}, opened.call_args.kwargs)

    def test_open_and_header_phase_cannot_outlive_total_deadline(self) -> None:
        registered = threading.Event()
        release = threading.Event()
        clock = AdvancingClock()
        opener = downloader.build_https_opener()
        registry = getattr(opener, "_x86qw_connection_registry")

        class CancelConnection:
            def close(self) -> None:
                release.set()

        def blocked_open(_request: object, *, timeout: float) -> FakeResponse:
            self.assertGreater(timeout, 0)
            registry.register(threading.get_ident(), CancelConnection())
            registered.set()
            release.wait(2)
            return FakeResponse(b"late")

        real_thread_start = downloader.threading.Thread.start
        real_thread_join = downloader.threading.Thread.join
        controller_joins: list[float | None] = []

        def start_then_expire_deadline(thread: threading.Thread) -> None:
            real_thread_start(thread)
            if thread.name == "x86qw-download-open":
                self.assertTrue(registered.wait(1))
                clock.advance(2)

        def record_controller_join(
            thread: threading.Thread, timeout: float | None = None,
        ) -> None:
            if thread.name == "x86qw-download-open":
                controller_joins.append(timeout)
                return
            real_thread_join(thread, timeout)

        with mock.patch.object(
            opener, "open", side_effect=blocked_open,
        ), mock.patch.object(
            downloader.threading.Thread, "start", start_then_expire_deadline,
        ), mock.patch.object(
            downloader.threading.Thread, "join", record_controller_join,
        ):
            with self.assertRaises(downloader.DownloadDeadlineError):
                downloader.download(
                    downloader.BoundedMetadata(
                        url=self.URL,
                        maximum_size=1024,
                        deadline_seconds=1,
                        retry=downloader.RetryPolicy(attempts=1),
                    ),
                    opener=opener,
                    clock=clock,
                )
        self.assertTrue(release.is_set())
        self.assertEqual([], controller_joins)
        for _ in range(50):
            if not any(
                thread.name == "x86qw-download-open" and thread.is_alive()
                for thread in threading.enumerate()
            ):
                break
            time.sleep(0.01)
        self.assertFalse(any(
            thread.name == "x86qw-download-open" and thread.is_alive()
            for thread in threading.enumerate()
        ))

    def test_transient_timeout_is_retried_but_http_404_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "artifact.zip"
            opens = 0
            sleeps: list[float] = []

            def transient_then_success(_request: object, _timeout: float) -> FakeResponse:
                nonlocal opens
                opens += 1
                if opens == 1:
                    raise urllib.error.URLError(socket.timeout("timed out"))
                return FakeResponse(self.PAYLOAD)

            result = downloader.download(
                self.pinned(
                    destination,
                    attempts=2,
                    initial_backoff=0.25,
                    maximum_backoff=1,
                ),
                open_url=transient_then_success,
                clock=lambda: 0,
                sleep=sleeps.append,
            )
            self.assertEqual(2, opens)
            self.assertEqual(2, result.attempts)
            self.assertEqual([0.25], sleeps)
            self.assertEqual(self.PAYLOAD, destination.read_bytes())

            destination.write_bytes(b"preserve")
            not_found_opens = 0

            def not_found(_request: object, _timeout: float) -> FakeResponse:
                nonlocal not_found_opens
                not_found_opens += 1
                error = urllib.error.HTTPError(
                    self.URL, 404, "Not Found", {}, None,
                )
                error.close()
                raise error

            with self.assertRaises(downloader.DownloadTransportError):
                downloader.download(
                    self.pinned(destination, attempts=3),
                    open_url=not_found,
                    clock=lambda: 0,
                    sleep=lambda _delay: self.fail("HTTP 404 não deve aguardar retry"),
                )
            self.assertEqual(1, not_found_opens)
            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, root)

    def test_chunked_style_reader_returns_to_monotonic_deadline_between_reads(self) -> None:
        clock = AdvancingClock()
        response = ReadOneResponse(b"metadata", clock)

        with self.assertRaises(downloader.DownloadDeadlineError):
            downloader.download(
                downloader.BoundedMetadata(
                    url=self.URL,
                    maximum_size=1024,
                    deadline_seconds=1,
                    retry=downloader.RetryPolicy(attempts=1),
                ),
                open_url=self.response_opener(response),
                clock=clock,
            )

        self.assertEqual(0, response.read_calls)
        self.assertEqual(2, response.read1_calls)

    def test_blocking_socket_read_receives_timeout_and_terminates_promptly(self) -> None:
        reader_socket, writer_socket = socket.socketpair()
        applied_timeouts: list[float] = []

        class RecordingSocket:
            def settimeout(self, value: float) -> None:
                applied_timeouts.append(value)
                reader_socket.settimeout(value)

            def recv(self, size: int) -> bytes:
                return reader_socket.recv(size)

        class BlockingSocketResponse(FakeResponse):
            def __init__(self) -> None:
                super().__init__()
                self._sock = RecordingSocket()

            def read1(self, size: int) -> bytes:
                return self._sock.recv(size)

        started = time.monotonic()
        try:
            with self.assertRaises(downloader.DownloadTransientError):
                downloader.download(
                    downloader.BoundedMetadata(
                        url=self.URL,
                        maximum_size=1024,
                        deadline_seconds=0.1,
                        retry=downloader.RetryPolicy(attempts=1),
                    ),
                    open_url=self.response_opener(BlockingSocketResponse()),
                )
        finally:
            reader_socket.close()
            writer_socket.close()

        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(1, len(applied_timeouts))
        self.assertGreater(applied_timeouts[0], 0)
        self.assertLessEqual(applied_timeouts[0], 0.1)

    def test_http_error_response_is_closed_before_returning(self) -> None:
        body = downloader.io.BytesIO(b"error")

        def not_found(_request: object, _timeout: float) -> FakeResponse:
            raise urllib.error.HTTPError(self.URL, 404, "Not Found", {}, body)

        with self.assertRaises(downloader.DownloadHTTPError):
            downloader.download(
                downloader.BoundedMetadata(
                    url=self.URL,
                    maximum_size=1024,
                    deadline_seconds=10,
                    retry=downloader.RetryPolicy(attempts=1),
                ),
                open_url=not_found,
            )
        self.assertTrue(body.closed)

    def test_http_error_close_failure_does_not_mask_the_typed_error(self) -> None:
        error = urllib.error.HTTPError(self.URL, 404, "Not Found", {}, None)
        original_close = error.close

        def fail_close() -> None:
            original_close()
            raise OSError(errno.EIO, "close failed")

        error.close = mock.Mock(side_effect=fail_close)

        def not_found(_request: object, _timeout: float) -> FakeResponse:
            raise error

        with self.assertRaises(downloader.DownloadHTTPError) as raised:
            downloader.download(
                downloader.BoundedMetadata(
                    url=self.URL,
                    maximum_size=1024,
                    deadline_seconds=10,
                    retry=downloader.RetryPolicy(attempts=1),
                ),
                open_url=not_found,
            )
        self.assertEqual(404, raised.exception.status)
        error.close.assert_called_once_with()

    def test_temporary_dns_failure_is_retried(self) -> None:
        opens = 0

        def dns_then_success(_request: object, _timeout: float) -> FakeResponse:
            nonlocal opens
            opens += 1
            if opens == 1:
                raise urllib.error.URLError(
                    socket.gaierror(socket.EAI_AGAIN, "temporary failure")
                )
            return FakeResponse(b"metadata")

        result = downloader.download(
            downloader.BoundedMetadata(
                url=self.URL,
                maximum_size=1024,
                deadline_seconds=10,
                retry=downloader.RetryPolicy(
                    attempts=2,
                    initial_backoff=0,
                    maximum_backoff=0,
                    jitter=0,
                ),
            ),
            open_url=dns_then_success,
            clock=lambda: 0,
            sleep=lambda _delay: None,
        )
        self.assertEqual(2, opens)
        self.assertEqual(b"metadata", result.data)

    def test_retry_after_controls_transient_http_retry_delay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            opens = 0
            sleeps: list[float] = []

            def busy_then_success(_request: object, _timeout: float) -> FakeResponse:
                nonlocal opens
                opens += 1
                if opens == 1:
                    error = urllib.error.HTTPError(
                        self.URL,
                        503,
                        "Service Unavailable",
                        {"retry-after": "3"},
                        None,
                    )
                    error.close()
                    raise error
                return FakeResponse(self.PAYLOAD)

            result = downloader.download(
                self.pinned(
                    destination,
                    attempts=2,
                    initial_backoff=0.25,
                    maximum_backoff=10,
                ),
                open_url=busy_then_success,
                clock=lambda: 0,
                sleep=sleeps.append,
            )

            self.assertEqual(2, result.attempts)
            self.assertEqual([3.0], sleeps)

    def test_retry_after_http_date_controls_transient_retry_delay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            opens = 0
            sleeps: list[float] = []
            now = 1_000.0
            retry_at = email.utils.formatdate(now + 5, usegmt=True)

            def busy_then_success(_request: object, _timeout: float) -> FakeResponse:
                nonlocal opens
                opens += 1
                if opens == 1:
                    raise urllib.error.HTTPError(
                        self.URL,
                        503,
                        "Service Unavailable",
                        {"Retry-After": retry_at},
                        None,
                    )
                return FakeResponse(self.PAYLOAD)

            result = downloader.download(
                self.pinned(
                    destination,
                    attempts=2,
                    initial_backoff=0.25,
                    maximum_backoff=10,
                ),
                open_url=busy_then_success,
                clock=lambda: 0,
                wall_clock=lambda: now,
                sleep=sleeps.append,
            )

            self.assertEqual(2, result.attempts)
            self.assertEqual([5.0], sleeps)

    def test_invalid_retry_after_falls_back_to_exponential_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            opens = 0
            sleeps: list[float] = []

            def busy_then_success(_request: object, _timeout: float) -> FakeResponse:
                nonlocal opens
                opens += 1
                if opens == 1:
                    raise urllib.error.HTTPError(
                        self.URL,
                        503,
                        "Service Unavailable",
                        {"Retry-After": "not-a-date"},
                        None,
                    )
                return FakeResponse(self.PAYLOAD)

            result = downloader.download(
                self.pinned(
                    destination,
                    attempts=2,
                    initial_backoff=0.5,
                    maximum_backoff=10,
                ),
                open_url=busy_then_success,
                clock=lambda: 0,
                sleep=sleeps.append,
            )

            self.assertEqual(2, result.attempts)
            self.assertEqual([0.5], sleeps)

    def test_retry_after_larger_than_total_deadline_fails_without_sleeping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            sleeps: list[float] = []

            def busy(_request: object, _timeout: float) -> FakeResponse:
                raise urllib.error.HTTPError(
                    self.URL,
                    503,
                    "Service Unavailable",
                    {"Retry-After": "3"},
                    None,
                )

            with self.assertRaises(downloader.DownloadRetryBudgetError):
                downloader.download(
                    self.pinned(
                        destination,
                        deadline_seconds=2,
                        attempts=2,
                        initial_backoff=0.5,
                        maximum_backoff=10,
                    ),
                    open_url=busy,
                    clock=lambda: 0,
                    sleep=sleeps.append,
                )

            self.assertEqual([], sleeps)
            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_exponential_backoff_and_jitter_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            opens = 0
            sleeps: list[float] = []
            random_values = iter((0.0, 0.5, 1.0))

            def transient_then_success(_request: object, _timeout: float) -> FakeResponse:
                nonlocal opens
                opens += 1
                if opens <= 3:
                    raise urllib.error.URLError(socket.timeout("timed out"))
                return FakeResponse(self.PAYLOAD)

            result = downloader.download(
                downloader.PinnedArtifact(
                    url=self.URL,
                    destination=destination,
                    expected_size=len(self.PAYLOAD),
                    expected_sha256=hashlib.sha256(self.PAYLOAD).hexdigest(),
                    maximum_size=len(self.PAYLOAD),
                    deadline_seconds=100,
                    retry=downloader.RetryPolicy(
                        attempts=4,
                        initial_backoff=1,
                        maximum_backoff=8,
                        jitter=0.25,
                    ),
                ),
                open_url=transient_then_success,
                clock=lambda: 0,
                sleep=sleeps.append,
                random_value=lambda: next(random_values),
            )

            self.assertEqual(4, result.attempts)
            self.assertEqual(3, len(sleeps))
            for actual, expected in zip(sleeps, (0.75, 2.0, 5.0), strict=True):
                self.assertAlmostEqual(expected, actual)

    def test_http_status_retry_matrix_distinguishes_transient_and_permanent(self) -> None:
        for status in sorted(downloader.TRANSIENT_HTTP_STATUSES):
            with self.subTest(kind="transient", status=status):
                opens = 0

                def transient_then_success(
                    _request: object,
                    _timeout: float,
                    *,
                    response_status: int = status,
                ) -> FakeResponse:
                    nonlocal opens
                    opens += 1
                    if opens == 1:
                        raise urllib.error.HTTPError(
                            self.URL, response_status, "transient", {}, None,
                        )
                    return FakeResponse(b"metadata")

                result = downloader.download(
                    downloader.BoundedMetadata(
                        url=self.URL,
                        maximum_size=1024,
                        deadline_seconds=10,
                        retry=downloader.RetryPolicy(
                            attempts=2,
                            initial_backoff=0,
                            maximum_backoff=0,
                            jitter=0,
                        ),
                    ),
                    open_url=transient_then_success,
                    clock=lambda: 0,
                    sleep=lambda _delay: None,
                )
                self.assertEqual(2, result.attempts)
                self.assertEqual(2, opens)

        for status in (400, 401, 403, 404, 409, 418, 501):
            with self.subTest(kind="permanent", status=status):
                opens = 0

                def permanent(
                    _request: object,
                    _timeout: float,
                    *,
                    response_status: int = status,
                ) -> FakeResponse:
                    nonlocal opens
                    opens += 1
                    raise urllib.error.HTTPError(
                        self.URL, response_status, "permanent", {}, None,
                    )

                with self.assertRaises(downloader.DownloadHTTPError) as raised:
                    downloader.download(
                        downloader.BoundedMetadata(
                            url=self.URL,
                            maximum_size=1024,
                            deadline_seconds=10,
                            retry=downloader.RetryPolicy(attempts=3),
                        ),
                        open_url=permanent,
                        clock=lambda: 0,
                        sleep=lambda _delay: self.fail(
                            f"HTTP {status} permanente não deve aguardar retry"
                        ),
                    )
                self.assertEqual(status, raised.exception.status)
                self.assertEqual(1, opens)

    def test_retry_callback_reports_next_attempt_and_delay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            opens = 0
            notices: list[tuple[int, str, float]] = []

            def transient_then_success(_request: object, _timeout: float) -> FakeResponse:
                nonlocal opens
                opens += 1
                if opens == 1:
                    raise urllib.error.URLError(socket.timeout("timed out"))
                return FakeResponse(self.PAYLOAD)

            downloader.download(
                self.pinned(
                    destination,
                    attempts=2,
                    initial_backoff=0.25,
                    maximum_backoff=1,
                ),
                open_url=transient_then_success,
                clock=lambda: 0,
                sleep=lambda _delay: None,
                on_retry=lambda attempt, error, delay: notices.append(
                    (attempt, str(error), delay)
                ),
            )

            self.assertEqual([(2, "Falha temporária de rede: timed out", 0.25)], notices)

    def test_http_redirect_is_rejected_before_destination_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "artifact.zip"
            destination.write_bytes(b"preserve")
            response = FakeResponse(
                self.PAYLOAD,
                url="http://downloads.example.invalid/artifact.zip",
            )

            with self.assertRaises(downloader.DownloadRedirectError):
                downloader.download(
                    self.pinned(destination),
                    open_url=self.response_opener(response),
                )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assert_no_download_temporaries(self, destination.parent)

    def test_https_redirects_close_intermediate_response_without_reading_body(self) -> None:
        redirect_statuses = (301, 302, 303, 307, 308)

        class IntermediateResponse:
            def __init__(self) -> None:
                self.close_calls = 0

            def read(self, *_args: object, **_kwargs: object) -> bytes:
                raise AssertionError("o corpo intermediário não pode ser lido")

            def close(self) -> None:
                self.close_calls += 1

        class ParentOpener:
            def __init__(self) -> None:
                self.calls: list[tuple[urllib.request.Request, float]] = []

            def open(self, request: urllib.request.Request, *, timeout: float) -> object:
                self.calls.append((request, timeout))
                return request

        for index, status in enumerate(redirect_statuses):
            with self.subTest(status=status):
                handler = downloader.HTTPSOnlyRedirectHandler()
                parent = ParentOpener()
                handler.parent = parent
                request = downloader.urllib.request.Request(self.URL)
                request.timeout = 7.5
                response = IntermediateResponse()
                headers = Message()
                header_name = "Location" if index % 2 == 0 else "URI"
                headers[header_name] = f"/redirected/{status}"

                result = getattr(handler, f"http_error_{status}")(
                    request, response, status, "Redirect", headers,
                )

                self.assertEqual(1, response.close_calls)
                self.assertEqual(1, len(parent.calls))
                redirected, timeout = parent.calls[0]
                self.assertIs(result, redirected)
                self.assertEqual(
                    f"https://downloads.example.invalid/redirected/{status}",
                    redirected.full_url,
                )
                self.assertEqual(7.5, timeout)

    def test_redirect_loop_and_http_downgrade_close_without_reading_body(self) -> None:
        class IntermediateResponse:
            def __init__(self) -> None:
                self.close_calls = 0

            def read(self, *_args: object, **_kwargs: object) -> bytes:
                raise AssertionError("o corpo intermediário não pode ser lido")

            def close(self) -> None:
                self.close_calls += 1

        handler = downloader.HTTPSOnlyRedirectHandler()
        parent = mock.Mock()
        handler.parent = parent

        for target, expected_error in (
            (
                "https://downloads.example.invalid/loop",
                downloader.DownloadRedirectError,
            ),
            ("http://downloads.example.invalid/downgrade", downloader.DownloadPolicyError),
        ):
            with self.subTest(target=target):
                request = downloader.urllib.request.Request(self.URL)
                request.timeout = 7.5
                if target.startswith("https:"):
                    request.redirect_dict = {target: handler.max_repeats}
                response = IntermediateResponse()
                headers = Message()
                headers["Location"] = target

                with self.assertRaises(expected_error):
                    handler.http_error_302(
                        request, response, 302, "Redirect", headers,
                    )

                self.assertEqual(1, response.close_calls)
        parent.open.assert_not_called()

    def test_redirect_handler_rejects_http_before_following_it(self) -> None:
        handler = downloader.HTTPSOnlyRedirectHandler()
        request = downloader.urllib.request.Request(self.URL)
        with mock.patch.object(
            downloader.urllib.request.HTTPRedirectHandler,
            "redirect_request",
            side_effect=AssertionError("redirect HTTP não pode ser seguido"),
        ) as parent_redirect:
            with self.assertRaises(downloader.DownloadPolicyError):
                handler.redirect_request(
                    request,
                    None,
                    302,
                    "Found",
                    {},
                    "http://downloads.example.invalid/artifact.zip",
                )
        parent_redirect.assert_not_called()

    def test_redirect_handler_preserves_head_on_python_310(self) -> None:
        handler = downloader.HTTPSOnlyRedirectHandler()
        request = downloader.urllib.request.Request(self.URL, method="HEAD")
        redirected = downloader.urllib.request.Request(
            "https://downloads.example.invalid/final", method="GET"
        )
        with mock.patch.object(
            downloader.urllib.request.HTTPRedirectHandler,
            "redirect_request",
            return_value=redirected,
        ):
            result = handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://downloads.example.invalid/final",
            )
        self.assertIsNotNone(result)
        self.assertEqual("HEAD", result.get_method())

    def test_cross_origin_redirect_rejects_private_headers(self) -> None:
        handler = downloader.HTTPSOnlyRedirectHandler()
        request = downloader.urllib.request.Request(
            self.URL,
            headers={"Authorization": "Bearer secret", "User-Agent": "x86qw-test"},
        )
        with mock.patch.object(
            downloader.urllib.request.HTTPRedirectHandler,
            "redirect_request",
            side_effect=AssertionError("headers privados não podem sair da origem"),
        ) as parent_redirect:
            with self.assertRaises(downloader.DownloadRedirectError):
                handler.redirect_request(
                    request,
                    None,
                    302,
                    "Found",
                    {},
                    "https://other.example.invalid/artifact.zip",
                )
        parent_redirect.assert_not_called()

    def test_cross_origin_redirect_allows_only_public_transport_headers(self) -> None:
        handler = downloader.HTTPSOnlyRedirectHandler()
        request = downloader.urllib.request.Request(
            self.URL,
            headers={"Accept": "application/octet-stream", "User-Agent": "x86qw-test"},
        )
        redirected = downloader.urllib.request.Request(
            "https://other.example.invalid/artifact.zip"
        )
        with mock.patch.object(
            downloader.urllib.request.HTTPRedirectHandler,
            "redirect_request",
            return_value=redirected,
        ) as parent_redirect:
            result = handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                redirected.full_url,
            )
        self.assertIs(result, redirected)
        parent_redirect.assert_called_once()

    def test_contract_rejects_non_finite_or_unsafe_policy_values(self) -> None:
        invalid_policies = (
            lambda: downloader.RetryPolicy(attempts=True),
            lambda: downloader.RetryPolicy(initial_backoff=float("nan")),
            lambda: downloader.RetryPolicy(maximum_backoff=float("inf")),
            lambda: downloader.RetryPolicy(jitter=float("nan")),
            lambda: downloader.RetryPolicy(transient_statuses=frozenset({404})),
        )
        for build in invalid_policies:
            with self.subTest(build=build), self.assertRaises(downloader.DownloadPolicyError):
                build()

        for maximum, deadline in ((True, 1), (1024, float("nan"))):
            with self.subTest(maximum=maximum, deadline=deadline), self.assertRaises(
                downloader.DownloadPolicyError
            ):
                downloader.download(
                    downloader.BoundedMetadata(
                        url=self.URL,
                        maximum_size=maximum,
                        deadline_seconds=deadline,
                    ),
                    open_url=self.response_opener(FakeResponse()),
                )

    def _assert_output_failure_preserves_destination(
        self,
        *,
        write_error: OSError | None = None,
        flush_error: OSError | None = None,
        short_write: bool = False,
        close_error: OSError | None = None,
        fsync_error: OSError | None = None,
        replace_error: OSError | None = None,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "artifact.zip"
            destination.write_bytes(b"preserve")
            temporary_path = root / ".controlled.download"
            handle = temporary_path.open("wb")
            output = OutputProxy(
                handle,
                write_error=write_error,
                flush_error=flush_error,
                short_write=short_write,
                close_error=close_error,
            )

            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(
                    downloader,
                    "_open_temporary",
                    return_value=(output, temporary_path),
                ))
                if fsync_error is not None:
                    stack.enter_context(mock.patch.object(
                        downloader.os, "fsync", side_effect=fsync_error,
                    ))
                if replace_error is not None:
                    stack.enter_context(mock.patch.object(
                        downloader.os, "replace", side_effect=replace_error,
                    ))
                with self.assertRaises(downloader.DownloadStorageError):
                    downloader.download(
                        self.pinned(destination),
                        open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                    )

            self.assertEqual(b"preserve", destination.read_bytes())
            self.assertFalse(temporary_path.exists())

    def test_successful_promotion_orders_flush_fsync_close_and_replace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "artifact.zip"
            destination.write_bytes(b"preserve")
            temporary_path = root / ".controlled.download"
            handle = temporary_path.open("wb")
            events: list[str] = []

            class OrderedOutput(OutputProxy):
                def flush(self) -> None:
                    events.append("flush")
                    super().flush()

                def close(self) -> None:
                    events.append("close")
                    super().close()

            output = OrderedOutput(handle)
            original_replace = os.replace

            def fsync(_descriptor: int) -> None:
                events.append("fsync")

            def replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                events.append("replace")
                original_replace(source, target)

            with mock.patch.object(
                downloader,
                "_open_temporary",
                return_value=(output, temporary_path),
            ), mock.patch.object(
                downloader.os,
                "fsync",
                side_effect=fsync,
            ), mock.patch.object(
                downloader.os,
                "replace",
                side_effect=replace,
            ):
                downloader.download(
                    self.pinned(destination),
                    open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                )

            self.assertEqual(["flush", "fsync", "close", "replace"], events)
            self.assertEqual(self.PAYLOAD, destination.read_bytes())
            self.assertFalse(temporary_path.exists())

    def test_mkstemp_permission_and_disk_full_failures_preserve_destination(self) -> None:
        for error_number, message in (
            (errno.EACCES, "permission denied"),
            (errno.ENOSPC, "no space left"),
        ):
            with self.subTest(errno=error_number), tempfile.TemporaryDirectory() as temporary:
                destination = Path(temporary) / "artifact.zip"
                destination.write_bytes(b"preserve")

                with mock.patch.object(
                    downloader.tempfile,
                    "mkstemp",
                    side_effect=OSError(error_number, message),
                ), self.assertRaises(downloader.DownloadStorageError):
                    downloader.download(
                        self.pinned(destination),
                        open_url=self.response_opener(FakeResponse(self.PAYLOAD)),
                    )

                self.assertEqual(b"preserve", destination.read_bytes())
                self.assert_no_download_temporaries(self, destination.parent)

    def test_enospc_while_writing_preserves_destination(self) -> None:
        self._assert_output_failure_preserves_destination(
            write_error=OSError(errno.ENOSPC, "no space left"),
        )

    def test_short_write_preserves_destination(self) -> None:
        self._assert_output_failure_preserves_destination(short_write=True)

    def test_close_failure_is_typed_and_preserves_destination(self) -> None:
        self._assert_output_failure_preserves_destination(
            close_error=OSError(errno.EIO, "close failed"),
        )

    def test_flush_failure_preserves_destination(self) -> None:
        self._assert_output_failure_preserves_destination(
            flush_error=OSError(errno.EIO, "flush failed"),
        )

    def test_fsync_failure_preserves_destination(self) -> None:
        self._assert_output_failure_preserves_destination(
            fsync_error=OSError(errno.EIO, "fsync failed"),
        )

    def test_replace_failure_preserves_destination(self) -> None:
        self._assert_output_failure_preserves_destination(
            replace_error=OSError(errno.EIO, "replace failed"),
        )


if __name__ == "__main__":
    unittest.main()
