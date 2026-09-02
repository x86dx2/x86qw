"""Polished terminal renderers for the canonical x86QW menu engine.

The public navigation API, state machine, key decoding, search semantics and
fallbacks remain owned by :mod:`x86qw_runtime.ui.menu`.  This module only swaps
its private presentation functions, preserving one canonical module and one
exception set across every installer and gameplay entrypoint.
"""

from __future__ import annotations

import os
import re
import textwrap
import unicodedata
from inspect import Parameter, signature
from typing import Any


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_MIN_WIDTH = 54
_SPLIT_WIDTH = 88
_MAX_WIDTH = 118
_MENU: Any = None
_ORIGINALS: dict[str, Any] = {}
_PATCH_NAMES = (
    "_render_navigation",
    "_wizard_frame",
    "_wizard_multiple_frame",
    "_wizard_text_frame",
    "_wizard_collapse",
    "_wizard_multiple_collapse",
)


def install(menu: Any) -> None:
    """Install the presentation layer into the canonical menu module.

    Installation is idempotent and reload-safe.  The original renderers are
    retained on the target module so a reloaded adapter cannot accidentally
    capture its own functions as fallbacks.
    """

    global _MENU, _ORIGINALS
    _MENU = menu
    current = getattr(menu, "_x86qw_polished_originals", None)
    renderer = getattr(menu, "_render_navigation")
    if renderer.__module__ == __name__ and isinstance(current, dict):
        _ORIGINALS = current
    else:
        _ORIGINALS = {name: getattr(menu, name) for name in _PATCH_NAMES}
        setattr(menu, "_x86qw_polished_originals", _ORIGINALS)

    menu._render_navigation = _render_navigation
    menu._wizard_frame = _wizard_frame
    menu._wizard_multiple_frame = _wizard_multiple_frame
    menu._wizard_text_frame = _wizard_text_frame
    menu._wizard_collapse = _wizard_collapse
    menu._wizard_multiple_collapse = _wizard_multiple_collapse
    setattr(menu, "_x86qw_polished_ui", True)


def _terminal() -> os.terminal_size:
    return _MENU.terminal_size((96, 28))


def _width() -> int:
    return max(20, min(_terminal().columns, _MAX_WIDTH))


def _classic() -> bool:
    return os.environ.get("X86QW_CLASSIC_UI") == "1" or _width() < _MIN_WIDTH


def _tone(value: str, name: str) -> str:
    code = getattr(_MENU, name, "")
    return _MENU._paint(value, code) if code else value


def _wizard(value: str, color_name: str) -> str:
    return _MENU._wizard_color(value, getattr(_MENU, color_name))


def _plain(value: str) -> str:
    return _ANSI.sub("", value)


def _cells(value: str) -> int:
    width = 0
    for character in _plain(value):
        if unicodedata.combining(character):
            continue
        width += 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
    return width


def _clip(value: str, width: int) -> str:
    if width <= 0:
        return ""
    if _cells(value) <= width:
        return value
    result: list[str] = []
    used = 0
    for character in _plain(value):
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
        lines.extend(
            textwrap.wrap(
                source,
                width=max(8, width),
                break_long_words=False,
                break_on_hyphens=False,
            )
            or [""]
        )
    return lines


