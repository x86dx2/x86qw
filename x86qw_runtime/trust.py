"""TUF trust boundary for the authenticated public package catalog."""

from __future__ import annotations

import json
import io
import base64
import hashlib
import os
import re
import sys
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timezone
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .io import private_fs
from .io.downloader import DownloadHTTPError as RuntimeDownloadHTTPError
from .io.metadata import read_bounded_regular_file


CATALOG_TARGET = "catalog/catalog.json"
CATALOG_MAX_BYTES = 2 * 1024 * 1024
# Compatibility limit for generic bounded metadata consumers. TUF role-specific
# limits remain stricter inside the verifier.
MAX_METADATA_BYTES = 1 * 1024 * 1024
EXPECTED_ROLE_POLICY = {
    "root": (3, 2),
    "targets": (3, 2),
    "snapshot": (2, 1),
    "timestamp": (2, 1),
}


class TrustError(RuntimeError):
    """The authenticated metadata chain could not establish catalog trust."""


EVIDENCE_ROOT_FORMAT = "x86qw-m3-evidence-root-v1"
EVIDENCE_ROOT_THRESHOLD = 2
EVIDENCE_ROOT_KEY_COUNT = 3
EVIDENCE_ROOT_MAX_BYTES = 512 * 1024
RELEASE_EVIDENCE_MAX_BYTES = 2 * 1024 * 1024
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _load_unique_json(payload: bytes, label: str, maximum_size: int) -> dict[str, object]:
    if not isinstance(payload, bytes) or not payload or len(payload) > maximum_size:
        raise TrustError(f"{label} ausente ou excede o limite")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise TrustError(f"{label} contém chave duplicada")
            value[key] = item
        return value

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except TrustError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustError(f"{label} não é JSON UTF-8 válido") from error
    if not isinstance(value, dict):
        raise TrustError(f"{label} precisa ser um objeto JSON")
    return value


