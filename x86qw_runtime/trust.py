"""TUF trust boundary for the authenticated public package catalog."""

from __future__ import annotations

import json
import io
import os
import urllib.parse
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .io import private_fs
from .io.metadata import read_bounded_regular_file


CATALOG_TARGET = "catalog/catalog.json"
CATALOG_MAX_BYTES = 2 * 1024 * 1024
EXPECTED_ROLE_POLICY = {
    "root": (3, 2),
    "targets": (3, 2),
    "snapshot": (2, 1),
    "timestamp": (2, 1),
}


class TrustError(RuntimeError):
    """The authenticated metadata chain could not establish catalog trust."""


class BoundedTufFetcher:
    """Route python-tuf reads through the existing bounded HTTPS boundary."""

    def __init__(self, get: Any) -> None:
        if not callable(get):
            raise TypeError("bounded TUF fetcher requires a callable GET boundary")
        self._get = get

    @staticmethod
    def _policy_limit(url: str) -> int:
        name = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
        if name.endswith(".root.json"):
            return 512 * 1024
        if name == "timestamp.json":
            return 64 * 1024
        return CATALOG_MAX_BYTES

    def download_bytes(self, url: str, max_length: int) -> bytes:
        if type(max_length) is not int or max_length <= 0:
            raise ValueError("TUF maximum download length must be positive")
        exceptions, _Metadata, _Root, _Updater, _UpdaterConfig = _tuf_api()
        maximum_size = min(max_length, self._policy_limit(url))
        try:
            payload = self._get(
                url,
                maximum_size=maximum_size,
                timeout=10.0,
                attempts=1,
            )
        except exceptions.DownloadHTTPError:
            raise
        except Exception as error:
            raise exceptions.DownloadError(f"bounded TUF download failed: {url}") from error
        if len(payload) > max_length:
            raise exceptions.DownloadLengthMismatchError(
                f"downloaded {len(payload)} bytes exceeds TUF limit {max_length}"
            )
        return payload

    @contextmanager
    def download_file(self, url: str, max_length: int):
        with io.BytesIO(self.download_bytes(url, max_length)) as stream:
            yield stream


class _ObservingFetcher:
    """Capture timestamp bytes while preserving the fetcher's bounded contract."""

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.timestamp: bytes | None = None

    def download_bytes(self, url: str, max_length: int) -> bytes:
        payload = self.delegate.download_bytes(url, max_length)
        if len(payload) > max_length:
            exceptions, _Metadata, _Root, _Updater, _UpdaterConfig = _tuf_api()
            raise exceptions.DownloadLengthMismatchError(
                f"downloaded {len(payload)} bytes exceeds TUF limit {max_length}"
            )
        if urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1] == "timestamp.json":
            self.timestamp = payload
        return payload

    @contextmanager
    def download_file(self, url: str, max_length: int):
        payload = self.download_bytes(url, max_length)
        with io.BytesIO(payload) as replay:
            yield replay


def _tuf_api():
    try:
        from tuf.api import exceptions
        from tuf.api.metadata import Metadata, Root
        from tuf.ngclient import Updater
        from tuf.ngclient.config import UpdaterConfig
    except ImportError as error:
        raise TrustError("dependências TUF fixadas estão indisponíveis") from error
    return exceptions, Metadata, Root, Updater, UpdaterConfig


def _base_url(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise TrustError(f"{label} de trust inválida")
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.endswith("/")
    ):
        raise TrustError(f"{label} de trust deve ser uma base HTTPS sem credenciais ou query")
    return value


def validate_bootstrap_policy(bootstrap_root: bytes) -> None:
    """Reject a root that diverges from the approved Ed25519 role policy."""

    if not isinstance(bootstrap_root, bytes) or not bootstrap_root:
        raise TrustError("root TUF incorporada está ausente")
    _exceptions, Metadata, Root, _Updater, _UpdaterConfig = _tuf_api()
    try:
        metadata = Metadata.from_bytes(bootstrap_root)
    except Exception as error:
        raise TrustError("root TUF incorporada é inválida") from error
    if not isinstance(metadata.signed, Root):
        raise TrustError("âncora incorporada não é metadata root TUF")
    root = metadata.signed
    if root.consistent_snapshot is not True:
        raise TrustError("root TUF deve habilitar consistent snapshots")
    if set(root.roles) != set(EXPECTED_ROLE_POLICY):
        raise TrustError("root TUF declara roles fora da política aprovada")

    used_keyids: set[str] = set()
    used_public_material: set[tuple[str, str, str]] = set()
    for role_name, (key_count, threshold) in EXPECTED_ROLE_POLICY.items():
        role = root.roles[role_name]
        if len(role.keyids) != key_count or role.threshold != threshold:
            raise TrustError(
                f"role {role_name} diverge da política {threshold}-de-{key_count}"
            )
        if len(set(role.keyids)) != len(role.keyids):
            raise TrustError(f"role {role_name} repete key IDs")
        if used_keyids.intersection(role.keyids):
            raise TrustError("uma chave TUF não pode pertencer a mais de uma role")
        used_keyids.update(role.keyids)
        for keyid in role.keyids:
            try:
                key = root.keys[keyid]
            except KeyError as error:
                raise TrustError(f"role {role_name} referencia chave ausente") from error
            if key.keytype != "ed25519" or key.scheme != "ed25519":
                raise TrustError("todas as chaves TUF devem usar Ed25519")
            material = (
                key.keytype,
                key.scheme,
                json.dumps(key.keyval, sort_keys=True, separators=(",", ":")),
            )
            if material in used_public_material:
                raise TrustError("material público de chave TUF duplicado")
            used_public_material.add(material)
    if set(root.keys) != used_keyids:
        raise TrustError("root TUF contém chaves sem role aprovada")
    try:
        root.verify_delegate("root", metadata.signed_bytes, metadata.signatures)
    except Exception as error:
        raise TrustError("root TUF não satisfaz seu próprio threshold") from error


