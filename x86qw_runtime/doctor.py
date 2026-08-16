"""Read-only installation diagnostics for the public `doctor` command."""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import zipfile
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path


CHECK_IDS = (
    "installation",
    "catalog",
    "trust",
    "runtime",
    "network",
    "disk",
    "permissions",
)
CHECK_STATUSES = frozenset({"ok", "warn", "fail", "skip"})
AUDIENCE = "owner-only"
OWNER_ONLY_FIRST_RUN = (
    "Modo owner-only: um usuário, Apple M3. Instalação limpa permitida. "
    "Windows e Linux continuam preview."
)
DEFAULT_BUNDLE_NAME = "x86qw-doctor.zip"
_BUNDLE_NOTICE = (
    OWNER_ONLY_FIRST_RUN
    + "\nSem upload automático. Revise o conteúdo antes de partilhar.\n"
)
_LOW_DISK_BYTES = 512 * 1024 * 1024
_CRITICAL_DISK_BYTES = 64 * 1024 * 1024
_TRUST_WARNING = timedelta(hours=6)
_TRUST_TIMESTAMP_MAX_BYTES = 64 * 1024


def diagnose(
    target: Path,
    *,
    catalog_commands: Sequence[str] | None = None,
    trust_timestamp_path: Path | None = None,
    now: datetime | None = None,
    network: tuple[str, str] | None = None,
    python_version: tuple[int, int] | None = None,
    platform_name: str | None = None,
    machine: str | None = None,
    disk_free_bytes: int | None = None,
) -> dict[str, object]:
    """Return a closed, side-effect-free doctor report for one target."""

    target = Path(target)
    clock = now or datetime.now(timezone.utc)
    checks = (
        _check("installation", *_installation(target)),
        _check("catalog", *_catalog(catalog_commands)),
        _check("trust", *_trust(trust_timestamp_path, clock)),
        _check("runtime", *_runtime(python_version, platform_name, machine)),
        _check("network", *_network(network)),
        _check("disk", *_disk(target, disk_free_bytes)),
        _check("permissions", *_permissions(target)),
    )
    return {
        "target": str(target),
        "audience": AUDIENCE,
        "healthy": not any(item["status"] == "fail" for item in checks),
        "checks": list(checks),
    }


def render_doctor_report(report: Mapping[str, object]) -> str:
    lines = [
        f"x86QW doctor — {report['audience']}",
        OWNER_ONLY_FIRST_RUN,
        f"Destino: {report['target']}",
        "",
    ]
    for item in report["checks"]:  # type: ignore[union-attr]
        lines.append(f"[{item['status']}] {item['id']}: {item['summary']}")
    lines.append("")
    lines.append(
        "Nenhum problema encontrado."
        if report["healthy"]
        else "Problemas encontrados."
    )
    return "\n".join(lines) + "\n"


def sanitize_doctor_report(
    report: Mapping[str, object],
    *,
    home: Path | None = None,
) -> dict[str, object]:
    """Return a shareable copy with home paths and secrets removed."""

    from .contracts.output import redact_json

    payload = json.dumps(dict(report), ensure_ascii=False)
    home_text = str(home or Path.home())
    if home_text:
        payload = payload.replace(home_text, "~")
    sanitized = redact_json(json.loads(payload))
    if not isinstance(sanitized, dict):
        raise ValueError("doctor report must remain an object after sanitization")
    return sanitized


