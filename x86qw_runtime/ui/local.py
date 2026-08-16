"""Read-only local HTML over doctor and library contracts."""

from __future__ import annotations

import html
from pathlib import Path

from ..doctor import diagnose
from ..library import load_library


def render_local_ui(target: Path) -> str:
    """Return a closed HTML snapshot. Never writes into the installation."""

    report = diagnose(Path(target))
    library = load_library(Path(target))
    checks = "".join(
        (
            "<li><code>"
            f"{html.escape(str(item['id']))}</code> "
            f"{html.escape(str(item['status']))}: "
            f"{html.escape(str(item['summary']))}</li>"
        )
        for item in report["checks"]
    )
    favorites = _entries(library["favorites"])
    recents = _entries(library["recents"])
    healthy = "saudável" if report["healthy"] else "com falhas"
    return (
        "<!DOCTYPE html>\n"
        '<html lang="pt-BR"><head><meta charset="utf-8">'
        "<title>x86QW</title></head><body>"
        f"<p>x86QW {html.escape(str(report['audience']))} — {html.escape(healthy)}</p>"
        f"<p>Destino: <code>{html.escape(str(report['target']))}</code></p>"
        f"<h1>doctor</h1><ul>{checks}</ul>"
        f"<h1>favorites</h1><ul>{favorites}</ul>"
        f"<h1>recents</h1><ul>{recents}</ul>"
        "</body></html>\n"
    )


def write_local_ui(target: Path, destination: Path) -> Path:
    """Write the snapshot outside the installation target."""

    destination = Path(destination)
    target = Path(target).resolve()
    output = destination.resolve()
    if output == target or target in output.parents:
        raise OSError("local UI output must stay outside the installation")
    if output.exists() or output.is_symlink():
        raise OSError("local UI output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_local_ui(target), encoding="utf-8")
    return output


def _entries(items: tuple[dict[str, str], ...]) -> str:
    if not items:
        return "<li>(nenhum)</li>"
    return "".join(
        (
            "<li><code>"
            f"{html.escape(item['address'])}</code> "
            f"{html.escape(item['title'])} "
            f"({html.escape(item['origin'])})</li>"
        )
        for item in items
    )
