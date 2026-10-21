# Consolidation baseline — July 31, 2026

This note records the state observed before the requested consolidation. It does not
replace the catalog, the inventories or the roadmaps.

## Analyzed identity

- synced branch: `main`;
- origin HEAD: `eeea45786401aae166efcd04f5d126faea740da2`;
- current installer version: `0.1.25`;
- public catalog: 48 packages;
- BOM: 21 components;
- initial tests: 194 maintenance tests and 3 site tests; 1 network test
  explicitly skipped;
- initial validation: `PYTHONDONTWRITEBYTECODE=1 ./maintenance/manage.py verify`
  completed on macOS arm64.

## Existing surface

The CLI already offers `install`, `play`, `host`, `proxy`, `qtv`, `version`, `update`,
`upgrade`, `components`, `presets`, `hub`, `verify`, `cleanup` and `uninstall`.
The installed CLI restricts `install`, `components` and `presets` to the bootstrap.

The five existing games are KTX, Final Arena, Pro-X, Team Fortress and Total
Destruction 2. Stable and nightly coexist. MVDSV, QTV and QWFWD are already components
that run in the foreground; they are not new work of this consolidation.

## Platforms

The catalog ships ezQuake for universal macOS, Linux x86-64 and Windows
x64. The services ship macOS arm64, Linux amd64 and Windows x64; macOS Intel
has no service runtime.

Before this task there was no `.github/workflows/`. The full suite had been
run locally on macOS arm64, including smokes recorded in the roadmap.
Linux and Windows were declared and had format validations, but did not yet
have a real continuous matrix. Portable tests on runners are not the same as a
smoke of the native runtime.

## Confirmed couplings and hardcoding

- `manager.py` holds the fixed ezQuake matrix and system detection;
- `gameplay.py` uses `LOCAL_GAMES`, sets of legacy capabilities and
  conditionals by game name;
- `services.py` uses `RUNTIME_NAMES`, `runtime_variant` and conditionals for KTX and
  Pro-X;
- KTX mode data already comes from `modes.json` and serves as the declarative pattern;
- `play` and `host` call `ensure_local_play_support`, which can create a payload,
  a receipt and personal settings while running;
- the dedicated PK3 materialization uses `Path(info.filename)`, so it depends on the
  host's path semantics;
- QTV accepted the upstream as generic text and there was no joint port
  preflight;
- passwords were accepted only as arguments;
- there was no recoverable journal of interrupted sessions.

## Confirmed documentation contradictions

- the site and the manual still said “18 components”, while the BOM has 21;
- the README's first list left out `host`, `proxy` and `qtv`;
- `ROADMAP.md` still treated MVDSV, QTV, QWFWD and the KTX menu as future work;
- the dedicated roadmap pointed to an earlier HEAD and mixed functional
  delivery with platform validation;
- counts, version, commands and platforms were repeated by hand.

## Delivered versus planned

Already delivered: a cross-platform installer, stable/nightly, five games, 24 declarative KTX
modes, Frogbots, `play`, `host`, MVDSV, QTV HTTP, QWFWD, `hub`,
update/upgrade by profile, verification, cleanup and uninstall.

Still planned at the baseline: cross-platform CI, full hardening of archives
and endpoints, passwords off the command line, full preflight/readiness,
journal and recovery, a declarative catalog of runtimes/games, execution without
mutation, explicit repair, operational host profiles and a demo center.

## Invariants of this consolidation

- preserve `id1/pak0.pak` and `id1/pak1.pak` and the current technical strategy;
- do not add any new runtime, engine, mod or map;
- preserve the existing toolchains without adding a new runtime;
- preserve commands, receipts, inventories, personal settings and published
  bundles;
- do not publish or change the public bootstrap without separate approval.
