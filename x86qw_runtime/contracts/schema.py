"""Versioned document and catalog contracts.

This module is deliberately side-effect free.  It does not fetch a catalog or
write state; callers hand it an already loaded mapping and receive immutable
models or a defensive copy.  The existing ``format`` fields remain accepted
for historical state/receipt/catalog documents, while newly emitted metadata
can carry explicit schema versions and CLI compatibility bounds.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from types import MappingProxyType
from typing import Any, Iterator, TypeVar

from ..versioning import SemVer, parse_semver


CATALOG_SCHEMA_VERSION = 1
STATE_SCHEMA_VERSION = 2
RECEIPT_SCHEMA_VERSION = 1
CURRENT_CATALOG_VERSION = CATALOG_SCHEMA_VERSION
CURRENT_STATE_VERSION = STATE_SCHEMA_VERSION
CURRENT_RECEIPT_VERSION = RECEIPT_SCHEMA_VERSION

# The baseline installer remains usable as a reader for all documents emitted
# during the 0.x line.  A caller can override this when freezing a newer line.
DEFAULT_MIN_CLI_VERSION = "0.7.0"
IMMUTABLE_COORDINATE = re.compile(
    r"^(?:refs/tags/[A-Za-z0-9][A-Za-z0-9._-]{0,127}|commit/[0-9a-f]{40}|snapshot/[0-9a-f]{64})$"
)


class ContractError(ValueError):
    """A versioned document does not satisfy the frozen runtime contract."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_contract",
        field_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field_name = field_name


class IncompatibleCliError(ContractError):
    """The active CLI lies outside a document's declared compatibility range."""

    def __init__(self, message: str, *, field_name: str | None = None) -> None:
        super().__init__(message, code="incompatible_cli", field_name=field_name)


class SchemaKind(str):
    """String constants for the three persisted/public document families."""

    CATALOG = "catalog"
    STATE = "state"
    RECEIPT = "receipt"


_KIND_FIELDS = {
    SchemaKind.CATALOG: ("catalog_version", CATALOG_SCHEMA_VERSION, "format"),
    SchemaKind.STATE: ("state_version", STATE_SCHEMA_VERSION, "format"),
    SchemaKind.RECEIPT: ("receipt_version", RECEIPT_SCHEMA_VERSION, "format"),
}
_SUPPORTED_SCHEMA_VERSIONS = {
    SchemaKind.CATALOG: frozenset({1}),
    # State format 1 remains a readable historical contract; format 2 is the
    # current durable writer.
    SchemaKind.STATE: frozenset({1, 2}),
    SchemaKind.RECEIPT: frozenset({1}),
}
_T = TypeVar("_T")


def _validate_json_object_keys(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(
                    "canonical JSON object keys must be strings",
                    code="json",
                    field_name=str(key),
                )
            _validate_json_object_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_json_object_keys(item)


def _canonical_bytes(value: object) -> bytes:
    try:
        _validate_json_object_keys(value)
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ContractError("value cannot be represented as canonical JSON", code="json") from error
    return encoded


def canonical_json(value: object) -> bytes:
    """Return deterministic UTF-8 JSON bytes for hashes and output fixtures."""

    return _canonical_bytes(value)


def _freeze(value: _T) -> _T:
    if isinstance(value, Mapping):
        frozen = {key: _freeze(item) for key, item in value.items()}
        return MappingProxyType(frozen)  # type: ignore[return-value]
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)  # type: ignore[return-value]
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)  # type: ignore[return-value]
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    return value


def _coerce_positive_int(
    value: object,
    field_name: str,
    *,
    supported: frozenset[int] | None = None,
) -> int:
    if type(value) is not int or value < 1:
        raise ContractError(
            f"{field_name} must be a positive integer",
            code="schema_version",
            field_name=field_name,
        )
    if supported is not None and value not in supported:
        raise ContractError(
            f"{field_name} is not a supported schema version: {value}",
            code="schema_version",
            field_name=field_name,
        )
    return value


def _coerce_semver(value: object, field_name: str) -> SemVer:
    if isinstance(value, SemVer):
        return value
    if not isinstance(value, str):
        raise ContractError(
            f"{field_name} must be a SemVer string",
            code="cli_version",
            field_name=field_name,
        )
    try:
        return parse_semver(value)
    except ValueError as error:
        raise ContractError(
            f"{field_name} must be a SemVer string: {value!r}",
            code="cli_version",
            field_name=field_name,
        ) from error


