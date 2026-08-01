#!/usr/bin/env python3
"""Portable, dependency-free navigation primitives for the x86QW CLI."""

from __future__ import annotations

import os
import select
import shutil
import sys
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


class MenuCancelled(Exception):
    """Raised when the user leaves an interactive selection."""


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
        return [index for index, option in enumerate(options) if option.enabled]
    folded = query.casefold()
    return [
        index for index, option in enumerate(options)
        if option.enabled and folded in " ".join((
            option.key, option.label, option.description, option.detail, *option.aliases,
        )).casefold()
    ]


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
) -> None:
    width = max(48, min(shutil.get_terminal_size((88, 24)).columns, 110))
    height = max(5, shutil.get_terminal_size((88, 24)).lines - 10)
    start = max(0, min(selected - height // 2, max(0, len(matches) - height)))
    visible = matches[start:start + height]
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
    print()
    if not visible:
        print("  Nenhum resultado.")
    for position, option_index in enumerate(visible, start):
        option = options[option_index]
        active = position == selected
        prefix = _paint("›", "1;36") if active else " "
        label = _paint(option.label, "1;37") if active else option.label
        description = f"  {option.description}" if option.description else ""
        line = f"{prefix} {label}{description}"
        print(line[:width])
        if active and option.detail:
            print(_paint(f"    {option.detail}"[:width], "2"))
    footer = ["↑↓ navegar", "Enter selecionar"]
    if searchable:
        footer.append("/ buscar")
    if allow_back:
        footer.append("Esc voltar")
    else:
        footer.append("Esc cancelar")
    print("\n" + _paint("   ".join(footer), "2"), flush=True)


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
    matches = _matching(options, query)
    if not matches:
        raise ValueError("menu has no enabled options")
    selected = min(default, len(matches) - 1)
    while True:
        _render_navigation(
            title=title, options=options, matches=matches, selected=selected,
            breadcrumb=breadcrumb, subtitle=subtitle, query=query,
            searching=searching, searchable=searchable, allow_back=allow_back,
        )
        key = key_reader()
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
            selected = min(selected, max(0, len(matches) - 1))
            continue
        if key in ("up", "k") and matches:
            selected = (selected - 1) % len(matches)
        elif key in ("down", "j") and matches:
            selected = (selected + 1) % len(matches)
        elif key in ("enter", "right") and matches:
            return options[matches[selected]].key
        elif key in ("escape", "left", "q"):
            if allow_back:
                return None
            raise MenuCancelled(title)
        elif key == "/" and searchable:
            searching = True
        elif key.isdigit() and key != "0":
            position = int(key) - 1
            if position < len(matches):
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
    while True:
        if breadcrumb:
            print(f"\n{breadcrumb}")
        print(f"\n{title}")
        if subtitle:
            print(subtitle)
        labels = [
            option.label + (" (padrão)" if index == default else "")
            for index, option in enumerate(enabled)
        ]
        index_width = len(str(len(enabled)))
        label_width = max(len(label) for label in labels)
        description_width = max((len(option.description) for option in enabled), default=0)
        for index, option in enumerate(enabled, 1):
            label = labels[index - 1]
            line = f"  {index:>{index_width}}) {label:<{label_width}}"
            if description_width:
                line += f"  {option.description:<{description_width}}"
            if option.detail:
                line += f"  {option.detail}"
            print(line.rstrip())
        prompt = f"Escolha [1-{len(enabled)}]"
        if searchable:
            prompt += " ou digite para buscar"
        if allow_back:
            prompt += "; b para voltar"
        try:
            answer = input_fn(prompt + ": ").strip()
        except EOFError as error:
            raise MenuCancelled(title) from error
        if not answer:
            return enabled[min(default, len(enabled) - 1)].key
        if allow_back and answer.casefold() in {"b", "voltar", "q", "sair"}:
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(enabled):
            return enabled[int(answer) - 1].key
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
                option for option in enabled
                if answer.casefold() in " ".join((
                    option.key, option.label, option.description, option.detail, *option.aliases,
                )).casefold()
            ]
            if len(found) == 1:
                return found[0].key
            if found:
                enabled = found
                default = 0
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
) -> None:
    width = max(48, min(shutil.get_terminal_size((88, 24)).columns, 110))
    height = max(5, shutil.get_terminal_size((88, 24)).lines - 10)
    start = max(0, min(selected - height // 2, max(0, len(matches) - height)))
    visible = matches[start:start + height]
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
    print()
    if not visible:
        print("  Nenhum resultado.")
    for position, option_index in enumerate(visible, start):
        option = options[option_index]
        active = position == selected
        prefix = _paint("›", "1;36") if active else " "
        mark = _paint("[✓]", "1;32") if option.key in checked else "[ ]"
        label = _paint(option.label, "1;37") if active else option.label
        description = f"  {option.description}" if option.description else ""
        print(f"{prefix} {mark} {label}{description}"[:width])
        if active and option.detail:
            print(_paint(f"        {option.detail}"[:width], "2"))
    footer = ["↑↓ navegar", "Espaço marcar", "Enter concluir"]
    if searchable:
        footer.append("/ buscar")
    footer.append("Esc voltar" if allow_back else "Esc cancelar")
    print("\n" + _paint("   ".join(footer), "2"), flush=True)


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
        labels = [option.label for option in enabled]
        index_width = len(str(len(enabled)))
        label_width = max(len(label) for label in labels)
        print(f"\n{breadcrumb}" if breadcrumb else "", end="")
        print(f"\n{title}")
        if subtitle:
            print(subtitle)
        for index, option in enumerate(enabled, 1):
            marker = "x" if option.key in checked else " "
            detail = f"  {option.description}" if option.description else ""
            print(f"  {index:>{index_width}}) [{marker}] {option.label:<{label_width}}{detail}".rstrip())
        prompt = "Informe números ou identificadores separados por vírgula"
        if allow_back:
            prompt += "; b para voltar"
        try:
            answer = (input_fn or input)(prompt + ": ").strip()
        except EOFError as error:
            raise MenuCancelled(title) from error
        if allow_back and answer.casefold() in {"b", "voltar", "q", "sair"}:
            return None
        if not answer:
            result = tuple(option.key for option in enabled if option.key in checked)
            if result or allow_empty:
                return result
            raise MenuCancelled(title)
        chosen: set[str] = set()
        for token in (item.strip() for item in answer.split(",")):
            if token.isdigit() and 1 <= int(token) <= len(enabled):
                chosen.add(enabled[int(token) - 1].key)
                continue
            matches = [
                option for option in enabled
                if token.casefold() in {
                    option.key.casefold(), option.label.casefold(),
                    *(alias.casefold() for alias in option.aliases),
                }
            ]
            if len(matches) != 1:
                raise ValueError(f"unknown menu option: {token or '(empty)'}")
            chosen.add(matches[0].key)
        return tuple(option.key for option in enabled if option.key in chosen)

    query = ""
    searching = False
    matches = _matching(entries, query)
    position = 0
    reader = key_reader or read_key
    while True:
        _render_multiple(
            title=title, options=entries, matches=matches, selected=position,
            checked=checked, breadcrumb=breadcrumb, subtitle=subtitle,
            query=query, searching=searching, searchable=searchable,
            allow_back=allow_back,
        )
        key = reader()
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
            position = min(position, max(0, len(matches) - 1))
            continue
        if key in ("up", "k") and matches:
            position = (position - 1) % len(matches)
        elif key in ("down", "j") and matches:
            position = (position + 1) % len(matches)
        elif key == " " and matches:
            option_key = entries[matches[position]].key
            checked.symmetric_difference_update({option_key})
        elif key in ("enter", "right"):
            result = tuple(option.key for option in enabled if option.key in checked)
            if result or allow_empty:
                return result
        elif key in ("escape", "left", "q"):
            if allow_back:
                return None
            raise MenuCancelled(title)
        elif key == "/" and searchable:
            searching = True
        elif key.isdigit() and key != "0":
            shortcut = int(key) - 1
            if shortcut < len(matches):
                option_key = entries[matches[shortcut]].key
                checked.symmetric_difference_update({option_key})


def confirm(
    title: str,
    *,
    breadcrumb: str = "",
    description: str = "",
    default: bool = False,
    invalid_message: str = "Resposta inválida. Digite sim ou não.",
    interactive: bool | None = None,
    input_fn: Callable[[str], str] | None = None,
) -> bool:
    result = select_one(
        title,
        (
            MenuOption(
                "yes", "Sim", description if default else "", aliases=("s", "sim", "y", "yes"),
            ),
            MenuOption(
                "no", "Não", description if not default else "", aliases=("n", "nao", "não", "no"),
            ),
        ),
        breadcrumb=breadcrumb,
        default=0 if default else 1,
        invalid_message=invalid_message,
        interactive=interactive,
        input_fn=input_fn or input,
    )
    return result == "yes"
