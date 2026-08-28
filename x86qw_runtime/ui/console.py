"""Shared terminal reporting primitives for installed x86QW entrypoints."""

from __future__ import annotations

import os
import shutil
import sys
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


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

    def configure(self, *, verbose: bool, no_color: bool) -> None:
        self.verbose = verbose
        self.color = sys.stdout.isatty() and not no_color and "NO_COLOR" not in os.environ

    def paint(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def banner(self, action: str, target: Path) -> None:
        version = f" {self._version()}" if self._version is not None else ""
        title = self.paint(f"x86-qw{version}", "1;36")
        print(f"\n{title} · instalador QuakeWorld", flush=True)
        print(f"Ação: {action}  |  Destino: {target}", flush=True)

    def section(self, title: str) -> None:
        print(f"\n{self.paint(title, '1;36')}", flush=True)

    def heading(self, title: str) -> None:
        print(f"\n{self.paint('==>', '1;36')} {self.paint(title, '1')}", flush=True)

    def info(self, message: str) -> None:
        print(f"{self.paint('[INFO]', '36')} {message}", flush=True)

    def success(self, message: str) -> None:
        print(f"{self.paint('[OK]', '32')} {message}", flush=True)

    def warning(self, message: str) -> None:
        print(f"{self.paint('[ATENÇÃO]', '33')} {message}", flush=True)

    def detail(self, message: str) -> None:
        if self.verbose:
            print(self.paint(f"       {message}", "2"), flush=True)

    def error(self, message: str) -> None:
        label = self.paint("[ERRO]", "31") if self.color and sys.stderr.isatty() else "[ERRO]"
        print(f"{label} {message}", file=sys.stderr, flush=True)

    def update_plan(self, rows: list[UpdatePlanRow], action: str) -> None:
        noun = "pacote" if len(rows) == 1 else "pacotes"
        action_label = {
            "install": "instalar", "update": "atualizar",
            "upgrade": "incorporar", "repair": "reparar",
        }[action]
        suffix = "" if action == "install" else (
            " desatualizado" if len(rows) == 1 else " desatualizados"
        )
        self.heading(f"Plano: {action_label} {len(rows)} {noun}{suffix}")
        names = [row.item for row in rows]
        installed = [row.installed for row in rows]
        available = [row.available for row in rows]
        name_width = max(map(len, names))
        installed_width = max(map(len, installed))
        available_width = max(map(len, available))
        terminal_width = max(40, min(shutil.get_terminal_size((100, 24)).columns, 120))
        for row in rows:
            size = f" ({format_bytes_compact(row.size)})" if row.size is not None else ""
            line = (
                f"{row.item.ljust(name_width)}  "
                f"{row.installed.ljust(installed_width)} -> "
                f"{row.available.ljust(available_width)}{size}"
            )
            if len(line) <= terminal_width:
                print(line, flush=True)
                continue
            print("\n".join(textwrap.wrap(
                row.item, width=terminal_width,
                initial_indent="  ", subsequent_indent="    ",
                break_long_words=False, break_on_hyphens=False,
            )), flush=True)
            print(f"    Instalado  | {row.installed}", flush=True)
            print(f"    Disponível | {row.available}", flush=True)
            if row.size is not None:
                print(f"    Download   | {format_bytes_compact(row.size)}", flush=True)

    def download_result(
        self, label: str, *, size: int, status: str = "Baixado",
    ) -> None:
        self._download_label = None
        amount = format_bytes_compact(size)
        check = self.paint("✔︎", "32")
        line = f"{check} {label:<48} {status:>10}  {amount:>9}/{amount}"
        terminal_width = max(40, min(shutil.get_terminal_size((100, 24)).columns, 120))
        if len(line) <= terminal_width:
            print(line, flush=True)
        else:
            print(f"{check} {label}", flush=True)
            print(f"    {status} | {amount}/{amount}", flush=True)

    def download_start(self, label: str, *, size: int | None) -> None:
        safe_label = terminal_label(label)
        self._download_label = safe_label
        total = format_bytes_compact(size) if size is not None else "?"
        marker = self.paint("⠋", "36")
        # Package identifiers are intentionally public UI, never credentials.
        # codeql[py/clear-text-logging-sensitive-data]
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
