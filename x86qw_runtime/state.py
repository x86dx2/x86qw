"""Typed, side-effect-free contracts for persisted x86QW installation state."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from os import PathLike
from typing import Iterable

from .catalogs import profile_fingerprint
from .io.metadata import MetadataFileError, read_bounded_regular_file
from .versioning import COMPONENT_VERSION


class StateError(ValueError):
    """A state document does not satisfy the current persisted contract."""

    def __init__(self, message: str, *, code: str = "invalid", field_name: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field_name = field_name


MAX_INSTALL_STATE_BYTES = 256 * 1024
CURRENT_STATE_FORMAT = 2
SUPPORTED_STATE_FORMATS = frozenset({1, CURRENT_STATE_FORMAT})
_INSTALLATION_VERSION = re.compile(
    r"^(?:0\.(?:7|8|9)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"|1\.0(?:\.(0|[1-9][0-9]*))?(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"|0\.(?:7|8|9)\.x)$"
)
INSTALLATION_PROFILES = frozenset({
    "none", "custom", "essential", "recommended", "complete",
})


@dataclass(frozen=True)
class InstallState:
    format: int
    project: str
    profile: str
    requested_components: tuple[str, ...]
    recorded_components: tuple[str, ...]
    known_components: tuple[str, ...]
    capabilities: tuple[str, ...]
    component_fingerprint: str | None
    _document: dict[str, object] = field(repr=False, compare=False)

    def to_document(self) -> dict[str, object]:
        """Return an isolated document preserving the accepted persisted shape."""

        return copy.deepcopy(self._document)

    @property
    def installation_version(self) -> str | None:
        """Version marker when a historical installer recorded one."""

        return installation_version(self._document)


def installation_version(document: object) -> str | None:
    """Read the optional installer-version marker without guessing ownership."""

    if not isinstance(document, dict):
        return None
    values = [
        document[name]
        for name in ("installation_version", "installer_version", "version")
        if name in document
    ]
    if not values:
        return None
    if len(values) != 1 or not isinstance(values[0], str) or not _INSTALLATION_VERSION.fullmatch(values[0]):
        raise StateError("invalid installation version marker", code="version")
    return values[0]


# Compatibility spelling used by migration callers that treat the value as a
# source-contract marker rather than an installer field.
read_installation_version = installation_version


def _component_list(document: dict[str, object], field_name: str) -> tuple[str, ...]:
    value = document.get(field_name)
    if (
        not isinstance(value, list)
        or not all(
            isinstance(item, str) and COMPONENT_VERSION.fullmatch(item)
            for item in value
        )
        or len(value) != len(set(value))
    ):
        raise StateError(
            f"invalid {field_name}", code="component_field", field_name=field_name,
        )
    return tuple(value)


def parse_install_state(
    document: object,
    *,
    allowed_profiles: Iterable[str],
    allowed_capabilities: Iterable[str],
) -> InstallState:
    """Validate a format-2 document without reading or mutating the filesystem."""

    if not isinstance(document, dict):
        raise StateError("invalid install state document", code="identity")
    profiles = frozenset(allowed_profiles)
    capabilities_allowed = frozenset(allowed_capabilities)
    profile = document.get("profile")
    format_value = document.get("format")
    if (
        type(format_value) is not int
        or format_value not in SUPPORTED_STATE_FORMATS
        or document.get("project") != "x86qw"
        or not isinstance(profile, str)
        or profile not in profiles
    ):
        raise StateError("invalid install state identity", code="identity")
    requested = _component_list(document, "requested_components")
    recorded = _component_list(document, "recorded_components")
    known = _component_list(document, "known_components")
    installation_version(document)
    if profile != "custom" and requested:
        raise StateError(
            "only custom profiles may persist requested components",
            code="custom_requested",
        )
    if format_value == CURRENT_STATE_FORMAT:
        capabilities = _component_list(document, "capabilities")
        fingerprint = document.get("component_fingerprint")
        if (
            set(capabilities) - capabilities_allowed
            or fingerprint != profile_fingerprint(list(recorded))
        ):
            raise StateError(
                "invalid capabilities or component fingerprint", code="capabilities",
            )
    else:
        capabilities = ()
        fingerprint = None
    return InstallState(
        format=int(format_value),
        project="x86qw",
        profile=profile,
        requested_components=requested,
        recorded_components=recorded,
        known_components=known,
        capabilities=capabilities,
        component_fingerprint=fingerprint if isinstance(fingerprint, str) else None,
        _document=copy.deepcopy(document),
    )


def read_install_state(
    path: PathLike[str] | str,
    *,
    allowed_profiles: Iterable[str],
    allowed_capabilities: Iterable[str],
    maximum_size: int = MAX_INSTALL_STATE_BYTES,
) -> InstallState:
    """Read one stable, bounded regular state file and parse it without writes."""

    if type(maximum_size) is not int or maximum_size < 1:
        raise ValueError("maximum_size must be a positive integer")
    try:
        payload = read_bounded_regular_file(path, maximum_size=maximum_size)
    except MetadataFileError as error:
        raise StateError("install state file could not be read safely") from error
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StateError("invalid install state JSON") from error
    return parse_install_state(
        document,
        allowed_profiles=allowed_profiles,
        allowed_capabilities=allowed_capabilities,
    )


def serialize_install_state(state: InstallState) -> bytes:
    """Encode the exact canonical JSON form produced by the existing installer."""

    return (
        json.dumps(state.to_document(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")


def install_state_sha256(state: InstallState | dict[str, object]) -> str:
    """Return the digest of canonical state bytes for a migration checkpoint."""

    if isinstance(state, InstallState):
        payload = serialize_install_state(state)
    elif isinstance(state, dict):
        # Validate callers' documents before hashing so a checkpoint never
        # authenticates malformed state.
        raise TypeError("a raw state document must be parsed before hashing")
    else:
        raise TypeError("state must be InstallState")
    return hashlib.sha256(payload).hexdigest()