@dataclass(frozen=True)
class ContractVersions:
    """The versions and CLI range carried by a frozen contract.

    ``catalog_version``, ``state_version`` and ``receipt_version`` are kept in
    one model so a release can negotiate all persisted surfaces atomically.
    Individual documents may serialize only the field relevant to their kind.
    """

    catalog_version: int = CATALOG_SCHEMA_VERSION
    state_version: int = STATE_SCHEMA_VERSION
    receipt_version: int = RECEIPT_SCHEMA_VERSION
    min_cli_version: SemVer | str = DEFAULT_MIN_CLI_VERSION
    max_cli_version: SemVer | str | None = None

    def __post_init__(self) -> None:
        for kind, field_name in (
            (SchemaKind.CATALOG, "catalog_version"),
            (SchemaKind.STATE, "state_version"),
            (SchemaKind.RECEIPT, "receipt_version"),
        ):
            _coerce_positive_int(
                getattr(self, field_name),
                field_name,
                supported=_SUPPORTED_SCHEMA_VERSIONS[kind],
            )
        minimum = _coerce_semver(self.min_cli_version, "min_cli_version")
        maximum = (
            _coerce_semver(self.max_cli_version, "max_cli_version")
            if self.max_cli_version is not None
            else None
        )
        if maximum is not None and maximum < minimum:
            raise ContractError(
                "max_cli_version must not be lower than min_cli_version",
                code="cli_range",
                field_name="max_cli_version",
            )
        object.__setattr__(self, "min_cli_version", minimum)
        object.__setattr__(self, "max_cli_version", maximum)

    @property
    def catalog_schema_version(self) -> int:
        return self.catalog_version

    @property
    def state_schema_version(self) -> int:
        return self.state_version

    @property
    def receipt_schema_version(self) -> int:
        return self.receipt_version

    def to_document(self, *, kind: str | None = None) -> dict[str, object]:
        """Serialize canonical version fields, optionally scoped to one kind."""

        if kind is not None and kind not in _KIND_FIELDS:
            raise ContractError(f"unknown contract kind: {kind}", code="kind")
        values: dict[str, object] = {
            "catalog_version": self.catalog_version,
            "state_version": self.state_version,
            "receipt_version": self.receipt_version,
            "min_cli_version": str(self.min_cli_version),
        }
        if self.max_cli_version is not None:
            values["max_cli_version"] = str(self.max_cli_version)
        if kind is not None:
            selected = _KIND_FIELDS[kind][0]
            values = {
                selected: values[selected],
                "min_cli_version": values["min_cli_version"],
                **({"max_cli_version": values["max_cli_version"]} if "max_cli_version" in values else {}),
            }
        return values

    def supports(self, cli_version: str | SemVer) -> bool:
        candidate = _coerce_semver(cli_version, "cli_version")
        return candidate >= self.min_cli_version and (
            self.max_cli_version is None or candidate <= self.max_cli_version
        )

    def require_compatible(self, cli_version: str | SemVer) -> None:
        if not self.supports(cli_version):
            raise IncompatibleCliError(
                f"CLI {cli_version} is outside the supported range "
                f"{self.min_cli_version}..{self.max_cli_version or 'unbounded'}"
            )

    @classmethod
    def from_document(
        cls,
        document: Mapping[str, object],
        *,
        kind: str | None = None,
        allow_legacy: bool = True,
    ) -> "ContractVersions":
        if not isinstance(document, Mapping):
            raise ContractError("contract document must be an object", code="identity")
        if kind is not None and kind not in _KIND_FIELDS:
            raise ContractError(f"unknown contract kind: {kind}", code="kind")

        def has_schema_field(field_name: str) -> bool:
            return field_name in document or field_name.replace("_version", "_schema_version") in document

        explicit_schema_fields = {
            field_name for field_name, _default, _legacy in _KIND_FIELDS.values()
            if has_schema_field(field_name)
        }

        def value_for(field_name: str, default: int) -> object:
            schema_alias = field_name.replace("_version", "_schema_version")
            if field_name in document and schema_alias in document and document[field_name] != document[schema_alias]:
                raise ContractError(
                    f"{field_name} and {schema_alias} disagree",
                    code="schema_version",
                    field_name=field_name,
                )
            if field_name in document:
                return document[field_name]
            if schema_alias in document:
                return document[schema_alias]
            if allow_legacy:
                return default
            raise ContractError(
                f"missing {field_name}", code="schema_version", field_name=field_name,
            )

        if kind is None:
            catalog = value_for("catalog_version", CATALOG_SCHEMA_VERSION)
            state = value_for("state_version", STATE_SCHEMA_VERSION)
            receipt = value_for("receipt_version", RECEIPT_SCHEMA_VERSION)
            if explicit_schema_fields and "min_cli_version" not in document:
                raise ContractError(
                    "explicit schema versions require min_cli_version",
                    code="cli_version",
                    field_name="min_cli_version",
                )
        else:
            selected, default, legacy_field = _KIND_FIELDS[kind]
            other_schema_fields = explicit_schema_fields - {selected}
            if other_schema_fields:
                raise ContractError(
                    f"{kind} document contains unrelated schema fields: {sorted(other_schema_fields)}",
                    code="schema_version",
                    field_name=selected,
                )
            schema_alias = selected.replace("_version", "_schema_version")
            selected_explicit = has_schema_field(selected)
            if selected in document and schema_alias in document and document[selected] != document[schema_alias]:
                raise ContractError(
                    f"{selected} and {schema_alias} disagree",
                    code="schema_version",
                    field_name=selected,
                )
            selected_value = document.get(selected, document.get(schema_alias))
            if selected_value is None and allow_legacy and not selected_explicit:
                selected_value = document.get(legacy_field, default)
                # Historical TSV receipts encode ``format`` as the string
                # ``"1"``; accept that representation only on the legacy
                # fallback while keeping explicit schema fields typed.
                if legacy_field == "format" and isinstance(selected_value, str) and selected_value.isdigit():
                    selected_value = int(selected_value)
            elif selected_value is not None and allow_legacy and legacy_field in document:
                legacy_value = document[legacy_field]
                if legacy_field == "format" and isinstance(legacy_value, str) and legacy_value.isdigit():
                    legacy_value = int(legacy_value)
                elif legacy_field == "format" and type(legacy_value) is not int:
                    raise ContractError(
                        f"legacy {legacy_field} must be an integer",
                        code="schema_version",
                        field_name=selected,
                    )
                if type(legacy_value) is int and legacy_value != selected_value:
                    raise ContractError(
                        f"{selected} and legacy {legacy_field} disagree",
                        code="schema_version",
                        field_name=selected,
                    )
            if selected_value is None:
                raise ContractError(f"missing {selected}", code="schema_version", field_name=selected)
            if selected_explicit and "min_cli_version" not in document:
                raise ContractError(
                    "explicit schema versions require min_cli_version",
                    code="cli_version",
                    field_name="min_cli_version",
                )
            catalog = document.get("catalog_version", CATALOG_SCHEMA_VERSION)
            state = document.get("state_version", STATE_SCHEMA_VERSION)
            receipt = document.get("receipt_version", RECEIPT_SCHEMA_VERSION)
            if selected == "catalog_version":
                catalog = selected_value
            elif selected == "state_version":
                state = selected_value
            else:
                receipt = selected_value

        if "max_cli_version" in document and "min_cli_version" not in document:
            raise ContractError(
                "max_cli_version requires min_cli_version",
                code="cli_version",
                field_name="min_cli_version",
            )
        if "min_cli_version" not in document and not allow_legacy:
            raise ContractError(
                "missing min_cli_version",
                code="cli_version",
                field_name="min_cli_version",
            )
        minimum = document.get("min_cli_version", DEFAULT_MIN_CLI_VERSION)
        maximum = document.get("max_cli_version")
        return cls(catalog, state, receipt, minimum, maximum)  # type: ignore[arg-type]


