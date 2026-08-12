"""Small, auditable trust-metadata verifier for the installed runtime.

The runtime intentionally has no network, filesystem or maintenance dependency
here.  Callers provide already-bounded bytes and persist the returned versions
atomically.  Metadata follows a deliberately small TUF-like chain:

``root -> current -> snapshot -> catalog``

The pinned root is an RSA-PSS-SHA256 public key.  Python's standard library
does not provide an asymmetric signature primitive, so this module contains a
verification-only implementation of RFC 8017's EMSA-PSS encoding.  It is not a
replacement for a cryptographic library; production key material must remain
offline and this implementation requires an independent cross-vector review.
"""

from __future__ import annotations

import base64
import binascii
import dataclasses
import hashlib
import hmac
import json
import re
import unicodedata
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping


MAX_METADATA_BYTES = 1 * 1024 * 1024
MAX_METADATA_DEPTH = 24
MAX_METADATA_VALUES = 20_000
MAX_TARGET_LENGTH = 256 * 1024 * 1024
CURRENT_MAX_LIFETIME = timedelta(days=7)
# Native evidence is a release gate, not a long-lived catalog pointer.  The
# legacy evidence shape has no top-level expiry; its per-platform
# ``recorded_at`` values are therefore bounded by the same seven-day policy.
EVIDENCE_MAX_LIFETIME = timedelta(days=7)
MAX_CLOCK_SKEW = timedelta(minutes=5)

_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_PLATFORM_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_EVIDENCE_SECRET_FIELDS = frozenset({
    "access_token",
    "api_key",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "credentials",
    "passphrase",
    "password",
    "private",
    "private_key",
    "private_key_pem",
    "secret",
    "seed",
    "token",
})
_SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


# This public key is intentionally the only trust anchor shipped by the
# runtime.  Its private counterpart is not present in this repository.
ROOT_KEY_ID = "e7d419d9b6e7da0b813f717bab2ad9094b389e7bf29a452d1cacdb0624c7acdd"
ROOT_PUBLIC_KEY: dict[str, object] = {
    "keytype": "rsa",
    "scheme": "rsassa-pss-sha256",
    "keyval": {
        "public": {
            "e": 65537,
            "n": (
                "bfb4470b29a57a1b797de83b9f2981646e4b59e0a184ed4807142f501190830"
                "89d9bb18c37905f5415ba97fa14f2c4a7264fe1aa3e59da238da5a615078de9f"
                "7de63a09fb967bfbaf7479a4dcda76464e9fc6cc27be66b5193787807ac0d573a"
                "23f76019d57a6926e7a31d5c411d7bb1b1308b00a0c2dcb1f6477420c3ad38a86"
                "ab58a767719f840fab2e8aa8d781c75969156795fcab1ba69d954f474476ed429"
                "abfc28d5467376895615a43b9ca4bfa54ab16000f803667a08071cd3b910fdac5d"
                "be5ce2b02c21b100d14769ff07f41774f80f900b1b3535abf83cce94d51b5e218"
                "663119603dc7e8f78aa9bb4a8bd212e8129a09bb2d89260a4e08e86746783846a"
                "a321f4f387f2dcf75cece76b8af95c01a7c7fc81ee3de96fe59aba9bd2e16871d4"
                "79ecbb30307b2303ddf89ca53c7c7f01cda13c0fbe9a08d71d4f925d72536fe35a"
                "2bb5607449474333f1154a2f3a3a5af86fe85782b7272a44e0f546af197991e1f"
                "d77f4dfb9b81c13f4e4d4d7fa437505f3ef055e2f3f6a5119b847"
            ),
        },
    },
}


class TrustError(ValueError):
    """Base class for fail-closed trust metadata errors."""


class FormatError(TrustError):
    """Metadata has an unsupported or ambiguous representation."""


class SignatureError(TrustError):
    """A signature, key, or signature encoding is invalid."""


class ThresholdError(SignatureError):
    """A role did not receive enough distinct valid signatures."""


class ExpiryError(TrustError):
    """Metadata is expired or issued outside the permitted clock window."""


class RollbackError(TrustError):
    """Metadata is older than, or equivocates with, trusted state."""


class FreezeError(TrustError):
    """A current pointer is too old to provide freeze protection."""


class RotationError(TrustError):
    """A root rotation does not form one contiguous, signed transition."""


class DigestError(TrustError):
    """A signed target does not match the supplied bytes."""


@dataclasses.dataclass(frozen=True)
class Target:
    path: str
    version: int
    length: int
    sha256: str


