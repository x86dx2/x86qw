"""Stable JSON output contracts for public x86QW commands.

Rendering is pure and deterministic: one JSON document is emitted per command,
without ANSI, prompts or human diagnostics.  Callers should send explanatory
diagnostics to stderr and pass only structured data here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import ipaddress
import json
import re
import unicodedata
from types import MappingProxyType

from ..errors import ExitCode
from ..versioning import parse_semver
from .schema import ContractError, canonical_json


JSON_SCHEMA_VERSION = 1
CLI_JSON_SCHEMA_VERSION = JSON_SCHEMA_VERSION
_STABLE_EXIT_VALUES = frozenset(int(code) for code in ExitCode)
JSON_COMMANDS = frozenset({
    "version",
    "status",
    "hub",
    "verify",
    "repair",
    "update",
    "upgrade",
})
DRY_RUN_COMMANDS = frozenset({"repair", "update", "upgrade"})
COMMAND_DATA_SCHEMAS = MappingProxyType({
    # These descriptors are intentionally executable by the validator below;
    # the envelope is not a license for command data to remain an unbounded
    # object.  A data-shape change requires a new JSON_SCHEMA_VERSION.
    "version": MappingProxyType({
        "kind": "object",
        "closed": True,
        "required": frozenset({"project", "version"}),
        "properties": MappingProxyType({"project": "x86qw", "version": "semver"}),
    }),
    "status": MappingProxyType({
        "kind": "object",
        "closed": True,
        "required": frozenset({"project", "target", "installation", "state", "sessions"}),
        "properties": MappingProxyType({
            "project": "x86qw",
            "target": "string",
            "installation": frozenset({"present", "missing"}),
            "state": frozenset({"present", "missing"}),
            "sessions": "array",
        }),
        "session_fields": frozenset({"session_id", "status", "command"}),
    }),
    "hub": MappingProxyType({
        "kind": "object",
        "closed": True,
        "required": frozenset({"target", "servers"}),
        "properties": MappingProxyType({"target": "string", "servers": "array"}),
        "server_fields": frozenset({"address", "title", "mode", "map", "players", "qtv_stream"}),
        "player_fields": frozenset({"humans", "bots"}),
    }),
    "verify": MappingProxyType({
        "kind": "object",
        "closed": True,
        "required": frozenset({"target", "verified"}),
        "properties": MappingProxyType({"target": "string", "verified": True}),
    }),
    "repair": MappingProxyType({
        "kind": "object", "closed": True, "dry_run": True,
        "required": frozenset({"target", "status", "operations"}),
        "properties": MappingProxyType({"target": "string", "status": "enum", "operations": "array"}),
        "operation_fields": frozenset({"kind", "item", "installed", "available", "action", "size"}),
    }),
    "update": MappingProxyType({
        "kind": "object", "closed": True, "dry_run": True,
        "required": frozenset({"target", "status", "operations"}),
        "properties": MappingProxyType({"target": "string", "status": "enum", "operations": "array"}),
        "operation_fields": frozenset({"kind", "item", "installed", "available", "action", "size"}),
    }),
    "upgrade": MappingProxyType({
        "kind": "object", "closed": True, "dry_run": True,
        "required": frozenset({"target", "status", "operations"}),
        "properties": MappingProxyType({"target": "string", "status": "enum", "operations": "array"}),
        "operation_fields": frozenset({"kind", "item", "installed", "available", "action", "size"}),
    }),
})

_MAX_OUTPUT_TEXT = 4096
_MAX_STATUS_SESSIONS = 1024
_MAX_HUB_SERVERS = 20
_MAX_HUB_PLAYERS = 10000
_MAX_DRY_RUN_OPERATIONS = 4096
_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY = re.compile(
    r"(?:pass(?:word|phrase)?|secret|token|authorization|credential|"
    r"private[_-]?key|api[_-]?key|cookie|session[_-]?id|access[_-]?key)",
    re.IGNORECASE,
)
_SENSITIVE_URL = re.compile(r"(?i)(https?://)([^/@\s]+):([^/@\s]+)@")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passphrase|secret|token|authorization|credential|api[_-]?key)\s*([:=])\s*([^\s,;]+)"
)


def _sorted_keys(values: object) -> list[object]:
    try:
        return sorted(values, key=lambda item: str(item))  # type: ignore[arg-type]
    except TypeError as error:
        raise JsonOutputError("JSON fields must be comparable names", code="fields") from error


class JsonOutputError(ContractError):
    """JSON output did not satisfy the frozen public shape."""

    def __init__(self, message: str, *, code: str = "json_output", field_name: str | None = None) -> None:
        super().__init__(message, code=code, field_name=field_name)


def _redact_json(value: object, *, preserve_keys: frozenset[str]) -> object:
    """Defensively redact secrets in structured output.

    Redaction is key-based for structured values and also removes credentials
    embedded in URLs.  It returns a fresh value and never mutates caller data.
    """

    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text in preserve_keys:
                result[key_text] = _redact_json(item, preserve_keys=frozenset())
            elif _SENSITIVE_KEY.search(key_text):
                result[key_text] = _REDACTED
            else:
                result[key_text] = _redact_json(item, preserve_keys=preserve_keys)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact_json(item, preserve_keys=preserve_keys) for item in value]
    if isinstance(value, str):
        value = _SENSITIVE_URL.sub(r"\1" + _REDACTED + "@", value)
        return _SENSITIVE_ASSIGNMENT.sub(r"\1\2" + _REDACTED, value)
    return value


def redact_json(value: object) -> object:
    """Return a defensive copy with sensitive fields and credentials redacted."""

    return _redact_json(value, preserve_keys=frozenset())


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise JsonOutputError(f"duplicate JSON field: {key}", code="duplicate_field", field_name=key)
        result[key] = value
    return result


def _validate_data(value: object, *, preserve_keys: frozenset[str] = frozenset()) -> object:
    try:
        # Validate finite JSON and reject arbitrary Python objects before the
        # output model is constructed.
        canonical_json(value)
    except ContractError as error:
        raise JsonOutputError(str(error), code="data") from error
    return _redact_json(value, preserve_keys=preserve_keys)


def _closed_object(value: object, expected: frozenset[str], label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise JsonOutputError(f"{label} must be an object", code="data")
    fields = set(value)
    if fields != expected:
        missing = _sorted_keys(expected - fields)
        extra = _sorted_keys(fields - expected)
        detail = f"missing={missing}" if missing else f"unknown={extra}"
        raise JsonOutputError(f"invalid {label} fields: {detail}", code="data")
    return dict(value)


def _output_text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or (not allow_empty and not value)
        or len(value) > _MAX_OUTPUT_TEXT
        or any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            or character == "\ufffd"
            for character in value
        )
    ):
        raise JsonOutputError(f"{label} must be safe text", code="data")
    return value


def _validate_version_data(value: object) -> dict[str, object]:
    document = _closed_object(value, frozenset({"project", "version"}), "version data")
    if document["project"] != "x86qw":
        raise JsonOutputError("version data has an invalid project", code="data")
    version = _output_text(document["version"], "version")
    try:
        parse_semver(version)
    except ValueError as error:
        raise JsonOutputError("version data has an invalid SemVer", code="data") from error
    return {"project": "x86qw", "version": version}


def _validate_status_data(value: object) -> dict[str, object]:
    document = _closed_object(
        value,
        frozenset({"project", "target", "installation", "state", "sessions"}),
        "status data",
    )
    if document["project"] != "x86qw":
        raise JsonOutputError("status data has an invalid project", code="data")
    target = _output_text(document["target"], "status.target")
    installation = document["installation"]
    state = document["state"]
    if (
        not isinstance(installation, str)
        or installation not in {"present", "missing"}
        or not isinstance(state, str)
        or state not in {"present", "missing"}
    ):
        raise JsonOutputError("status data has an invalid installation/state enum", code="data")
    sessions = document["sessions"]
    if not isinstance(sessions, list) or len(sessions) > _MAX_STATUS_SESSIONS:
        raise JsonOutputError("status.sessions must be a bounded array", code="data")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in sessions:
        session = _closed_object(
            item,
            frozenset({"session_id", "status", "command"}),
            "status session",
        )
        session_id = _output_text(session["session_id"], "status.session_id")
        if session_id in seen:
            raise JsonOutputError("status.sessions contains duplicate session_id", code="data")
        seen.add(session_id)
        normalized.append({
            "session_id": session_id,
            "status": _output_text(session["status"], "status.session.status"),
            "command": _output_text(session["command"], "status.session.command"),
        })
    normalized.sort(key=lambda item: item["session_id"])
    return {
        "project": "x86qw",
        "target": target,
        "installation": installation,
        "state": state,
        "sessions": normalized,
    }


def _validate_network_endpoint(value: object, label: str) -> str:
    """Validate an unambiguous IPv4/IPv6/hostname endpoint."""

    address = _output_text(value, label)
    if address.startswith("["):
        closing = address.find("]")
        if (
            closing <= 1
            or closing + 1 >= len(address)
            or address[closing + 1] != ":"
            or address.find("]", closing + 1) != -1
        ):
            raise JsonOutputError(f"{label} is not a valid host:port", code="data")
        host = address[1:closing]
        port_text = address[closing + 2:]
        try:
            parsed_host = ipaddress.IPv6Address(host)
        except ValueError as error:
            raise JsonOutputError(f"{label} is not a valid host:port", code="data") from error
        normalized_host = f"[{parsed_host.compressed}]"
    else:
        if address.count(":") != 1:
            raise JsonOutputError(f"{label} is not a valid host:port", code="data")
        host, port_text = address.rsplit(":", 1)
        if not host:
            raise JsonOutputError(f"{label} is not a valid host:port", code="data")
        try:
            normalized_host = str(ipaddress.IPv4Address(host))
        except ValueError:
            numeric_labels = host.split(".")
            if all(
                part and part.isascii() and part.isdigit()
                for part in numeric_labels
            ):
                raise JsonOutputError(f"{label} is not a valid host:port", code="data")
            if len(host) > 253 or host.endswith("."):
                raise JsonOutputError(f"{label} is not a valid host:port", code="data")
            labels = host.split(".")
            if any(
                not _HOST_LABEL.fullmatch(part)
                for part in labels
            ):
                raise JsonOutputError(f"{label} is not a valid host:port", code="data")
            normalized_host = host.casefold()
    if not port_text.isascii() or not port_text.isdigit():
        raise JsonOutputError(f"{label} has an invalid port", code="data")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise JsonOutputError(f"{label} has an invalid port", code="data")
    return f"{normalized_host}:{port}"


def _validate_hub_qtv(value: object) -> str | None:
    if value is None:
        return None
    stream = _output_text(value, "hub.qtv_stream")
    prefix, separator, address = stream.partition("@")
    if not separator or not prefix.isascii() or not prefix.isdigit() or not address:
        raise JsonOutputError("hub.qtv_stream is invalid", code="data")
    return f"{prefix}@{_validate_network_endpoint(address, 'hub.qtv_stream')}"


def _validate_hub_data(value: object) -> dict[str, object]:
    document = _closed_object(value, frozenset({"target", "servers"}), "hub data")
    target = _output_text(document["target"], "hub.target")
    servers = document["servers"]
    if not isinstance(servers, list) or not servers or len(servers) > _MAX_HUB_SERVERS:
        raise JsonOutputError("hub.servers must be a bounded non-empty array", code="data")
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in servers:
        server = _closed_object(
            item,
            frozenset({"address", "title", "mode", "map", "players", "qtv_stream"}),
            "hub server",
        )
        address = _validate_network_endpoint(server["address"], "hub.address")
        if address in seen:
            raise JsonOutputError("hub.servers contains duplicate addresses", code="data")
        seen.add(address)
        players = _closed_object(
            server["players"],
            frozenset({"humans", "bots"}),
            "hub server.players",
        )
        counts: dict[str, int] = {}
        for name in ("humans", "bots"):
            count = players[name]
            if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= _MAX_HUB_PLAYERS:
                raise JsonOutputError(f"hub server.players.{name} is invalid", code="data")
            counts[name] = count
        normalized.append({
            "address": address,
            "title": _output_text(server["title"], "hub.title"),
            "mode": _output_text(server["mode"], "hub.mode"),
            "map": _output_text(server["map"], "hub.map"),
            "players": counts,
            "qtv_stream": _validate_hub_qtv(server["qtv_stream"]),
        })
    normalized.sort(key=lambda item: str(item["address"]))
    return {"target": target, "servers": normalized}


def _validate_verify_data(value: object) -> dict[str, object]:
    document = _closed_object(value, frozenset({"target", "verified"}), "verify data")
    if document["verified"] is not True:
        raise JsonOutputError("verify data must confirm verified=true", code="data")
    return {"target": _output_text(document["target"], "verify.target"), "verified": True}


def _validate_command_data(command: str, value: object, *, ok: bool) -> object:
    if not ok:
        if value != {}:
            raise JsonOutputError("failed output data must be an empty object", code="data")
        return {}
    if command == "version":
        return _validate_version_data(value)
    if command == "status":
        return _validate_status_data(value)
    if command == "hub":
        return _validate_hub_data(value)
    if command == "verify":
        return _validate_verify_data(value)
    return _validate_dry_run_data(value, required=True)


def _validate_dry_run_data(value: object, *, required: bool) -> object:
    """Validate the shared maintenance-plan shape used by JSON dry-runs."""

    if not isinstance(value, Mapping):
        raise JsonOutputError("dry-run data must be an object", code="data")
    if not required and not value:
        return {}
    expected = {"target", "status", "operations"}
    if set(value) != expected:
        missing = _sorted_keys(expected - set(value))
        extra = _sorted_keys(set(value) - expected)
        detail = f"missing={missing}" if missing else f"unknown={extra}"
        raise JsonOutputError(f"invalid dry-run data fields: {detail}", code="data")
    target = value["target"]
    status = value["status"]
    operations = value["operations"]
    target = _output_text(target, "dry-run.target")
    if not isinstance(status, str) or status not in {"noop", "planned", "blocked"}:
        raise JsonOutputError("invalid dry-run status", code="data")
    if not isinstance(operations, list) or len(operations) > _MAX_DRY_RUN_OPERATIONS:
        raise JsonOutputError("dry-run operations must be an array", code="data")
    operation_fields = {"kind", "item", "installed", "available", "action", "size"}
    normalized: list[dict[str, object]] = []
    for operation in operations:
        if not isinstance(operation, Mapping) or set(operation) != operation_fields:
            raise JsonOutputError("invalid dry-run operation fields", code="data")
        for field_name in ("kind", "item", "installed", "available", "action"):
            _output_text(operation[field_name], f"dry-run operation {field_name}")
        size = operation["size"]
        if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
            raise JsonOutputError("dry-run operation size must be a non-negative integer or null", code="data")
        normalized.append({field: operation[field] for field in operation_fields})
    if status == "noop" and normalized:
        raise JsonOutputError("noop dry-run cannot contain operations", code="data")
    if status == "planned" and not normalized:
        raise JsonOutputError("planned dry-run requires operations", code="data")
    if status == "blocked" and not normalized:
        # A blocked operation may fail before a plan can be assembled; its
        # diagnostic belongs in the envelope's errors array.
        return {"target": target, "status": status, "operations": []}
    return {"target": target, "status": status, "operations": normalized}


@dataclass(frozen=True)
class JsonCommandOutput:
    """One stable JSON response for a supported command."""

    command: str
    ok: bool
    data: object
    errors: tuple[Mapping[str, object], ...] = ()
    exit_code: int | ExitCode = ExitCode.SUCCESS
    schema_version: int = JSON_SCHEMA_VERSION
    dry_run: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.command, str) or not self.command:
            raise JsonOutputError("command must be a non-empty string", code="command", field_name="command")
        if self.command not in JSON_COMMANDS:
            raise JsonOutputError(f"unsupported JSON command: {self.command!r}", code="command", field_name="command")
        if type(self.schema_version) is not int or self.schema_version != JSON_SCHEMA_VERSION:
            raise JsonOutputError("unsupported JSON schema version", code="schema_version", field_name="schema_version")
        if type(self.ok) is not bool:
            raise JsonOutputError("ok must be boolean", code="type", field_name="ok")
        if type(self.dry_run) is not bool:
            raise JsonOutputError("dry_run must be boolean", code="type", field_name="dry_run")
        if self.dry_run and self.command not in DRY_RUN_COMMANDS:
            raise JsonOutputError(
                "dry_run is valid only for repair/update/upgrade",
                code="dry_run",
                field_name="dry_run",
            )
        if self.command in DRY_RUN_COMMANDS and not self.dry_run:
            raise JsonOutputError(
                "repair/update/upgrade JSON output requires --dry-run",
                code="dry_run",
                field_name="dry_run",
            )
        if isinstance(self.exit_code, bool) or not isinstance(self.exit_code, (int, ExitCode)):
            raise JsonOutputError("exit_code must be an integer", code="exit_code", field_name="exit_code")
        code = int(self.exit_code)
        if code not in _STABLE_EXIT_VALUES:
            raise JsonOutputError(
                "exit_code is outside the stable public contract",
                code="exit_code",
                field_name="exit_code",
            )
        if self.ok and code != int(ExitCode.SUCCESS):
            raise JsonOutputError("successful output must use exit code 0", code="exit_code", field_name="exit_code")
        if not self.ok and code == int(ExitCode.SUCCESS):
            raise JsonOutputError(
                "failed output must use a non-zero exit code",
                code="exit_code",
                field_name="exit_code",
            )
        if not isinstance(self.errors, (tuple, list)):
            raise JsonOutputError("errors must be an array", code="errors", field_name="errors")
        if not isinstance(self.errors, tuple):
            object.__setattr__(self, "errors", tuple(self.errors))
        if self.ok and self.errors:
            raise JsonOutputError(
                "successful output must not contain errors",
                code="errors",
                field_name="errors",
            )
        if not self.ok and not self.errors:
            raise JsonOutputError(
                "failed output must contain at least one error",
                code="errors",
                field_name="errors",
            )
        for entry in self.errors:
            if not isinstance(entry, Mapping):
                raise JsonOutputError("errors must contain objects", code="errors", field_name="errors")
            if set(entry) != {"code", "message"}:
                raise JsonOutputError(
                    "each error must contain exactly code and message",
                    code="errors",
                    field_name="errors",
                )
            if (
                not isinstance(entry["code"], str)
                or not entry["code"]
                or not isinstance(entry["message"], str)
                or not entry["message"]
            ):
                raise JsonOutputError(
                    "error code and message must be non-empty strings",
                    code="errors",
                    field_name="errors",
                )
        normalized_data = _validate_data(
            self.data,
            preserve_keys=frozenset({"session_id"}) if self.command == "status" else frozenset(),
        )
        normalized_data = _validate_command_data(self.command, normalized_data, ok=self.ok)
        object.__setattr__(self, "data", normalized_data)
        object.__setattr__(
            self,
            "errors",
            tuple(redact_json(dict(entry)) for entry in self.errors),
        )

    def to_document(self) -> dict[str, object]:
        """Return the canonical field set in a stable, documented shape."""

        return {
            "schema_version": self.schema_version,
            "command": self.command,
            "ok": self.ok,
            "exit_code": int(self.exit_code),
            "dry_run": self.dry_run,
            "data": self.data,
            "errors": list(self.errors),
        }

    def to_json(self) -> str:
        return canonical_json(self.to_document()).decode("utf-8") + "\n"

    @classmethod
    def from_document(cls, document: Mapping[str, object]) -> "JsonCommandOutput":
        if not isinstance(document, Mapping):
            raise JsonOutputError("JSON output must be an object", code="identity")
        required = {"schema_version", "command", "ok", "exit_code", "dry_run", "data", "errors"}
        if set(document) != required:
            missing = _sorted_keys(required - set(document))
            extra = _sorted_keys(set(document) - required)
            detail = f"missing={missing}" if missing else f"unknown={extra}"
            raise JsonOutputError(f"invalid JSON output fields: {detail}", code="fields")
        errors = document["errors"]
        if not isinstance(errors, list):
            raise JsonOutputError("errors must be an array", code="errors", field_name="errors")
        if not isinstance(document["command"], str):
            raise JsonOutputError("command must be a non-empty string", code="command", field_name="command")
        if type(document["ok"]) is not bool:
            raise JsonOutputError("ok must be boolean", code="type", field_name="ok")
        if type(document["dry_run"]) is not bool:
            raise JsonOutputError("dry_run must be boolean", code="type", field_name="dry_run")
        return cls(
            command=document["command"],  # type: ignore[arg-type]
            ok=document["ok"],  # type: ignore[arg-type]
            data=document["data"],
            errors=tuple(errors),  # type: ignore[arg-type]
            exit_code=document["exit_code"],  # type: ignore[arg-type]
            schema_version=document["schema_version"],  # type: ignore[arg-type]
            dry_run=document["dry_run"],  # type: ignore[arg-type]
        )


def make_json_output(
    command: str,
    *,
    data: object | None = None,
    ok: bool = True,
    exit_code: int | ExitCode = ExitCode.SUCCESS,
    errors: Sequence[Mapping[str, object]] = (),
    dry_run: bool = False,
) -> JsonCommandOutput:
    """Build one output document after validating/redacting all data."""

    # Accept the human invocation spelling while freezing the machine field to
    # the base command plus an explicit boolean.
    if isinstance(command, str) and command.endswith(" --dry-run"):
        command = command[: -len(" --dry-run")]
        dry_run = True

    return JsonCommandOutput(
        command=command,
        ok=ok,
        data={} if data is None else data,
        errors=tuple(errors),
        exit_code=exit_code,
        dry_run=dry_run,
    )


def render_json_output(output: JsonCommandOutput | Mapping[str, object]) -> str:
    """Render an output object exactly once, with a trailing newline."""

    if isinstance(output, JsonCommandOutput):
        model = output
    else:
        model = JsonCommandOutput.from_document(output)
    return model.to_json()


def serialize_json_output(output: JsonCommandOutput | Mapping[str, object]) -> bytes:
    return render_json_output(output).encode("utf-8")


def parse_json_output(payload: bytes | str) -> JsonCommandOutput:
    try:
        document = json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise JsonOutputError("invalid JSON output", code="json") from error
    return JsonCommandOutput.from_document(document)


# The values are frozen in x86qw_runtime.errors.  Re-exporting the enum and a
# name-to-value map makes the stability promise discoverable without creating a
# second set of numeric values.
STABLE_EXIT_CODES = MappingProxyType({
    "success": int(ExitCode.SUCCESS),
    "failure": int(ExitCode.FAILURE),
    "usage": int(ExitCode.USAGE),
    "interrupted": int(ExitCode.INTERRUPTED),
})


__all__ = [
    "CLI_JSON_SCHEMA_VERSION",
    "COMMAND_DATA_SCHEMAS",
    "DRY_RUN_COMMANDS",
    "ExitCode",
    "JSON_COMMANDS",
    "JSON_SCHEMA_VERSION",
    "JsonCommandOutput",
    "JsonOutputError",
    "STABLE_EXIT_CODES",
    "make_json_output",
    "parse_json_output",
    "redact_json",
    "render_json_output",
    "serialize_json_output",
]
