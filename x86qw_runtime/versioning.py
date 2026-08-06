"""Version contracts shared by the x86QW runtime and release tooling.

The installer historically exposed three regular expressions from this module.
Those names remain intentionally compatible, while :class:`SemVer` provides a
complete SemVer 2.0 parser and ordering implementation for new contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import total_ordering
import re
from typing import Iterable


# These expressions are part of the pre-1.0 Python API.  In particular,
# ``STABLE_VERSION`` must continue to reject prereleases because existing
# receipt and bundle validators use it when they expect a stable selection;
# its numeric components now also obey SemVer's no-leading-zero rule.
STABLE_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)
NIGHTLY_VERSION = re.compile(r"^[0-9]{8}-[0-9]{6}_[0-9a-f]{7}$")
COMPONENT_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$")


_NUMERIC_IDENTIFIER = r"(?:0|[1-9][0-9]*)"
_ALPHANUMERIC_IDENTIFIER = r"(?:[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
_PRERELEASE_IDENTIFIER = rf"(?:{_NUMERIC_IDENTIFIER}|{_ALPHANUMERIC_IDENTIFIER})"
_BUILD_IDENTIFIER = r"[0-9A-Za-z-]+"
_SEMVER_PATTERN = re.compile(
    rf"^(?P<major>{_NUMERIC_IDENTIFIER})\."
    rf"(?P<minor>{_NUMERIC_IDENTIFIER})\."
    rf"(?P<patch>{_NUMERIC_IDENTIFIER})"
    rf"(?:-(?P<prerelease>{_PRERELEASE_IDENTIFIER}(?:\.{_PRERELEASE_IDENTIFIER})*))?"
    rf"(?:\+(?P<build>{_BUILD_IDENTIFIER}(?:\.{_BUILD_IDENTIFIER})*))?$"
)

# Public aliases use both spellings found in early release tooling.
SEMVER = _SEMVER_PATTERN
SEMVER_VERSION = _SEMVER_PATTERN
SEMVER_PATTERN = _SEMVER_PATTERN
SEMVER_REGEX = _SEMVER_PATTERN


class VersionError(ValueError):
    """A version string is malformed or outside a requested policy."""


class ReleaseStage(str, Enum):
    """SemVer release stages understood by the x86QW release policy."""

    ALPHA = "alpha"
    BETA = "beta"
    RC = "rc"
    STABLE = "stable"
    OTHER = "other"


def _validate_identifier(identifier: object, *, build: bool = False) -> str:
    if not isinstance(identifier, str) or not identifier:
        raise VersionError("SemVer identifiers must be non-empty ASCII strings")
    if any(ord(character) > 127 or character in ".+" for character in identifier):
        raise VersionError(f"invalid SemVer identifier: {identifier!r}")
    if not re.fullmatch(r"[0-9A-Za-z-]+", identifier):
        raise VersionError(f"invalid SemVer identifier: {identifier!r}")
    if not build and identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
        raise VersionError(f"numeric SemVer identifiers cannot have leading zeroes: {identifier!r}")
    return identifier


@total_ordering
@dataclass(frozen=True, eq=False)
class SemVer:
    """A SemVer 2.0.0 value with SemVer precedence semantics.

    Build metadata is retained for round-tripping, but deliberately ignored by
    equality and ordering as required by SemVer 2.0.0.
    """

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("major", "minor", "patch"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise VersionError(f"SemVer {name} must be a non-negative integer")
        for name in ("prerelease", "build"):
            value = getattr(self, name)
            if isinstance(value, (str, bytes)) or value is None:
                raise VersionError(f"SemVer {name} must be a sequence of identifiers")
            if not isinstance(value, tuple):
                try:
                    value = tuple(value)
                except TypeError as error:
                    raise VersionError(
                        f"SemVer {name} must be a sequence of identifiers"
                    ) from error
                object.__setattr__(self, name, value)
        for identifier in self.prerelease:
            _validate_identifier(identifier)
        for identifier in self.build:
            _validate_identifier(identifier, build=True)

    @classmethod
    def parse(cls, value: str) -> "SemVer":
        """Parse one strict SemVer value.

        A leading ``v`` and numeric components with leading zeroes are rejected
        rather than silently normalized.  This matters for signed metadata and
        for deterministic snapshot coordinates.
        """

        if not isinstance(value, str):
            raise VersionError(f"invalid SemVer: {value!r}")
        match = _SEMVER_PATTERN.fullmatch(value)
        if match is None:
            raise VersionError(f"invalid SemVer: {value!r}")
        prerelease = tuple(
            match.group("prerelease").split(".")
            if match.group("prerelease")
            else ()
        )
        build = tuple(
            match.group("build").split(".") if match.group("build") else ()
        )
        return cls(
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            prerelease,
            build,
        )

    from_string = parse

    @property
    def stage(self) -> ReleaseStage:
        if not self.prerelease:
            return ReleaseStage.STABLE
        first = self.prerelease[0]
        if first == "alpha":
            return ReleaseStage.ALPHA
        if first == "beta":
            return ReleaseStage.BETA
        if first == "rc":
            return ReleaseStage.RC
        return ReleaseStage.OTHER

    @property
    def channel(self) -> str:
        """Return the policy channel name for this SemVer value."""

        return self.stage.value

    @property
    def is_stable(self) -> bool:
        return self.stage is ReleaseStage.STABLE

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    @property
    def precedence_key(self) -> tuple[object, ...]:
        if not self.prerelease:
            # Stable has higher precedence than every prerelease of the same
            # core version.  The integer marker keeps tuple comparisons valid.
            return self.major, self.minor, self.patch, ((2, ""),)
        identifiers: list[tuple[int, object]] = []
        for identifier in self.prerelease:
            if identifier.isdigit():
                identifiers.append((0, int(identifier)))
            else:
                identifiers.append((1, identifier))
        return self.major, self.minor, self.patch, tuple(identifiers)

    def __str__(self) -> str:
        value = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            value += "-" + ".".join(self.prerelease)
        if self.build:
            value += "+" + ".".join(self.build)
        return value

    def __repr__(self) -> str:
        return f"SemVer.parse({str(self)!r})"

    def __hash__(self) -> int:
        # Build metadata does not participate in SemVer equality.
        return hash(self.precedence_key)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self.precedence_key == other.precedence_key

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        return self.precedence_key < other.precedence_key


def parse_semver(value: str) -> SemVer:
    """Functional parser alias used by contract validators."""

    return SemVer.parse(value)


# Compatibility aliases used by release scripts and early contract drafts.
parse_version = parse_semver
Version = SemVer
VersionStage = ReleaseStage


def is_semver(value: object) -> bool:
    try:
        SemVer.parse(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return True


def compare_versions(left: str | SemVer, right: str | SemVer) -> int:
    """Compare two SemVer values, returning ``-1``, ``0`` or ``1``."""

    lhs = left if isinstance(left, SemVer) else SemVer.parse(left)
    rhs = right if isinstance(right, SemVer) else SemVer.parse(right)
    return (lhs > rhs) - (lhs < rhs)


def sort_versions(values: Iterable[str | SemVer], *, reverse: bool = False) -> list[SemVer]:
    parsed = [value if isinstance(value, SemVer) else SemVer.parse(value) for value in values]
    return sorted(parsed, reverse=reverse)


def classify_version(value: str) -> ReleaseStage | str:
    """Classify SemVer prereleases while preserving legacy nightly strings."""

    if isinstance(value, str) and NIGHTLY_VERSION.fullmatch(value):
        return "nightly"
    return SemVer.parse(value).stage


def version_key(version: str) -> tuple[int, int, int]:
    """Return the existing three-integer ordering key for stable releases.

    The legacy helper intentionally remains stable-only.  Callers that need
    alpha/beta/rc ordering should use :func:`parse_semver` or
    :func:`compare_versions` instead.
    """

    if not isinstance(version, str) or not STABLE_VERSION.fullmatch(version):
        raise ValueError(f"invalid installer version: {version}")
    major, minor, patch = version.split(".")
    return int(major), int(minor), int(patch)


__all__ = [
    "COMPONENT_VERSION",
    "NIGHTLY_VERSION",
    "ReleaseStage",
    "SEMVER",
    "SEMVER_PATTERN",
    "SEMVER_REGEX",
    "SEMVER_VERSION",
    "STABLE_VERSION",
    "SemVer",
    "Version",
    "VersionError",
    "VersionStage",
    "classify_version",
    "compare_versions",
    "is_semver",
    "parse_semver",
    "parse_version",
    "sort_versions",
    "version_key",
]