@dataclasses.dataclass(frozen=True)
class TrustedVersions:
    """Versions and signed-body digests retained by the caller.

    A version by itself is not sufficient to accept a replay: same-version
    metadata must carry the previously trusted digest.  ``root_metadata`` is
    the previously trusted root envelope when a rotation is being followed.
    ``evidence_version`` and ``evidence_digest`` bind the promoted release
    evidence independently from the integer catalog-role versions.
    """

    root: int = 0
    snapshot: int = 0
    current: int = 0
    root_digest: str | None = None
    snapshot_digest: str | None = None
    current_digest: str | None = None
    root_metadata: bytes | Mapping[str, object] | None = None
    # Release evidence uses SemVer rather than the integer versions used by
    # the TUF-like roles above.  Keep its state in the same atomic document so
    # a caller can reject both a version rollback and same-version
    # equivocation before persisting a newly verified release.
    evidence_version: str | None = None
    evidence_digest: str | None = None

    def __post_init__(self) -> None:
        for name in ("root", "snapshot", "current"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} trusted version must be a non-negative integer")
        for name in ("root_digest", "snapshot_digest", "current_digest"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
            ):
                raise ValueError(f"{name} trusted digest must be lowercase SHA-256")
        if (self.evidence_version is None) != (self.evidence_digest is None):
            raise ValueError("trusted evidence state is incomplete")
        if self.evidence_version is not None:
            try:
                _semver_parts(self.evidence_version, "evidence_version")
            except TrustError as error:
                raise ValueError("evidence_version trusted state must be SemVer") from error
        if self.evidence_digest is not None and not _HEX64.fullmatch(self.evidence_digest):
            raise ValueError("evidence_digest trusted state must be lowercase SHA-256")
        if self.root_metadata is not None and not isinstance(self.root_metadata, (bytes, Mapping)):
            raise ValueError("root_metadata must be an envelope or its bytes")
        if isinstance(self.root_metadata, bytes):
            try:
                normalized_root = parse_json_bytes(self.root_metadata)
            except TrustError as error:
                raise ValueError("root_metadata must be strict UTF-8 JSON") from error
            object.__setattr__(self, "root_metadata", normalized_root)
        # A persisted catalog state is one atomic checkpoint.  Accepting a
        # higher role version without the digest/root that authenticated it
        # would turn a malformed or truncated state file into a rollback
        # bypass on the next verification.  Evidence-only state is allowed
        # while the catalog chain is still at its zero value.
        if self.root == 0:
            if (
                self.root_metadata is not None
                or self.root_digest is not None
                or self.snapshot != 0
                or self.snapshot_digest is not None
                or self.current != 0
                or self.current_digest is not None
            ):
                raise ValueError("trusted catalog state requires an authenticated root")
        elif self.root_metadata is None or self.root_digest is None:
            raise ValueError("trusted root state is incomplete")
        for name in ("snapshot", "current"):
            version = getattr(self, name)
            digest = getattr(self, f"{name}_digest")
            if (version == 0) != (digest is None):
                raise ValueError(f"trusted {name} state is incomplete")
        if (self.snapshot == 0) != (self.current == 0):
            raise ValueError("trusted catalog role checkpoint is incomplete")

    def as_tuple(self) -> tuple[int, int, int]:
        return self.root, self.snapshot, self.current

    def evidence_tuple(self) -> tuple[str | None, str | None]:
        return self.evidence_version, self.evidence_digest

    @property
    def evidence_release(self) -> str | None:
        """Compatibility spelling for callers that name the release directly."""

        return self.evidence_version

    def with_evidence(self, version: str, digest: str) -> "TrustedVersions":
        """Return a copy carrying the newly accepted evidence state."""

        if not isinstance(version, str):
            raise ValueError("evidence version must be SemVer")
        try:
            _semver_parts(version, "evidence version")
        except TrustError as error:
            raise ValueError("evidence version must be SemVer") from error
        if not isinstance(digest, str) or _HEX64.fullmatch(digest) is None:
            raise ValueError("evidence digest must be lowercase SHA-256")
        if self.evidence_version is not None:
            comparison = _semver_compare(version, self.evidence_version)
            if comparison < 0:
                raise RollbackError(
                    f"evidence release rolls back from {self.evidence_version} to {version}"
                )
            if comparison == 0 and (
                self.evidence_digest is None
                or not hmac.compare_digest(self.evidence_digest, digest)
            ):
                raise RollbackError(
                    f"evidence release equivocates at version {version}"
                )
        return dataclasses.replace(
            self,
            evidence_version=version,
            evidence_digest=digest,
        )


@dataclasses.dataclass(frozen=True)
class VerifiedMetadata:
    versions: TrustedVersions
    catalog: Target
    release: str
    root: Mapping[str, object]
    current: Mapping[str, object]
    snapshot: Mapping[str, object]


@dataclasses.dataclass(frozen=True, eq=False)
class VerifiedEvidence(Mapping[str, object]):
    """Verified release evidence plus the state ready for atomic persistence.

    The mapping interface preserves the historical ``result["candidate"]``
    call shape while exposing the anti-rollback checkpoint produced by the
    same verification.  Callers must persist :attr:`trusted` together with
    the accepted document; keeping the pair in one result avoids accidentally
    persisting the old evidence generation after a successful verification.
    """

    document: Mapping[str, object]
    trusted: TrustedVersions
    digest: str

    def __getitem__(self, key: str) -> object:
        return self.document[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.document)

    def __len__(self) -> int:
        return len(self.document)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, VerifiedEvidence):
            return (
                dict(self.document) == dict(other.document)
                and self.trusted == other.trusted
                and self.digest == other.digest
            )
        if isinstance(other, Mapping):
            return dict(self.document) == dict(other)
        return NotImplemented

    @property
    def evidence_digest(self) -> str:
        """Canonical digest of the signed evidence body."""

        return self.digest

    @property
    def evidence(self) -> Mapping[str, object]:
        """The verified document under an explicit name for new callers."""

        return self.document

    @property
    def trusted_versions(self) -> TrustedVersions:
        """Compatibility alias for callers that name the checkpoint fully."""

        return self.trusted

    @property
    def versions(self) -> TrustedVersions:
        """Short alias matching :class:`VerifiedMetadata`."""

        return self.trusted


TRUSTED_STATE_LEGACY_FORMAT = 1
TRUSTED_STATE_FORMAT = 2
TRUSTED_STATE_PROJECT = "x86qw"


def _reject_constant(value: str) -> Any:
    raise FormatError(f"non-finite JSON number: {value}")


def _reject_float(value: str) -> Any:
    raise FormatError(f"floating-point JSON numbers are not supported: {value}")


def _parse_int(value: str) -> int:
    if value == "-0":
        raise FormatError("negative zero is not canonical JSON")
    if len(value.lstrip("-")) > 128:
        raise FormatError("JSON integer is oversized")
    return int(value)


def _pairs_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise FormatError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _validate_tree(value: object, *, depth: int = 0, count: list[int] | None = None) -> None:
    if count is None:
        count = [0]
    count[0] += 1
    if count[0] > MAX_METADATA_VALUES:
        raise FormatError("metadata contains too many values")
    if depth > MAX_METADATA_DEPTH:
        raise FormatError("metadata nesting is too deep")
    if isinstance(value, str):
        if unicodedata.normalize("NFC", value) != value:
            raise FormatError("metadata strings must be NFC")
        # JSON strings may legitimately contain escaped horizontal tab,
        # newline and carriage return characters (the public catalog uses
        # them in release notes). Other C0 controls remain forbidden.
        if any(
            (ord(character) < 0x20 and character not in "\t\n\r")
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        ):
            raise FormatError("metadata contains control or surrogate characters")
    elif isinstance(value, list):
        for item in value:
            _validate_tree(item, depth=depth + 1, count=count)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise FormatError("JSON object keys must be strings")
            _validate_tree(key, depth=depth + 1, count=count)
            _validate_tree(item, depth=depth + 1, count=count)
    elif isinstance(value, (int, bool)) or value is None:
        return
    else:
        raise FormatError(f"unsupported JSON value type: {type(value).__name__}")


def parse_json_bytes(payload: bytes, *, maximum_size: int = MAX_METADATA_BYTES) -> dict[str, object]:
    """Parse strict UTF-8 JSON while rejecting ambiguity and resource bombs."""

    if not isinstance(payload, bytes) or len(payload) > maximum_size:
        raise FormatError("metadata is missing or oversized")
    try:
        text = payload.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_constant,
            parse_float=_reject_float,
            parse_int=_parse_int,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise FormatError("metadata is not strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise FormatError("metadata root must be an object")
    _validate_tree(value)
    return value


def canonical_json_bytes(value: object) -> bytes:
    """Return the one JSON representation covered by signatures."""

    _validate_tree(value)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError) as error:
        raise FormatError("value cannot be represented canonically") from error


