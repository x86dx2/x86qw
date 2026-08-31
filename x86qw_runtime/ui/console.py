"""Shared terminal reporting primitives for installed x86QW entrypoints."""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


ACCENT = "38;2;255;77;77"
INFO = "38;2;136;146;176"
SUCCESS = "38;2;0;229;204"
WARNING = "38;2;255;176;32"
ERROR = "38;2;230;57;70"
MUTED = "38;2;90;100;128"


@dataclass(frozen=True)
class UpdatePlanRow:
    kind: str
    item: str
    installed: str
    available: str
    action: str
    size: int | None = None


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def format_bytes_compact(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1000
    return f"{value:.1f}GB"


def terminal_label(value: str) -> str:
    """Keep public package identities on one bounded terminal line."""

    return "".join(character if character.isprintable() else "?" for character in value)[:96]


class Console:
    """Small stateful terminal reporter without repository dependencies."""

    def __init__(self, version: Callable[[], str] | None = None) -> None:
        self.verbose = False
        self.color = False
        self._version = version
        self._download_label: str | None = None
        self._cached_items = 0
        self._cached_bytes = 0
        self._activity_visible = False

    def configure(self, *, verbose: bool, no_color: bool) -> None:
        self.verbose = verbose
        self.color = sys.stdout.isatty() and not no_color and "NO_COLOR" not in os.environ
        self._download_label = None
        self._cached_items = 0
        self._cached_bytes = 0
        self._activity_visible = False

    def paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def bold_color(self, text: str, code: str) -> str:
        if not self.color:
            return text
        return f"\033[{code}m\033[1m{text}\033[0m"

    def banner(self, action: str, target: Path) -> None:
        if os.environ.get("X86QW_BOOTSTRAP_UI") == "1":
            return
        version = self._version() if self._version is not None else ""
        print(self.paint("Preparando interface do instalador...", INFO), flush=True)
        print(self.bold_color("\n  [X] Instalador x86QW", ACCENT), flush=True)
        print(self.paint("  Cinco jogos. Um menu. Uma partida.", INFO), flush=True)
        identity = "  qw.x86.com.br"
        if version:
            identity += f" | instalador {version}"
        print(self.paint(identity, MUTED), flush=True)
        print(flush=True)
        self.key_value("Ação", action)
        self.key_value("Destino", str(target))

    def key_value(self, key: str, value: str) -> None:
        print(f"{self.paint(key + ':', MUTED)} {value}", flush=True)

    def section(self, title: str) -> None:
        self.activity_done()
        self.flush_download_summary()
        print(f"\n{self.bold_color(title, ACCENT)}", flush=True)

    def heading(self, title: str) -> None:
        self.activity_done()
        self.flush_download_summary()
        print(f"\n{self.bold_color(title, ACCENT)}", flush=True)

    def info(self, message: str) -> None:
        self.activity_done()
        print(f"{self.paint('·', MUTED)} {message}", flush=True)

    def success(self, message: str) -> None:
        self.activity_done()
        self.flush_download_summary()
        print(f"{self.paint('✓', SUCCESS)} {message}", flush=True)

    def warning(self, message: str) -> None:
        self.activity_done()
        print(f"{self.paint('!', WARNING)} {message}", flush=True)

    def detail(self, message: str) -> None:
        if self.verbose:
            print(self.paint(f"  {message}", MUTED), flush=True)

    def error(self, message: str) -> None:
        self.activity_done()
        label = (
            f"\033[{ERROR}m✗\033[0m"
            if self.color and sys.stderr.isatty() else "✗"
        )
        print(f"{label} {message}", file=sys.stderr, flush=True)

    def update_plan(self, rows: list[UpdatePlanRow], action: str) -> str:
        noun = "pacote" if len(rows) == 1 else "pacotes"
        action_label = {
            "install": "instalar", "update": "atualizar",
            "upgrade": "incorporar", "repair": "reparar",
        }[action]
        suffix = "" if action == "install" else (
            " desatualizado" if len(rows) == 1 else " desatualizados"
        )
        total_size = sum(row.size or 0 for row in rows)
        size_suffix = f" · {format_bytes_compact(total_size)}" if total_size else ""
        title = f"Plano: {action_label} {len(rows)} {noun}{suffix}"
        self.heading(title + size_suffix)
        confirmation_lines = [title + size_suffix]
        component_rows = [row for row in rows if row.kind.casefold() == "componente"]
        grouped_install = action == "install" and len(component_rows) > 1
        display_rows = [row for row in rows if row not in component_rows] if grouped_install else rows
        names = [row.item for row in rows]
        installed = [row.installed for row in rows]
        available = [row.available for row in rows]
        name_width = max(map(len, names))
        installed_width = max(map(len, installed))
        available_width = max(map(len, available))
        terminal_width = max(40, min(shutil.get_terminal_size((100, 24)).columns, 120))
        for row in display_rows:
            size = f" ({format_bytes_compact(row.size)})" if row.size is not None else ""
            line = (
                f"{row.item.ljust(name_width)}  "
                f"{row.installed.ljust(installed_width)} -> "
                f"{row.available.ljust(available_width)}{size}"
            )
            if len(line) <= terminal_width:
                print(line, flush=True)
            else:
                print("\n".join(textwrap.wrap(
                    row.item, width=terminal_width,
                    initial_indent="  ", subsequent_indent="    ",
                    break_long_words=False, break_on_hyphens=False,
                )), flush=True)
                print(f"    Instalado  | {row.installed}", flush=True)
                print(f"    Disponível | {row.available}", flush=True)
                if row.size is not None:
                    print(f"    Download   | {format_bytes_compact(row.size)}", flush=True)
            detail = f"{row.item}: {row.installed} -> {row.available}"
            if row.size is not None:
                detail += f" · {format_bytes_compact(row.size)}"
            confirmation_lines.append(detail)
        if grouped_install:
            component_size = sum(row.size or 0 for row in component_rows)
            component_label = f"{len(component_rows)} componentes x86QW"
            if component_size:
                component_label += f" · {format_bytes_compact(component_size)}"
            print(component_label, flush=True)
            confirmation_lines.append(component_label)
        return "\n".join(confirmation_lines)

    def activity(self, current: int, total: int) -> None:
        if self.verbose:
            self.info(f"[{current}/{total}] Processando pacote")
            return
        if not sys.stdout.isatty():
            return
        print(
            f"\r\033[2K{self.paint('⠋', '36')} "
            f"[{current}/{total}] Processando pacote",
            end="", flush=True,
        )
        self._activity_visible = True

    def activity_done(self) -> None:
        if not self._activity_visible:
            return
        print("\r\033[2K", end="", flush=True)
        self._activity_visible = False

    def flush_download_summary(self) -> None:
        if not self._cached_items:
            return
        count = self._cached_items
        size = self._cached_bytes
        self._cached_items = 0
        self._cached_bytes = 0
        noun = "pacote" if count == 1 else "pacotes"
        adjective = "validado" if count == 1 else "validados"
        check = self.paint("✓", SUCCESS)
        print(
            f"{check} {count} {noun} no cache · {format_bytes_compact(size)} {adjective}",
            flush=True,
        )

    def download_result(
        self, label: str, *, size: int, status: str = "Baixado",
    ) -> None:
        self._download_label = None
        self.activity_done()
        if status == "Cached" and not self.verbose:
            self._cached_items += 1
            self._cached_bytes += size
            return
        amount = format_bytes_compact(size)
        check = self.paint("✓", SUCCESS)
        line = f"{check} {label:<48} {status:>10}  {amount:>9}/{amount}"
        terminal_width = max(40, min(shutil.get_terminal_size((100, 24)).columns, 120))
        if len(line) <= terminal_width:
            print(line, flush=True)
        else:
            print(f"{check} {label}", flush=True)
            print(f"    {status} | {amount}/{amount}", flush=True)

    def download_start(self, label: str, *, size: int | None) -> None:
        self.activity_done()
        self.flush_download_summary()
        safe_label = terminal_label(label)
        self._download_label = safe_label
        total = format_bytes_compact(size) if size is not None else "?"
        marker = self.paint("⠋", "36")
        # Package identifiers are intentionally public UI, never credentials.
        # lgtm[py/clear-text-logging-sensitive-data]
        print(f"{marker} {safe_label:<48} {'Baixando':>10}  {'0B':>9}/{total}", flush=True)

    def download_progress(self, received: int, total: int | None, *, done: bool = False) -> None:
        if not sys.stdout.isatty():
            return
        label = f"{self._download_label}  " if self._download_label else ""
        if total:
            width = 24
            ratio = min(received / total, 1)
            filled = int(width * ratio)
            bar = "#" * filled + "-" * (width - filled)
            status = f"[{bar}] {ratio:6.1%}  {format_bytes(received)} / {format_bytes(total)}"
        else:
            status = f"Recebidos {format_bytes(received)}"
        print(f"\r       {label}{status}", end="\n" if done else "", flush=True)


__all__ = (
    "Console", "UpdatePlanRow", "format_bytes", "format_bytes_compact", "terminal_label",
)
