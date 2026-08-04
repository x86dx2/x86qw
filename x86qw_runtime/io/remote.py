"""HTTPS download composition and user-facing diagnostics for installed clients."""

from __future__ import annotations

import time
import urllib.parse
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Protocol

from ..errors import InstallerError
from .downloader import (
    BoundedMetadata,
    DownloadError,
    DownloadHTTPError,
    DownloadPolicyError,
    DownloadResult,
    PinnedArtifact,
    RetryPolicy,
    download as bounded_download,
    download_mirrors as bounded_download_mirrors,
    safe_url_for_log,
    validate_https_url as validate_download_url,
)


class Reporter(Protocol):
    def detail(self, message: str) -> None: ...
    def warning(self, message: str) -> None: ...
    def download_progress(
        self, received: int, total: int | None, *, done: bool = False,
    ) -> None: ...


DownloadOne = Callable[..., DownloadResult]
DownloadMany = Callable[..., DownloadResult]


def validate_https_url(url: object, label: str) -> urllib.parse.SplitResult:
    try:
        return validate_download_url(url, label)
    except DownloadPolicyError as error:
        raise InstallerError(str(error)) from error


def https_url_filename(url: object, label: str) -> str:
    parsed = validate_https_url(url, label)
    filename = PurePosixPath(urllib.parse.unquote(parsed.path)).name
    if not filename or filename in (".", ".."):
        raise InstallerError(f"{label} não identifica um arquivo")
    return filename


class RemoteClient:
    def __init__(
        self,
        reporter: Reporter,
        *,
        download_one: DownloadOne | None = None,
        download_many: DownloadMany | None = None,
    ) -> None:
        self.reporter = reporter
        self.download_one = download_one or bounded_download
        self.download_many = download_many or bounded_download_mirrors

    @staticmethod
    def _contract(
        url: str,
        destination: Path | None,
        headers: dict[str, str] | None,
        *,
        expected_size: int | None,
        expected_sha256: str | None,
        maximum_size: int | None,
        timeout: float,
        attempts: int,
    ) -> PinnedArtifact | BoundedMetadata:
        retry = RetryPolicy(attempts=attempts)
        request_headers = {"User-Agent": "x86-qw-installer/1", **(headers or {})}
        if destination is None:
            if maximum_size is None:
                raise InstallerError(
                    "O download de metadados exige um limite máximo explícito."
                )
            return BoundedMetadata(
                url=url,
                maximum_size=maximum_size,
                deadline_seconds=timeout,
                retry=retry,
                headers=request_headers,
                label="metadados x86QW",
            )
        if expected_size is None or expected_sha256 is None:
            raise InstallerError(
                "O download de um artefato exige tamanho e SHA-256 esperados."
            )
        return PinnedArtifact(
            url=url,
            destination=destination,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
            maximum_size=maximum_size if maximum_size is not None else expected_size,
            deadline_seconds=timeout,
            retry=retry,
            headers=request_headers,
            label=destination.name,
        )

    def _callbacks(self, attempts: int):
        last_update = 0.0

        def progress(received: int, total: int | None, done: bool) -> None:
            nonlocal last_update
            now = time.monotonic()
            if done or now - last_update >= 0.1:
                self.reporter.download_progress(received, total, done=done)
                last_update = now

        def retry_notice(next_attempt: int, error: Exception, delay: float) -> None:
            self.reporter.detail(f"Tentativa de download falhou: {error}")
            self.reporter.warning(
                "Falha temporária no download. "
                f"Tentando novamente ({next_attempt}/{attempts}) em {delay:.1f}s..."
            )

        return progress, retry_notice

    @staticmethod
    def _raise_rate_limit(error: DownloadHTTPError) -> None:
        remaining = next((
            value for name, value in error.headers.items()
            if name.casefold() == "x-ratelimit-remaining"
        ), None)
        if error.status == 403 and remaining == "0":
            raise InstallerError(
                "O limite temporário de consultas do GitHub foi atingido. Aguarde a "
                "renovação ou defina GITHUB_TOKEN para ampliar o limite."
            ) from error

    def get(
        self,
        url: str,
        destination: Path | None = None,
        headers: dict[str, str] | None = None,
        *,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
        maximum_size: int | None = None,
        timeout: float = 60.0,
        attempts: int = 3,
    ) -> bytes:
        validate_https_url(url, "URL de download")
        display_url = safe_url_for_log(url)
        self.reporter.detail(f"GET {display_url}")
        contract = self._contract(
            url, destination, headers,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
            maximum_size=maximum_size,
            timeout=timeout,
            attempts=attempts,
        )
        progress, retry_notice = self._callbacks(attempts)
        try:
            result = self.download_one(
                contract,
                progress=progress if destination is not None else None,
                on_retry=retry_notice,
            )
        except DownloadHTTPError as error:
            self._raise_rate_limit(error)
            self.reporter.detail(f"Tentativa de download falhou: {error}")
            raise InstallerError(f"Não foi possível baixar {display_url}: {error}") from error
        except DownloadError as error:
            self.reporter.detail(f"Tentativa de download falhou: {error}")
            raise InstallerError(f"Não foi possível baixar {display_url}: {error}") from error
        return result.data or b""

    def get_mirrors(
        self,
        urls: tuple[str, ...],
        destination: Path | None = None,
        headers: dict[str, str] | None = None,
        *,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
        maximum_size: int | None = None,
        timeout: float = 60.0,
        attempts: int = 3,
        mirror_label: str = "Mirror",
    ) -> tuple[bytes, str]:
        if not urls:
            raise InstallerError("O download exige ao menos um mirror.")
        display_urls = []
        for index, url in enumerate(urls, start=1):
            validate_https_url(url, f"URL do mirror {index}")
            display_urls.append(safe_url_for_log(url))
        contracts = tuple(
            self._contract(
                url, destination, headers,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
                maximum_size=maximum_size,
                timeout=timeout,
                attempts=attempts,
            )
            for url in urls
        )
        progress, retry_notice = self._callbacks(attempts)
        selected_index = 0

        def mirror_failure(index: int, _contract: object, error: DownloadError) -> None:
            nonlocal selected_index
            selected_index = index
            self.reporter.detail(str(error))
            if index < len(urls):
                host = urllib.parse.urlsplit(urls[index - 1]).hostname or urls[index - 1]
                self.reporter.warning(
                    f"{mirror_label} indisponível ou inválido em {host}; "
                    "tentando a próxima cópia..."
                )

        for display_url in display_urls:
            self.reporter.detail(f"GET {display_url}")
        try:
            result = self.download_many(
                contracts,
                progress=progress if destination is not None else None,
                on_retry=retry_notice,
                on_mirror_failure=mirror_failure,
            )
        except DownloadHTTPError as error:
            self._raise_rate_limit(error)
            self.reporter.detail(f"Tentativa de download falhou: {error}")
            raise InstallerError(f"O download por mirrors falhou: {error}") from error
        except DownloadError as error:
            self.reporter.detail(f"Tentativa de download falhou: {error}")
            raise InstallerError(f"O download por mirrors falhou: {error}") from error
        return result.data or b"", urls[selected_index]


__all__ = (
    "RemoteClient", "https_url_filename", "validate_https_url",
)
