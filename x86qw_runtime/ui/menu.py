#!/usr/bin/env python3
"""Portable, dependency-free navigation primitives for the x86QW CLI."""

from __future__ import annotations

import os
import select
import sys
import textwrap
import unicodedata
from dataclasses import dataclass
from typing import Callable, Iterable

from x86qw_runtime.ui.console import (
    ACCENT, ERROR, INFO, MUTED, SUCCESS, WARNING, terminal_size,
)


@dataclass(frozen=True)
class MenuOption:
    key: str
    label: str
    description: str = ""
    detail: str = ""
    enabled: bool = True
    aliases: tuple[str, ...] = ()
    disabled_reason: str = ""
    group: str = ""


class MenuCancelled(Exception):
    """Raised when the user leaves an interactive selection."""


class MenuExit(Exception):
    """Raised when the user explicitly asks to leave the menu navigator."""


_NO_COLOR = False
_ESCAPE_INITIAL_TIMEOUT = 0.12
_ESCAPE_CONTINUATION_TIMEOUT = 0.04
_ESCAPE_SEQUENCE_LIMIT = 16
_CLEAR = "\033[2J\033[H"
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"
_TITLE = f"{ACCENT}m\033[1"
_ACCENT = f"{SUCCESS}m\033[1"
_MUTED = MUTED
_SEARCH = f"{WARNING}m\033[1"
_OK = f"{SUCCESS}m\033[1"
_WIZARD_PROMPT = ACCENT
_WIZARD_DETAIL = MUTED


def configure(*, no_color: bool = False) -> None:
    global _NO_COLOR
    _NO_COLOR = no_color or "NO_COLOR" in os.environ


def _paint(value: str, code: str) -> str:
    if not code or _NO_COLOR or not _isatty(sys.stdout):
        return value
    return f"\033[{code}m{value}\033[0m"


def _wizard_color(value: str, code: str, reset: str = "39") -> str:
    if _NO_COLOR or not _isatty(sys.stdout):
        return value
    return f"\033[{code}m{value}\033[{reset}m"


def _wizard_dim(value: str) -> str:
    if _NO_COLOR or not _isatty(sys.stdout):
        return value
    return f"\033[2m{value}\033[22m"


def _isatty(stream: object) -> bool:
    checker = getattr(stream, "isatty", None)
    return bool(checker is not None and checker())


def supports_navigation() -> bool:
    if not _isatty(sys.stdin) or not _isatty(sys.stdout):
        return False
    if os.environ.get("TERM", "").casefold() == "dumb":
        return False
    return os.name == "nt" or sys.stdin.fileno() >= 0


def _read_windows_key() -> str:  # pragma: no cover - exercised on Windows
    import msvcrt

    key = msvcrt.getwch()
    if key in ("\x00", "\xe0"):
        return {
            "H": "up", "P": "down", "K": "left", "M": "right",
            "I": "pageup", "Q": "pagedown", "G": "home", "O": "end",
        }.get(msvcrt.getwch(), "unknown")
    return {
        "\r": "enter", "\x1b": "escape", "\x08": "backspace",
        "\x03": "interrupt",
    }.get(key, key)


def _decode_posix_escape(sequence: bytes) -> str:
    """Decode a complete CSI or SS3 sequence without treating it as bare Esc."""
    if sequence[:1] not in (b"[", b"O"):
        return "unknown"
    if sequence[:1] == b"O":
        return {
            b"A": "up", b"B": "down", b"C": "right", b"D": "left",
            b"H": "home", b"F": "end",
        }.get(sequence[-1:], "unknown")
    body = sequence[1:]
    if body.endswith(b"~"):
        code = body[:-1].split(b";", 1)[0]
        return {
            b"1": "home", b"4": "end", b"5": "pageup", b"6": "pagedown",
        }.get(code, "unknown")
    return {
        b"A": "up", b"B": "down", b"C": "right", b"D": "left",
        b"H": "home", b"F": "end",
    }.get(sequence[-1:], "unknown")


def _read_posix_escape(descriptor: int) -> str:
    sequence = b""
    timeout = _ESCAPE_INITIAL_TIMEOUT
    while len(sequence) < _ESCAPE_SEQUENCE_LIMIT:
        if not select.select([descriptor], [], [], timeout)[0]:
            break
        part = os.read(descriptor, 1)
        if not part:
            break
        sequence += part
        timeout = _ESCAPE_CONTINUATION_TIMEOUT
        if sequence[:1] == b"[":
            if len(sequence) >= 2 and 0x40 <= sequence[-1] <= 0x7E:
                break
        elif sequence[:1] == b"O":
            if len(sequence) >= 2:
                break
        else:
            break
    return "escape" if not sequence else _decode_posix_escape(sequence)


def _utf8_needed(lead: int) -> int | None:
    if lead < 0x80:
        return 1
    if 0xC2 <= lead <= 0xDF:
        return 2
    if 0xE0 <= lead <= 0xEF:
        return 3
    if 0xF0 <= lead <= 0xF4:
        return 4
    return None


def _decode_utf8_character(
    first: bytes,
    read_byte: Callable[[], bytes],
    *,
    wait: Callable[[], bool] | None = None,
) -> str:
    """Assemble one UTF-8 character from a lead byte plus optional continuations."""
    needed = _utf8_needed(first[0]) if first else None
    if needed is None:
        return "unknown"
    data = first
    while len(data) < needed:
        if wait is not None and not wait():
            return "unknown"
        part = read_byte()
        if not part:
            return "unknown"
        data += part
    try:
        character = data.decode("utf-8")
    except UnicodeDecodeError:
        return "unknown"
    return character if character.isprintable() else "unknown"


