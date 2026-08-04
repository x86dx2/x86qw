#!/usr/bin/env python3
"""Portable, dependency-free navigation primitives for the x86QW CLI."""

from __future__ import annotations

import os
import select
import shutil
import sys
import textwrap
from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(frozen=True)
class MenuOption:
    key: str
    label: str
    description: str = ""
    detail: str = ""
    enabled: bool = True
    aliases: tuple[str, ...] = ()
    disabled_reason: str = ""


class MenuCancelled(Exception):
    """Raised when the user leaves an interactive selection."""


class MenuExit(Exception):
    """Raised when the user explicitly asks to leave the menu navigator."""


_NO_COLOR = False
_ESCAPE_INITIAL_TIMEOUT = 0.12
_ESCAPE_CONTINUATION_TIMEOUT = 0.04
_ESCAPE_SEQUENCE_LIMIT = 16


def configure(*, no_color: bool = False) -> None:
    global _NO_COLOR
    _NO_COLOR = no_color or "NO_COLOR" in os.environ


def _paint(value: str, code: str) -> str:
    if _NO_COLOR or not sys.stdout.isatty():
        return value
    return f"\033[{code}m{value}\033[0m"


def supports_navigation() -> bool:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
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
        }.get(msvcrt.getwch(), "unknown")
    return {
        "\r": "enter", "\x1b": "escape", "\x08": "backspace",
        "\x03": "interrupt",
    }.get(key, key)


def _decode_posix_escape(sequence: bytes) -> str:
    """Decode a complete CSI or SS3 sequence without treating it as bare Esc."""
    if sequence[:1] not in (b"[", b"O"):
        return "unknown"
    return {
        b"A": "up", b"B": "down", b"C": "right", b"D": "left",
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
        try:
            return first.decode("utf-8")
        except UnicodeDecodeError:
            return "unknown"
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, previous)


def read_key() -> str:
    return _read_windows_key() if os.name == "nt" else _read_posix_key()