def _key_shape(key: Mapping[str, object]) -> tuple[int, int]:
    if set(key) != {"keytype", "scheme", "keyval"}:
        raise FormatError("RSA key fields are not closed")
    if key.get("keytype") != "rsa" or key.get("scheme") != "rsassa-pss-sha256":
        raise FormatError("unsupported RSA signature scheme")
    keyval = key.get("keyval")
    if not isinstance(keyval, Mapping) or set(keyval) != {"public"}:
        raise FormatError("invalid RSA key value")
    public = keyval.get("public")
    if not isinstance(public, Mapping) or set(public) != {"e", "n"}:
        raise FormatError("RSA public key must contain only n and e")
    exponent = public.get("e")
    modulus = public.get("n")
    if (
        type(exponent) is not int
        or exponent < 3
        or exponent % 2 == 0
        or not isinstance(modulus, str)
        or not re.fullmatch(r"[0-9a-f]+", modulus)
    ):
        raise FormatError("invalid RSA modulus or exponent")
    n = int(modulus, 16)
    if n.bit_length() < 3072 or n % 2 == 0 or exponent >= n:
        raise FormatError("RSA modulus must be an odd key of at least 3072 bits")
    if len(modulus) > 8192:
        raise FormatError("RSA modulus is oversized")
    return n, exponent


def key_id(key: Mapping[str, object]) -> str:
    """Return the TUF-style SHA-256 id of a canonical public-key object."""

    _key_shape(key)
    return hashlib.sha256(canonical_json_bytes(key)).hexdigest()


def _decode_signature(value: object) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise SignatureError("signature must be unpadded base64url")
    if len(value) % 4 == 1:
        raise SignatureError("invalid base64url signature length")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError) as error:
        raise SignatureError("invalid base64url signature") from error
    if not decoded or base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value:
        raise SignatureError("non-canonical base64url signature")
    return decoded


def _mgf1(seed: bytes, length: int) -> bytes:
    if length < 0 or length > 1 << 20:
        raise SignatureError("RSA-PSS mask is oversized")
    output = bytearray()
    for counter in range((length + 31) // 32):
        output.extend(hashlib.sha256(seed + counter.to_bytes(4, "big")).digest())
    return bytes(output[:length])


def verify_rsa_pss_sha256(public_key: Mapping[str, object], message: bytes, signature: bytes) -> None:
    """Verify one RSA-PSS-SHA256 signature or raise ``SignatureError``.

    This is the verification half of RFC 8017 EMSA-PSS with a fixed 32-byte
    salt and SHA-256 for both the message hash and MGF1.  No private operation
    exists in the runtime.
    """

    if not isinstance(message, bytes) or not isinstance(signature, bytes):
        raise SignatureError("RSA-PSS inputs must be bytes")
    modulus, exponent = _key_shape(public_key)
    mod_bits = modulus.bit_length()
    key_bytes = (mod_bits + 7) // 8
    em_bits = mod_bits - 1
    em_len = (em_bits + 7) // 8
    if len(signature) != key_bytes:
        raise SignatureError("RSA signature length does not match key")
    value = int.from_bytes(signature, "big")
    if value >= modulus:
        raise SignatureError("RSA signature representative is out of range")
    encoded = pow(value, exponent, modulus).to_bytes(key_bytes, "big")
    if key_bytes > em_len:
        if any(encoded[: key_bytes - em_len]):
            raise SignatureError("RSA-PSS encoded message has a non-zero prefix")
        encoded = encoded[-em_len:]
    elif key_bytes != em_len:
        raise SignatureError("invalid RSA-PSS encoded message length")
    if not encoded or encoded[-1] != 0xBC:
        raise SignatureError("RSA-PSS trailer field is invalid")
    hash_size = hashlib.sha256().digest_size
    db_len = em_len - hash_size - 1
    if db_len < hash_size + 2:
        raise SignatureError("RSA key is too short for the required PSS salt")
    masked_db = encoded[:db_len]
    digest = encoded[db_len:db_len + hash_size]
    unused = 8 * em_len - em_bits
    if unused and masked_db[0] & (0xFF << (8 - unused)):
        raise SignatureError("RSA-PSS leftmost bits are not masked")
    db = bytearray(a ^ b for a, b in zip(masked_db, _mgf1(digest, db_len)))
    if unused:
        db[0] &= 0xFF >> unused
    separator = db_len - hash_size - 1
    if separator < 0 or any(db[:separator]) or db[separator] != 1:
        raise SignatureError("RSA-PSS padding is invalid")
    salt = bytes(db[-hash_size:])
    expected = hashlib.sha256(b"\0" * 8 + hashlib.sha256(message).digest() + salt).digest()
    if not hmac.compare_digest(expected, digest):
        raise SignatureError("RSA-PSS digest does not match")


def _envelope(payload: bytes, kind: str) -> dict[str, object]:
    envelope = parse_json_bytes(payload)
    if set(envelope) != {"signatures", "signed"}:
        raise FormatError("metadata envelope fields are not closed")
    signatures = envelope.get("signatures")
    signed = envelope.get("signed")
    if not isinstance(signatures, list) or not isinstance(signed, dict):
        raise FormatError("metadata envelope has invalid members")
    if signed.get("_type") != kind:
        raise FormatError(f"metadata role is not {kind}")
    return envelope


def _role_shape(role: object, keys: Mapping[str, object], label: str) -> tuple[tuple[str, ...], int]:
    if not isinstance(role, Mapping) or set(role) != {"keyids", "threshold"}:
        raise FormatError(f"{label} role fields are not closed")
    keyids = role.get("keyids")
    threshold = role.get("threshold")
    valid_keyids = (
        isinstance(keyids, list)
        and bool(keyids)
        and all(isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item) for item in keyids)
    )
    if (
        not valid_keyids
        or len(keyids) != len(set(keyids))
        or type(threshold) is not int
        or threshold < 1
        or threshold > len(keyids)
    ):
        raise FormatError(f"invalid {label} threshold")
    for identifier in keyids:
        if identifier not in keys:
            raise FormatError(f"{label} references an unknown key")
    return tuple(keyids), threshold