def contract_versions_for(
    document: Mapping[str, object], *, kind: str, current_cli_version: str | SemVer | None = None,
) -> ContractVersions:
    """Parse and optionally negotiate a document's version metadata."""

    versions = ContractVersions.from_document(document, kind=kind)
    if current_cli_version is not None:
        versions.require_compatible(current_cli_version)
    return versions


def add_contract_versions(
    document: Mapping[str, object],
    versions: ContractVersions,
    *,
    kind: str | None = None,
) -> dict[str, object]:
    """Return a defensive copy carrying explicit, canonical version fields."""

    if not isinstance(document, Mapping):
        raise ContractError("contract document must be an object", code="identity")
    result = dict(_thaw(document))  # type: ignore[arg-type]
    result.update(versions.to_document(kind=kind))
    return result


def validate_document_versions(
    document: Mapping[str, object],
    *,
    kind: str,
    current_cli_version: str | SemVer | None = None,
    allow_legacy: bool = True,
) -> ContractVersions:
    """Validate one catalog/state/receipt document without mutating it."""

    if kind not in _KIND_FIELDS:
        raise ContractError(f"unknown contract kind: {kind}", code="kind")
    versions = ContractVersions.from_document(document, kind=kind, allow_legacy=allow_legacy)
    if current_cli_version is not None:
        versions.require_compatible(current_cli_version)
    return versions


