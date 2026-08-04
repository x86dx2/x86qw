"""Native macOS operations kept outside installer and gameplay entrypoints."""

from __future__ import annotations

import plistlib
import subprocess
import sys
from dataclasses import dataclass
from typing import Mapping

from ..errors import InstallerError


MAX_PREFERENCE_DOMAIN_BYTES = 1024 * 1024


class MacOSAdapterError(InstallerError):
    """A native macOS fact or mutation could not be proved."""


@dataclass(frozen=True)
class PreferenceSnapshot:
    domain: str
    keys: tuple[str, ...]
    encoded_values: bytes


def ensure_process_absent(exact_name: str) -> None:
    if not exact_name or any(ord(character) < 32 for character in exact_name):
        raise ValueError("invalid process name")
    _require_macos()
    try:
        result = subprocess.run(
            ["pgrep", "-x", exact_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
    except FileNotFoundError as error:
        raise MacOSAdapterError("O utilitário nativo pgrep não foi encontrado no macOS.") from error
    except subprocess.TimeoutExpired as error:
        raise MacOSAdapterError(
            f"Não foi possível verificar se o {exact_name} está aberto."
        ) from error
    if result.returncode == 0:
        raise MacOSAdapterError(
            f"Feche o {exact_name} antes de continuar. O macOS mantém a autorização "
            "do diretório do jogo enquanto o aplicativo está aberto."
        )
    if result.returncode != 1:
        detail = (result.stderr or result.stdout or b"").decode(
            "utf-8", errors="replace",
        ).strip()
        suffix = f": {detail}" if detail else ""
        raise MacOSAdapterError(
            f"Não foi possível verificar se o {exact_name} está aberto{suffix}"
        )


def _validate_preference_identity(domain: str, keys: tuple[str, ...]) -> None:
    if not domain or any(ord(character) < 32 for character in domain):
        raise ValueError("invalid preference domain")
    if not keys or len(keys) != len(set(keys)) or any(
        not key or any(ord(character) < 32 for character in key) for key in keys
    ):
        raise ValueError("invalid preference keys")


def _snapshot_from_values(
    domain: str, keys: tuple[str, ...], values: Mapping[str, object],
) -> PreferenceSnapshot:
    _validate_preference_identity(domain, keys)
    selected = {key: values[key] for key in keys if key in values}
    encoded = plistlib.dumps(
        {
            "format": 1,
            "present": list(selected),
            "values": selected,
        },
        fmt=plistlib.FMT_BINARY,
        sort_keys=True,
    )
    return PreferenceSnapshot(domain, keys, encoded)


def _snapshot_values(snapshot: PreferenceSnapshot) -> tuple[set[str], dict[str, object]]:
    try:
        document = plistlib.loads(snapshot.encoded_values)
    except (ValueError, plistlib.InvalidFileException) as error:
        raise MacOSAdapterError("Snapshot de preferências macOS inválido.") from error
    if not isinstance(document, dict) or document.get("format") != 1:
        raise MacOSAdapterError("Snapshot de preferências macOS inválido.")
    present = document.get("present")
    values = document.get("values")
    if (
        not isinstance(present, list)
        or any(not isinstance(key, str) for key in present)
        or len(present) != len(set(present))
        or not isinstance(values, dict)
        or set(values) != set(present)
        or not set(present).issubset(snapshot.keys)
    ):
        raise MacOSAdapterError("Snapshot de preferências macOS inválido.")
    return set(present), dict(values)


def _require_macos() -> None:
    if sys.platform != "darwin":
        raise MacOSAdapterError("Operação nativa do macOS indisponível nesta plataforma.")


def _run_defaults(arguments: list[str], *, payload: bytes | None = None) -> subprocess.CompletedProcess:
    _require_macos()
    try:
        return subprocess.run(
            ["defaults", *arguments],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
    except FileNotFoundError as error:
        raise MacOSAdapterError("O utilitário nativo defaults não foi encontrado no macOS.") from error
    except subprocess.TimeoutExpired as error:
        raise MacOSAdapterError("O utilitário defaults não respondeu no tempo esperado.") from error


def _export_preference_domain(domain: str) -> dict[str, object]:
    result = _run_defaults(["export", domain, "-"])
    if result.returncode != 0:
        domains = _run_defaults(["domains"])
        if domains.returncode != 0:
            raise MacOSAdapterError("Não foi possível consultar as preferências do ezQuake.")
        registered = {
            entry.strip()
            for entry in domains.stdout.decode("utf-8", errors="strict").split(",")
            if entry.strip()
        }
        if domain not in registered:
            return {}
        raise MacOSAdapterError("Não foi possível exportar as preferências do ezQuake.")
    if len(result.stdout) > MAX_PREFERENCE_DOMAIN_BYTES:
        raise MacOSAdapterError("As preferências do ezQuake excedem o limite seguro.")
    try:
        document = plistlib.loads(result.stdout)
    except (ValueError, plistlib.InvalidFileException) as error:
        raise MacOSAdapterError("As preferências exportadas do ezQuake são inválidas.") from error
    if not isinstance(document, dict) or any(not isinstance(key, str) for key in document):
        raise MacOSAdapterError("As preferências exportadas do ezQuake são inválidas.")
    return document


def _publish_preference_domain(domain: str, values: Mapping[str, object]) -> None:
    try:
        payload = plistlib.dumps(dict(values), fmt=plistlib.FMT_BINARY, sort_keys=True)
    except (TypeError, ValueError) as error:
        raise MacOSAdapterError("As preferências preparadas do ezQuake são inválidas.") from error
    result = _run_defaults(["import", domain, "-"], payload=payload)
    if result.returncode != 0:
        raise MacOSAdapterError("Não foi possível publicar as preferências do ezQuake.")


def snapshot_preference_keys(
    domain: str, keys: tuple[str, ...],
) -> PreferenceSnapshot:
    return _snapshot_from_values(domain, keys, _export_preference_domain(domain))


def restore_preference_keys(snapshot: PreferenceSnapshot) -> None:
    _validate_preference_identity(snapshot.domain, snapshot.keys)
    present, saved = _snapshot_values(snapshot)
    current = _export_preference_domain(snapshot.domain)
    restored = dict(current)
    for key in snapshot.keys:
        if key in present:
            restored[key] = saved[key]
        else:
            restored.pop(key, None)
    _publish_preference_domain(snapshot.domain, restored)
    if snapshot_preference_keys(snapshot.domain, snapshot.keys) != snapshot:
        raise MacOSAdapterError("Não foi possível restaurar as preferências do ezQuake.")


def clear_preference_keys(snapshot: PreferenceSnapshot) -> PreferenceSnapshot:
    _validate_preference_identity(snapshot.domain, snapshot.keys)
    current = _export_preference_domain(snapshot.domain)
    if _snapshot_from_values(snapshot.domain, snapshot.keys, current) != snapshot:
        raise MacOSAdapterError(
            "As preferências do ezQuake mudaram depois da confirmação."
        )
    cleared = dict(current)
    for key in snapshot.keys:
        cleared.pop(key, None)
    try:
        _publish_preference_domain(snapshot.domain, cleared)
        empty = _snapshot_from_values(snapshot.domain, snapshot.keys, {})
        if snapshot_preference_keys(snapshot.domain, snapshot.keys) != empty:
            raise MacOSAdapterError("Não foi possível limpar as preferências do ezQuake.")
    except BaseException:
        try:
            restore_preference_keys(snapshot)
        except BaseException as rollback_error:
            raise MacOSAdapterError(
                "A limpeza das preferências falhou e o rollback ficou incompleto."
            ) from rollback_error
        raise
    return snapshot
