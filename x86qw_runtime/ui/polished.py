"""Responsive terminal presentation layered over the canonical x86QW menu.

The canonical engine remains responsible for input decoding, search semantics and
compact fallbacks.  This adapter adds a modern command center for game menus and
a scrollback-preserving guided flow for installation, without new dependencies.
"""

from __future__ import annotations

import importlib
import os
import re
import sys
import textwrap
import unicodedata
from collections.abc import Callable, Iterable
from typing import Any

from x86qw_runtime.ui.console import (
    ACCENT, ERROR, INFO, MUTED, SUCCESS, WARNING, terminal_size,
)


_legacy = importlib.import_module(f"{__package__}.menu")
MenuOption = _legacy.MenuOption
MenuCancelled = _legacy.MenuCancelled
MenuExit = _legacy.MenuExit

_NO_COLOR = False
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CLEAR = "\033[2J\033[H"
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"
_MIN_WIDTH = 54
_SPLIT_WIDTH = 88
_MAX_WIDTH = 118


def configure(*, no_color: bool = False) -> None:
    global _NO_COLOR
    _NO_COLOR = no_color or "NO_COLOR" in os.environ
    _legacy.configure(no_color=no_color)


def supports_navigation() -> bool:
    return bool(_legacy.supports_navigation())


def read_key() -> str:
    return str(_legacy.read_key())


def _isatty(stream: object) -> bool:
    checker = getattr(stream, "isatty", None)
    return bool(checker is not None and checker())


def _paint(value: str, code: str, *, bold: bool = False, dim: bool = False) -> str:
    if _NO_COLOR or not _isatty(sys.stdout):
        return value
    attributes = [code]
    if bold:
        attributes.append("1")
    if dim:
        attributes.append("2")
    return f"\033[{';'.join(attributes)}m{value}\033[0m"


def _cells(value: str) -> int:
    width = 0
    for character in _ANSI.sub("", value):
        if unicodedata.combining(character):
            continue
        width += 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
    return width


def _clip(value: str, width: int) -> str:
    plain = _ANSI.sub("", value)
    if _cells(plain) <= width:
        return plain
    result: list[str] = []
    used = 0
    for character in plain:
        size = 0 if unicodedata.combining(character) else (
            2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
        )
        if used + size > max(0, width - 1):
            break
        result.append(character)
        used += size
    return "".join(result).rstrip() + "…"


def _pad(value: str, width: int) -> str:
    return value + " " * max(0, width - _cells(value))


def _wrap(value: str, width: int) -> list[str]:
    lines: list[str] = []
    for source in value.splitlines() or [""]:
        lines.extend(textwrap.wrap(
            source, width=max(8, width), break_long_words=False,
            break_on_hyphens=False,
        ) or [""])
    return lines


def _width() -> int:
    return max(20, min(terminal_size((96, 28)).columns, _MAX_WIDTH))


def _enabled(interactive: bool) -> bool:
    return (
        interactive and _isatty(sys.stdout) and _width() >= _MIN_WIDTH
        and os.environ.get("X86QW_CLASSIC_UI") != "1"
    )


def _key(value: str) -> str:
    return _paint(f"[{value}]", INFO, bold=True)


def _rule(width: int) -> str:
    return _paint("─" * width, MUTED)


def _brand(surface: str, width: int) -> str:
    left = f"X86QW  /  {surface}"
    right = "● PRONTO"
    gap = max(2, width - _cells(left) - _cells(right))
    return (
        _paint("X86QW", ACCENT, bold=True)
        + _paint(f"  /  {surface}", INFO, bold=True)
        + " " * gap
        + _paint("●", SUCCESS, bold=True)
        + _paint(" PRONTO", MUTED)
    )


def _panel(title: str, body: list[str], width: int, height: int) -> list[str]:
    inner = width - 2
    label = f" {title.upper()} "
    top = "╭─" + label + "─" * max(0, inner - len(label) - 1) + "╮"
    lines = [_paint(top, MUTED)]
    for index in range(height):
        value = body[index] if index < len(body) else ""
        plain = _clip(value, inner - 2)
        # Keep emphasis when clipping was unnecessary.
        rendered = (
            value
            if value != _ANSI.sub("", value) and plain == _ANSI.sub("", value)
            else plain
        )
        lines.append(
            _paint("│", MUTED) + " " + _pad(rendered, inner - 2)
            + " " + _paint("│", MUTED)
        )
    lines.append(_paint("╰" + "─" * inner + "╯", MUTED))
    return lines