def resolve_bundle_destination(
    raw: str,
    target: Path,
    *,
    cwd: Path | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve a zip path that is never written inside the installation."""

    requested = Path(raw).expanduser()
    destination = requested if requested.is_absolute() else (Path(cwd or Path.cwd()) / requested)
    destination = destination.resolve()
    root = Path(target).expanduser().resolve()
    try:
        destination.relative_to(root)
    except ValueError:
        return destination
    if requested.parent != Path("."):
        raise OSError("o bundle não pode ser gravado dentro da instalação")
    fallback = (Path(home or Path.home()) / requested.name).resolve()
    try:
        fallback.relative_to(root)
    except ValueError:
        return fallback
    raise OSError("o bundle não pode ser gravado dentro da instalação")


def write_doctor_bundle(
    report: Mapping[str, object],
    destination: Path,
    *,
    home: Path | None = None,
) -> Path:
    """Write a reviewable zip that does not touch the installation target."""

    from .contracts.output import make_json_output

    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise OSError("bundle destination already exists")
    document = make_json_output("doctor", data=sanitize_doctor_report(report, home=home))
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_name(destination.name + ".tmp")
    try:
        with zipfile.ZipFile(staging, "w") as archive:
            archive.writestr("NOTICE.txt", _BUNDLE_NOTICE)
            archive.writestr("doctor.json", document.to_json())
        staging.replace(destination)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    return destination


def _check(check_id: str, status: str, summary: str) -> dict[str, str]:
    if check_id not in CHECK_IDS or status not in CHECK_STATUSES:
        raise ValueError(f"invalid doctor check: {check_id}/{status}")
    return {"id": check_id, "status": status, "summary": summary}


def _installation(target: Path) -> tuple[str, str]:
    if target.is_symlink():
        return "fail", "destino é um symlink"
    if not target.exists():
        return "fail", "instalação ausente"
    state = target / ".x86qw" / "state.json"
    if state.is_symlink() or not state.is_file():
        return "warn", "instalação sem state.json"
    return "ok", "instalação presente"


def _catalog(commands: Sequence[str] | None) -> tuple[str, str]:
    if commands is None:
        return "skip", "catálogo do bundle não lido"
    if not commands:
        return "fail", "catálogo sem comandos públicos"
    return "ok", "catálogo de capacidades legível"


def _trust(path: Path | None, now: datetime) -> tuple[str, str]:
    if path is None or path.is_symlink() or not path.is_file():
        return "skip", "sem metadata TUF local"
    try:
        from .io.metadata import read_bounded_regular_file

        payload = read_bounded_regular_file(path, maximum_size=_TRUST_TIMESTAMP_MAX_BYTES)
        document = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        return "fail", "metadata TUF local ilegível"
    signed = document.get("signed") if isinstance(document, dict) else None
    expires = signed.get("expires") if isinstance(signed, dict) else None
    if not isinstance(expires, str) or not expires:
        return "fail", "timestamp TUF sem validade"
    try:
        expiry = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    except ValueError:
        return "fail", "timestamp TUF com validade inválida"
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if now >= expiry:
        version = signed.get("version")
        if isinstance(version, int) and not isinstance(version, bool):
            return "warn", f"cache TUF local v{version} expirado"
        return "warn", "cache TUF local expirado"
    if expiry - now <= _TRUST_WARNING:
        return "warn", "timestamp TUF perto de expirar"
    return "ok", "timestamp TUF válido"


def _runtime(
    python_version: tuple[int, int] | None,
    platform_name: str | None,
    machine: str | None,
) -> tuple[str, str]:
    version = python_version or (sys.version_info.major, sys.version_info.minor)
    if version < (3, 10):
        return "fail", "Python 3.10 ou mais recente é exigido"
    system = platform_name if platform_name is not None else sys.platform
    arch = (machine if machine is not None else platform.machine()).casefold()
    if system == "darwin" and arch in {"arm64", "aarch64"}:
        return "ok", "runtime macOS arm64"
    return "warn", "plataforma sem evidência nativa M3"


def _network(network: tuple[str, str] | None) -> tuple[str, str]:
    if network is None:
        return "skip", "rede não consultada"
    status, summary = network
    if status not in CHECK_STATUSES or not summary:
        return "skip", "rede não consultada"
    return status, summary


def _disk(target: Path, disk_free_bytes: int | None) -> tuple[str, str]:
    if disk_free_bytes is None:
        probe = target if target.exists() else target.parent
        try:
            disk_free_bytes = shutil.disk_usage(probe).free
        except OSError:
            return "skip", "espaço em disco indisponível"
    if disk_free_bytes < _CRITICAL_DISK_BYTES:
        return "fail", "espaço livre insuficiente"
    if disk_free_bytes < _LOW_DISK_BYTES:
        return "warn", "espaço livre baixo"
    return "ok", "espaço em disco suficiente"


def _permissions(target: Path) -> tuple[str, str]:
    probe = target if target.exists() else target.parent
    if not os.access(probe, os.R_OK):
        return "fail", "sem permissão de leitura"
    if target.exists() and not os.access(target, os.W_OK):
        return "warn", "sem permissão de escrita"
    return "ok", "permissões de leitura ok"