@dataclass(frozen=True)
class CatalogSnapshot(Mapping[str, object]):
    """An immutable, content-addressed snapshot of a catalog document."""

    document: Mapping[str, object]
    catalog_version: int = CATALOG_SCHEMA_VERSION
    digest: str | None = None
    coordinate: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.document, Mapping):
            raise ContractError("catalog snapshot must contain an object", code="identity")
        _coerce_positive_int(
            self.catalog_version,
            "catalog_version",
            supported=_SUPPORTED_SCHEMA_VERSIONS[SchemaKind.CATALOG],
        )
        versions = validate_document_versions(self.document, kind=SchemaKind.CATALOG)
        if versions.catalog_version != self.catalog_version:
            raise ContractError(
                "catalog snapshot version does not match its document",
                code="schema_version",
                field_name="catalog_version",
            )
        frozen = _freeze(dict(self.document))
        object.__setattr__(self, "document", frozen)
        digest = sha256(_canonical_bytes(_thaw(frozen))).hexdigest()
        if self.digest is not None:
            if not isinstance(self.digest, str) or self.digest != digest:
                raise ContractError(
                    "catalog snapshot digest does not match its document",
                    code="snapshot_digest",
                    field_name="digest",
                )
        object.__setattr__(self, "digest", digest)
        if self.coordinate is not None and (
            not isinstance(self.coordinate, str)
            or IMMUTABLE_COORDINATE.fullmatch(self.coordinate) is None
        ):
            raise ContractError(
                "catalog snapshots require an immutable coordinate",
                code="mutable_coordinate",
                field_name="coordinate",
            )

    @classmethod
    def from_document(
        cls,
        document: Mapping[str, object],
        *,
        catalog_version: int = CATALOG_SCHEMA_VERSION,
        coordinate: str | None = None,
        digest: str | None = None,
    ) -> "CatalogSnapshot":
        return cls(document, catalog_version, digest, coordinate)

    @property
    def snapshot_id(self) -> str:
        assert self.digest is not None
        return self.digest

    def __getitem__(self, key: str) -> object:
        return self.document[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.document)

    def __len__(self) -> int:
        return len(self.document)

    def to_document(self) -> dict[str, object]:
        return _thaw(self.document)  # type: ignore[return-value]

    def verify(self, digest: str) -> bool:
        return isinstance(digest, str) and digest == self.digest


def snapshot_catalog(
    document: Mapping[str, object],
    *,
    catalog_version: int = CATALOG_SCHEMA_VERSION,
    coordinate: str | None = None,
    digest: str | None = None,
) -> CatalogSnapshot:
    return CatalogSnapshot.from_document(
        document,
        catalog_version=catalog_version,
        coordinate=coordinate,
        digest=digest,
    )


def freeze_catalog(
    document: Mapping[str, object],
    *,
    catalog_version: int = CATALOG_SCHEMA_VERSION,
    coordinate: str | None = None,
    digest: str | None = None,
) -> CatalogSnapshot:
    """Explicitly named alias for callers freezing a loaded catalog."""

    return snapshot_catalog(
        document,
        catalog_version=catalog_version,
        coordinate=coordinate,
        digest=digest,
    )


def negotiate_cli_version(
    document: Mapping[str, object],
    *,
    kind: str,
    cli_version: str | SemVer,
) -> ContractVersions:
    """Validate schema metadata and require a compatible CLI version."""

    return validate_document_versions(
        document,
        kind=kind,
        current_cli_version=cli_version,
    )


validate_schema_versions = validate_document_versions


# Compatibility aliases used by early contract prototypes.
SchemaVersions = ContractVersions
CatalogContract = CatalogSnapshot
CatalogSchema = ContractVersions
StateSchema = ContractVersions
ReceiptSchema = ContractVersions


__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "CURRENT_CATALOG_VERSION",
    "CURRENT_RECEIPT_VERSION",
    "CURRENT_STATE_VERSION",
    "DEFAULT_MIN_CLI_VERSION",
    "RECEIPT_SCHEMA_VERSION",
    "STATE_SCHEMA_VERSION",
    "CatalogContract",
    "CatalogSchema",
    "CatalogSnapshot",
    "ContractError",
    "ContractVersions",
    "IncompatibleCliError",
    "SchemaKind",
    "SchemaVersions",
    "StateSchema",
    "ReceiptSchema",
    "add_contract_versions",
    "canonical_json",
    "contract_versions_for",
    "freeze_catalog",
    "negotiate_cli_version",
    "snapshot_catalog",
    "validate_schema_versions",
    "validate_document_versions",
]