def _window(length: int, selected: int, capacity: int) -> tuple[int, int]:
    if length <= capacity:
        return 0, length
    start = max(0, selected - capacity // 2)
    end = min(length, start + capacity)
    return max(0, end - capacity), end


def _option_rows(
    options: tuple[MenuOption, ...], matches: list[int], selected: int,
    *, width: int, capacity: int,
) -> list[str]:
    start, end = _window(len(matches), selected, capacity)
    rows: list[str] = []
    previous_group = ""
    for position in range(start, end):
        option = options[matches[position]]
        if option.group and option.group != previous_group:
            rows.append(_paint(_clip(option.group.upper(), width), MUTED, bold=True))
            previous_group = option.group
        marker = "▌" if position == selected else " "
        label = option.label + (" · indisponível" if not option.enabled else "")
        value = _clip(f"{marker} {position + 1:>2}  {label}", width)
        if not option.enabled:
            rows.append(_paint(value, MUTED, dim=True))
        elif position == selected:
            rows.append(_paint(value, ACCENT, bold=True))
        else:
            rows.append(_paint(value, INFO))
    if len(matches) > end - start:
        rows.append(_paint(f"{start + 1}–{end} de {len(matches)}", MUTED))
    return rows


def _details(option: MenuOption | None, width: int) -> list[str]:
    if option is None:
        return [_paint("Nenhum resultado.", MUTED)]
    rows = [_paint(_clip(option.label, width), ACCENT, bold=True)]
    if option.description:
        rows.extend(_paint(line, INFO) for line in _wrap(option.description, width))
    rows.append("")
    detail = option.detail or option.disabled_reason or "Pressione Enter para continuar."
    rows.extend(_paint(line, MUTED) for line in _wrap(detail, width))
    if option.aliases:
        rows.append("")
        rows.extend(_paint(line, MUTED) for line in _wrap(
            "Atalhos: " + " · ".join(option.aliases[:4]), width,
        ))
    return rows


def _footer(*, searchable: bool, allow_back: bool, searching: bool) -> str:
    if searching:
        return f"{_key('digite')} buscar  {_key('enter')} aplicar  {_key('esc')} limpar"
    items = [f"{_key('↑↓')} navegar", f"{_key('enter')} selecionar"]
    if searchable:
        items.append(f"{_key('/')} buscar")
    if allow_back:
        items.append(f"{_key('←')} voltar")
    items.append(f"{_key('esc')} sair")
    return "  ".join(items)


def _render_menu(
    title: str, options: tuple[MenuOption, ...], matches: list[int], selected: int,
    *, breadcrumb: str, subtitle: str, query: str, searching: bool,
    searchable: bool, allow_back: bool, numeric_buffer: str,
) -> None:
    width = _width()
    terminal_height = max(18, terminal_size((96, 28)).lines)
    sys.stdout.write(_CLEAR)
    if _isatty(sys.stdout):
        sys.stdout.write(_HIDE_CURSOR)
    print(_brand("CENTRAL X86QW", width))
    print(_rule(width))
    if breadcrumb:
        for line in _wrap(breadcrumb, width):
            print(_paint(line, MUTED))
    for line in _wrap(title, width):
        print(_paint(line, ACCENT, bold=True))
    if subtitle:
        for line in _wrap(subtitle, width):
            print(_paint(line, INFO))
    if searching or query:
        cursor = "█" if searching else ""
        print(_paint(_clip(f"⌕  {query}{cursor}", width), WARNING, bold=True))
    if numeric_buffer:
        print(_paint(f"IR PARA O ITEM: {numeric_buffer}█", WARNING, bold=True))
    print()

    capacity = max(3, min(11, terminal_height - 13))
    left_width = max(24, int(width * .47)) if width >= _SPLIT_WIDTH else width
    right_width = width - left_width - 2 if width >= _SPLIT_WIDTH else width
    option_rows = _option_rows(
        options, matches, selected, width=left_width - 4, capacity=capacity,
    )
    active = options[matches[selected]] if matches else None
    detail_rows = _details(active, right_width - 6)
    if width >= _SPLIT_WIDTH:
        height = max(8, min(max(len(option_rows), len(detail_rows)), capacity + 2))
        left = _panel("opções", option_rows, left_width, height)
        right = _panel("detalhes", detail_rows, right_width, height)
        for left_line, right_line in zip(left, right):
            print(f"{left_line}  {right_line}")
    else:
        height = max(7, min(len(option_rows), capacity + 1))
        for line in _panel("opções", option_rows, width, height):
            print(line)
        if active is not None:
            summary = " · ".join(
                item for item in (active.label, active.description, active.detail) if item
            )
            print(_paint(_clip(summary, width), MUTED))
    print()
    print(_rule(width))
    print(_footer(
        searchable=searchable, allow_back=allow_back, searching=searching,
    ), flush=True)


def _wizard_frame(
    title: str, options: tuple[MenuOption, ...], matches: list[int], selected: int,
    *, subtitle: str, query: str, searching: bool, searchable: bool,
    allow_back: bool, numeric_buffer: str = "", checked: set[str] | None = None,
    validation: str = "",
) -> str:
    width = _width()
    multiple = checked is not None
    lines = [_brand("INSTALAÇÃO GUIADA", width), _rule(width)]
    lines.extend(_paint(line, ACCENT, bold=True) for line in _wrap(f"◆  {title}", width))
    if subtitle:
        lines.extend(_paint(line, INFO) for line in _wrap(subtitle, width - 3))
    if searching or query:
        cursor = "█" if searching else ""
        lines.append(_paint(_clip(f"│  ⌕  {query}{cursor}", width), WARNING, bold=True))
    if numeric_buffer:
        label = "Marcar item" if multiple else "Ir para o item"
        lines.append(_paint(f"│  {label}: {numeric_buffer}█", WARNING, bold=True))
    lines.append(_paint("│", MUTED))

    capacity = max(3, min(10, terminal_size((96, 28)).lines - 11))
    start, end = _window(len(matches), selected, capacity)
    for position in range(start, end):
        option = options[matches[position]]
        if multiple:
            marker = "■" if option.key in checked else "□"
        else:
            marker = "●" if position == selected else "○"
        value = _clip(f"{marker}  {position + 1:>2}  {option.label}", width - 6)
        if not option.enabled:
            value = _paint(value, MUTED, dim=True)
        elif position == selected:
            value = _paint(value, ACCENT, bold=True)
        elif multiple and option.key in checked:
            value = _paint(value, SUCCESS)
        else:
            value = _paint(value, MUTED)
        lines.append(_paint("│", MUTED) + "  " + value)
    if len(matches) > end - start:
        lines.append(_paint("│", MUTED) + "  " + _paint(
            f"{start + 1}–{end} de {len(matches)}", MUTED,
        ))
    active = options[matches[selected]] if matches else None
    if active is not None and (active.description or active.detail):
        lines.append(_paint("│", MUTED))
        for part in _wrap(active.description or active.detail, width - 6)[:2]:
            lines.append(_paint("│", MUTED) + "  " + _paint(part, INFO))
    if validation:
        lines.append(_paint("│", MUTED) + "  " + _paint(validation, ERROR, bold=True))
    if searching:
        footer = f"{_key('digite')} buscar  {_key('enter')} aplicar  {_key('esc')} limpar"
    elif multiple:
        footer = (
            f"{_key('↑↓')} navegar  {_key('espaço')} marcar  {_key('enter')} concluir"
            f"  {_key('a')} tudo  {_key('d')} limpar"
        )
        if searchable:
            footer += f"  {_key('/')} buscar"
    else:
        footer = _footer(
            searchable=searchable, allow_back=allow_back, searching=False,
        )
    lines.extend((_paint("│", MUTED), _paint("╰─ ", MUTED) + footer))
    return "\n".join(lines) + "\n"


def _erase(lines: int) -> None:
    if lines:
        sys.stdout.write(f"\033[999D\033[{lines}A\033[J")


def _restore() -> None:
    if _isatty(sys.stdout):
        sys.stdout.write(_SHOW_CURSOR)
        sys.stdout.flush()


def _collapse(title: str, value: str) -> str:
    return (
        f"{_paint('✓', SUCCESS, bold=True)} {_paint(title, INFO)}\n"
        f"  {_paint('╰─', MUTED)} {_paint(value, MUTED)}\n"
    )


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
    if not _enabled(bool(navigation)):
        return _legacy.select_one(
            title, entries, breadcrumb=breadcrumb, subtitle=subtitle, default=default,
            searchable=searchable, allow_back=allow_back,
            invalid_message=invalid_message, interactive=interactive,
            key_reader=key_reader, input_fn=input_fn, presentation=presentation,
        )

    query = ""
    searching = False
    numeric = ""
    matches = _legacy._matching(entries, query)
    if not any(option.enabled for option in entries):
        raise ValueError("menu has no enabled options")
    selected = _legacy._default_position(entries, matches, default)
    reader = key_reader or read_key
    previous = 0
    if _isatty(sys.stdout):
        sys.stdout.write(_HIDE_CURSOR)
    try:
        while True:
            if presentation == "wizard":
                frame = _wizard_frame(
                    title, entries, matches, selected, subtitle=subtitle, query=query,
                    searching=searching, searchable=searchable, allow_back=allow_back,
                    numeric_buffer=numeric,
                )
                _erase(previous)
                sys.stdout.write(frame)
                sys.stdout.flush()
                previous = frame.count("\n")
            else:
                _render_menu(
                    title, entries, matches, selected, breadcrumb=breadcrumb,
                    subtitle=subtitle, query=query, searching=searching,
                    searchable=searchable, allow_back=allow_back,
                    numeric_buffer=numeric,
                )
            key = reader()
            if key == "interrupt":
                raise KeyboardInterrupt
            if searching:
                if key == "enter":
                    searching = False
                elif key == "escape":
                    query, searching = "", False
                elif key == "backspace":
                    query = query[:-1]
                elif len(key) == 1 and key.isprintable():
                    query += key
                matches = _legacy._matching(entries, query)
                selected = _legacy._default_position(entries, matches, 0)
                continue
            selectable = _legacy._selectable_positions(entries, matches)
            if numeric:
                if key.isdigit() and int(numeric + key) <= len(matches):
                    numeric += key
                elif key == "backspace":
                    numeric = numeric[:-1]
                elif key in ("enter", "right"):
                    position = int(numeric) - 1
                    if 0 <= position < len(matches) and entries[matches[position]].enabled:
                        option = entries[matches[position]]
                        if presentation == "wizard":
                            _erase(previous)
                            sys.stdout.write(_collapse(
                                title,
                                option.label + (
                                    f" · {option.description}"
                                    if option.description else ""
                                ),
                            ))
                        return option.key
                    numeric = ""
                elif key == "escape":
                    numeric = ""
                else:
                    numeric = ""
                continue
            if key in {"up", "down", "k", "j", "home", "end", "pageup", "pagedown"}:
                selected = _legacy._move_selection(
                    selected, selectable, key,
                    page=max(1, terminal_size((96, 28)).lines // 3),
                )
            elif key in ("enter", "right") and selectable:
                option = entries[matches[selected]]
                if presentation == "wizard":
                    _erase(previous)
                    sys.stdout.write(_collapse(
                        title,
                        option.label + (
                            f" · {option.description}" if option.description else ""
                        ),
                    ))
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
                    numeric = key
                else:
                    position = int(key) - 1
                    if position < len(matches) and entries[matches[position]].enabled:
                        option = entries[matches[position]]
                        if presentation == "wizard":
                            _erase(previous)
                            sys.stdout.write(_collapse(title, option.label))
                        return option.key
    finally:
        _restore()


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
    navigation = supports_navigation() if interactive is None else interactive
    if presentation != "wizard" or not _enabled(bool(navigation)):
        return _legacy.select_many(
            title, entries, breadcrumb=breadcrumb, subtitle=subtitle,
            selected=selected, searchable=searchable, allow_back=allow_back,
            allow_empty=allow_empty, interactive=interactive, key_reader=key_reader,
            input_fn=input_fn, presentation=presentation,
        )
    if not entries or len({option.key for option in entries}) != len(entries):
        raise ValueError("menu options must be non-empty and have unique keys")
    enabled = tuple(option for option in entries if option.enabled)
    if not enabled:
        raise ValueError("menu has no enabled options")
    enabled_keys = {option.key for option in enabled}
    checked = {key for key in selected if key in enabled_keys}
    query = ""
    searching = False
    numeric = ""
    validation = ""
    matches = _legacy._matching(entries, query)
    position = _legacy._default_position(entries, matches, 0)
    reader = key_reader or read_key
    previous = 0
    sys.stdout.write(_HIDE_CURSOR)
    try:
        while True:
            frame = _wizard_frame(
                title, entries, matches, position, subtitle=subtitle, query=query,
                searching=searching, searchable=searchable, allow_back=allow_back,
                numeric_buffer=numeric, checked=checked, validation=validation,
            )
            _erase(previous)
            sys.stdout.write(frame)
            sys.stdout.flush()
            previous = frame.count("\n")
            key = reader()
            if key == "interrupt":
                raise KeyboardInterrupt
            if searching:
                if key == "enter":
                    searching = False
                elif key == "escape":
                    query, searching = "", False
                elif key == "backspace":
                    query = query[:-1]
                elif len(key) == 1 and key.isprintable():
                    query += key
                matches = _legacy._matching(entries, query)
                position = _legacy._default_position(entries, matches, 0)
                validation = ""
                continue
            selectable = _legacy._selectable_positions(entries, matches)
            if numeric:
                if key.isdigit() and int(numeric + key) <= len(matches):
                    numeric += key
                elif key == "backspace":
                    numeric = numeric[:-1]
                elif key in (" ", "enter"):
                    shortcut = int(numeric) - 1
                    if 0 <= shortcut < len(matches):
                        option = entries[matches[shortcut]]
                        if option.enabled:
                            checked.symmetric_difference_update({option.key})
                    numeric, validation = "", ""
                elif key == "escape":
                    numeric = ""
                else:
                    numeric = ""
                continue
            if key in {"up", "down", "k", "j", "home", "end", "pageup", "pagedown"}:
                position = _legacy._move_selection(
                    position, selectable, key,
                    page=max(1, terminal_size((96, 28)).lines // 3),
                )
            elif key == " " and selectable:
                checked.symmetric_difference_update({entries[matches[position]].key})
                validation = ""
            elif key.casefold() == "a":
                checked, validation = set(enabled_keys), ""
            elif key.casefold() == "d":
                checked.clear()
                validation = ""
            elif key in ("enter", "right"):
                result = tuple(option.key for option in enabled if option.key in checked)
                if result or allow_empty:
                    _erase(previous)
                    noun = "componente" if len(result) == 1 else "componentes"
                    sys.stdout.write(_collapse(title, f"{len(result)} {noun} selecionados"))
                    return result
                validation = "Selecione ao menos um componente."
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
                    numeric = key
                else:
                    shortcut = int(key) - 1
                    if shortcut < len(matches):
                        option = entries[matches[shortcut]]
                        if option.enabled:
                            checked.symmetric_difference_update({option.key})
                            validation = ""
    finally:
        _restore()


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
    if presentation != "wizard" or not _enabled(bool(navigation)):
        return _legacy.prompt_text(
            title, default=default, description=description, interactive=interactive,
            key_reader=key_reader, input_fn=input_fn, presentation=presentation,
        )
    value = default
    reader = key_reader or read_key
    previous = 0
    sys.stdout.write(_HIDE_CURSOR)
    try:
        while True:
            width = _width()
            lines = [_brand("INSTALAÇÃO GUIADA", width), _rule(width)]
            lines.extend(_paint(line, ACCENT, bold=True) for line in _wrap(f"◆  {title}", width))
            lines.extend((
                _paint("│", MUTED),
                _paint("│", MUTED) + "  " + _paint(_clip(value + "█", width - 5), INFO, bold=True),
            ))
            if description:
                lines.extend(
                    _paint("│", MUTED) + "  " + _paint(part, MUTED)
                    for part in _wrap(description, width - 5)
                )
            lines.append(_paint("╰─ ", MUTED) + f"{_key('enter')} confirmar  {_key('esc')} sair")
            frame = "\n".join(lines) + "\n"
            _erase(previous)
            sys.stdout.write(frame)
            sys.stdout.flush()
            previous = frame.count("\n")
            key = reader()
            if key == "interrupt":
                raise KeyboardInterrupt
            if key == "enter":
                answer = value or default
                _erase(previous)
                sys.stdout.write(_collapse(title, answer))
                return answer
            if key in ("escape", "q"):
                raise MenuExit(title)
            if key == "backspace":
                value = value[:-1]
            elif len(key) == 1 and key.isprintable():
                value += key
    finally:
        _restore()


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
            MenuOption("no", "Não", "não executar esta ação", aliases=("n", "nao", "não", "no")),
        ),
        breadcrumb=breadcrumb, subtitle=subtitle, default=0 if default else 1,
        invalid_message=invalid_message, interactive=interactive,
        input_fn=input_fn, allow_back=allow_back, presentation=presentation,
    )
    return None if result is None else result == "yes"


def __getattr__(name: str) -> Any:
    return getattr(_legacy, name)


__all__ = (
    "MenuCancelled", "MenuExit", "MenuOption", "configure", "confirm",
    "prompt_text", "read_key", "select_many", "select_one", "supports_navigation",
)