def _brand(surface: str, width: int, *, status: str = "PRONTO") -> str:
    left_plain = f"X86QW  /  {surface}"
    status_label = _clip(status.strip().upper() or "PRONTO", max(8, width // 3))
    right_plain = f"● {status_label}"
    gap = max(2, width - _cells(left_plain) - _cells(right_plain))
    return (
        _tone("X86QW", "_TITLE")
        + _tone(f"  /  {surface}", "_SEARCH")
        + " " * gap
        + _tone("●", "_OK")
        + _tone(f" {status_label}", "_MUTED")
    )


def _call_original(name: str, **kwargs: Any) -> Any:
    """Call a canonical renderer across additive presentation contracts."""

    renderer = _ORIGINALS[name]
    parameters = signature(renderer).parameters.values()
    if any(parameter.kind is Parameter.VAR_KEYWORD for parameter in parameters):
        return renderer(**kwargs)
    accepted = signature(renderer).parameters
    return renderer(**{key: value for key, value in kwargs.items() if key in accepted})


def _rule(width: int) -> str:
    return _tone("─" * max(1, width), "_MUTED")


def _key(value: str) -> str:
    return _tone(f"[{value}]", "_SEARCH")


def _panel(
    title: str,
    rows: list[tuple[str, str]],
    width: int,
    height: int,
) -> list[str]:
    inner = max(8, width - 2)
    label = f" {title.upper()} "
    top = "╭─" + label + "─" * max(0, inner - len(label) - 1) + "╮"
    output = [_tone(_clip(top, width), "_MUTED")]
    for index in range(height):
        value, tone = rows[index] if index < len(rows) else ("", "")
        value = _clip(value, inner - 2)
        rendered = _tone(value, tone) if tone else value
        output.append(
            _tone("│", "_MUTED")
            + " "
            + _pad(rendered, inner - 2)
            + " "
            + _tone("│", "_MUTED")
        )
    output.append(_tone("╰" + "─" * inner + "╯", "_MUTED"))
    return output


def _window(lengths: list[int], selected: int, budget: int) -> tuple[int, int]:
    if not lengths:
        return 0, 0
    return _MENU._row_window(lengths, selected, max(1, budget))


def _option_rows(
    options: tuple[Any, ...],
    matches: list[int],
    selected: int,
    *,
    width: int,
    capacity: int,
) -> list[tuple[str, str]]:
    previous_group = ""
    blocks: list[list[tuple[str, str]]] = []
    lengths: list[int] = []
    for position, option_index in enumerate(matches):
        option = options[option_index]
        block: list[tuple[str, str]] = []
        if option.group and option.group != previous_group:
            block.append((option.group.upper(), "_MUTED"))
            previous_group = option.group
        marker = "▌" if position == selected else " "
        suffix = " · indisponível" if not option.enabled else ""
        line = _clip(f"{marker} {position + 1:>2}  {option.label}{suffix}", width)
        if not option.enabled:
            tone = "_MUTED"
        elif position == selected:
            tone = "_TITLE"
        else:
            tone = "_SEARCH"
        block.append((line, tone))
        blocks.append(block)
        lengths.append(len(block))

    start, end = _window(lengths, selected, capacity)
    rows = [row for block in blocks[start:end] for row in block]
    if len(matches) > end - start:
        rows.append((f"{start + 1}–{end} de {len(matches)}", "_MUTED"))
    if not rows:
        rows.append(("Nenhum resultado para esta busca.", "_MUTED"))
    return rows


def _detail_rows(option: Any | None, width: int) -> list[tuple[str, str]]:
    if option is None:
        return [("Nenhum resultado.", "_MUTED")]
    rows: list[tuple[str, str]] = [(option.label, "_TITLE")]
    if option.description:
        rows.extend((line, "_SEARCH") for line in _wrap(option.description, width))
    rows.append(("", ""))
    detail = option.detail or option.disabled_reason or "Pressione Enter para continuar."
    rows.extend((line, "_MUTED") for line in _wrap(detail, width))
    if option.aliases:
        rows.append(("", ""))
        rows.extend(
            (line, "_MUTED")
            for line in _wrap("Atalhos: " + " · ".join(option.aliases[:4]), width)
        )
    return rows


def _footer(
    *,
    searchable: bool,
    allow_back: bool,
    searching: bool,
    numeric_buffer: str,
    action_label: str = "selecionar",
) -> str:
    if searching:
        return f"{_key('digite')} buscar  {_key('enter')} aplicar  {_key('esc')} limpar"
    if numeric_buffer:
        return f"{_key('0–9')} completar  {_key('enter')} selecionar  {_key('esc')} limpar"
    action = action_label.strip() or "selecionar"
    items = [f"{_key('↑↓')} navegar", f"{_key('enter')} {action}"]
    if searchable:
        items.append(f"{_key('/')} buscar")
    items.append(f"{_key('←')} {'voltar' if allow_back else 'cancelar'}")
    items.append(f"{_key('esc')} sair")
    return "  ".join(items)


def _render_navigation(
    *,
    title: str,
    options: tuple[Any, ...],
    matches: list[int],
    selected: int,
    breadcrumb: str,
    subtitle: str,
    query: str,
    searching: bool,
    searchable: bool,
    allow_back: bool,
    numeric_buffer: str,
    status: str = "PRONTO",
    action_label: str = "selecionar",
) -> None:
    if _classic():
        _call_original(
            "_render_navigation",
            title=title,
            options=options,
            matches=matches,
            selected=selected,
            breadcrumb=breadcrumb,
            subtitle=subtitle,
            query=query,
            searching=searching,
            searchable=searchable,
            allow_back=allow_back,
            numeric_buffer=numeric_buffer,
            status=status,
            action_label=action_label,
        )
        return

    width = _width()
    terminal_height = max(18, _terminal().lines)
    _MENU._begin_frame()
    print(_brand("CENTRAL X86QW", width, status=status))
    print(_rule(width))
    if breadcrumb:
        for line in _wrap(breadcrumb, width):
            print(_tone(line, "_MUTED"))
    for line in _wrap(title, width):
        print(_tone(line, "_TITLE"))
    if subtitle:
        for line in _wrap(subtitle, width):
            print(_tone(line, "_SEARCH"))
    if searching or query:
        cursor = "█" if searching else ""
        print(_tone(_clip(f"⌕  {query}{cursor}", width), "_SEARCH"))
    elif searchable:
        print(_tone("⌕  / para buscar", "_MUTED"))
    if numeric_buffer:
        print(_tone(f"IR PARA O ITEM: {numeric_buffer}█", "_SEARCH"))
    print()

    capacity = max(4, min(12, terminal_height - 14))
    if width >= _SPLIT_WIDTH:
        left_width = max(36, int(width * 0.47))
        right_width = width - left_width - 2
        option_rows = _option_rows(
            options,
            matches,
            selected,
            width=left_width - 4,
            capacity=capacity,
        )
        active = options[matches[selected]] if matches else None
        detail_rows = _detail_rows(active, right_width - 6)
        height = max(8, min(max(len(option_rows), len(detail_rows)), capacity + 2))
        left = _panel("opções", option_rows, left_width, height)
        right = _panel("detalhes", detail_rows, right_width, height)
        for left_line, right_line in zip(left, right):
            print(f"{left_line}  {right_line}")
    else:
        option_rows = _option_rows(
            options,
            matches,
            selected,
            width=width - 4,
            capacity=capacity,
        )
        height = max(7, min(len(option_rows), capacity + 1))
        for line in _panel("opções", option_rows, width, height):
            print(line)
        if matches:
            active = options[matches[selected]]
            summary = " · ".join(
                item for item in (active.label, active.description, active.detail) if item
            )
            if summary:
                print(_tone(_clip(summary, width), "_MUTED"))

    print()
    print(_rule(width))
    print(
        _footer(
            searchable=searchable,
            allow_back=allow_back,
            searching=searching,
            numeric_buffer=numeric_buffer,
            action_label=action_label,
        ),
        flush=True,
    )


def _wizard_header(title: str, width: int) -> list[str]:
    lines = [_brand("INSTALAÇÃO GUIADA", width), _rule(width)]
    wrapped = _wrap(title, max(10, width - 4))
    lines.append(
        f"{_wizard('◆', 'SUCCESS')}  "
        f"{_MENU._wizard_color(wrapped[0], _MENU._WIZARD_PROMPT)}"
    )
    connector = _wizard("│", "INFO")
    for continuation in wrapped[1:]:
        lines.append(f"{connector}  {_MENU._wizard_color(continuation, _MENU._WIZARD_PROMPT)}")
    return lines


def _wizard_footer(parts: list[str]) -> str:
    return _wizard("╰─", "INFO") + " " + "  ".join(
        _MENU._wizard_dim(part) for part in parts
    )


def _wizard_frame(
    title: str,
    options: tuple[Any, ...],
    matches: list[int],
    selected: int,
    *,
    query: str,
    searching: bool,
    searchable: bool,
    allow_back: bool,
    numeric_buffer: str,
) -> str:
    if _classic():
        return _ORIGINALS["_wizard_frame"](
            title,
            options,
            matches,
            selected,
            query=query,
            searching=searching,
            searchable=searchable,
            allow_back=allow_back,
            numeric_buffer=numeric_buffer,
        )

    width = _width()
    connector = _wizard("│", "INFO")
    lines = _wizard_header(title, width)
    if searching or query:
        cursor = "█" if searching else ""
        lines.append(f"{connector}  {_wizard('⌕', 'WARNING')}  {query}{cursor}")
    if numeric_buffer:
        lines.append(
            f"{connector}  {_MENU._wizard_dim('Ir para o item:')} {numeric_buffer}█"
        )
    lines.append(_wizard("│", "MUTED"))

    row_budget = max(3, min(10, _terminal().lines - 12))
    selectable = _MENU._selectable_positions(options, matches)
    if selected not in selectable and selectable:
        selected = selectable[0]
    start = max(0, selected - row_budget // 2)
    end = min(len(matches), start + row_budget)
    start = max(0, end - row_budget)
    for position in range(start, end):
        option = options[matches[position]]
        if not option.enabled:
            marker = _MENU._wizard_dim("○")
            label = _MENU._wizard_dim(option.label)
            detail = option.disabled_reason or option.description
        elif position == selected:
            marker = _wizard("●", "SUCCESS")
            label = _MENU._wizard_color(option.label, _MENU._WIZARD_PROMPT)
            detail = option.description
        else:
            marker = _MENU._wizard_dim("○")
            label = _MENU._wizard_dim(option.label)
            detail = ""
        row = f"{connector}  {marker}  {position + 1:>2}  {label}"
        if detail:
            row += "  " + _MENU._wizard_dim(f"— {detail}")
        lines.append(row)
    if len(matches) > end - start:
        lines.append(
            f"{connector}  {_MENU._wizard_dim(f'{start + 1}–{end} de {len(matches)}')}"
        )

    if searching:
        footer = ["digite: buscar", "enter: aplicar", "esc: limpar"]
    elif numeric_buffer:
        footer = ["0–9: completar", "enter: selecionar", "esc: limpar"]
    else:
        footer = ["↑↓: navegar", "enter: confirmar"]
        if searchable:
            footer.append("/: buscar")
        if allow_back:
            footer.append("←: voltar")
    lines.append(_wizard("│", "MUTED"))
    lines.append(_wizard_footer(footer))
    return "\n".join(lines) + "\n"


def _wizard_multiple_frame(
    title: str,
    options: tuple[Any, ...],
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
    if _classic():
        return _ORIGINALS["_wizard_multiple_frame"](
            title,
            options,
            matches,
            selected,
            checked,
            query=query,
            searching=searching,
            searchable=searchable,
            allow_back=allow_back,
            numeric_buffer=numeric_buffer,
            validation_message=validation_message,
        )

    width = _width()
    connector = _wizard("│", "INFO")
    lines = _wizard_header(title, width)
    if searching or query:
        cursor = "█" if searching else ""
        lines.append(f"{connector}  {_wizard('⌕', 'WARNING')}  {query}{cursor}")
    if numeric_buffer:
        lines.append(
            f"{connector}  {_MENU._wizard_dim('Marcar item:')} {numeric_buffer}█"
        )
    lines.append(_wizard("│", "MUTED"))

    row_budget = max(3, min(10, _terminal().lines - 13))
    selectable = _MENU._selectable_positions(options, matches)
    if selected not in selectable and selectable:
        selected = selectable[0]
    start = max(0, selected - row_budget // 2)
    end = min(len(matches), start + row_budget)
    start = max(0, end - row_budget)
    for position in range(start, end):
        option = options[matches[position]]
        marker_text = "[✓]" if option.key in checked else "[ ]"
        if not option.enabled:
            marker = _MENU._wizard_dim(marker_text)
            label = _MENU._wizard_dim(option.label)
            detail = option.disabled_reason or option.description
        elif position == selected:
            marker = _wizard(marker_text, "SUCCESS")
            label = _MENU._wizard_color(option.label, _MENU._WIZARD_PROMPT)
            detail = option.description
        elif option.key in checked:
            marker = _wizard(marker_text, "SUCCESS")
            label = _MENU._wizard_dim(option.label)
            detail = ""
        else:
            marker = _MENU._wizard_dim(marker_text)
            label = _MENU._wizard_dim(option.label)
            detail = ""
        row = f"{connector}  {marker}  {position + 1:>2}  {label}"
        if detail:
            row += "  " + _MENU._wizard_dim(f"— {detail}")
        lines.append(row)
    if len(matches) > end - start:
        lines.append(
            f"{connector}  {_MENU._wizard_dim(f'{start + 1}–{end} de {len(matches)}')}"
        )
    if validation_message:
        lines.append(f"{connector}  {_wizard(validation_message, 'ERROR')}")

    if searching:
        footer = ["digite: buscar", "enter: aplicar", "esc: limpar"]
    elif numeric_buffer:
        footer = ["0–9: completar", "espaço/enter: marcar", "esc: limpar"]
    else:
        footer = [
            "↑↓: navegar",
            "espaço: marcar",
            "enter: concluir",
            "a: tudo",
            "d: limpar",
        ]
        if searchable:
            footer.append("/: buscar")
        if allow_back:
            footer.append("←: voltar")
    lines.append(_wizard("│", "MUTED"))
    lines.append(_wizard_footer(footer))
    return "\n".join(lines) + "\n"


def _wizard_text_frame(title: str, value: str, description: str) -> str:
    if _classic():
        return _ORIGINALS["_wizard_text_frame"](title, value, description)
    width = _width()
    connector = _wizard("│", "INFO")
    lines = _wizard_header(title, width)
    lines.extend(
        (
            _wizard("│", "MUTED"),
            f"{connector}  {_MENU._wizard_color(_clip(value + '█', width - 5), _MENU._WIZARD_PROMPT)}",
        )
    )
    if description:
        for part in _wrap(description, width - 5):
            lines.append(f"{connector}  {_MENU._wizard_dim(part)}")
    lines.append(_wizard_footer(["enter: confirmar", "esc: sair"]))
    return "\n".join(lines) + "\n"


def _wizard_collapse(title: str, option: Any) -> str:
    if _classic():
        return _ORIGINALS["_wizard_collapse"](title, option)
    summary = option.label + (f" · {option.description}" if option.description else "")
    return (
        f"{_wizard('◇', 'SUCCESS')}  "
        f"{_MENU._wizard_color(title, _MENU._WIZARD_PROMPT)}\n"
        f"{_wizard('╰─', 'MUTED')} {_MENU._wizard_dim(summary)}\n"
    )


def _wizard_multiple_collapse(title: str, count: int) -> str:
    if _classic():
        return _ORIGINALS["_wizard_multiple_collapse"](title, count)
    noun = "componente selecionado" if count == 1 else "componentes selecionados"
    return (
        f"{_wizard('◇', 'SUCCESS')}  "
        f"{_MENU._wizard_color(title, _MENU._WIZARD_PROMPT)}\n"
        f"{_wizard('╰─', 'MUTED')} {_MENU._wizard_dim(f'{count} {noun}')}\n"
    )


__all__ = ("install",)