def _signature_bytes(value: object, label: str) -> bytes:
    if not isinstance(value, str) or _BASE64URL.fullmatch(value) is None:
        raise TrustError(f"{label} possui assinatura inválida")
    padded = value + ("=" * (-len(value) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise TrustError(f"{label} possui assinatura inválida") from error
    if len(decoded) != 64 or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
        raise TrustError(f"{label} possui assinatura não canônica")
    return decoded


def _evidence_key_id(keytype: str, scheme: str, keyval: dict[str, str]) -> str:
    return hashlib.sha256(_canonical_json({
        "keytype": keytype,
        "scheme": scheme,
        "keyval": keyval,
    })).hexdigest()


def _load_evidence_keys(signed_root: dict[str, object]) -> dict[str, Any]:
    keys = signed_root.get("keys")
    if not isinstance(keys, dict) or len(keys) != EVIDENCE_ROOT_KEY_COUNT:
        raise TrustError("root M3 precisa declarar exatamente três chaves")
    try:
        from securesystemslib.signer import SSlibKey
    except ImportError as error:
        raise TrustError("backend Ed25519 da evidência está indisponível") from error
    result: dict[str, Any] = {}
    for keyid, raw_key in keys.items():
        if not isinstance(keyid, str) or _HEX64.fullmatch(keyid) is None:
            raise TrustError("root M3 contém key ID inválido")
        if not isinstance(raw_key, dict) or set(raw_key) != {"keytype", "scheme", "keyval"}:
            raise TrustError("root M3 contém chave com schema inválido")
        keytype = raw_key.get("keytype")
        scheme = raw_key.get("scheme")
        keyval = raw_key.get("keyval")
        if (
            keytype != "ed25519"
            or scheme != "ed25519"
            or not isinstance(keyval, dict)
            or set(keyval) != {"public"}
            or not isinstance(keyval.get("public"), str)
            or _HEX64.fullmatch(keyval["public"]) is None
            or _evidence_key_id(keytype, scheme, keyval) != keyid
        ):
            raise TrustError("root M3 contém material Ed25519 inválido")
        result[keyid] = SSlibKey(keyid, keytype, scheme, keyval)
    return result


def _verify_evidence_signatures(
    signatures: object,
    *,
    keys: Mapping[str, Any],
    payload: bytes,
    threshold: int,
    label: str,
) -> None:
    if not isinstance(signatures, list) or len(signatures) < threshold:
        raise TrustError(f"{label} não satisfaz o threshold M3")
    used: set[str] = set()
    try:
        from securesystemslib.signer import Signature
    except ImportError as error:
        raise TrustError("backend Ed25519 da evidência está indisponível") from error
    for index, raw_signature in enumerate(signatures, start=1):
        if not isinstance(raw_signature, dict) or set(raw_signature) != {"keyid", "sig"}:
            raise TrustError(f"{label} contém assinatura {index} inválida")
        keyid = raw_signature.get("keyid")
        if not isinstance(keyid, str) or keyid not in keys or keyid in used:
            raise TrustError(f"{label} contém key ID duplicado ou não autorizado")
        used.add(keyid)
        signature = _signature_bytes(raw_signature.get("sig"), f"{label} {index}")
        try:
            keys[keyid].verify_signature(Signature(keyid, signature.hex()), payload)
        except Exception as error:
            raise TrustError(f"{label} contém assinatura criptograficamente inválida") from error


def _load_evidence_root(payload: bytes) -> dict[str, Any]:
    root = _load_unique_json(payload, "root M3", EVIDENCE_ROOT_MAX_BYTES)
    if set(root) != {"signed", "signatures"} or not isinstance(root.get("signed"), dict):
        raise TrustError("root M3 possui envelope inválido")
    signed = root["signed"]
    assert isinstance(signed, dict)
    expected_fields = {
        "format", "project", "role", "version", "expires", "threshold", "keys",
    }
    if set(signed) != expected_fields or signed.get("format") != EVIDENCE_ROOT_FORMAT:
        raise TrustError("root M3 possui identidade inválida")
    if (
        signed.get("project") != "x86qw"
        or signed.get("role") != "evidence"
        or type(signed.get("version")) is not int
        or signed["version"] < 1
        or signed.get("threshold") != EVIDENCE_ROOT_THRESHOLD
    ):
        raise TrustError("root M3 diverge da política 2-de-3")
    expires = signed.get("expires")
    if not isinstance(expires, str):
        raise TrustError("root M3 não possui expiração UTC")
    try:
        expiry = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    except ValueError as error:
        raise TrustError("expiração da root M3 é inválida") from error
    if expiry.tzinfo is None or expiry.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        raise TrustError("root M3 expirada")
    keys = _load_evidence_keys(signed)
    _verify_evidence_signatures(
        root.get("signatures"),
        keys=keys,
        payload=_canonical_json(signed),
        threshold=EVIDENCE_ROOT_THRESHOLD,
        label="assinaturas da root M3",
    )
    return keys


def verify_release_evidence(
    trust_root: bytes,
    evidence: bytes,
    *,
    expected_identity: Mapping[str, object],
) -> dict[str, object]:
    """Verify external 2-of-3 M3 evidence without handling private keys."""
    if (
        not isinstance(expected_identity, Mapping)
        or set(expected_identity) != {"version", "commit", "manifest_sha256"}
        or not isinstance(expected_identity.get("version"), str)
        or not isinstance(expected_identity.get("commit"), str)
        or not isinstance(expected_identity.get("manifest_sha256"), str)
        or _HEX40.fullmatch(expected_identity["commit"]) is None
        or _HEX64.fullmatch(expected_identity["manifest_sha256"]) is None
    ):
        raise TrustError("identidade esperada do candidato é inválida")
    keys = _load_evidence_root(trust_root)
    document = _load_unique_json(evidence, "agregado de evidência M3", RELEASE_EVIDENCE_MAX_BYTES)
    expected_fields = {
        "format", "project", "version", "commit", "status", "candidate", "platforms", "signatures",
    }
    if set(document) != expected_fields:
        raise TrustError("agregado de evidência M3 possui campos inválidos")
    if (
        document.get("format") != 1
        or document.get("project") != "x86qw"
        or document.get("version") != expected_identity["version"]
        or document.get("commit") != expected_identity["commit"]
        or document.get("status") != "complete"
        or document.get("candidate") != dict(expected_identity)
        or not isinstance(document.get("platforms"), dict)
        or not document["platforms"]
    ):
        raise TrustError("agregado de evidência M3 diverge do candidato")
    body = {key: value for key, value in document.items() if key != "signatures"}
    _verify_evidence_signatures(
        document.get("signatures"),
        keys=keys,
        payload=_canonical_json(body),
        threshold=EVIDENCE_ROOT_THRESHOLD,
        label="assinaturas da evidência M3",
    )
    return document


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
            http_error = error if isinstance(error, RuntimeDownloadHTTPError) else error.__cause__
            if isinstance(http_error, RuntimeDownloadHTTPError):
                raise exceptions.DownloadHTTPError(
                    str(http_error), http_error.status,
                ) from error
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
        # The source checkout keeps the exact wheel inputs beside the
        # maintenance tooling; the installed zipapp flattens those same
        # packages into its own import root.  This small fallback keeps both
        # entrypoints on the identical pinned dependency set without adding a
        # runtime dependency on ``maintenance``.
        vendor = Path(__file__).resolve().parents[1] / "maintenance/vendor/wheels"
        if vendor.is_dir():
            for wheel in sorted(vendor.glob("*.whl"), reverse=True):
                wheel_path = os.fspath(wheel)
                if wheel_path not in sys.path:
                    sys.path.insert(0, wheel_path)
            try:
                from tuf.api import exceptions
                from tuf.api.metadata import Metadata, Root
                from tuf.ngclient import Updater
                from tuf.ngclient.config import UpdaterConfig
            except ImportError:
                pass
            else:
                return exceptions, Metadata, Root, Updater, UpdaterConfig
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
