"""Stable/nightly/alpha/beta/rc and deprecation policy contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from types import MappingProxyType
from typing import Mapping

from ..versioning import NIGHTLY_VERSION, ReleaseStage, SemVer, parse_semver
from .schema import ContractError


IMMUTABLE_COORDINATE = re.compile(
    r"^(?:refs/tags/[A-Za-z0-9][A-Za-z0-9._-]{0,127}|commit/[0-9a-f]{40}|snapshot/[0-9a-f]{64})$"
)


class ReleaseChannel(str, Enum):
    ALPHA = "alpha"
    BETA = "beta"
    RC = "rc"
    STABLE = "stable"
    NIGHTLY = "nightly"


def _as_semver(value: str | SemVer, field_name: str = "version") -> SemVer:
    if isinstance(value, SemVer):
        return value
    try:
        return parse_semver(value)
    except (TypeError, ValueError) as error:
        raise ContractError(f"{field_name} must be SemVer", code="version", field_name=field_name) from error


def classify_release(value: str | SemVer) -> ReleaseChannel:
    """Return the release channel represented by a version string.

    Nightly builds retain their historical timestamp/hash syntax rather than
    pretending to be SemVer.  All other channels use SemVer prerelease labels.
    """

    if isinstance(value, str) and NIGHTLY_VERSION.fullmatch(value):
        return ReleaseChannel.NIGHTLY
    version = _as_semver(value)
    stage = version.stage
    if stage is ReleaseStage.ALPHA:
        return ReleaseChannel.ALPHA
    if stage is ReleaseStage.BETA:
        return ReleaseChannel.BETA
    if stage is ReleaseStage.RC:
        return ReleaseChannel.RC
    if stage is ReleaseStage.STABLE:
        return ReleaseChannel.STABLE
    raise ContractError(
        f"unsupported prerelease label in {version}",
        code="release_channel",
        field_name="version",
    )


def validate_release_version(value: str | SemVer, channel: str | ReleaseChannel) -> str | SemVer:
    """Validate a version against a declared release channel."""

    try:
        expected = channel if isinstance(channel, ReleaseChannel) else ReleaseChannel(channel)
    except (TypeError, ValueError) as error:
        raise ContractError(f"unknown release channel: {channel!r}", code="release_channel") from error
    actual = classify_release(value)
    if actual is not expected:
        raise ContractError(
            f"version {value!s} belongs to {actual.value}, not {expected.value}",
            code="release_channel",
            field_name="channel",
        )
    return value


@dataclass(frozen=True)
class ReleasePolicy:
    """Policy for one release channel.

    ``immutable`` applies to published coordinates.  A nightly *pointer* may
    move, but every coordinate consumed by a client must still be immutable.
    """

    channel: ReleaseChannel | str
    immutable: bool = True
    allow_mutable_pointer: bool = False
    requires_snapshot: bool = True
    description: str = ""

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> "ReleasePolicy":
        if not isinstance(document, Mapping):
            raise ContractError("release policy must be an object", code="release_policy")
        unknown = set(document) - {
            "channel", "immutable", "allow_mutable_pointer", "requires_snapshot", "description",
        }
        if unknown:
            raise ContractError(
                f"unknown release policy fields: {sorted(unknown)}",
                code="release_policy",
            )
        return cls(
            document.get("channel"),  # type: ignore[arg-type]
            document.get("immutable", True),  # type: ignore[arg-type]
            document.get("allow_mutable_pointer", False),  # type: ignore[arg-type]
            document.get("requires_snapshot", True),  # type: ignore[arg-type]
            document.get("description", ""),  # type: ignore[arg-type]
        )

    def __post_init__(self) -> None:
        try:
            channel = self.channel if isinstance(self.channel, ReleaseChannel) else ReleaseChannel(self.channel)
        except (TypeError, ValueError) as error:
            raise ContractError(f"unknown release channel: {self.channel!r}", code="release_channel") from error
        if channel is ReleaseChannel.NIGHTLY and self.allow_mutable_pointer and self.immutable:
            # The pointer can move only when the referenced snapshot remains
            # immutable; represent that distinction explicitly.
            pass
        if type(self.immutable) is not bool or type(self.allow_mutable_pointer) is not bool:
            raise ContractError("release policy flags must be booleans", code="release_policy")
        if type(self.requires_snapshot) is not bool:
            raise ContractError("requires_snapshot must be a boolean", code="release_policy")
        object.__setattr__(self, "channel", channel)

    def validate(self, version: str | SemVer) -> None:
        validate_release_version(version, self.channel)
        if self.requires_snapshot and not self.immutable:
            raise ContractError(
                "published releases must point at immutable snapshots",
                code="mutable_coordinate",
            )

    def allows_coordinate(self, coordinate: str) -> bool:
        if not isinstance(coordinate, str) or IMMUTABLE_COORDINATE.fullmatch(coordinate) is None:
            return False
        return True

    def allows_pointer(self, coordinate: str) -> bool:
        """Whether release automation may resolve a moving nightly pointer."""

        return (
            self.channel is ReleaseChannel.NIGHTLY
            and self.allow_mutable_pointer
            and coordinate in {"main", "master", "latest", "current"}
        )


CHANNEL_POLICIES: Mapping[str, ReleasePolicy] = MappingProxyType({
    "alpha": ReleasePolicy(ReleaseChannel.ALPHA, description="early integration; no stable support promise"),
    "beta": ReleasePolicy(ReleaseChannel.BETA, description="feature complete; compatibility may still change"),
    "rc": ReleasePolicy(ReleaseChannel.RC, description="release candidate; compatibility frozen"),
    "stable": ReleasePolicy(ReleaseChannel.STABLE, description="immutable supported release"),
    "nightly": ReleasePolicy(
        ReleaseChannel.NIGHTLY,
        immutable=True,
        allow_mutable_pointer=True,
        description="timestamped immutable snapshots; moving pointer is opt-in",
    ),
})
RELEASE_POLICIES = CHANNEL_POLICIES


class DeprecationState(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    REMOVED = "removed"


@dataclass(frozen=True)
class DeprecationPolicy:
    """A one-way deprecation declaration for a contract or field."""

    deprecated_in: str | SemVer | None = None
    removed_in: str | SemVer | None = None
    replacement: str | None = None
    notice: str | None = None

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> "DeprecationPolicy":
        if not isinstance(document, Mapping):
            raise ContractError("deprecation policy must be an object", code="deprecation")
        unknown = set(document) - {"deprecated_in", "removed_in", "replacement", "notice"}
        if unknown:
            raise ContractError(
                f"unknown deprecation fields: {sorted(unknown)}",
                code="deprecation",
            )
        return cls(
            deprecated_in=document.get("deprecated_in"),  # type: ignore[arg-type]
            removed_in=document.get("removed_in"),  # type: ignore[arg-type]
            replacement=document.get("replacement"),  # type: ignore[arg-type]
            notice=document.get("notice"),  # type: ignore[arg-type]
        )

    def __post_init__(self) -> None:
        deprecated = _as_semver(self.deprecated_in, "deprecated_in") if self.deprecated_in is not None else None
        removed = _as_semver(self.removed_in, "removed_in") if self.removed_in is not None else None
        if removed is not None and deprecated is None:
            raise ContractError("removed_in requires deprecated_in", code="deprecation")
        if removed is not None and deprecated is not None and removed < deprecated:
            raise ContractError("removed_in must not precede deprecated_in", code="deprecation")
        if self.replacement is not None and (
            not isinstance(self.replacement, str) or not self.replacement.strip()
        ):
            raise ContractError("replacement must be a non-empty string", code="deprecation")
        if self.notice is not None and not isinstance(self.notice, str):
            raise ContractError("notice must be a string", code="deprecation")
        object.__setattr__(self, "deprecated_in", deprecated)
        object.__setattr__(self, "removed_in", removed)

    def state_at(self, version: str | SemVer) -> DeprecationState:
        candidate = _as_semver(version, "version")
        if self.removed_in is not None and candidate >= self.removed_in:
            return DeprecationState.REMOVED
        if self.deprecated_in is not None and candidate >= self.deprecated_in:
            return DeprecationState.DEPRECATED
        return DeprecationState.ACTIVE

    def to_document(self) -> dict[str, object]:
        result: dict[str, object] = {}
        if self.deprecated_in is not None:
            result["deprecated_in"] = str(self.deprecated_in)
        if self.removed_in is not None:
            result["removed_in"] = str(self.removed_in)
        if self.replacement is not None:
            result["replacement"] = self.replacement
        if self.notice is not None:
            result["notice"] = self.notice
        return result


__all__ = [
    "CHANNEL_POLICIES",
    "DeprecationPolicy",
    "DeprecationState",
    "RELEASE_POLICIES",
    "ReleaseChannel",
    "ReleasePolicy",
    "classify_release",
    "validate_release_version",
]
