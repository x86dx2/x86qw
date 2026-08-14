"""Native macOS operations kept outside installer and gameplay entrypoints."""

from __future__ import annotations

import hashlib
import os
import plistlib
import stat
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from collections.abc import Callable

from ..errors import InstallerError
from ..io.atomic import AtomicWriteError, atomic_write_bytes
from ..io.metadata import MetadataFileError, read_bounded_regular_file


MAX_PREFERENCE_DOMAIN_BYTES = 1024 * 1024
MAX_BUNDLE_PLIST_BYTES = 1024 * 1024
_MACOS_SAFE_AREA_KEY = "NSPrefersDisplaySafeAreaCompatibilityMode"


class MacOSAdapterError(InstallerError):
    """A native macOS fact or mutation could not be proved."""


@dataclass(frozen=True)
class PreferenceSnapshot:
    domain: str
    keys: tuple[str, ...]
    encoded_values: bytes


def _require_regular_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise MacOSAdapterError(f"{label} inválido no bundle macOS: {path}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise MacOSAdapterError(f"{label} inválido no bundle macOS: {path}")


def _require_regular_file(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise MacOSAdapterError(f"{label} inválido no bundle macOS: {path}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise MacOSAdapterError(f"{label} inválido no bundle macOS: {path}")


def _bundle_directory(app: Path, *parts: str) -> Path:
    """Resolve a fixed bundle directory only through ordinary directories."""

    directory = Path(app)
    _require_regular_directory(directory, "Aplicativo")
    for part in parts:
        directory /= part
        _require_regular_directory(directory, "Diretório")
    return directory


def _require_optional_regular_directory(path: Path, label: str) -> None:
    """Reject a present bundle boundary without requiring a staged member."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise MacOSAdapterError(f"{label} inválido no bundle macOS: {path}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise MacOSAdapterError(f"{label} inválido no bundle macOS: {path}")


def app_executable(app: Path) -> Path:
    return app_launch_paths(app)[-1]


def app_launch_paths(app: Path) -> tuple[Path, Path, Path, Path]:
    """Return the fixed bundle chain after rejecting redirected directories."""

    app = Path(app)
    contents = _bundle_directory(app, "Contents")
    macos = _bundle_directory(app, "Contents", "MacOS")
    executable = macos / "ezQuake"
    _require_regular_file(executable, "Executável")
    return app, contents, macos, executable


def _read_bundle_plist(app: Path) -> tuple[dict[str, object], bytes]:
    plist = _bundle_directory(app, "Contents") / "Info.plist"
    _require_regular_file(plist, "Info.plist")
    try:
        payload = read_bounded_regular_file(
            plist, maximum_size=MAX_BUNDLE_PLIST_BYTES,
        )
        document = plistlib.loads(payload)
    except (MetadataFileError, ValueError, plistlib.InvalidFileException) as error:
        raise MacOSAdapterError(f"Info.plist inválido no bundle macOS: {app}") from error
    if not isinstance(document, dict):
        raise MacOSAdapterError(f"Info.plist inválido no bundle macOS: {app}")
    return document, payload


def _hash_regular_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError("not a regular file")
            while block := source.read(1024 * 1024):
                digest.update(block)
    except OSError as error:
        raise MacOSAdapterError(f"Executável inválido no bundle macOS: {path}") from error
    return digest.hexdigest()


def verify_app_signature(app: Path) -> None:
    _require_macos()
    _bundle_directory(app, "Contents", "MacOS")
    _bundle_directory(app, "Contents", "_CodeSignature")
    _run_codesign(["codesign", "--verify", "--deep", "--strict", str(app)])


def _run_codesign(arguments: list[str]) -> None:
    try:
        result = subprocess.run(
            arguments,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
        )
    except FileNotFoundError as error:
        raise MacOSAdapterError(
            "O utilitário nativo codesign não foi encontrado no macOS."
        ) from error
    except subprocess.TimeoutExpired as error:
        raise MacOSAdapterError(
            "A verificação da assinatura do ezQuake excedeu o tempo limite."
        ) from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"").decode(
            "utf-8", errors="replace",
        ).strip()
        suffix = f": {detail}" if detail else ""
        raise MacOSAdapterError(
            f"O comando codesign falhou{suffix}"
        )


def _inspect_universal_macho(binary: Path) -> None:
    try:
        with binary.open("rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError("not a regular file")
            file_size = int(metadata.st_size)
            data = source.read(4096)
            if len(data) < 8:
                raise MacOSAdapterError(f"Executável Mach-O inválido: {binary}")
            magic, count = struct.unpack_from(">II", data)
            if magic not in (0xCAFEBABE, 0xCAFEBABF) or not 2 <= count <= 32:
                raise MacOSAdapterError(
                    f"Executável Mach-O universal esperado: {binary}"
                )
            entry_size = 20 if magic == 0xCAFEBABE else 32
            header_size = 8 + count * entry_size
            if len(data) < header_size:
                raise MacOSAdapterError(f"Cabeçalho Mach-O universal inválido: {binary}")
            entries: list[tuple[int, int, int]] = []
            for index in range(count):
                offset = 8 + index * entry_size
                if entry_size == 20:
                    cpu_type, _subtype, slice_offset, slice_size, _align = struct.unpack_from(
                        ">IIIII", data, offset,
                    )
                else:
                    cpu_type, _subtype, slice_offset, slice_size, _align, _reserved = (
                        struct.unpack_from(">IIQQII", data, offset)
                    )
                if (
                    slice_offset < header_size
                    or slice_size < 8
                    or slice_offset + slice_size > file_size
                ):
                    raise MacOSAdapterError(f"Fatias Mach-O inválidas: {binary}")
                entries.append((cpu_type, slice_offset, slice_size))
            previous_end = header_size
            for _cpu_type, slice_offset, slice_size in sorted(
                entries, key=lambda entry: entry[1],
            ):
                if slice_offset < previous_end:
                    raise MacOSAdapterError(f"Fatias Mach-O sobrepostas: {binary}")
                previous_end = slice_offset + slice_size
            architectures = {cpu_type for cpu_type, _offset, _size in entries}
            if not {0x01000007, 0x0100000C}.issubset(architectures):
                raise MacOSAdapterError(f"Bundle macOS não contém arm64 e x86_64: {binary}")
            for cpu_type, slice_offset, _slice_size in entries:
                source.seek(slice_offset)
                slice_header = source.read(8)
                if slice_header[:4] == b"\xcf\xfa\xed\xfe":
                    byte_order = "<"
                elif slice_header[:4] == b"\xfe\xed\xfa\xcf":
                    byte_order = ">"
                else:
                    raise MacOSAdapterError(f"Fatias Mach-O inválidas: {binary}")
                if struct.unpack(f"{byte_order}I", slice_header[4:])[0] != cpu_type:
                    raise MacOSAdapterError(f"Fatias Mach-O inválidas: {binary}")
    except MacOSAdapterError:
        raise
    except (OSError, struct.error) as error:
        raise MacOSAdapterError(f"Executável Mach-O inválido: {binary}") from error


def inspect_ezquake_bundle(
    app: Path,
    *,
    verify_signature: bool | Callable[[Path], None],
) -> tuple[str, str]:
    """Validate one ezQuake app without following its security boundaries."""

    app = Path(app)
    binary = _bundle_directory(app, "Contents", "MacOS") / "ezQuake"
    resources = _bundle_directory(app, "Contents", "_CodeSignature") / "CodeResources"
    _require_regular_file(binary, "Executável")
    _require_regular_file(resources, "Assinatura")
    metadata, _ = _read_bundle_plist(app)
    version = metadata.get("CFBundleShortVersionString")
    if not isinstance(version, str) or version != metadata.get("CFBundleVersion"):
        raise MacOSAdapterError(f"Versões divergentes no bundle macOS: {app}")
    _inspect_universal_macho(binary)
    if callable(verify_signature):
        verify_signature(app)
    elif verify_signature:
        verify_app_signature(app)
    return version, _hash_regular_file(binary)


def _entitlements_payload(output: bytes) -> bytes | None:
    """Extract the plist emitted by codesign, if the bundle has one."""

    binary = output.find(b"bplist00")
    if binary >= 0:
        return output[binary:]
    start = output.find(b"<?xml")
    end = output.rfind(b"</plist>")
    if start >= 0 and end >= start:
        return output[start:end + len(b"</plist>")]
    return None


def app_is_sandboxed(app: Path) -> bool:
    _require_macos()
    _bundle_directory(Path(app), "Contents", "MacOS")
    _bundle_directory(Path(app), "Contents", "_CodeSignature")
    try:
        result = subprocess.run(
            ["codesign", "-d", "--entitlements", ":-", str(app)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
    except FileNotFoundError as error:
        raise MacOSAdapterError(
            "O utilitário nativo codesign não foi encontrado no macOS."
        ) from error
    except subprocess.TimeoutExpired as error:
        raise MacOSAdapterError(
            "A leitura dos entitlements do ezQuake excedeu o tempo limite."
        ) from error
    if result.returncode != 0:
        raise MacOSAdapterError("Não foi possível ler os entitlements do ezQuake.")
    payload = _entitlements_payload(result.stdout + result.stderr)
    if payload is None:
        return False
    try:
        document = plistlib.loads(payload)
    except (ValueError, plistlib.InvalidFileException) as error:
        raise MacOSAdapterError("Os entitlements do ezQuake são inválidos.") from error
    if not isinstance(document, dict):
        raise MacOSAdapterError("Os entitlements do ezQuake são inválidos.")
    return document.get("com.apple.security.app-sandbox") is True


def app_uses_full_display(app: Path) -> bool:
    document, _ = _read_bundle_plist(Path(app))
    return document.get(_MACOS_SAFE_AREA_KEY) is False


def enable_full_display(app: Path) -> None:
    app = Path(app)
    _bundle_directory(app, "Contents")
    document, original = _read_bundle_plist(app)
    document[_MACOS_SAFE_AREA_KEY] = False
    format_value = plistlib.FMT_BINARY if original.startswith(b"bplist00") else plistlib.FMT_XML
    try:
        payload = plistlib.dumps(document, fmt=format_value, sort_keys=False)
        atomic_write_bytes(app / "Contents/Info.plist", payload, mode=0o644)
    except (TypeError, ValueError, AtomicWriteError) as error:
        raise MacOSAdapterError(f"Info.plist não pôde ser atualizado: {app}") from error


def prepare_nightly_bundle(
    app: Path,
    *,
    sandbox_probe: Callable[[Path], bool] | None = None,
    display_probe: Callable[[Path], bool] | None = None,
    command_runner: Callable[[list[str]], object] | None = None,
) -> tuple[bool, bool]:
    """Remove nightly-only sandboxing and enable the full macOS display."""

    app = Path(app)
    contents = _bundle_directory(app, "Contents")
    _require_optional_regular_directory(contents / "MacOS", "Diretório")
    _require_optional_regular_directory(contents / "_CodeSignature", "Diretório")
    sandbox_probe = sandbox_probe or app_is_sandboxed
    display_probe = display_probe or app_uses_full_display
    command_runner = command_runner or _run_codesign
    sandboxed = sandbox_probe(app)
    full_display = display_probe(app)
    if not full_display:
        enable_full_display(app)
    command_runner(["codesign", "--force", "--deep", "--sign", "-", str(app)])
    command_runner(["codesign", "--verify", "--deep", "--strict", str(app)])
    if sandbox_probe(app):
        raise MacOSAdapterError(f"Não foi possível remover o sandbox incompatível de {app}.")
    if not display_probe(app):
        raise MacOSAdapterError(f"Não foi possível habilitar o fullscreen integral em {app}.")
    return sandboxed, not full_display


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


def _delete_preference_keys(domain: str, keys: tuple[str, ...]) -> None:
    _validate_preference_identity(domain, keys)
    for key in keys:
        result = _run_defaults(["delete", domain, key])
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or b"").decode(
                "utf-8", errors="replace",
            ).strip()
            suffix = f": {detail}" if detail else ""
            raise MacOSAdapterError(
                f"Não foi possível remover a preferência macOS {key}{suffix}"
            )


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
    # A primeira instalação normalmente has no ezQuake domain yet (or only
    # unrelated preferences).  Clearing an already-empty managed subset must
    # be a true no-op: importing an empty plist can fail on macOS when the
    # domain has not been materialized, even though there is nothing to reset.
    present, _saved = _snapshot_values(snapshot)
    if not present:
        return snapshot
    cleared = dict(current)
    for key in snapshot.keys:
        cleared.pop(key, None)
    try:
        _publish_preference_domain(snapshot.domain, cleared)
        empty = _snapshot_from_values(snapshot.domain, snapshot.keys, {})
        if snapshot_preference_keys(snapshot.domain, snapshot.keys) != empty:
            _delete_preference_keys(
                snapshot.domain,
                tuple(key for key in snapshot.keys if key in present),
            )
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
