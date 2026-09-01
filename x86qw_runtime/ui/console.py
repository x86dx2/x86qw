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

INSTALLER_LOGO = (
    "                  ⢀⣤⣶⣶⣿⣿⣶⣶⣤⡀    ⣀⣤⣶⣶⣿⣷⣶⣶⡄    ⣠⣤⣶⣶⣿⣿⣶⣶⣤⡀   ⣶⣶⣶⣶⡄  ⢠⣶⣶⣶⣶   ⣰⣶⣶⣶⣶",
    "                 ⣰⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷  ⢠⣾⣿⣿⣿⣿⣿⣿⣿⣿⡇  ⣠⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣆  ⣿⣿⣿⣿⡇ ⢀⣿⣿⣿⣿⣿⡇ ⢠⣿⣿⣿⣿⠏",
    "    ⢰⣿⣿⣿⣷⡀ ⣰⣾⣿⣿⣿⠂⣿⣿⣿⣿⡏ ⢸⣿⣿⣿⣿ ⣰⣿⣿⣿⡿⢋⣀⣀ ⠈⠉⠁ ⣼⣿⣿⣿⣿⠟⠉ ⠉⢻⣿⣿⣿⣿⡆ ⣿⣿⣿⣿⡇ ⣾⣿⣿⣿⣿⣿⡇⢀⣾⣿⣿⣿⡟",
    "     ⢻⣿⣿⣿⣷⣾⣿⣿⣿⠟⠁ ⠹⣿⣿⣿⣿⣾⣿⣿⣿⡿⠃⢀⣿⣿⣿⣿⣿⣿⣿⣿⣿⣶⡀ ⢰⣿⣿⣿⣿⠇    ⢸⣿⣿⣿⣿⡇ ⣿⣿⣿⣿⡇⣸⣿⣿⣿⣿⣿⣿⡇⣼⣿⣿⣿⡿",
    "      ⢻⣿⣿⣿⣿⣿⡿⠃  ⣠⣾⣿⣿⣿⣿⣿⣿⣿⣿⣦ ⢸⣿⣿⣿⣿⡿⠟⢿⣿⣿⣿⣿ ⣿⣿⣿⣿⣿     ⢸⣿⣿⣿⣿⡇ ⣿⣿⣿⣿⣷⣿⣿⣿⠇⣿⣿⣿⣷⣿⣿⣿⣿⠁",
    "     ⢀⣴⣿⣿⣿⣿⣿⡄  ⢰⣿⣿⣿⣿⠁ ⢈⣿⣿⣿⣿ ⢸⣿⣿⣿⣿⠁ ⢸⣿⣿⣿⣿ ⣿⣿⣿⣿⣿    ⢠⣿⣿⣿⣿⡿  ⢹⣿⣿⣿⣿⣿⣿⡏ ⣿⣿⣿⣿⣿⣿⣿⠇",
    "    ⣰⣿⣿⣿⣿⣿⣿⣿⣿⡄ ⢸⣿⣿⣿⣿⣤⣤⣾⣿⣿⣿⡿ ⠸⣿⣿⣿⣿⣄⣤⣾⣿⣿⣿⠏ ⠸⣿⣿⣿⣿⣷⣤⣤⣶⣿⣿⣿⣿⡟⠁  ⢸⣿⣿⣿⣿⣿⡿  ⣿⣿⣿⣿⣿⣿⡏",
    "  ⢠⣾⣿⣿⣿⠟⠁⠘⣿⣿⣿⣿⡄⠈⠻⣿⣿⣿⣿⣿⣿⣿⣿⠟⠁  ⠙⢿⣿⣿⣿⣿⣿⣿⡿⠋   ⠘⢿⣿⣿⣿⣿⣿⣿⣿⣿⠟⠋    ⢸⣿⣿⣿⣿⣿⠁  ⢿⣿⣿⣿⣿⡟",
    "  ⠈⠉⠉⠉⠉   ⠉⠉⠉⠉⠁  ⠈⠉⠛⠛⠛⠉⠉       ⠉⠙⠛⠛⠉⠁       ⠈⠉⠛⠛⣿⣿⣿⣿⣄     ⠈⠉⠉⠉⠉⠁   ⠈⠉⠉⠉⠉⠁",
    "                                                ⠘⠿⠿⠿⠿⠆",
)
INSTALLER_LOGO_WIDTH = max(map(len, INSTALLER_LOGO))


