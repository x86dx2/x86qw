# Terminal experience

The x86QW terminal UI has two coordinated surfaces built on the same canonical
navigation engine and the existing product palette.

## Command center

Interactive game and management menus use a responsive command-center layout:

- a persistent product header and breadcrumb;
- a compact option rail with explicit focus state;
- a contextual detail pane on terminals at least 88 columns wide;
- a stacked layout between 54 and 87 columns;
- the canonical compact renderer below 54 columns.

The detail pane is intentionally contextual rather than decorative. It exposes
the description, constraints and aliases of the active action without adding a
new navigation model.

## Guided installation

Installer questions use a linear wizard that preserves the terminal scrollback.
Each completed answer collapses into a compact receipt before the next question,
so the full installation decision trail remains readable. Multi-select, search,
multi-digit shortcuts and text prompts use the same visual grammar.

## Compatibility and accessibility

The modern surface introduces no runtime dependency. It delegates input parsing,
search normalization, option semantics and fallback rendering to
`x86qw_runtime.ui.menu`.

- `NO_COLOR` remains authoritative.
- `X86QW_CLASSIC_UI=1` forces the canonical renderer.
- Non-TTY output remains deterministic and script-friendly.
- Selection never relies on color alone.
- Narrow terminals automatically retain the canonical compact UI.
