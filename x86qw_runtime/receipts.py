"""Typed codecs for persisted x86QW receipts and inventories."""

from __future__ import annotations

import copy
import json
import re
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass, fields
from pathlib import PurePosixPath

from .io.downloader import DownloadPolicyError, validate_https_url
from .contracts.schema import ContractError, SchemaKind, validate_document_versions
from .versioning import NIGHTLY_VERSION, SEMVER_VERSION, STABLE_VERSION


HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_INVENTORY_BYTES = 16 * 1024 * 1024


class ReceiptError(ValueError):
    """Receipt bytes do not match their declared runtime context."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid",
        field_name: str | None = None,
        value: object = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field_name = field_name
        self.value = value


@dataclass(frozen=True)
class EzQuakeReceiptContext:
    platform: str
    architecture: str
    channel: str
    install_name: str
    stable_archive: str
    nightly_suffix: str


@dataclass(frozen=True)
class EzQuakeReceipt:
    format: str
    platform: str
    architecture: str
    channel: str
    selection: str
    install_name: str
    bundle_version: str
    artifact_name: str
    artifact_url: str
    artifact_sha256: str
    binary_sha256: str

    def to_legacy_dict(self) -> dict[str, str]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


_EZQUAKE_FIELDS = tuple(item.name for item in fields(EzQuakeReceipt))


def _parse_table(payload: bytes, expected_fields: tuple[str, ...]) -> dict[str, str]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ReceiptError("receipt is not valid UTF-8") from error
    result: dict[str, str] = {}
    expected = frozenset(expected_fields)
    for line in lines:
        parts = line.split("\t")
        if len(parts) != 2 or parts[0] not in expected or parts[0] in result:
            raise ReceiptError("invalid receipt table", code="table", value=line)
        result[parts[0]] = parts[1]
    if set(result) != expected:
        raise ReceiptError("incomplete receipt table", code="table")
    return result


def _https_filename(value: str) -> str:
    try:
        parsed = validate_https_url(value, "URL do artefato no recibo")
    except DownloadPolicyError as error:
        raise ReceiptError(
            str(error), code="ezquake_artifact_url",
        ) from error
    filename = PurePosixPath(urllib.parse.unquote(parsed.path)).name
    if not filename or filename in {".", ".."}:
        raise ReceiptError(
            "URL do artefato no recibo não identifica um arquivo",
            code="ezquake_artifact_url",
        )
    return filename


def parse_ezquake_receipt(
    payload: bytes,
    *,
    context: EzQuakeReceiptContext,
) -> EzQuakeReceipt:
    """Parse and validate one stable or nightly ezQuake receipt."""

    values = _parse_table(payload, _EZQUAKE_FIELDS)
    receipt = EzQuakeReceipt(**values)
    if (
        receipt.format != "1"
        or receipt.platform != context.platform
        or receipt.architecture != context.architecture
    ):
        raise ReceiptError("invalid ezQuake platform", code="ezquake_platform")
    if (
        receipt.channel != context.channel
        or receipt.install_name != context.install_name
        or context.channel not in {"stable", "nightly"}
    ):
        raise ReceiptError("invalid ezQuake target", code="ezquake_target")
    if not HEX64.fullmatch(receipt.artifact_sha256):
        raise ReceiptError(
            "invalid ezQuake artifact hash",
            code="ezquake_hash",
            field_name="artifact SHA-256 in ezQuake receipt",
            value=receipt.artifact_sha256,
        )
    if not HEX64.fullmatch(receipt.binary_sha256):
        raise ReceiptError(
            "invalid ezQuake binary hash",
            code="ezquake_hash",
            field_name="binary SHA-256 in ezQuake receipt",
            value=receipt.binary_sha256,
        )
    if context.channel == "stable":
        if (
            not STABLE_VERSION.fullmatch(receipt.selection)
            or receipt.bundle_version != receipt.selection
        ):
            raise ReceiptError(
                "invalid stable ezQuake selection",
                code="ezquake_stable_selection",
                value=receipt.selection,
            )
        expected_name = context.stable_archive
    else:
        if not NIGHTLY_VERSION.fullmatch(receipt.selection):
            raise ReceiptError(
                "invalid nightly ezQuake selection",
                code="ezquake_nightly_selection",
                value=receipt.selection,
            )
        if context.platform == "macos":
            git_revision = receipt.selection.rsplit("_", 1)[-1]
            if f"-g{git_revision}" not in receipt.bundle_version:
                raise ReceiptError(
                    "nightly bundle version differs from ezQuake selection",
                    code="ezquake_macos_bundle",
                )
        elif receipt.bundle_version != receipt.selection:
            raise ReceiptError(
                "nightly version differs from ezQuake selection",
                code="ezquake_nightly_bundle",
            )
        expected_name = receipt.selection + context.nightly_suffix
    if receipt.artifact_name != expected_name or _https_filename(receipt.artifact_url) != expected_name:
        raise ReceiptError("unexpected ezQuake artifact", code="ezquake_artifact")
    return receipt


def serialize_ezquake_receipt(receipt: EzQuakeReceipt) -> bytes:
    """Encode the exact field order historically emitted by the installer."""

    return "".join(
        f"{field_name}\t{getattr(receipt, field_name)}\n"
        for field_name in _EZQUAKE_FIELDS
    ).encode("utf-8")


@dataclass(frozen=True)
class ComponentReceipt:
    format: str
    component: str
    selection: str
    source: str
    inventory_sha256: str

    def to_legacy_dict(self) -> dict[str, str]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(frozen=True)
class InventoryEntry:
    path: str
    sha256: str


_COMPONENT_FIELDS = tuple(item.name for item in fields(ComponentReceipt))


def parse_component_receipt(payload: bytes, *, component: str) -> ComponentReceipt:
    """Parse one component receipt independently from its bound inventory bytes."""

    values = _parse_table(payload, _COMPONENT_FIELDS)
    receipt = ComponentReceipt(**values)
    if receipt.format != "1" or receipt.component != component:
        raise ReceiptError("invalid component receipt identity", code="component_identity")
    if not receipt.selection:
        raise ReceiptError("empty component selection", code="component_selection")
    if not receipt.source:
        raise ReceiptError("empty component source", code="component_source")
    if not HEX64.fullmatch(receipt.inventory_sha256):
        raise ReceiptError(
            "invalid component inventory hash",
            code="component_inventory_hash",
            value=receipt.inventory_sha256,
        )
    return receipt


def serialize_component_receipt(receipt: ComponentReceipt) -> bytes:
    return "".join(
        f"{field_name}\t{getattr(receipt, field_name)}\n"
        for field_name in _COMPONENT_FIELDS
    ).encode("utf-8")


def parse_inventory(payload: bytes) -> tuple[InventoryEntry, ...]:
    """Parse inventory order exactly; product-specific path policy stays in its adapter."""

    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ReceiptError("inventory is not valid UTF-8") from error
    entries: list[InventoryEntry] = []
    seen: set[str] = set()
    for line in lines:
        parts = line.split("\t")
        if (
            len(parts) != 2
            or not parts[0]
            or parts[0] in seen
        ):
            raise ReceiptError(
                "invalid managed inventory entry", code="inventory_entry", value=line,
            )
        if not HEX64.fullmatch(parts[1]):
            raise ReceiptError(
                "invalid managed inventory hash",
                code="inventory_hash",
                field_name=parts[0],
                value=parts[1],
            )
        entries.append(InventoryEntry(parts[0], parts[1]))
        seen.add(parts[0])
    return tuple(entries)


def serialize_inventory(entries: Iterable[InventoryEntry]) -> bytes:
    return "".join(
        f"{entry.path}\t{entry.sha256}\n" for entry in entries
    ).encode("utf-8")


@dataclass(frozen=True)
class CliReceipt:
    format: int
    project: str
    version: str
    _document: dict[str, object]

    def to_legacy_dict(self) -> dict[str, object]:
        return copy.deepcopy(self._document)


def parse_cli_receipt(payload: bytes) -> CliReceipt:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReceiptError("invalid CLI receipt JSON") from error
    if not isinstance(document, dict):
        raise ReceiptError("invalid CLI receipt document")
    version = document.get("version")
    if (
        document.get("format") != 1
        or document.get("project") != "x86qw"
        or not isinstance(version, str)
    ):
        raise ReceiptError("invalid CLI receipt identity", code="cli_identity")
    if not SEMVER_VERSION.fullmatch(version):
        raise ReceiptError(
            "invalid CLI receipt version", code="cli_version", value=version,
        )
    try:
        validate_document_versions(
            document,
            kind=SchemaKind.RECEIPT,
            allow_legacy=True,
        )
    except ContractError as error:
        raise ReceiptError(
            "invalid CLI receipt contract", code="cli_contract",
        ) from error
    return CliReceipt(1, "x86qw", version, copy.deepcopy(document))


def serialize_cli_receipt(receipt: CliReceipt) -> bytes:
    return (
        json.dumps(
            receipt.to_legacy_dict(), ensure_ascii=False, indent=2, sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


@dataclass(frozen=True)
class LegacyNQuakeReceipt:
    format: str
    distfiles_commit: str
    inventory_sha256: str

    def to_legacy_dict(self) -> dict[str, str]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


_LEGACY_NQUAKE_FIELDS = tuple(item.name for item in fields(LegacyNQuakeReceipt))


def parse_legacy_nquake_receipt(payload: bytes) -> LegacyNQuakeReceipt:
    values = _parse_table(payload, _LEGACY_NQUAKE_FIELDS)
    receipt = LegacyNQuakeReceipt(**values)
    if receipt.format != "1":
        raise ReceiptError(
            "unsupported legacy nQuake receipt format",
            code="legacy_nquake_format",
            value=receipt.format,
        )
    if not HEX40.fullmatch(receipt.distfiles_commit):
        raise ReceiptError(
            "invalid legacy nQuake revision", code="legacy_nquake_revision",
        )
    if not HEX64.fullmatch(receipt.inventory_sha256):
        raise ReceiptError(
            "invalid legacy nQuake inventory hash",
            code="legacy_nquake_inventory_hash",
        )
    return receipt
