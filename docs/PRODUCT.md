# Product

## Register

product

## Users

People discovering QuakeWorld and veteran players who want to install,
update, play and host matches without memorizing the CLI. The main flow must
guide beginners and stay quick for those who already know games, modes, maps
and services.

## Product Purpose

Offer a modern, reproducible and auditable QuakeWorld distribution, with a
terminal app that reveals the relevant options at the right moment. The app is
a front door to the same automatable commands; it neither replaces nor changes
the semantics of the public flags, and the actions it does not perform itself
run the equivalent `x86qw` commands.

## Brand Personality

Direct, competitive and reliable. The interface should convey the speed of the
game and the precision of a well-kept tool, without sacrificing legibility.

## Anti-references

Do not imitate graphical launchers inside the terminal, decorative retro menus,
neon gamer aesthetics, deep trees without context or wizards that hide the
flags actually used. Do not require a mouse nor depend on color to communicate
state.

## Design Principles

- Start from intent: in the installer, play, host or both; in the app, Play,
  Servers and Host before maintenance.
- Use progressive disclosure: ask only what applies to the current choice.
- One screen at a time, as an accordion, with a fixed footer saying that
  state's keys; nothing moves while the screen updates.
- Dialogs use space to change, Enter to confirm and Esc to cancel.
- Show what is going to happen and ask for confirmation before installing,
  updating, uninstalling or stopping a hosting stack.
- Let people look at the running stack, its endpoints and non-sensitive
  parameters without interrupting or changing the services, and offer a
  separate, confirmed action to stop it.
- Allow foreground or background without creating two lifecycle models: both
  use the same lock, journal, readiness and cleanup.
- Show the command and the recipe equivalent to what was chosen, without
  passwords.
- Keep the existing commands as a stable contract for advanced users and
  automation.

## Accessibility & Inclusion

Guarantee full keyboard operation, visible focus, information independent of
color, support for `NO_COLOR` and for terminals without Unicode glyphs
(`X86QW_GLYPHS=ascii`), and English, or Portuguese when the system is set to
it. Without a terminal, the CLI prints its help and each command says what it
needs. The interface must work on Unix and Windows terminals and stay legible
at reduced widths.