def _matching(options: tuple[MenuOption, ...], query: str) -> list[int]:
    if not query:
        return list(range(len(options)))
    folded = query.casefold()
    return [
        index for index, option in enumerate(options)
        if folded in " ".join((
            option.key, option.label, option.description, option.detail,
            option.disabled_reason, *option.aliases,
        )).casefold()
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
) -> list[str]:
    """Render a responsive row while keeping ANSI outside its width budget."""
    label = f"{prefix} {option.label:<{label_width}}"
    description = ""
    if description_width:
        description = f" | {option.description:<{description_width}}"
    explanation = option.detail if active and option.detail else ""
    if not option.enabled:
        explanation = "indisponível" + (
            f": {option.disabled_reason}" if option.disabled_reason else ""
        )
    primary = label + description
    color = "1;36" if active else "2" if not option.enabled else ""
    if len(primary) <= width:
        suffix = f" < {explanation}" if explanation else ""
        remaining = max(0, width - len(primary))
        rendered = _paint(primary, color) if color else primary
        if not suffix:
            return [rendered]
        if len(suffix) <= remaining:
            return [rendered + _paint(suffix, "2")]
        continuation = " " * min(len(prefix) + 1, max(0, width - 2))
        return [
            rendered,
            *(
                _paint(continuation + part, "2")
                for part in _wrapped("< " + explanation, width - len(continuation))
            ),
        ]

    first = _clip(label.rstrip(), width)
    lines = [_paint(first, color) if color else first]
    indent = min(len(prefix) + 1, max(0, width - 2))
    continuation = " " * indent
    if option.description:
        for part in _wrapped("| " + option.description, width - indent):
            line = continuation + part
            lines.append(_paint(line, color) if color else line)
    if explanation:
        for part in _wrapped("< " + explanation, width - indent):
            lines.append(_paint(continuation + part, "2"))
    return lines


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
    width = max(20, min(shutil.get_terminal_size((88, 24)).columns, 110))
    row_budget = max(3, shutil.get_terminal_size((88, 24)).lines - 10)
    index_width = len(str(len(matches)))
    label_width = _label_width(options)
    description_width = _description_width(options)
    rendered_rows = [
        _option_lines(
            prefix=f"{'›' if position == selected else ' '} {position + 1:>{index_width}})",
            option=options[option_index],
            label_width=label_width,
            description_width=description_width,
            width=width,
            active=position == selected,
        )
        for position, option_index in enumerate(matches)
    ]
    start, end = _row_window(
        [len(lines) for lines in rendered_rows], selected, row_budget,
    )
    visible = matches[start:end]
    sys.stdout.write("\033[2J\033[H")
    if breadcrumb:
        print(_paint(breadcrumb, "2;36"))
        print()
    print(_paint(title, "1;36"))
    if subtitle:
        print(subtitle)
    if searchable:
        marker = "_" if searching else ""
        print(f"\nBuscar: {_paint(query + marker, '1;33') if query or searching else 'pressione /'}")
    if numeric_buffer:
        print(f"\nIr para o item: {_paint(numeric_buffer + '_', '1;33')}")
    print()
    if not visible:
        print("  Nenhum resultado.")
    for position, option_index in enumerate(visible, start):
        option = options[option_index]
        active = position == selected
        lines = rendered_rows[position]
        for line in lines:
            print(line)
    if len(matches) > len(visible):
        print(_paint(
            f"\nExibindo {start + 1}–{end} de {len(matches)}.", "2",
        ))
    if searching:
        footer = ["Digite para buscar", "Enter aplicar busca", "Esc limpar busca"]
    elif numeric_buffer:
        footer = ["Digite o número completo", "→/Enter selecionar", "Esc limpar número"]
    else:
        footer = ["↑↓ navegar", "→/Enter selecionar"]
        if searchable:
            footer.append("/ buscar")
        footer.append("← voltar" if allow_back else "← cancelar")
        footer.append("Esc Sair.")
    print()
    for line in _footer_lines(footer, width):
        print(_paint(line, "2"), flush=True)


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
        if key in ("up", "k") and selectable:
            current = selectable.index(selected) if selected in selectable else 0
            selected = selectable[(current - 1) % len(selectable)]
        elif key in ("down", "j") and selectable:
            current = selectable.index(selected) if selected in selectable else 0
            selected = selectable[(current + 1) % len(selectable)]
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
    while True:
        if breadcrumb:
            print(f"\n{breadcrumb}")
        print(f"\n{title}")
        if subtitle:
            print(subtitle)
        labels = [
            option.label + (" (padrão)" if option.key == default_key else "")
            for option in visible
        ]
        index_width = len(str(len(visible)))
        label_width = max(len(label) for label in labels)
        description_width = max(len(item.description) for item in visible)
        for index, option in enumerate(visible, 1):
            label = labels[index - 1]
            line = f"  {index:>{index_width}}) {label:<{label_width}}"
            if description_width:
                line += f" | {option.description:<{description_width}}"
            if option.detail:
                line += f" < {option.detail}"
            if not option.enabled:
                line += " < indisponível" + (
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
        exact = [
            option for option in enabled
            if answer.casefold() in {
                option.key.casefold(), option.label.casefold(),
                *(alias.casefold() for alias in option.aliases),
            }
        ]
        if len(exact) == 1:
            return exact[0].key
        if searchable:
            found = [
                option for option in visible
                if answer.casefold() in " ".join((
                    option.key, option.label, option.description, option.detail,
                    option.disabled_reason, *option.aliases,
                )).casefold()
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
) -> str | None:
    entries = tuple(options)
    if not entries or len({option.key for option in entries}) != len(entries):
        raise ValueError("menu options must be non-empty and have unique keys")
    navigation = supports_navigation() if interactive is None else interactive
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
    width = max(20, min(shutil.get_terminal_size((88, 24)).columns, 110))
    row_budget = max(3, shutil.get_terminal_size((88, 24)).lines - 10)
    index_width = len(str(len(matches)))
    label_width = _label_width(options)
    description_width = _description_width(options)
    rendered_rows = [
        _option_lines(
            prefix=(
                f"{'›' if position == selected else ' '} "
                f"{position + 1:>{index_width}}) "
                f"{'[✓]' if options[option_index].key in checked else '[ ]'}"
            ),
            option=options[option_index],
            label_width=label_width,
            description_width=description_width,
            width=width,
            active=position == selected,
        )
        for position, option_index in enumerate(matches)
    ]
    start, end = _row_window(
        [len(lines) for lines in rendered_rows], selected, row_budget,
    )
    visible = matches[start:end]
    sys.stdout.write("\033[2J\033[H")
    if breadcrumb:
        print(_paint(breadcrumb, "2;36"))
        print()
    print(_paint(title, "1;36"))
    if subtitle:
        print(subtitle)
    if searchable:
        marker = "_" if searching else ""
        print(f"\nBuscar: {_paint(query + marker, '1;33') if query or searching else 'pressione /'}")
    if numeric_buffer:
        print(f"\nMarcar item: {_paint(numeric_buffer + '_', '1;33')}")
    print()
    if not visible:
        print("  Nenhum resultado.")
    for position, option_index in enumerate(visible, start):
        option = options[option_index]
        active = position == selected
        lines = rendered_rows[position]
        for line in lines:
            if active:
                print(line)
            elif option.key in checked:
                print(line.replace("[✓]", _paint("[✓]", "1;32"), 1))
            else:
                print(line)
    if len(matches) > len(visible):
        print(_paint(
            f"\nExibindo {start + 1}–{end} de {len(matches)}.", "2",
        ))
    if searching:
        footer = ["Digite para buscar", "Enter aplicar busca", "Esc limpar busca"]
    elif numeric_buffer:
        footer = ["Digite o número completo", "Espaço/Enter marcar", "Esc limpar número"]
    else:
        footer = ["↑↓ navegar", "Espaço marcar", "→/Enter concluir"]
        if searchable:
            footer.append("/ buscar")
        footer.append("← voltar" if allow_back else "← cancelar")
        footer.append("Esc Sair.")
    print()
    for line in _footer_lines(footer, width):
        print(_paint(line, "2"), flush=True)


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
    if not navigation:
        reader = input_fn or input
        while True:
            labels = [option.label for option in entries]
            index_width = len(str(len(entries)))
            label_width = max(len(label) for label in labels)
            print(f"\n{breadcrumb}" if breadcrumb else "", end="")
            print(f"\n{title}")
            if subtitle:
                print(subtitle)
            description_width = max(len(item.description) for item in enabled)
            for index, option in enumerate(entries, 1):
                marker = "x" if option.key in checked else " "
                line = f"  {index:>{index_width}}) [{marker}] {option.label:<{label_width}}"
                if description_width:
                    line += f" | {option.description:<{description_width}}"
                if option.detail:
                    line += f" < {option.detail}"
                if not option.enabled:
                    line += " < indisponível" + (
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
                token_matches = [
                    option for option in enabled
                    if token.casefold() in {
                        option.key.casefold(), option.label.casefold(),
                        *(alias.casefold() for alias in option.aliases),
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
    while True:
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
                numeric_buffer = ""
            elif key == "escape":
                numeric_buffer = ""
            else:
                numeric_buffer = ""
            continue
        if key in ("up", "k") and selectable:
            current = selectable.index(position) if position in selectable else 0
            position = selectable[(current - 1) % len(selectable)]
        elif key in ("down", "j") and selectable:
            current = selectable.index(position) if position in selectable else 0
            position = selectable[(current + 1) % len(selectable)]
        elif key == " " and selectable:
            option_key = entries[matches[position]].key
            checked.symmetric_difference_update({option_key})
        elif key in ("enter", "right"):
            result = tuple(option.key for option in enabled if option.key in checked)
            if result or allow_empty:
                return result
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
    )
    if result is None:
        return None
    return result == "yes"