def _private_directory(path: Path) -> None:
    path = Path(path)
    try:
        if private_fs.lexists(path):
            private_fs.protect_private_directory(path)
        else:
            private_fs.create_private_directory(path)
        private_fs.validate_private_directory(path)
    except OSError as error:
        raise TrustError(f"diretório privado de trust indisponível: {path}") from error


def _protect_tree(root: Path) -> None:
    """Normalize files created by python-tuf under an already-private root."""

    try:
        children = sorted(
            root.rglob("*"), key=lambda path: (len(path.parts), str(path)),
            reverse=True,
        )
        for path in children:
            if path.is_symlink():
                target = path.resolve(strict=True)
                try:
                    target.relative_to(root.resolve(strict=True))
                except ValueError as error:
                    raise OSError(
                        f"symlink externo no cache TUF: {path}"
                    ) from error
                if not target.is_file() or target.is_symlink():
                    raise OSError(f"symlink inválido no cache TUF: {path}")
                continue
            if path.is_dir():
                private_fs.protect_private_directory(path)
                private_fs.validate_private_directory(path)
            elif path.is_file():
                private_fs.protect_private_file(path)
                private_fs.validate_private_file(path)
            else:
                raise OSError(f"objeto inesperado no cache TUF: {path}")
        private_fs.protect_private_directory(root)
        private_fs.validate_private_directory(root)
    except OSError as error:
        raise TrustError(f"cache TUF não pôde ser mantido privado: {root}") from error


def load_trusted_catalog(
    *,
    bootstrap_root: bytes,
    metadata_dir: Path,
    target_dir: Path,
    metadata_base_url: str,
    target_base_url: str,
    fetcher: Any,
) -> dict[str, object]:
    """Refresh one TUF repository and return its authenticated catalog target."""

    validate_bootstrap_policy(bootstrap_root)
    metadata_base_url = _base_url(metadata_base_url, "URL de metadata")
    target_base_url = _base_url(target_base_url, "URL de targets")
    metadata_dir = Path(metadata_dir)
    target_dir = Path(target_dir)
    _private_directory(metadata_dir)
    _private_directory(target_dir)

    exceptions, _Metadata, _Root, Updater, UpdaterConfig = _tuf_api()
    config = UpdaterConfig(
        root_max_length=512 * 1024,
        timestamp_max_length=64 * 1024,
        snapshot_max_length=2 * 1024 * 1024,
        targets_max_length=2 * 1024 * 1024,
        max_root_rotations=32,
        max_delegations=1,
    )
    previous_timestamp: bytes | None = None
    timestamp_path = metadata_dir / "timestamp.json"
    if timestamp_path.is_file() and not timestamp_path.is_symlink():
        try:
            previous_timestamp = read_bounded_regular_file(
                timestamp_path, maximum_size=config.timestamp_max_length,
            )
        except OSError as error:
            raise TrustError("timestamp TUF local é inválido") from error
    observing_fetcher = _ObservingFetcher(fetcher)
    try:
        try:
            updater = Updater(
                metadata_dir=os.fspath(metadata_dir),
                metadata_base_url=metadata_base_url,
                target_dir=os.fspath(target_dir),
                target_base_url=target_base_url,
                fetcher=observing_fetcher,
                config=config,
                bootstrap=bootstrap_root,
            )
            updater.refresh()
            if previous_timestamp is not None and observing_fetcher.timestamp is not None:
                previous = _Metadata.from_bytes(previous_timestamp)
                observed = _Metadata.from_bytes(observing_fetcher.timestamp)
                if (
                    observed.signed.version == previous.signed.version
                    and observed.signed_bytes != previous.signed_bytes
                ):
                    raise TrustError(
                        "equivocação TUF: timestamp diferente reutilizou a mesma versão"
                    )
            target = updater.get_targetinfo(CATALOG_TARGET)
            if target is None:
                raise TrustError("catálogo não foi autorizado por targets TUF")
            downloaded = Path(updater.download_target(target))
            payload = read_bounded_regular_file(
                downloaded, maximum_size=CATALOG_MAX_BYTES,
            )
            catalog = json.loads(payload)
        finally:
            _protect_tree(metadata_dir)
            _protect_tree(target_dir)
    except TrustError:
        raise
    except (
        exceptions.RepositoryError,
        exceptions.DownloadError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as error:
        raise TrustError(f"falha de trust ao autenticar catálogo: {error}") from error
    if (
        not isinstance(catalog, dict)
        or catalog.get("format") != 1
        or catalog.get("project") != "x86qw"
        or not isinstance(catalog.get("packages"), list)
    ):
        raise TrustError("catálogo autenticado usa identidade ou formato incompatível")
    return catalog