@dataclass(frozen=True)
class UpdatePlanRow:
    kind: str
    item: str
    installed: str
    available: str
    action: str
    size: int | None = None
    details: tuple[tuple[str, str], ...] = ()


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


def terminal_size(fallback: tuple[int, int] = (100, 24)) -> os.terminal_size:
    """Prefer live TTY geometry over inherited COLUMNS/LINES values."""

    if sys.stdout.isatty():
        try:
            return os.get_terminal_size(sys.stdout.fileno())
        except (AttributeError, OSError, ValueError):
            pass
    return shutil.get_terminal_size(fallback)


def _table_lines(
    headers: tuple[str, ...], rows: list[tuple[str, ...]],
) -> list[str]:
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]

    def render(row: tuple[str, ...]) -> str:
        return "  " + " | ".join(
            value.ljust(widths[index]) for index, value in enumerate(row)
        ).rstrip()

    return [
        render(headers),
        "  " + "-+-".join("-" * width for width in widths),
        *(render(row) for row in rows),
    ]


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
        labels = {
            "[OK]": "✓",
            "[INFO]": "·",
            "[ATENÇÃO]": "!",
            "[ERRO]": "✗",
            "==>": "·",
        }
        palette = {
            "1;36": f"{ACCENT}m\033[1",
            "1": f"{ACCENT}m\033[1",
            "36": MUTED,
            "32": SUCCESS,
            "33": WARNING,
            "31": ERROR,
            "2": MUTED,
        }
        text = labels.get(text, text)
        code = palette.get(code, code)
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def bold_color(self, text: str, code: str) -> str:
        if not self.color:
            return text
        return f"\033[{code}m\033[1m{text}\033[0m"

    def banner(self, action: str, target: Path) -> None:
        if os.environ.get("X86QW_BOOTSTRAP_UI") == "1":
            return
        version = self._version() if self._version is not None else ""
        terminal_width = terminal_size(
            (INSTALLER_LOGO_WIDTH, 24),
        ).columns
        outer_padding = " " * max(0, (terminal_width - INSTALLER_LOGO_WIDTH) // 2)
        for line in INSTALLER_LOGO:
            print(self.bold_color(outer_padding + line, ACCENT), flush=True)
        print(flush=True)
        identity = "qw.x86.com.br"
        if version:
            identity += f" | instalador {version}"
        identity = outer_padding + identity.center(INSTALLER_LOGO_WIDTH).rstrip()
        print(self.paint(identity, MUTED), flush=True)
        print(flush=True)
        print(self.paint("Preparando interface do instalador...", INFO), flush=True)
        print(flush=True)

    def key_value(self, key: str, value: str) -> None:
        print(f"{self.paint(key + ':', MUTED)} {value}", flush=True)

    def section(self, title: str) -> None:
        self.activity_done()
        self.flush_download_summary()
        print(f"\n{self.paint(title, '1;36')}", flush=True)

    def heading(self, title: str) -> None:
        self.activity_done()
        self.flush_download_summary()
        print(f"\n{self.paint('==>', '1;36')} {self.paint(title, '1')}", flush=True)

    def plan_section(self, title: str) -> None:
        print(self.paint(title, "1"), flush=True)

    def plan_table(self, lines: list[str]) -> None:
        connector = self.paint("│", MUTED)
        for index, line in enumerate(lines):
            content = line[2:] if line.startswith("  ") else line
            if index == 0:
                content = self.paint(content, SUCCESS)
            elif index == 1:
                content = self.paint(content, MUTED)
            print(f"{connector} {content}", flush=True)

    def info(self, message: str) -> None:
        self.activity_done()
        print(f"{self.paint('[INFO]', '36')} {message}", flush=True)

    def success(self, message: str) -> None:
        self.activity_done()
        self.flush_download_summary()
        print(f"{self.paint('[OK]', '32')} {message}", flush=True)

    def warning(self, message: str) -> None:
        self.activity_done()
        print(f"{self.paint('[ATENÇÃO]', '33')} {message}", flush=True)

    def detail(self, message: str) -> None:
        if self.verbose:
            print(self.paint(f"       {message}", "2"), flush=True)

    def error(self, message: str) -> None:
        self.activity_done()
        label = self.paint("[ERRO]", "31") if self.color and sys.stderr.isatty() else "✗"
        print(f"{label} {message}", file=sys.stderr, flush=True)

    def update_plan(
        self,
        rows: list[UpdatePlanRow],
        action: str,
        *,
        destination: str = "",
        profile: str = "",
    ) -> str:
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
        if action == "install":
            if destination:
                destination_line = f"Destino: {destination}"
                self.key_value("Destino", destination)
                confirmation_lines.append(destination_line)
            if profile:
                profile_line = f"Perfil: {profile}"
                self.key_value("Perfil", profile)
                confirmation_lines.append(profile_line)

            client_rows = [row for row in rows if row.kind.casefold() != "componente"]
            if client_rows:
                self.plan_section("Cliente")
                confirmation_lines.append("Cliente")
                client_table = []
                client_paths = []
                for row in client_rows:
                    details = dict(row.details)
                    client_table.append((
                        details.get("Cliente", row.item),
                        details.get("Plataforma", "—"),
                        details.get("Arquitetura", "—"),
                        details.get("Canal", "—"),
                        row.available,
                        format_bytes_compact(row.size) if row.size is not None else "—",
                    ))
                    if details.get("Caminho"):
                        client_paths.append(details["Caminho"])
                client_lines = _table_lines(
                    ("Cliente", "Plataforma", "Arquitetura", "Canal", "Versão", "Tamanho"),
                    client_table,
                )
                self.plan_table(client_lines)
                confirmation_lines.extend(client_lines)
                for path in client_paths:
                    path_line = f"Caminho do cliente: {path}"
                    self.key_value("Caminho do cliente", path)
                    confirmation_lines.append(path_line)

            component_rows = [
                row for row in rows if row.kind.casefold() == "componente"
            ]
            if component_rows:
                component_size = sum(row.size or 0 for row in component_rows)
                component_heading = (
                    f"Módulos x86QW · {len(component_rows)}"
                    + (
                        f" · {format_bytes_compact(component_size)}"
                        if component_size else ""
                    )
                )
                self.plan_section(component_heading)
                confirmation_lines.append(component_heading)
                show_origin = any(dict(row.details).get("Origem") for row in component_rows)
                headers = ("#", "Módulo", "Versão", "Tamanho") + (
                    ("Origem",) if show_origin else ()
                )
                component_table = []
                for index, row in enumerate(component_rows, 1):
                    details = dict(row.details)
                    package = details.get("Pacote")
                    package_version = (
                        f"{package}@{row.available}" if package else row.available
                    )
                    values = (
                        str(index),
                        row.item,
                        package_version,
                        format_bytes_compact(row.size) if row.size is not None else "—",
                    )
                    if show_origin:
                        values += (details.get("Origem", "—"),)
                    component_table.append(values)
                component_lines = _table_lines(headers, component_table)
                self.plan_table(component_lines)
                confirmation_lines.extend(component_lines)
            return "\n".join(confirmation_lines)

        display_rows = rows
        names = [row.item for row in rows]
        installed = [row.installed for row in rows]
        available = [row.available for row in rows]
        name_width = max(map(len, names))
        installed_width = max(map(len, installed))
        available_width = max(map(len, available))
        terminal_width = max(40, min(terminal_size((100, 24)).columns, 120))
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
        return "\n".join(confirmation_lines)

    def activity(self, current: int, total: int) -> None:
        print(
            f"{self.paint('[INFO]', '36')} [{current}/{total}] Processando pacote",
            flush=True,
        )

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
        terminal_width = max(40, min(terminal_size((100, 24)).columns, 120))
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
    "terminal_size",
)
