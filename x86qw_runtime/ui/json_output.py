"""Compatibility import for the canonical JSON contract renderer.

The implementation lives under ``x86qw_runtime.contracts`` so output models
remain independent from terminal rendering.  This facade gives UI callers a
stable import path without a second implementation.
"""

from ..contracts.output import (
    CLI_JSON_SCHEMA_VERSION,
    COMMAND_DATA_SCHEMAS,
    DRY_RUN_COMMANDS,
    ExitCode,
    JSON_COMMANDS,
    JSON_SCHEMA_VERSION,
    JsonCommandOutput,
    JsonOutputError,
    STABLE_EXIT_CODES,
    make_json_output,
    parse_json_output,
    redact_json,
    render_json_output,
    serialize_json_output,
)

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
