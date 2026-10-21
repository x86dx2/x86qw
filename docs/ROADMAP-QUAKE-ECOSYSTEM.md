# Long-term view of the QuakeWorld ecosystem

This document describes where the ecosystem may evolve after the
distribution stabilizes. It is not a second authority on status: the sequence
up to `1.0.0` is in [ROADMAP.md](ROADMAP.md), the present state in
[PROJECT-STATUS.md](PROJECT-STATUS.md) and the executable gates in the
[stabilization plan](implementation/stabilization-1.0-plan.md).

## Principles of evolution

- keep upstream bytes and x86QW customizations in identifiable layers;
- treat catalog, contract, artifact, support and validation as different
  dimensions;
- keep an installation verifiable, with a reversible lifecycle and no mutation
  while running;
- require origin, license, hash, ownership, migration and platform evidence
  for each expansion;
- keep the local operational flow **without native gates** when the task only
  needs portable contracts; the absence of a native smoke must stay explicit,
  never implicit.

## Distribution and runtime

The desired architecture is one canonical source of inventories and contracts
that projects catalogs, bundles and documentation without duplicating facts.
The runtime must stay independent of `maintenance`, `dist` and entry facades,
with single boundaries for downloads, files, state, receipts, migrations,
transactions, UI, platform and supervisor.

The next architectural improvements should favor:

- versioned contracts and stable JSON envelopes;
- one-way migration of published states, keeping the ownership of personal
  files;
- candidates built once, with checksums, SBOM, provenance and converging
  mirrors;
- trust metadata with approved custody, rotation and expiry;
- platform support declared only as far as the candidate's evidence goes.

## QuakeWorld experience

The ecosystem should keep offering a coherent installation for the games and
modes already in the catalog, without turning a new mode into a reason to mix
upstream content, runtime or service. Evolution may explore:

- declarative selection of games, modes, maps and profiles;
- training and assisted matches as explicit, opt-in contracts;
- demo analysis tools and formal MVD/QWD validation;
- compatibility of clients, servers and relays without confusing catalog with
  native execution;
- operational documentation that keeps commands, defaults and rollback
  understandable.

Any change to gameplay, KTX, Frogbot, host, supervisor or downloader needs its
own issue. It cannot be pulled incidentally out of the checkpoint nor go into a
release PR.

## Services and network

MVDSV, QTV and QWFWD must stay independent components, secure by default and
combined only when the operator asks. The future view includes:

- formal readiness and preflight for each service;
- isolated tests of protocol, MVD generation and UDP forwarding;
- external exposure always explicit, with credentials off the command line;
- journaled recovery and clear ownership between launcher, guardian and
  children;
- persistent system services only after a proposal for lifecycle,
  installation, update, uninstall and rollback.

## Platform and publication

The compatibility of Linux, Windows, Intel macOS and nightly should stay
visible in the catalog without being promoted to support by inference.
Platform evolution needs a non-blocking harness for portable contracts and
native evidence of the exact candidate when the state changes to `supported`
or `conditional`.

The publication pipeline should keep preparation, trust, evidence, promotion
and metadata as separate steps. Actions, sources and dependencies must be
pinned by immutable identity; no divergent mirror may be accepted by silent
fallback.

## Post-1.0 backlog

The items below are long-term intentions, not current work:

- a demo center, MVD/QWD analysis and training as a command of its own;
- new clients, engines, games, mods and external maps;
- additional operational profiles on top of installed capabilities;
- persistent system services and observability integration;
- visual architecture tools, ecosystem maps and navigable documentation;
- syncing or using the GitLab mirror only after deciding its role and
  authority contract.

Each item needs a separate proposal with an issue, contract, artifact,
license, migration, tests, platform validation, security gates and its own
release PR. The ecosystem should not grow as a side effect of `play`, `host`,
`update` or `upgrade`.