def _read_posix_key() -> str:  # pragma: no cover - real terminal path
    import termios
    import tty

    descriptor = sys.stdin.fileno()
    previous = termios.tcgetattr(descriptor)
    try:
        tty.setraw(descriptor, when=termios.TCSANOW)
        first = os.read(descriptor, 1)
        if first == b"\x1b":
            return _read_posix_escape(descriptor)
        if first in (b"\r", b"\n"):
            return "enter"
        if first in (b"\x7f", b"\x08"):
            return "backspace"
        if first == b"\x03":
            return "interrupt"
        wait = lambda: bool(
            select.select([descriptor], [], [], _ESCAPE_CONTINUATION_TIMEOUT)[0]
        )
        return _decode_utf8_character(
            first, lambda: os.read(descriptor, 1), wait=wait,
        )
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)


def read_key() -> str:
    return _read_windows_key() if os.name == "nt" else _read_posix_key()


def _fold(value: str) -> str:
    stripped = unicodedata.normalize("NFD", value.casefold())
    return "".join(
        character for character in stripped if unicodedata.category(character) != "Mn"
    )


def _option_search_text(option: MenuOption) -> str:
    return " ".join((
        option.key, option.label, option.description, option.detail,
        option.disabled_reason, option.group, *option.aliases,
    ))


def _matching(options: tuple[MenuOption, ...], query: str) -> list[int]:
    if not query:
        return list(range(len(options)))
    folded = _fold(query)
    return [
        index for index, option in enumerate(options)
        if folded in _fold(_option_search_text(option))
    ]


def _label_width(options: tuple[MenuOption, ...]) -> int:
    return max(len(option.label) for option in options)


def _description_width(options: tuple[MenuOption, ...]) -> int:
    return max((len(option.description) for option in options), default=0)