def _verify_threshold(
    envelope: Mapping[str, object],
    *,
    keys: Mapping[str, object],
    role: object,
    label: str,
    allow_unknown: bool = False,
    required_keyid: str | None = None,
) -> None:
    keyids, threshold = _role_shape(role, keys, label)
    signatures = envelope.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        raise SignatureError(f"{label} has no signatures")
    signed = envelope.get("signed")
    assert isinstance(signed, Mapping)
    message = canonical_json_bytes(signed)
    seen: set[str] = set()
    valid_keyids: set[str] = set()
    valid = 0
    for entry in signatures:
        if not isinstance(entry, Mapping) or set(entry) != {"keyid", "sig"}:
            raise SignatureError(f"malformed {label} signature")
        identifier = entry.get("keyid")
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[0-9a-f]{64}", identifier)
            or identifier in seen
        ):
            raise SignatureError(f"duplicate or invalid {label} signature keyid")
        seen.add(identifier)
        signature = _decode_signature(entry.get("sig"))
        if identifier not in keyids:
            if allow_unknown:
                continue
            raise SignatureError(f"{label} signature uses an unauthorized key")
        key = keys.get(identifier)
        if not isinstance(key, Mapping) or key_id(key) != identifier:
            raise SignatureError(f"{label} keyid does not match its public key")
        verify_rsa_pss_sha256(key, message, signature)
        valid += 1
        valid_keyids.add(identifier)
    if valid < threshold:
        raise ThresholdError(f"{label} threshold {threshold} was not met")
    if required_keyid is not None and required_keyid not in valid_keyids:
        raise ThresholdError(f"{label} is missing required signature {required_keyid}")


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
        raise FormatError(f"invalid {field} timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as error:
        raise FormatError(f"invalid {field} timestamp") from error


def _check_clock(signed: Mapping[str, object], *, now: datetime, kind: str) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ExpiryError("verification clock must be timezone-aware")
    issued = _utc(signed.get("issued"), f"{kind}.issued")
    expires = _utc(signed.get("expires"), f"{kind}.expires")
    now = now.astimezone(timezone.utc)
    if issued > now + MAX_CLOCK_SKEW:
        raise ExpiryError(f"{kind} metadata is issued in the future")
    if expires <= now:
        if kind == "current":
            raise FreezeError("current metadata is expired")
        raise ExpiryError(f"{kind} metadata is expired")
    if expires <= issued:
        raise ExpiryError(f"{kind} metadata expiry is not after issuance")
    if kind == "current" and expires - issued > CURRENT_MAX_LIFETIME:
        raise FreezeError("current metadata lifetime is too long")


def _version(value: object, field: str) -> int:
    if type(value) is not int or value < 1:
        raise FormatError(f"invalid {field} version")
    return value


def _semver_parts(value: object, field: str) -> tuple[int, int, int, tuple[str, ...]]:
    """Validate SemVer and return its precedence components."""

    if not isinstance(value, str):
        raise FormatError(f"invalid {field} SemVer")
    match = _SEMVER.fullmatch(value)
    if match is None:
        raise FormatError(f"invalid {field} SemVer")
    major, minor, patch = (int(match.group(index)) for index in (1, 2, 3))
    prerelease = match.group(4)
    if prerelease and any(
        identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0")
        for identifier in prerelease.split(".")
    ):
        raise FormatError(f"invalid {field} SemVer")
    return major, minor, patch, tuple(prerelease.split(".")) if prerelease else ()


def _semver_compare(left: str, right: str) -> int:
    """Compare two already-validated SemVer strings per semver.org."""

    left_parts = _semver_parts(left, "left")
    right_parts = _semver_parts(right, "right")
    if left_parts[:3] != right_parts[:3]:
        return (left_parts[:3] > right_parts[:3]) - (left_parts[:3] < right_parts[:3])
    left_pre, right_pre = left_parts[3], right_parts[3]
    if not left_pre or not right_pre:
        return (not left_pre) - (not right_pre)
    for left_identifier, right_identifier in zip(left_pre, right_pre):
        if left_identifier == right_identifier:
            continue
        left_numeric = left_identifier.isdigit()
        right_numeric = right_identifier.isdigit()
        if left_numeric and right_numeric:
            return (int(left_identifier) > int(right_identifier)) - (
                int(left_identifier) < int(right_identifier)
            )
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return (left_identifier > right_identifier) - (left_identifier < right_identifier)
    return (len(left_pre) > len(right_pre)) - (len(left_pre) < len(right_pre))


def _evidence_identity(value: object, field: str) -> dict[str, str]:
    """Validate the closed release identity carried by evidence."""

    if not isinstance(value, Mapping) or set(value) != {
        "version", "commit", "manifest_sha256",
    }:
        raise FormatError(f"{field} identity fields are not closed")
    version = value.get("version")
    commit = value.get("commit")
    manifest_sha256 = value.get("manifest_sha256")
    _semver_parts(version, f"{field}.version")
    if not isinstance(commit, str) or _HEX40.fullmatch(commit) is None:
        raise FormatError(f"invalid {field}.commit")
    if not isinstance(manifest_sha256, str) or _HEX64.fullmatch(manifest_sha256) is None:
        raise FormatError(f"invalid {field}.manifest_sha256")
    return {
        "version": version,
        "commit": commit,
        "manifest_sha256": manifest_sha256,
    }


def _reject_evidence_secret_fields(value: object, field: str) -> None:
    """Reject secret-bearing report fields before accepting signed evidence.

    The release-candidate validator applies the complete native-report schema,
    but ``verify_release_evidence`` is also a public runtime boundary and must
    not become a way to persist a free-form credential or private key.  Keep
    the check deliberately key-based so existing report extensions remain
    possible; the one explicit exception is the contract's ``secrets`` marker,
    which is allowed only with the literal redaction value.
    """

    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise FormatError(f"{field} contains a non-text field name")
            normalized = key.casefold().replace("-", "_")
            if normalized == "secrets":
                if item != "redacted":
                    raise FormatError(f"{field}.secrets must be redacted")
            elif normalized in _EVIDENCE_SECRET_FIELDS:
                raise FormatError(f"{field} contains secret material")
            _reject_evidence_secret_fields(item, f"{field}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_evidence_secret_fields(item, f"{field}[{index}]")


def _trusted_evidence(version: str, digest: str, trusted: TrustedVersions) -> None:
    previous = trusted.evidence_version
    if previous is None:
        return
    comparison = _semver_compare(version, previous)
    if comparison < 0:
        raise RollbackError(
            f"evidence release rolls back from {previous} to {version}"
        )
    if comparison == 0 and (
        trusted.evidence_digest is None
        or not hmac.compare_digest(trusted.evidence_digest, digest)
    ):
        raise RollbackError(
            f"evidence release equivocates at version {version}"
        )


def _target(value: object, field: str) -> Target:
    if not isinstance(value, Mapping) or set(value) != {"path", "version", "length", "sha256"}:
        raise FormatError(f"{field} target fields are not closed")
    path = value.get("path")
    if (
        not isinstance(path, str)
        or not path
        or path.startswith(("/", "\\"))
        or "\\" in path
        or "://" in path
        or ":" in path
        or path != unicodedata.normalize("NFC", path)
    ):
        raise FormatError(f"invalid {field} target path")
    raw_parts = path.split("/")
    path_parts = PurePosixPath(path).parts
    if any(part in {"", ".", ".."} for part in raw_parts) or any(
        part in {".", ".."} for part in path_parts
    ):
        raise FormatError(f"unsafe {field} target path")
    version = _version(value.get("version"), f"{field}.version")
    length = value.get("length")
    if type(length) is not int or length < 0 or length > MAX_TARGET_LENGTH:
        raise FormatError(f"invalid {field} target length")
    sha256 = value.get("sha256")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise FormatError(f"invalid {field} target digest")
    return Target(path, version, length, sha256)


def _root_signed(
    envelope: Mapping[str, object], *, now: datetime | None = None,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    signed = envelope["signed"]
    assert isinstance(signed, Mapping)
    if set(signed) != {"_type", "spec_version", "version", "expires", "consistent_snapshot", "keys", "roles"}:
        raise FormatError("root metadata fields are not closed")
    if signed.get("spec_version") != "1.0" or signed.get("consistent_snapshot") is not True:
        raise FormatError("unsupported root metadata contract")
    _version(signed.get("version"), "root")
    expires = _utc(signed.get("expires"), "root.expires")
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ExpiryError("verification clock must be timezone-aware")
    if expires <= now.astimezone(timezone.utc):
        raise ExpiryError("root metadata is expired")
    keys = signed.get("keys")
    roles = signed.get("roles")
    if not isinstance(keys, Mapping) or not isinstance(roles, Mapping):
        raise FormatError("root keys or roles are invalid")
    if set(roles) != {"root", "snapshot", "current", "evidence"}:
        raise FormatError("root roles are not closed")
    for identifier, key in keys.items():
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{64}", identifier) or not isinstance(key, Mapping):
            raise FormatError("invalid root key map")
        if key_id(key) != identifier:
            raise FormatError("root key id does not match public key")
    for label in roles:
        _role_shape(roles[label], keys, f"root.{label}")
    return keys, roles


def _verify_evidence_root(
    root_bytes: bytes,
    *,
    now: datetime,
    trusted: TrustedVersions | None,
) -> tuple[Mapping[str, object], Mapping[str, object], Mapping[str, object]]:
    """Validate the root that authorizes the release-evidence role.

    Evidence is checked after the catalog chain in the release workflow, but
    this helper keeps the standalone API fail-closed as well.  The initial
    root must be anchored to the pinned public key; subsequent roots require
    the previously persisted root and its contiguous transition.
    """

    root = _envelope(root_bytes, "root")
    keys, roles = _root_signed(root, now=now)
    root_version = _version(root["signed"]["version"], "root")  # type: ignore[index]
    root_digest = _signed_digest(root)
    if trusted is not None and trusted.root_metadata is None and (
        trusted.root
        or trusted.snapshot
        or trusted.current
        or trusted.root_digest
        or trusted.snapshot_digest
        or trusted.current_digest
    ):
        raise RollbackError("persisted evidence trust state is incomplete")
    if trusted is None or trusted.root_metadata is None:
        if root_version != 1 or ROOT_KEY_ID not in roles["root"]["keyids"]:  # type: ignore[index]
            raise RotationError("initial evidence root is not anchored to the pinned key")
        if keys.get(ROOT_KEY_ID) != ROOT_PUBLIC_KEY:
            raise RotationError("initial evidence root does not contain the pinned key")
        _verify_threshold(
            root,
            keys=keys,
            role=roles["root"],
            label="root",
            required_keyid=ROOT_KEY_ID,
        )
    else:
        _trusted_version("root", root_version, root_digest, trusted)
        previous = _previous_root(trusted.root_metadata)
        previous_keys, previous_roles = _root_signed(previous, now=now)
        previous_version = _version(previous["signed"]["version"], "trusted root")  # type: ignore[index]
        if root_version == previous_version + 1:
            _verify_threshold(
                root,
                keys=previous_keys,
                role=previous_roles["root"],
                label="previous-root",
                allow_unknown=True,
            )
            _verify_threshold(
                root,
                keys=keys,
                role=roles["root"],
                label="root",
                allow_unknown=True,
            )
        elif root_version == previous_version:
            if not trusted.root_digest or not hmac.compare_digest(trusted.root_digest, root_digest):
                raise RollbackError("same-version evidence root differs")
            _verify_threshold(root, keys=keys, role=roles["root"], label="root")
        else:
            raise RotationError("evidence root rotation must advance exactly one version")
    return root, keys, roles


def verify_release_evidence(
    root_bytes: bytes,
    evidence_bytes: bytes,
    *,
    expected_identity: Mapping[str, object],
    now: datetime | None = None,
    trusted: TrustedVersions | None = None,
) -> VerifiedEvidence:
    """Verify a signed aggregate ``release-evidence.json`` document.

    The canonical top-level ``signatures`` list is over the canonical JSON
    body with that field removed.  A legacy singular ``signature`` mapping is
    accepted only for a threshold-one evidence role.  Every signature is
    authorized by the distinct ``evidence`` role in the pinned/rotated root;
    a structural ``keyid``/``sig`` check is never sufficient.  The caller's
    expected candidate identity is mandatory so evidence cannot be accepted
    without a release binding.  The returned mapping also carries a
    ``TrustedVersions`` checkpoint with the accepted evidence generation.
    """

    clock = datetime.now(timezone.utc) if now is None else now
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ExpiryError("evidence verification clock must be timezone-aware")
    if not isinstance(expected_identity, Mapping):
        raise FormatError("expected release identity is required")
    if trusted is not None and not isinstance(trusted, TrustedVersions):
        raise FormatError("trusted evidence state is invalid")
    if trusted is not None:
        _validate_trusted_state(trusted)
    evidence = parse_json_bytes(evidence_bytes)
    base_fields = {
        "format", "project", "version", "commit", "status", "candidate",
        "platforms",
    }
    if set(evidence) == base_fields | {"signatures"}:
        signature_field = "signatures"
        raw_signatures = evidence.get("signatures")
        if not isinstance(raw_signatures, list) or not raw_signatures:
            raise SignatureError("release evidence signatures are missing or malformed")
        signature_entries = raw_signatures
    elif set(evidence) == base_fields | {"signature"}:
        # Compatibility for the pre-PR9 candidate shape.  The role threshold
        # is checked below, so this cannot satisfy a threshold greater than 1.
        signature_field = "signature"
        raw_signature = evidence.get("signature")
        if not isinstance(raw_signature, Mapping):
            raise SignatureError("release evidence signature is missing or malformed")
        signature_entries = [raw_signature]
    else:
        raise FormatError("release evidence fields are not closed")
    if evidence.get("format") != 1 or evidence.get("project") != TRUSTED_STATE_PROJECT:
        raise FormatError("release evidence identity is invalid")
    if evidence.get("status") != "complete":
        raise FormatError("release evidence is not complete")
    top_version_value = evidence.get("version")
    _semver_parts(top_version_value, "release evidence.version")
    assert isinstance(top_version_value, str)
    top_version = top_version_value
    top_commit = evidence.get("commit")
    if not isinstance(top_commit, str) or _HEX40.fullmatch(top_commit) is None:
        raise FormatError("invalid release evidence.commit")
    candidate = _evidence_identity(evidence.get("candidate"), "release evidence.candidate")
    expected = _evidence_identity(expected_identity, "expected_identity")
    if candidate != expected:
        raise DigestError("release evidence candidate identity differs")
    if candidate["version"] != top_version or candidate["commit"] != top_commit:
        raise DigestError("release evidence top-level identity differs")
    platforms = evidence.get("platforms")
    if not isinstance(platforms, Mapping) or not platforms:
        raise FormatError("release evidence has no platform reports")
    now_utc = clock.astimezone(timezone.utc)
    for platform, report in platforms.items():
        if not isinstance(platform, str) or _PLATFORM_NAME.fullmatch(platform) is None:
            raise FormatError("release evidence has an invalid platform")
        if not isinstance(report, Mapping):
            raise FormatError(f"release evidence report is invalid: {platform}")
        _reject_evidence_secret_fields(report, f"evidence.{platform}")
        recorded = _utc(report.get("recorded_at"), f"evidence.{platform}.recorded_at")
        if recorded > now_utc + MAX_CLOCK_SKEW:
            raise ExpiryError(f"evidence for {platform} is issued in the future")
        if now_utc - recorded >= EVIDENCE_MAX_LIFETIME:
            raise ExpiryError(f"evidence for {platform} is stale")
    for entry in signature_entries:
        if not isinstance(entry, Mapping) or set(entry) != {"keyid", "sig"}:
            raise SignatureError("malformed release evidence signature")
        _decode_signature(entry.get("sig"))
    body = dict(evidence)
    del body[signature_field]
    root, keys, roles = _verify_evidence_root(
        root_bytes,
        now=clock.astimezone(timezone.utc),
        trusted=trusted,
    )
    envelope = {
        "signed": body,
        "signatures": [dict(entry) for entry in signature_entries],
    }
    _verify_threshold(
        envelope,
        keys=keys,
        role=roles["evidence"],
        label="evidence",
    )
    evidence_digest = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
    if trusted is not None:
        _trusted_evidence(top_version, evidence_digest, trusted)
    root_signed = root.get("signed")
    if not isinstance(root_signed, Mapping):
        raise FormatError("release evidence root is invalid")
    root_version = _version(root_signed.get("version"), "root")
    root_digest = _signed_digest(root)
    # Evidence verification is also a trust-state boundary.  Persist the
    # accepted root checkpoint together with the evidence generation so a
    # caller starting from evidence-only state can later follow N -> N+1
    # root rotation instead of silently reverting to the pinned-root bootstrap
    # path on every process restart.
    checkpoint = TrustedVersions() if trusted is None else trusted
    checkpoint = dataclasses.replace(
        checkpoint,
        root=root_version,
        root_digest=root_digest,
        root_metadata=canonical_json_bytes(root),
    )
    next_trusted = checkpoint.with_evidence(
        top_version,
        evidence_digest,
    )
    # Keep the signed document's top-level fields immutable; callers must
    # persist this document/checkpoint pair instead of rebuilding either from
    # a mutable result dict.
    return VerifiedEvidence(MappingProxyType(dict(evidence)), next_trusted, evidence_digest)


def _signed_digest(envelope: Mapping[str, object]) -> str:
    signed = envelope.get("signed")
    if not isinstance(signed, Mapping):
        raise FormatError("metadata signed body is invalid")
    return hashlib.sha256(canonical_json_bytes(signed)).hexdigest()


def _trusted_version(
    name: str,
    version: int,
    digest: str,
    trusted: TrustedVersions,
) -> None:
    previous = getattr(trusted, name)
    previous_digest = getattr(trusted, f"{name}_digest")
    if version < previous:
        raise RollbackError(f"{name} metadata rolls back from {previous} to {version}")
    if version == previous and (
        not isinstance(previous_digest, str)
        or not hmac.compare_digest(previous_digest, digest)
    ):
        raise RollbackError(f"{name} metadata equivocates at version {version}")


def _previous_root(value: bytes | Mapping[str, object]) -> dict[str, object]:
    if isinstance(value, bytes):
        return _envelope(value, "root")
    if isinstance(value, Mapping):
        if set(value) != {"signatures", "signed"}:
            raise FormatError("trusted root envelope is invalid")
        signatures = value.get("signatures")
        signed = value.get("signed")
        if not isinstance(signatures, list) or not signatures or not isinstance(signed, Mapping):
            raise FormatError("trusted root envelope is invalid")
        if signed.get("_type") != "root":
            raise FormatError("trusted root envelope role is invalid")
        # Validate the persisted envelope's signature shape before it can be
        # used as the previous-root authority during a rotation.  The
        # cryptographic transition is still checked against the previous
        # role below; this guard prevents malformed state from escaping as a
        # KeyError/AssertionError.
        for entry in signatures:
            if not isinstance(entry, Mapping) or set(entry) != {"keyid", "sig"}:
                raise FormatError("trusted root envelope signature is invalid")
            keyid = entry.get("keyid")
            if not isinstance(keyid, str) or _HEX64.fullmatch(keyid) is None:
                raise FormatError("trusted root envelope signature keyid is invalid")
            _decode_signature(entry.get("sig"))
        return dict(value)
    raise FormatError("trusted root envelope is invalid")


def _trusted_root_mapping(value: bytes | Mapping[str, object]) -> dict[str, object]:
    """Return a validated public root envelope suitable for persistence.

    The installed client retains the signed root envelope so a subsequent
    root rotation can be checked against the previously trusted key set.  It
    contains public keys and signatures only; accepting anything else here
    would make the on-disk trust state an unsafe extension point.
    """

    root = _previous_root(value)
    # Validate the closed root shape without making persistence dependent on
    # the current clock.  Verification performs the live expiry check again.
    _root_signed(root, now=datetime(1970, 1, 1, tzinfo=timezone.utc))
    return root


def _trusted_state_values(versions: TrustedVersions) -> dict[str, object]:
    if not isinstance(versions, TrustedVersions):
        raise ValueError("trusted versions must be a TrustedVersions value")
    root_metadata: dict[str, object] | None = None
    if versions.root_metadata is not None:
        root_metadata = _trusted_root_mapping(versions.root_metadata)
    if versions.root == 0:
        if (
            root_metadata is not None
            or versions.root_digest is not None
            or versions.snapshot != 0
            or versions.snapshot_digest is not None
            or versions.current != 0
            or versions.current_digest is not None
        ):
            raise ValueError("empty trusted root state contains catalog metadata")
    elif root_metadata is None or versions.root_digest is None:
        raise ValueError("trusted root state is incomplete")
    for name in ("snapshot", "current"):
        version = getattr(versions, name)
        digest = getattr(versions, f"{name}_digest")
        if (version == 0) != (digest is None):
            raise ValueError(f"trusted {name} state is incomplete")
    if (versions.snapshot == 0) != (versions.current == 0):
        raise ValueError("trusted catalog role checkpoint is incomplete")
    if (versions.evidence_version is None) != (versions.evidence_digest is None):
        raise ValueError("trusted evidence state is incomplete")
    if root_metadata is not None:
        signed = root_metadata.get("signed")
        assert isinstance(signed, Mapping)
        if signed.get("version") != versions.root:
            raise ValueError("trusted root metadata version does not match state")
        if versions.root_digest != _signed_digest(root_metadata):
            raise ValueError("trusted root metadata digest does not match state")
    return {
        "format": TRUSTED_STATE_FORMAT,
        "project": TRUSTED_STATE_PROJECT,
        "root": versions.root,
        "snapshot": versions.snapshot,
        "current": versions.current,
        "root_digest": versions.root_digest,
        "snapshot_digest": versions.snapshot_digest,
        "current_digest": versions.current_digest,
        "root_metadata": root_metadata,
        "evidence_version": versions.evidence_version,
        "evidence_digest": versions.evidence_digest,
    }


def _validate_trusted_state(versions: TrustedVersions) -> None:
    """Reject a malformed in-memory checkpoint before any trust decision."""

    try:
        _trusted_state_values(versions)
    except TrustError:
        raise
    except (TypeError, ValueError) as error:
        raise FormatError("trusted-version state is invalid") from error


def serialize_trusted_versions(versions: TrustedVersions) -> bytes:
    """Serialize trusted versions as strict canonical, secret-free JSON."""

    return canonical_json_bytes(_trusted_state_values(versions))


def deserialize_trusted_versions(payload: bytes) -> TrustedVersions:
    """Parse and validate one persisted trusted-version state document."""

    value = parse_json_bytes(payload, maximum_size=MAX_METADATA_BYTES)
    legacy_expected = {
        "format", "project", "root", "snapshot", "current", "root_digest",
        "snapshot_digest", "current_digest", "root_metadata",
    }
    current_expected = legacy_expected | {"evidence_version", "evidence_digest"}
    format_value = value.get("format")
    if format_value == TRUSTED_STATE_LEGACY_FORMAT:
        if set(value) != legacy_expected:
            raise FormatError("legacy trusted-version state fields are not closed")
    elif format_value == TRUSTED_STATE_FORMAT:
        if set(value) != current_expected:
            raise FormatError("trusted-version state fields are not closed")
    else:
        raise FormatError("unsupported trusted-version state format")
    if value.get("project") != TRUSTED_STATE_PROJECT:
        raise FormatError("trusted-version state identity is incompatible")
    try:
        versions = TrustedVersions(
            root=value["root"],  # type: ignore[arg-type]
            snapshot=value["snapshot"],  # type: ignore[arg-type]
            current=value["current"],  # type: ignore[arg-type]
            root_digest=value["root_digest"],  # type: ignore[arg-type]
            snapshot_digest=value["snapshot_digest"],  # type: ignore[arg-type]
            current_digest=value["current_digest"],  # type: ignore[arg-type]
            root_metadata=value["root_metadata"],  # type: ignore[arg-type]
            evidence_version=value.get("evidence_version"),  # type: ignore[arg-type]
            evidence_digest=value.get("evidence_digest"),  # type: ignore[arg-type]
        )
        _trusted_state_values(versions)
    except (TypeError, ValueError, TrustError) as error:
        if isinstance(error, TrustError):
            raise
        raise FormatError("trusted-version state is invalid") from error
    return versions


# ``load_trusted_versions`` is the descriptive name used by installed-client
# callers; retain the deserialize spelling for code that treats this as a
# wire-format boundary.
load_trusted_versions = deserialize_trusted_versions


def _verify_role_chain(
    root_bytes: bytes,
    current_bytes: bytes,
    snapshot_bytes: bytes,
    *,
    now: datetime | None,
    trusted: TrustedVersions | None,
) -> tuple[
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
    Mapping[str, object],
    datetime,
    TrustedVersions,
]:
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime):
        raise ExpiryError("verification clock must be a datetime")
    trusted = TrustedVersions() if trusted is None else trusted
    _validate_trusted_state(trusted)
    root = _envelope(root_bytes, "root")
    current = _envelope(current_bytes, "current")
    snapshot = _envelope(snapshot_bytes, "snapshot")
    keys, roles = _root_signed(root, now=now)
    root_version = _version(root["signed"]["version"], "root")  # type: ignore[index]
    root_digest = _signed_digest(root)
    _trusted_version("root", root_version, root_digest, trusted)
    if trusted.root and root_version > trusted.root + 1:
        raise RotationError("root rotation skipped a version")

    if trusted.root_metadata is None:
        if root_version != 1 or ROOT_KEY_ID not in roles["root"]["keyids"]:  # type: ignore[index]
            raise RotationError("initial root is not anchored to the pinned key")
        if keys.get(ROOT_KEY_ID) != ROOT_PUBLIC_KEY:
            raise RotationError("initial root does not contain the pinned public key")
        _verify_threshold(
            root,
            keys=keys,
            role=roles["root"],
            label="root",
            allow_unknown=True,
            required_keyid=ROOT_KEY_ID,
        )
    else:
        previous = _previous_root(trusted.root_metadata)
        previous_keys, previous_roles = _root_signed(previous, now=now)
        previous_version = _version(previous["signed"]["version"], "trusted root")  # type: ignore[index]
        if root_version == previous_version + 1:
            _verify_threshold(
                root,
                keys=previous_keys,
                role=previous_roles["root"],
                label="previous-root",
                allow_unknown=True,
            )
            _verify_threshold(
                root,
                keys=keys,
                role=roles["root"],
                label="root",
                allow_unknown=True,
            )
        elif root_version == previous_version:
            if trusted.root_digest and hmac.compare_digest(root_digest, trusted.root_digest):
                _verify_threshold(root, keys=keys, role=roles["root"], label="root")
            else:
                raise RollbackError("same-version root metadata differs")
        else:
            raise RotationError("root rotation must advance exactly one version")
    return root, current, snapshot, keys, roles, now, trusted


def verify_release_metadata_roles(
    root_bytes: bytes,
    current_bytes: bytes,
    snapshot_bytes: bytes,
    *,
    now: datetime | None = None,
    trusted: TrustedVersions | None = None,
) -> VerifiedMetadata:
    """Verify signed roles before a catalog is downloaded.

    Target length and digest checks for the catalog are intentionally deferred
    until :func:`verify_release_metadata` receives the catalog bytes.  All
    root/current/snapshot signatures, clocks, versions and snapshot binding
    are checked here so a bad role cannot trigger an unauthenticated catalog
    request.
    """

    root, current, snapshot, keys, roles, now, trusted = _verify_role_chain(
        root_bytes,
        current_bytes,
        snapshot_bytes,
        now=now,
        trusted=trusted,
    )
    return _verify_non_root_metadata(
        root,
        current,
        snapshot,
        snapshot_bytes,
        None,
        keys,
        roles,
        now,
        trusted,
    )


def verify_release_metadata(
    root_bytes: bytes,
    current_bytes: bytes,
    snapshot_bytes: bytes,
    catalog_bytes: bytes,
    *,
    now: datetime | None = None,
    trusted: TrustedVersions | None = None,
) -> VerifiedMetadata:
    """Verify root/current/snapshot and the catalog target they authenticate."""

    if not isinstance(catalog_bytes, bytes):
        raise DigestError("catalog payload must be bytes")
    root, current, snapshot, keys, roles, now, trusted = _verify_role_chain(
        root_bytes,
        current_bytes,
        snapshot_bytes,
        now=now,
        trusted=trusted,
    )
    return _verify_non_root_metadata(
        root,
        current,
        snapshot,
        snapshot_bytes,
        catalog_bytes,
        keys,
        roles,
        now,
        trusted,
    )


def _verify_non_root_metadata(
    root: Mapping[str, object],
    current: Mapping[str, object],
    snapshot: Mapping[str, object],
    snapshot_bytes: bytes,
    catalog_bytes: bytes | None,
    keys: Mapping[str, object],
    roles: Mapping[str, object],
    now: datetime,
    trusted: TrustedVersions,
) -> VerifiedMetadata:
    current_signed = current["signed"]
    snapshot_signed = snapshot["signed"]
    assert isinstance(current_signed, Mapping)
    assert isinstance(snapshot_signed, Mapping)
    if set(current_signed) != {"_type", "spec_version", "version", "issued", "expires", "snapshot", "release"}:
        raise FormatError("current metadata fields are not closed")
    if set(snapshot_signed) != {"_type", "spec_version", "version", "issued", "expires", "catalog"}:
        raise FormatError("snapshot metadata fields are not closed")
    for body, label in ((current_signed, "current"), (snapshot_signed, "snapshot")):
        if body.get("spec_version") != "1.0":
            raise FormatError(f"unsupported {label} metadata contract")
        _version(body.get("version"), label)
        _check_clock(body, now=now, kind=label)
    _verify_threshold(current, keys=keys, role=roles["current"], label="current")
    _verify_threshold(snapshot, keys=keys, role=roles["snapshot"], label="snapshot")

    current_version = _version(current_signed["version"], "current")
    snapshot_version = _version(snapshot_signed["version"], "snapshot")
    current_digest = _signed_digest(current)
    snapshot_digest = _signed_digest(snapshot)
    _trusted_version("current", current_version, current_digest, trusted)
    _trusted_version("snapshot", snapshot_version, snapshot_digest, trusted)

    current_target = _target(current_signed.get("snapshot"), "current.snapshot")
    snapshot_target = _target(snapshot_signed.get("catalog"), "snapshot.catalog")
    if current_target.version != snapshot_version:
        raise DigestError("current points to a different snapshot version")
    if current_target.path != "maintenance/inventory/trust/snapshot.json":
        raise DigestError("current points outside the canonical snapshot")
    if snapshot_target.version != snapshot_version:
        raise DigestError("snapshot points to a different catalog version")
    # ``current.snapshot`` authenticates the exact bytes fetched for the
    # snapshot role, not merely its parsed object.  The role signature covers
    # the canonical ``signed`` body, but an attacker could otherwise rewrite
    # envelope whitespace/order while leaving that signature intact and still
    # satisfy a digest computed from the re-serialized mapping.
    if (
        current_target.length != len(snapshot_bytes)
        or current_target.sha256 != hashlib.sha256(snapshot_bytes).hexdigest()
    ):
        raise DigestError("current snapshot target digest or length differs")
    if snapshot_target.path != "site/public/api/v1/catalog.json":
        raise DigestError("snapshot points outside the canonical catalog")
    if catalog_bytes is not None and (
        snapshot_target.length != len(catalog_bytes)
        or snapshot_target.sha256 != hashlib.sha256(catalog_bytes).hexdigest()
    ):
        raise DigestError("catalog target digest or length differs")
    release = current_signed.get("release")
    if not isinstance(release, str):
        raise FormatError("current release version is invalid")
    _semver_parts(release, "current.release")
    if "main" in release.casefold():
        raise FormatError("current release version is invalid")
    versions = TrustedVersions(
        root=_version(root["signed"]["version"], "root"),  # type: ignore[index]
        snapshot=snapshot_version,
        current=current_version,
        root_digest=_signed_digest(root),
        snapshot_digest=snapshot_digest,
        current_digest=current_digest,
        root_metadata=canonical_json_bytes(root),
        # Catalog-role verification must not discard the separately tracked
        # release-evidence generation.  Losing this pair here would let a
        # caller persist a state that no longer protects against same-version
        # evidence equivocation on the next verification.
        evidence_version=trusted.evidence_version,
        evidence_digest=trusted.evidence_digest,
    )
    return VerifiedMetadata(versions, snapshot_target, release, root, current, snapshot)


__all__ = [
    "CURRENT_MAX_LIFETIME",
    "DigestError",
    "EVIDENCE_MAX_LIFETIME",
    "ExpiryError",
    "FormatError",
    "FreezeError",
    "ROOT_KEY_ID",
    "ROOT_PUBLIC_KEY",
    "RollbackError",
    "RotationError",
    "SignatureError",
    "Target",
    "ThresholdError",
    "TrustError",
    "TrustedVersions",
    "TRUSTED_STATE_LEGACY_FORMAT",
    "TRUSTED_STATE_FORMAT",
    "TRUSTED_STATE_PROJECT",
    "VerifiedEvidence",
    "VerifiedMetadata",
    "canonical_json_bytes",
    "deserialize_trusted_versions",
    "key_id",
    "load_trusted_versions",
    "parse_json_bytes",
    "serialize_trusted_versions",
    "verify_release_metadata",
    "verify_release_metadata_roles",
    "verify_release_evidence",
    "verify_rsa_pss_sha256",
]