def _clip(value: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    if width == 1:
        return "…"
    return value[:width - 1].rstrip() + "…"


def _wrapped(value: str, width: int) -> list[str]:
    return textwrap.wrap(
        value, width=max(8, width), break_long_words=False,
        break_on_hyphens=False,
    ) or [""]


def _option_lines(
    *,
    prefix: str,
    option: MenuOption,
    label_width: int,
    description_width: int,
    width: int,
    active: bool,
    group: str = "",
) -> list[str]:
    """Render a responsive row. Color is never the only selected-state signal."""
    lines: list[str] = []
    if group:
        lines.append(_paint(_clip(group, width), _MUTED))
    label = f"{prefix} {option.label:<{label_width}}"
    description = ""
    if description_width:
        description = f" · {option.description:<{description_width}}"
    explanation = option.detail if active and option.detail else ""
    if not option.enabled:
        explanation = "indisponível" + (
            f": {option.disabled_reason}" if option.disabled_reason else ""
        )
    primary = label + description
    if len(primary) <= width:
        suffix = f"  < {explanation}" if explanation else ""
        remaining = max(0, width - len(primary))
        painted = _row_primary(primary, active=active, enabled=option.enabled)
        if not suffix:
            return [*lines, painted]
        if len(suffix) <= remaining:
            return [*lines, painted + _paint(suffix, _MUTED)]
        continuation = " " * min(len(prefix) + 1, max(0, width - 2))
        return [
            *lines,
            painted,
            *(
                _paint(continuation + part, _MUTED)
                for part in _wrapped("< " + explanation, width - len(continuation))
            ),
        ]

    first = _clip(label.rstrip(), width)
    body = [_row_primary(first, active=active, enabled=option.enabled)]
    indent = min(len(prefix) + 1, max(0, width - 2))
    continuation = " " * indent
    if option.description:
        for part in _wrapped("· " + option.description, width - indent):
            body.append(_row_primary(
                continuation + part, active=active, enabled=option.enabled,
            ))
    if explanation:
        for part in _wrapped("< " + explanation, width - indent):
            body.append(_paint(continuation + part, _MUTED))
    return [*lines, *body]


def _row_primary(value: str, *, active: bool, enabled: bool) -> str:
    if not enabled:
        return _paint(value, _MUTED)
    if active:
        return _paint(value, _ACCENT)
    return value


def _footer_lines(items: list[str], width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for item in items:
        candidate = item if not current else current + "   " + item
        if current and len(candidate) > width:
            lines.append(current)
            current = item
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _selectable_positions(options: tuple[MenuOption, ...], matches: list[int]) -> list[int]:
    return [position for position, index in enumerate(matches) if options[index].enabled]


def _default_position(
    options: tuple[MenuOption, ...], matches: list[int], default: int,
) -> int:
    selectable = _selectable_positions(options, matches)
    if not selectable:
        return 0
    return selectable[min(default, len(selectable) - 1)]


def _row_window(lengths: list[int], selected: int, budget: int) -> tuple[int, int]:
    """Return a contiguous item window that fits the available terminal rows."""
    if not lengths:
        return 0, 0
    start = max(0, min(selected, len(lengths) - 1))
    end = start + 1
    used = lengths[start]
    while True:
        before = start - 1 if start > 0 else None
        after = end if end < len(lengths) else None
        candidates = []
        if before is not None:
            candidates.append((lengths[before], "before"))
        if after is not None:
            candidates.append((lengths[after], "after"))
        fitting = [candidate for candidate in candidates if used + candidate[0] <= budget]
        if not fitting:
            break
        _length, side = min(fitting, key=lambda candidate: candidate[0])
        if side == "before":
            start -= 1
            used += lengths[start]
        else:
            used += lengths[end]
            end += 1
    return start, end


def _chrome_width() -> int:
    return max(20, min(terminal_size((88, 24)).columns, 110))


def _row_budget() -> int:
    lines = terminal_size((88, 24)).lines
    reserved = 8 if lines < 22 else 11
    return max(3, lines - reserved)


def _compact_chrome() -> bool:
    return terminal_size((88, 24)).lines < 22


def _begin_frame() -> None:
    sys.stdout.write(_CLEAR)
    if _isatty(sys.stdout):
        sys.stdout.write(_HIDE_CURSOR)


def _restore_cursor() -> None:
    if _isatty(sys.stdout):
        sys.stdout.write(_SHOW_CURSOR)
        sys.stdout.flush()


def _rule(width: int) -> str:
    return _paint("─" * min(width, 42), _MUTED)


def _emit_text(value: str, width: int, code: str = "") -> None:
    for line in value.splitlines() or [""]:
        if not line:
            print()
            continue
        for part in _wrapped(line, width):
            print(_paint(part, code) if code else part)


def _render_header(
    *,
    title: str,
    breadcrumb: str,
    subtitle: str,
    query: str,
    searching: bool,
    searchable: bool,
    numeric_buffer: str,
    numeric_label: str,
    width: int,
) -> None:
    if breadcrumb:
        _emit_text(breadcrumb, width, _MUTED)
        if not _compact_chrome():
            print(_rule(width))
    _emit_text(title, width, _TITLE)
    if subtitle:
        _emit_text(subtitle, width)
    if searchable:
        if searching or query:
            field = query + ("_" if searching else "")
            print(f"\n{_paint('/', _MUTED)} {_paint(field, _SEARCH)}")
        else:
            print(_paint("\n/  buscar", _MUTED))
    if numeric_buffer:
        print(f"\n{numeric_label}: {_paint(numeric_buffer + '_', _SEARCH)}")
    print()


def _render_rows(
    *,
    options: tuple[MenuOption, ...],
    matches: list[int],
    selected: int,
    visible: list[int],
    start: int,
    end: int,
    rendered_rows: list[list[str]],
    checked: set[str] | None = None,
) -> None:
    if not visible:
        print(_paint("  Nenhum resultado para esta busca.", _MUTED))
        return
    for position, option_index in enumerate(visible, start):
        option = options[option_index]
        lines = rendered_rows[position]
        for index, line in enumerate(lines):
            if (
                checked is not None
                and option.key in checked
                and position != selected
                and "[✓]" in line
            ):
                print(line.replace("[✓]", _paint("[✓]", _OK), 1))
            else:
                print(line)
    if len(matches) > len(visible):
        print(_paint(
            f"\n{start + 1}–{end} de {len(matches)}", _MUTED,
        ))


def _build_rows(
    *,
    options: tuple[MenuOption, ...],
    matches: list[int],
    selected: int,
    width: int,
    marker: Callable[[int, MenuOption], str],
) -> list[list[str]]:
    index_width = len(str(len(matches)))
    visible_options = tuple(options[index] for index in matches)
    label_width = _label_width(visible_options or options)
    description_width = _description_width(visible_options or options)
    previous_group = ""
    rows: list[list[str]] = []
    for position, option_index in enumerate(matches):
        option = options[option_index]
        show_group = bool(option.group) and option.group != previous_group
        previous_group = option.group or previous_group
        block = _option_lines(
            prefix=marker(position, option),
            option=option,
            label_width=label_width,
            description_width=description_width,
            width=width,
            active=position == selected,
            group=option.group if show_group else "",
        )
        if show_group and position > 0:
            block = ["", *block]
        rows.append(block)
    return rows


def _render_navigation(
    *,
    title: str,
    options: tuple[MenuOption, ...],
    matches: list[int],
    selected: int,
    breadcrumb: str,
    subtitle: str,
    query: str,
    searching: bool,
    searchable: bool,
    allow_back: bool,
    numeric_buffer: str,
) -> None:
    width = _chrome_width()
    row_budget = _row_budget()
    index_width = len(str(max(len(matches), 1)))

    def marker(position: int, _option: MenuOption) -> str:
        caret = "▸" if position == selected else " "
        return f"{caret} {position + 1:>{index_width}})"

    rendered_rows = _build_rows(
        options=options, matches=matches, selected=selected, width=width,
        marker=marker,
    )
    start, end = _row_window(
        [len(lines) for lines in rendered_rows], selected, row_budget,
    )
    visible = matches[start:end]
    _begin_frame()
    _render_header(
        title=title, breadcrumb=breadcrumb, subtitle=subtitle, query=query,
        searching=searching, searchable=searchable, numeric_buffer=numeric_buffer,
        numeric_label="Ir para o item", width=width,
    )
    _render_rows(
        options=options, matches=matches, selected=selected, visible=visible,
        start=start, end=end, rendered_rows=rendered_rows,
    )
    if searching:
        footer = ["Digite para buscar", "Enter aplicar busca", "Esc limpar busca"]
    elif numeric_buffer:
        footer = ["Digite o número completo", "→/Enter selecionar", "Esc limpar número"]
    else:
        footer = ["↑↓ navegar", "→/Enter selecionar"]
        if searchable:
            footer.append("/ buscar")
        footer.append("← voltar" if allow_back else "← cancelar")
        footer.append("esc sair")
    print()
    if not _compact_chrome():
        print(_rule(width))
    for line in _footer_lines(footer, width):
        print(_paint(line, _MUTED), flush=True)


def _move_selection(
    selected: int, selectable: list[int], key: str, page: int,
) -> int:
    if not selectable:
        return selected
    current = selectable.index(selected) if selected in selectable else 0
    if key in ("up", "k"):
        return selectable[(current - 1) % len(selectable)]
    if key in ("down", "j"):
        return selectable[(current + 1) % len(selectable)]
    if key == "home":
        return selectable[0]
    if key == "end":
        return selectable[-1]
    if key == "pageup":
        return selectable[max(0, current - page)]
    if key == "pagedown":
        return selectable[min(len(selectable) - 1, current + page)]
    return selected


def _select_navigation(
    title: str,
    options: tuple[MenuOption, ...],
    *,
    breadcrumb: str,
    subtitle: str,
    default: int,
    searchable: bool,
    allow_back: bool,
    key_reader: Callable[[], str],
) -> str | None:
    query = ""
    searching = False
    numeric_buffer = ""
    matches = _matching(options, query)
    if not any(option.enabled for option in options):
        raise ValueError("menu has no enabled options")
    selected = _default_position(options, matches, default)
    try:
        while True:
            _render_navigation(
                title=title, options=options, matches=matches, selected=selected,
                breadcrumb=breadcrumb, subtitle=subtitle, query=query,
                searching=searching, searchable=searchable, allow_back=allow_back,
                numeric_buffer=numeric_buffer,
            )
            key = key_reader()
            if key == "interrupt":
                raise KeyboardInterrupt
            if searching:
                if key == "enter":
                    searching = False
                elif key == "escape":
                    query = ""
                    searching = False
                elif key == "backspace":
                    query = query[:-1]
                elif len(key) == 1 and key.isprintable():
                    query += key
                matches = _matching(options, query)
                selected = _default_position(options, matches, 0)
                continue
            selectable = _selectable_positions(options, matches)
            if numeric_buffer:
                if key.isdigit():
                    candidate = numeric_buffer + key
                    if int(candidate) <= len(matches):
                        numeric_buffer = candidate
                elif key == "backspace":
                    numeric_buffer = numeric_buffer[:-1]
                elif key in ("enter", "right"):
                    position = int(numeric_buffer) - 1
                    if 0 <= position < len(matches) and options[matches[position]].enabled:
                        return options[matches[position]].key
                    numeric_buffer = ""
                elif key == "escape":
                    numeric_buffer = ""
                else:
                    numeric_buffer = ""
                continue
            if key in {"up", "down", "k", "j", "home", "end", "pageup", "pagedown"}:
                selected = _move_selection(
                    selected, selectable, key, page=max(1, _row_budget() // 2),
                )
            elif key in ("enter", "right") and selectable:
                return options[matches[selected]].key
            elif key == "left":
                if allow_back:
                    return None
                raise MenuCancelled(title)
            elif key in ("escape", "q"):
                raise MenuExit(title)
            elif key == "/" and searchable:
                searching = True
            elif key.isdigit() and key != "0":
                if len(matches) > 9:
                    numeric_buffer = key
                else:
                    position = int(key) - 1
                    if position < len(matches) and options[matches[position]].enabled:
                        return options[matches[position]].key
    finally:
        _restore_cursor()


def _wizard_frame(
    title: str,
    options: tuple[MenuOption, ...],
    matches: list[int],
    selected: int,
    *,
    query: str,
    searching: bool,
    searchable: bool,
    allow_back: bool,
    numeric_buffer: str,
) -> str:
    connector = _wizard_color("│", INFO)
    muted_connector = _wizard_color("│", MUTED)
    lines = [muted_connector]
    lines.append(
        f"{_wizard_color('◆', SUCCESS)}  "
        f"{_wizard_color(title, _WIZARD_PROMPT)}"
    )
    if searching or query:
        field = query + ("█" if searching else "")
        lines.append(f"{connector}  {_wizard_color('/', MUTED)} {field}")
    if numeric_buffer:
        lines.append(
            f"{connector}  {_wizard_dim('Ir para o item:')} {numeric_buffer}█"
        )
    row_budget = max(3, terminal_size((88, 24)).lines - 8)
    selectable = _selectable_positions(options, matches)
    if selected not in selectable and selectable:
        selected = selectable[0]
    start = max(0, selected - row_budget // 2)
    end = min(len(matches), start + row_budget)
    start = max(0, end - row_budget)
    for position in range(start, end):
        option = options[matches[position]]
        if not option.enabled:
            label = _wizard_dim(f"○ {option.label}")
            detail = option.disabled_reason or option.description
        elif position == selected:
            label = f"{_wizard_color('●', SUCCESS)} {option.label}"
            detail = option.description
        else:
            label = f"{_wizard_dim('○')} {_wizard_dim(option.label)}"
            detail = ""
        if detail:
            label += " " + _wizard_dim(
                f"({_wizard_color(detail, _WIZARD_DETAIL)})"
            )
        lines.append(f"{connector}  {label}")
    if len(matches) > end - start:
        lines.append(f"{connector}  {_wizard_dim(f'{start + 1}–{end} de {len(matches)}')}")
    if searching:
        footer = (
            f"{_wizard_dim('Digite')} para buscar • "
            f"{_wizard_dim('Enter:')} aplicar • {_wizard_dim('Esc:')} limpar"
        )
    elif numeric_buffer:
        footer = (
            f"{_wizard_dim('Digite')} o número completo • "
            f"{_wizard_dim('Enter:')} confirmar • {_wizard_dim('Esc:')} limpar"
        )
    else:
        footer = (
            f"{_wizard_dim('↑/↓')} para navegar • "
            f"{_wizard_dim('Enter:')} confirmar"
        )
        if searchable:
            footer += f" • {_wizard_dim('/:')} buscar"
        if allow_back:
            footer += f" • {_wizard_dim('←:')} voltar"
    lines.extend((f"{connector}  {footer}", _wizard_color("└", INFO)))
    return "\n".join(lines) + "\n"


def _wizard_collapse(title: str, option: MenuOption) -> str:
    return (
        f"{_wizard_color('◇', SUCCESS)}  "
        f"{_wizard_color(title, _WIZARD_PROMPT)}\n"
        f"{_wizard_color('│', MUTED)}  {_wizard_dim(option.label)}\n"
    )


def _wizard_multiple_frame(
    title: str,
    options: tuple[MenuOption, ...],
    matches: list[int],
    selected: int,
    checked: set[str],
    *,
    query: str,
    searching: bool,
    searchable: bool,
    allow_back: bool,
    numeric_buffer: str,
    validation_message: str,
) -> str:
    connector = _wizard_color("│", INFO)
    muted_connector = _wizard_color("│", MUTED)
    lines = [muted_connector]
    lines.append(
        f"{_wizard_color('◆', SUCCESS)}  "
        f"{_wizard_color(title, _WIZARD_PROMPT)}"
    )
    if searching or query:
        field = query + ("█" if searching else "")
        lines.append(f"{connector}  {_wizard_color('/', MUTED)} {field}")
    if numeric_buffer:
        lines.append(
            f"{connector}  {_wizard_dim('Marcar item:')} {numeric_buffer}█"
        )
    row_budget = max(3, terminal_size((88, 24)).lines - 9)
    selectable = _selectable_positions(options, matches)
    if selected not in selectable and selectable:
        selected = selectable[0]
    start = max(0, selected - row_budget // 2)
    end = min(len(matches), start + row_budget)
    start = max(0, end - row_budget)
    for position in range(start, end):
        option = options[matches[position]]
        marker = "[✓]" if option.key in checked else "[ ]"
        if not option.enabled:
            label = _wizard_dim(f"{marker} {option.label}")
            detail = option.disabled_reason or option.description
        elif position == selected:
            label = f"{_wizard_color(marker, SUCCESS)} {option.label}"
            detail = option.description
        else:
            label = f"{_wizard_dim(marker)} {_wizard_dim(option.label)}"
            detail = ""
        if detail:
            label += " " + _wizard_dim(
                f"({_wizard_color(detail, _WIZARD_DETAIL)})"
            )
        lines.append(f"{connector}  {label}")
    if len(matches) > end - start:
        lines.append(
            f"{connector}  {_wizard_dim(f'{start + 1}–{end} de {len(matches)}')}"
        )
    if validation_message:
        lines.append(f"{connector}  {_wizard_color(validation_message, ERROR)}")
    if searching:
        footer = (
            f"{_wizard_dim('Digite')} para buscar • "
            f"{_wizard_dim('Enter:')} aplicar • {_wizard_dim('Esc:')} limpar"
        )
    elif numeric_buffer:
        footer = (
            f"{_wizard_dim('Digite')} o número completo • "
            f"{_wizard_dim('Espaço/Enter:')} marcar • {_wizard_dim('Esc:')} limpar"
        )
    else:
        footer = (
            f"{_wizard_dim('↑/↓')} para navegar • "
            f"{_wizard_dim('Espaço:')} marcar • {_wizard_dim('Enter:')} concluir"
        )
        footer += (
            f" • {_wizard_dim('A:')} marcar tudo"
            f" • {_wizard_dim('D:')} desmarcar tudo"
        )
        if searchable:
            footer += f" • {_wizard_dim('/:')} buscar"
        if allow_back:
            footer += f" • {_wizard_dim('←:')} voltar"
    lines.extend((f"{connector}  {footer}", _wizard_color("└", INFO)))
    return "\n".join(lines) + "\n"


def _wizard_multiple_collapse(title: str, count: int) -> str:
    noun = "componente selecionado" if count == 1 else "componentes selecionados"
    return (
        f"{_wizard_color('◇', SUCCESS)}  "
        f"{_wizard_color(title, _WIZARD_PROMPT)}\n"
        f"{_wizard_color('│', MUTED)}  {_wizard_dim(f'{count} {noun}')}\n"
    )


def _select_wizard(
    title: str,
    options: tuple[MenuOption, ...],
    *,
    default: int,
    searchable: bool,
    allow_back: bool,
    key_reader: Callable[[], str],
) -> str | None:
    query = ""
    searching = False
    numeric_buffer = ""
    matches = _matching(options, query)
    if not any(option.enabled for option in options):
        raise ValueError("menu has no enabled options")
    selected = _default_position(options, matches, default)
    previous_lines = 0
    if _isatty(sys.stdout):
        sys.stdout.write(_HIDE_CURSOR)
    try:
        while True:
            frame = _wizard_frame(
                title, options, matches, selected, query=query,
                searching=searching, searchable=searchable,
                allow_back=allow_back,
                numeric_buffer=numeric_buffer,
            )
            if previous_lines:
                sys.stdout.write(f"\033[999D\033[{previous_lines}A\033[J")
            sys.stdout.write(frame)
            sys.stdout.flush()
            previous_lines = frame.count("\n")
            key = key_reader()
            if key == "interrupt":
                raise KeyboardInterrupt
            if searching:
                if key == "enter":
                    searching = False
                elif key == "escape":
                    query = ""
                    searching = False
                elif key == "backspace":
                    query = query[:-1]
                elif len(key) == 1 and key.isprintable():
                    query += key
                matches = _matching(options, query)
                selected = _default_position(options, matches, 0)
                continue
            selectable = _selectable_positions(options, matches)
            if numeric_buffer:
                if key.isdigit():
                    candidate = numeric_buffer + key
                    if int(candidate) <= len(matches):
                        numeric_buffer = candidate
                elif key == "backspace":
                    numeric_buffer = numeric_buffer[:-1]
                elif key in ("enter", "right"):
                    position = int(numeric_buffer) - 1
                    if 0 <= position < len(matches) and options[matches[position]].enabled:
                        option = options[matches[position]]
                        sys.stdout.write(f"\033[999D\033[{previous_lines}A\033[J")
                        sys.stdout.write(_wizard_collapse(title, option))
                        sys.stdout.flush()
                        return option.key
                    numeric_buffer = ""
                elif key == "escape":
                    numeric_buffer = ""
                else:
                    numeric_buffer = ""
                continue
            if key in {"up", "down", "k", "j", "home", "end", "pageup", "pagedown"}:
                selected = _move_selection(
                    selected, selectable, key,
                    page=max(1, _row_budget() // 2),
                )
            elif key in ("enter", "right") and selectable:
                option = options[matches[selected]]
                sys.stdout.write(f"\033[999D\033[{previous_lines}A\033[J")
                sys.stdout.write(_wizard_collapse(title, option))
                sys.stdout.flush()
                return option.key
            elif key == "left":
                if allow_back:
                    return None
                raise MenuCancelled(title)
            elif key in ("escape", "q"):
                raise MenuExit(title)
            elif key == "/" and searchable:
                searching = True
            elif key.isdigit() and key != "0":
                if len(matches) > 9:
                    numeric_buffer = key
                else:
                    position = int(key) - 1
                    if position < len(matches) and options[matches[position]].enabled:
                        option = options[matches[position]]
                        sys.stdout.write(f"\033[999D\033[{previous_lines}A\033[J")
                        sys.stdout.write(_wizard_collapse(title, option))
                        sys.stdout.flush()
                        return option.key
    finally:
        _restore_cursor()


def _wizard_text_frame(title: str, value: str, description: str) -> str:
    connector = _wizard_color("│", INFO)
    lines = [
        _wizard_color("│", MUTED),
        f"{_wizard_color('◆', SUCCESS)}  {_wizard_color(title, _WIZARD_PROMPT)}",
        f"{connector}  {value}█",
    ]
    if description:
        lines.append(f"{connector}  {_wizard_dim(description)}")
    lines.append(_wizard_color("└", INFO))
    return "\n".join(lines) + "\n"


def _prompt_text_wizard(
    title: str,
    *,
    default: str,
    description: str,
    key_reader: Callable[[], str],
) -> str:
    value = default
    previous_lines = 0
    if _isatty(sys.stdout):
        sys.stdout.write(_HIDE_CURSOR)
    try:
        while True:
            frame = _wizard_text_frame(title, value, description)
            if previous_lines:
                sys.stdout.write(f"\033[999D\033[{previous_lines}A\033[J")
            sys.stdout.write(frame)
            sys.stdout.flush()
            previous_lines = frame.count("\n")
            key = key_reader()
            if key == "interrupt":
                raise KeyboardInterrupt
            if key == "enter":
                answer = value or default
                sys.stdout.write(f"\033[999D\033[{previous_lines}A\033[J")
                sys.stdout.write(
                    f"{_wizard_color('◇', SUCCESS)}  "
                    f"{_wizard_color(title, _WIZARD_PROMPT)}\n"
                    f"{_wizard_color('│', MUTED)}  {_wizard_dim(answer)}\n"
                )
                sys.stdout.flush()
                return answer
            if key in ("escape", "q"):
                raise MenuExit(title)
            if key == "backspace":
                value = value[:-1]
            elif len(key) == 1 and key.isprintable():
                value += key
    finally:
        _restore_cursor()


def prompt_text(
    title: str,
    *,
    default: str = "",
    description: str = "",
    interactive: bool | None = None,
    key_reader: Callable[[], str] | None = None,
    input_fn: Callable[[str], str] | None = None,
    presentation: str = "menu",
) -> str:
    navigation = supports_navigation() if interactive is None else interactive
    if presentation not in {"menu", "wizard"}:
        raise ValueError("presentation must be menu or wizard")
    if navigation and presentation == "wizard":
        return _prompt_text_wizard(
            title, default=default, description=description,
            key_reader=key_reader or read_key,
        )
    print(f"\n{title}")
    if default:
        print(f"Sugestão: {default}")
    if description:
        print(description)
    try:
        answer = (input_fn or input)("Diretório de instalação: ").strip()
    except EOFError as error:
        raise MenuCancelled(title) from error
    return answer or default


def _select_fallback(
    title: str,
    options: tuple[MenuOption, ...],
    *,
    breadcrumb: str,
    subtitle: str,
    default: int,
    searchable: bool,
    allow_back: bool,
    invalid_message: str,
    input_fn: Callable[[str], str],
) -> str | None:
    enabled = [option for option in options if option.enabled]
    if not enabled:
        raise ValueError("menu has no enabled options")
    default_key = enabled[min(default, len(enabled) - 1)].key
    visible = list(options)
    width = _chrome_width()
    while True:
        if breadcrumb:
            print()
            _emit_text(breadcrumb, width)
        print()
        _emit_text(title, width)
        if subtitle:
            _emit_text(subtitle, width)
        labels = [
            option.label + (" (padrão)" if option.key == default_key else "")
            for option in visible
        ]
        index_width = len(str(len(visible)))
        label_width = max(len(label) for label in labels)
        description_width = max(len(item.description) for item in visible)
        previous_group = ""
        for index, option in enumerate(visible, 1):
            if option.group and option.group != previous_group:
                print(f"  {option.group}")
                previous_group = option.group
            label = labels[index - 1]
            line = f"  {index:>{index_width}}) {label:<{label_width}}"
            if description_width:
                line += f" · {option.description:<{description_width}}"
            if option.detail:
                line += f"  < {option.detail}"
            if not option.enabled:
                line += "  < indisponível" + (
                    f": {option.disabled_reason}" if option.disabled_reason else ""
                )
            print(line)
        prompt = f"Escolha [1-{len(visible)}]"
        if searchable:
            prompt += " ou digite para buscar"
            if len(visible) != len(options):
                prompt += "; / para limpar a busca"
        if allow_back:
            prompt += "; b para voltar"
        prompt += "; q para sair"
        try:
            answer = input_fn(prompt + ": ").strip()
        except EOFError as error:
            raise MenuCancelled(title) from error
        if not answer:
            return default_key
        if allow_back and answer.casefold() in {"b", "voltar"}:
            return None
        if answer.casefold() in {"q", "sair"}:
            raise MenuExit(title)
        if searchable and answer.casefold() in {"/", "limpar"}:
            visible = list(options)
            default_key = enabled[min(default, len(enabled) - 1)].key
            continue
        if answer.isdigit() and 1 <= int(answer) <= len(visible):
            option = visible[int(answer) - 1]
            if option.enabled:
                return option.key
            print("[ATENÇÃO] Esta opção não está disponível neste contexto.")
            continue
        folded_answer = _fold(answer)
        exact = [
            option for option in enabled
            if folded_answer in {
                _fold(option.key), _fold(option.label),
                *(_fold(alias) for alias in option.aliases),
            }
        ]
        if len(exact) == 1:
            return exact[0].key
        if searchable:
            found = [
                option for option in visible
                if folded_answer in _fold(_option_search_text(option))
            ]
            if len(found) == 1 and found[0].enabled:
                return found[0].key
            if found:
                visible = found
                available = [option for option in visible if option.enabled]
                if available:
                    default_key = available[0].key
                continue
        print(f"[ATENÇÃO] {invalid_message}")


def select_one(
    title: str,
    options: Iterable[MenuOption],
    *,
    breadcrumb: str = "",
    subtitle: str = "",
    default: int = 0,
    searchable: bool = False,
    allow_back: bool = False,
    invalid_message: str = "Seleção inválida; tente novamente.",
    interactive: bool | None = None,
    key_reader: Callable[[], str] | None = None,
    input_fn: Callable[[str], str] | None = None,
    presentation: str = "menu",
) -> str | None:
    entries = tuple(options)
    if not entries or len({option.key for option in entries}) != len(entries):
        raise ValueError("menu options must be non-empty and have unique keys")
    navigation = supports_navigation() if interactive is None else interactive
    if presentation not in {"menu", "wizard"}:
        raise ValueError("presentation must be menu or wizard")
    if navigation and presentation == "wizard":
        return _select_wizard(
            title, entries, default=default, searchable=searchable,
            allow_back=allow_back, key_reader=key_reader or read_key,
        )
    if navigation:
        return _select_navigation(
            title, entries, breadcrumb=breadcrumb, subtitle=subtitle,
            default=default, searchable=searchable, allow_back=allow_back,
            key_reader=key_reader or read_key,
        )
    return _select_fallback(
        title, entries, breadcrumb=breadcrumb, subtitle=subtitle,
        default=default, searchable=searchable, allow_back=allow_back,
        invalid_message=invalid_message,
        input_fn=input_fn or input,
    )


def _render_multiple(
    *,
    title: str,
    options: tuple[MenuOption, ...],
    matches: list[int],
    selected: int,
    checked: set[str],
    breadcrumb: str,
    subtitle: str,
    query: str,
    searching: bool,
    searchable: bool,
    allow_back: bool,
    numeric_buffer: str,
) -> None:
    width = _chrome_width()
    row_budget = _row_budget()
    index_width = len(str(max(len(matches), 1)))

    def marker(position: int, option: MenuOption) -> str:
        caret = "▸" if position == selected else " "
        box = "[✓]" if option.key in checked else "[ ]"
        return f"{caret} {position + 1:>{index_width}}) {box}"

    rendered_rows = _build_rows(
        options=options, matches=matches, selected=selected, width=width,
        marker=marker,
    )
    start, end = _row_window(
        [len(lines) for lines in rendered_rows], selected, row_budget,
    )
    visible = matches[start:end]
    _begin_frame()
    _render_header(
        title=title, breadcrumb=breadcrumb, subtitle=subtitle, query=query,
        searching=searching, searchable=searchable, numeric_buffer=numeric_buffer,
        numeric_label="Marcar item", width=width,
    )
    _render_rows(
        options=options, matches=matches, selected=selected, visible=visible,
        start=start, end=end, rendered_rows=rendered_rows, checked=checked,
    )
    if searching:
        footer = ["Digite para buscar", "Enter aplicar busca", "Esc limpar busca"]
    elif numeric_buffer:
        footer = ["Digite o número completo", "Espaço/Enter marcar", "Esc limpar número"]
    else:
        footer = [
            "↑↓ navegar", "Espaço marcar", "→/Enter concluir",
            "A marcar tudo", "D desmarcar tudo",
        ]
        if searchable:
            footer.append("/ buscar")
        footer.append("← voltar" if allow_back else "← cancelar")
        footer.append("esc sair")
    print()
    if not _compact_chrome():
        print(_rule(width))
    for line in _footer_lines(footer, width):
        print(_paint(line, _MUTED), flush=True)


def select_many(
    title: str,
    options: Iterable[MenuOption],
    *,
    breadcrumb: str = "",
    subtitle: str = "",
    selected: Iterable[str] = (),
    searchable: bool = False,
    allow_back: bool = False,
    allow_empty: bool = False,
    interactive: bool | None = None,
    key_reader: Callable[[], str] | None = None,
    input_fn: Callable[[str], str] | None = None,
    presentation: str = "menu",
) -> tuple[str, ...] | None:
    entries = tuple(options)
    if not entries or len({option.key for option in entries}) != len(entries):
        raise ValueError("menu options must be non-empty and have unique keys")
    enabled = tuple(option for option in entries if option.enabled)
    if not enabled:
        raise ValueError("menu has no enabled options")
    enabled_keys = {option.key for option in enabled}
    checked = {key for key in selected if key in enabled_keys}
    navigation = supports_navigation() if interactive is None else interactive
    if presentation not in {"menu", "wizard"}:
        raise ValueError("presentation must be menu or wizard")
    if not navigation:
        reader = input_fn or input
        while True:
            labels = [option.label for option in entries]
            index_width = len(str(len(entries)))
            label_width = max(len(label) for label in labels)
            print()
            if breadcrumb:
                _emit_text(breadcrumb, _chrome_width())
            _emit_text(title, _chrome_width())
            if subtitle:
                _emit_text(subtitle, _chrome_width())
            description_width = max(len(item.description) for item in enabled)
            previous_group = ""
            for index, option in enumerate(entries, 1):
                if option.group and option.group != previous_group:
                    print(f"  {option.group}")
                    previous_group = option.group
                marker = "x" if option.key in checked else " "
                line = f"  {index:>{index_width}}) [{marker}] {option.label:<{label_width}}"
                if description_width:
                    line += f" · {option.description:<{description_width}}"
                if option.detail:
                    line += f"  < {option.detail}"
                if not option.enabled:
                    line += "  < indisponível" + (
                        f": {option.disabled_reason}" if option.disabled_reason else ""
                    )
                print(line)
            prompt = "Informe números ou identificadores separados por vírgula"
            if allow_back:
                prompt += "; b para voltar"
            prompt += "; q para sair"
            try:
                answer = reader(prompt + ": ").strip()
            except EOFError as error:
                raise MenuCancelled(title) from error
            if allow_back and answer.casefold() in {"b", "voltar"}:
                return None
            if answer.casefold() in {"q", "sair"}:
                raise MenuExit(title)
            if not answer:
                result = tuple(option.key for option in enabled if option.key in checked)
                if result or allow_empty:
                    return result
                raise MenuCancelled(title)
            chosen: set[str] = set()
            invalid = False
            for token in (item.strip() for item in answer.split(",")):
                if token.isdigit() and 1 <= int(token) <= len(entries):
                    option = entries[int(token) - 1]
                    if option.enabled:
                        chosen.add(option.key)
                    else:
                        invalid = True
                    continue
                token_fold = _fold(token)
                token_matches = [
                    option for option in enabled
                    if token_fold in {
                        _fold(option.key), _fold(option.label),
                        *(_fold(alias) for alias in option.aliases),
                    }
                ]
                if len(token_matches) != 1:
                    invalid = True
                    break
                chosen.add(token_matches[0].key)
            if invalid:
                print("[ATENÇÃO] Seleção inválida; tente novamente.")
                continue
            return tuple(option.key for option in enabled if option.key in chosen)

    query = ""
    searching = False
    numeric_buffer = ""
    matches = _matching(entries, query)
    position = _default_position(entries, matches, 0)
    reader = key_reader or read_key
    previous_lines = 0
    validation_message = ""
    if presentation == "wizard" and _isatty(sys.stdout):
        sys.stdout.write(_HIDE_CURSOR)
    try:
        while True:
            if presentation == "wizard":
                frame = _wizard_multiple_frame(
                    title, entries, matches, position, checked,
                    query=query, searching=searching, searchable=searchable,
                    allow_back=allow_back, numeric_buffer=numeric_buffer,
                    validation_message=validation_message,
                )
                if previous_lines:
                    sys.stdout.write(f"\033[999D\033[{previous_lines}A\033[J")
                sys.stdout.write(frame)
                sys.stdout.flush()
                previous_lines = frame.count("\n")
            else:
                _render_multiple(
                    title=title, options=entries, matches=matches, selected=position,
                    checked=checked, breadcrumb=breadcrumb, subtitle=subtitle,
                    query=query, searching=searching, searchable=searchable,
                    allow_back=allow_back, numeric_buffer=numeric_buffer,
                )
            key = reader()
            if key == "interrupt":
                raise KeyboardInterrupt
            if searching:
                if key == "enter":
                    searching = False
                elif key == "escape":
                    query = ""
                    searching = False
                elif key == "backspace":
                    query = query[:-1]
                elif len(key) == 1 and key.isprintable():
                    query += key
                matches = _matching(entries, query)
                position = _default_position(entries, matches, 0)
                validation_message = ""
                continue
            selectable = _selectable_positions(entries, matches)
            if numeric_buffer:
                if key.isdigit():
                    candidate = numeric_buffer + key
                    if int(candidate) <= len(matches):
                        numeric_buffer = candidate
                elif key == "backspace":
                    numeric_buffer = numeric_buffer[:-1]
                elif key in (" ", "enter"):
                    shortcut = int(numeric_buffer) - 1
                    if 0 <= shortcut < len(matches):
                        option = entries[matches[shortcut]]
                        if option.enabled:
                            checked.symmetric_difference_update({option.key})
                    validation_message = ""
                    numeric_buffer = ""
                elif key == "escape":
                    numeric_buffer = ""
                else:
                    numeric_buffer = ""
                continue
            if key in {"up", "down", "k", "j", "home", "end", "pageup", "pagedown"}:
                position = _move_selection(
                    position, selectable, key, page=max(1, _row_budget() // 2),
                )
            elif key == " " and selectable:
                option_key = entries[matches[position]].key
                checked.symmetric_difference_update({option_key})
                validation_message = ""
            elif key.casefold() == "a":
                checked = set(enabled_keys)
                validation_message = ""
            elif key.casefold() == "d":
                checked.clear()
                validation_message = ""
            elif key in ("enter", "right"):
                result = tuple(option.key for option in enabled if option.key in checked)
                if result or allow_empty:
                    if presentation == "wizard":
                        sys.stdout.write(f"\033[999D\033[{previous_lines}A\033[J")
                        sys.stdout.write(_wizard_multiple_collapse(title, len(result)))
                        sys.stdout.flush()
                    return result
                validation_message = "Selecione ao menos um componente."
            elif key == "left":
                if allow_back:
                    return None
                raise MenuCancelled(title)
            elif key in ("escape", "q"):
                raise MenuExit(title)
            elif key == "/" and searchable:
                searching = True
            elif key.isdigit() and key != "0":
                if len(matches) > 9:
                    numeric_buffer = key
                else:
                    shortcut = int(key) - 1
                    if shortcut < len(matches):
                        option = entries[matches[shortcut]]
                        if option.enabled:
                            checked.symmetric_difference_update({option.key})
                            validation_message = ""
    finally:
        _restore_cursor()


def confirm(
    title: str,
    *,
    breadcrumb: str = "",
    subtitle: str = "",
    description: str = "",
    default: bool = False,
    invalid_message: str = "Resposta inválida. Digite sim ou não.",
    interactive: bool | None = None,
    input_fn: Callable[[str], str] | None = None,
    allow_back: bool = False,
    presentation: str = "menu",
) -> bool | None:
    result = select_one(
        title,
        (
            MenuOption(
                "yes", "Sim", description or "confirmar esta escolha",
                aliases=("s", "sim", "y", "yes"),
            ),
            MenuOption(
                "no", "Não", "não executar esta ação", aliases=("n", "nao", "não", "no"),
            ),
        ),
        breadcrumb=breadcrumb,
        subtitle=subtitle,
        default=0 if default else 1,
        invalid_message=invalid_message,
        interactive=interactive,
        input_fn=input_fn or input,
        allow_back=allow_back,
        presentation=presentation,
    )
    if result is None:
        return None
    return result == "yes"
