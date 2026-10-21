# Incremental runtime boundaries — PR 6 evidence

- **Issue:** [#52](https://github.com/x86dx2/x86qw/issues/52)
- **Initial baseline:** `00098330e5833ba2c83c7121272d644c2a204a7b`
- **Analyzed HEAD:** `29d76a48721190aad1203d0986a31d839d62070e`
- **Documented range:** `23194fd..29d76a4`
- **Preserved public version:** `0.7.3`
- **State:** historical snapshot integrated by PR #62 into the public `0.7.3` line; the PR6 track stays paused for the final architecture review and native validation of the 1.0 candidate

This note records only changes committed on the historical branch
`agent/runtime-boundaries`. The integration of that boundary was published in
`0.7.3`; the native matrix of the 1.0 candidate and the work reserved for PRs 7–12
are not presented as delivered in this note.

## Confirmed problem and risk

The installed runtime depended on rules spread across `manager.py`,
`gameplay.py`, `services.py`, `menu.py` and `maintenance.tools`. That grew the
zipapp, created cycles, loaded catalogs before they were needed and left HTTP,
persistence and lifecycle open to parallel implementations.

PR 6 introduces small, verifiable boundaries in `x86qw_runtime` without
rewriting the CLI or changing its public behavior. The architectural decision and
the limits of this range are in
[ADR 0005](../adr/0005-incremental-x86qw-runtime-boundaries.md).

## Committed increments

| Commit | Unit | Observed result |
|---|---|---|
| `23194fd` | downloader and errors | the bounded downloader moves to the runtime; maintenance becomes a facade; entrypoints share typed errors |
| `dccd7dc` | catalogs, version and lazy loading | models/loaders move to the runtime; help/version work without reading the catalog |
| `e378bef` | atomic writing | writers of state, journals and files converge on durable primitives |
| `20298c1` | state, receipts and migrations | codecs and migration of the current format leave the manager without changing the persisted format |
| `853e126` | component transactions | preflight, revalidation, apply and reverse rollback become an explicit contract |
| `e5d443d` | UI | the menu engine moves to the runtime; `menu.py` stays as a compatible facade |
| `bc3a6fb` | durability barrier | a failure after promotion is told apart from a still reversible failure |
| `3e1b6fc` | state composition | component staging is kept until the parent state's commit |
| `6a60323` | gameplay | models, catalog and planning move to the runtime with golden commands |
| `54f52a2` | process identity | PID, creation token and executable move to the platform adapter |
| `a30b41f` | readiness | MVDSV, QTV and QWFWD probes move to the runtime supervisor |
| `0966bc1` | gameplay graph | the import dependency between gameplay and manager is removed |
| `839d4ef` | clients | the client's runtime and receipt move to a transaction coordinated with the parent state |
| `73eb431` | lifecycle | execution, signals, groups/Job Object and shutdown move to the runtime supervisor |
| `49594cd` | component removal | managed payload and metadata gain an identified backup and rollback; legacy removals during update join the parent transaction |
| `cf6e959`–`3929c9c` | lock and composition | the installation mutex moves to the runtime platform; entrypoints receive explicit contexts; `session_control` becomes the canonical contract |
| `189679d` | generated components | inverses of derived content stay available until the parent state's result |
| `04e5672`–`71ca24c` | composition and Python | development composition stays out of the runtime; the Python resolver moves to the canonical platform; an unconsumed compatible projection leaves the zipapp |
| `3b14559`–`1d4fe21` | removals and defaults | interactive removals join the state commit and new defaults are reverted on a later failure |
| `f932f21` | sessions | journal, crash recovery and reconciliation move to `x86qw_runtime.supervisor.sessions`; materialized files move to `x86qw_runtime.io.managed_files` |
| `c5a9ae4` | installed CLI | the CLI's runtime, receipt and launchers are published as one transactional generation |
| `d2e81e5` | central PAKs | the two preserved PAKs go through atomic promotion and rollback without changing their bytes or policy |
| `513ffaa`–`a59452b` | repair and migrations | local permissions, deterministic metadata and personal settings keep inverses until the final result |
| `78310a8`–`422baef` | final composition | installation, state and repair inverses survive until the final verification and are discarded only after it |
| `1fea5ed` | installed surface | unconsumed compatibility facades leave the zipapp |
| `2807ceb` | zipapp contract | each of the 47 members gets an origin, consumer and contract in an independent manifest, checked against the builder and the generated file |
| `efc4875` | cleanup | every path selected by the cleanup forms one transaction; a later failure restores the whole selection |
| `d08f727` | uninstall | runtimes, components, metadata and the CLI generation are removed as one transaction; a failure restores the previous snapshot |
| `ff249ec`–`0cf2222` | gates and composition | the cycle detector covers relative imports and `from package import module`; UI, HTTP and catalogs move to the canonical boundaries and lazy loading is preserved |
| `7a1dc9b` | gameplay and platform | PAKs, runtime/personal settings and display/host/macOS facts move to the runtime, with identified rollback ownership |
| `a2f041f` | residual mutations | cache, purge and the installer's remaining operations go through plan/quarantine and identity revalidation |
| `1409607` | supervisor and session | authorship of locks, journals, logs, stop requests, groups and recovery moves to race-resistant canonical contracts |
| `7ab1677` | zipapp projection | the builder derives the members from the declarative manifest; a second static list no longer exists |
| `29d76a4` | quarantine finalization | regular files/directories use removal bound to the observation; Windows validates the native handle; links and special types are preserved |

The `x86qw_runtime.io.archive` scanner and extractor already belonged to the runtime in the
baseline, per ADR 0002. The range keeps that boundary as the sole owner
of ZIP/PK3/PYZ and does not change limits, canonical modes or extraction semantics.

## Resulting topology

```text
dist/installer/bin/
  manager.py ───────────────┐
  gameplay.py ──────────────┤
  services.py ──────────────┤
  menu.py (facade) ─────────┤
maintenance/tools/ ─────────┤
maintenance builders ───────┤
                            v
                      x86qw_runtime/
                      ├── catalogs, versioning, errors
                      ├── io/{archive,atomic,downloader,managed_files,
                      │       metadata,paths,personal_files,private_fs,
                      │       quarantine,remote}
                      ├── state, receipts, migrations, transaction
                      ├── gameplay
                      ├── platform/{display,host,locking,macos,processes,
                      │            python_runtime,windows_acl}
                      ├── session_control
                      ├── supervisor/{core,models,posix_guardian,readiness,sessions}
                      └── ui
```

The runtime cannot import `maintenance`, `dist` or installed facades. The
builder embeds the canonical modules and needed projections in the zipapp, but
not `maintenance/`.

The zipapp observed at `29d76a4` holds exactly 56 members: 44 modules of
`x86qw_runtime`, four top-level entrypoints/facades, two KTX projections and six
generated members. The file
`maintenance/inventory/installer-runtime-members.json` declares, for each
member, its origin, consumer and contract. The builder derives its projection from that
manifest and the boundary test requires exact equality with the generated ZIP;
an undeclared file, or one without a consumer, cannot slip in.

## Persistence and rollback already covered

The `MutationPlan` boundary captures the observed snapshot before confirmation,
revalidates it before the first change and applies steps in order. A failure
reverts the completed steps in reverse order. Subtransactions of components,
clients, the installed CLI, PAKs, defaults, repairs, migrations, cleanup and uninstall
keep their backups/staging until the parent state and the final verification, which
allows a composite rollback without discarding the inverses too early.

Atomic writes use a private temporary, validation, `flush`, `fsync` and
`replace`. An exception before the promotion preserves the old destination. An
exception after the promotion whose durability could not be proven raises an
inconclusive commit error and preserves the effects; it does not claim a rollback the
system cannot prove.

## Observed compatibility

- the old facades re-export the same canonical symbols when that
  identity is part of the existing internal contract;
- the current state and receipt formats stay the same;
- gameplay commands are compared with a golden fixture;
- `--help`, `--version`, `version`, `play --help` and `host --help` work in the
  zipapp even with deliberately corrupted catalogs;
- `host --help` and `status --help` run using the supervisor included in the
  zipapp;
- no intentional change was made to gameplay, public arguments,
  stable/nightly, profiles, components or PAKs.

## Focused evidence already obtained

These results were obtained during the increments and are recorded as
partial evidence, not as validation of the PR's final HEAD:

| Focus | Recorded result |
|---|---|
| full maintenance after the first component transaction | 817 tests; 35 explicit skips; passed |
| transaction, components and installer | 298 tests; 1 skip; passed |
| atomicity, transaction and modern components | 161 tests; passed |
| UI and runtime boundaries | 34 tests; passed |
| runtime gameplay and self-contained zipapp | 5 focused tests and 2 zipapp smokes; passed |
| modern regression after gameplay | 143 tests; passed |
| process identity and self-contained CLI | 5 tests; passed |
| readiness, services and network gate | 104 tests; 6 explicit skips; passed |
| HEAD `29d76a4`: full maintenance regression | 1,190 tests; passed; 37 explicit platform/network skips |
| HEAD `29d76a4`: site | 5 tests; passed |
| HEAD `29d76a4`: focused transaction, ownership and architecture | 349 tests; passed; 6 native Windows skips on macOS |

The full result was reproduced at the documented HEAD and includes the equality
of the 56 members with the projection derived from the manifest, the absence of
`maintenance/` in the zipapp, imports without cycles, late loading of catalogs,
facade identity, recovery without importing `services.py` and running
`host --help`/`status --help` in the zipapp. The adversarial tests exercise
rollback of cleanup, uninstall and purge; concurrent replacement of roots,
leaves and backups; ownership of locks, journals, logs and stop requests; and
preservation of paths that change identity. The local skips require native Windows
APIs or shells, plus one opt-in network smoke. The Linux, macOS and
Windows matrix must reproduce the snapshot sent to the PR.

## Residual risks

- `manager.py`, `gameplay.py` and `services.py` are still large entrypoints and
  composition roots; new reusable rules must not be implemented in them
  again;
- execution logs are append-only and take no part in rollback; the
  transactional contract covers durable mutations of payload, metadata and managed
  settings;
- quarantine finalization is the irreversible barrier after the commit;
  on Windows it removes through the validated handle; on POSIX it uses a descriptor, identity
  and an exclusive rename to an unpredictable private name, while links, special
  types, failures or observed swaps preserve the content for inspection;
- POSIX offers no conditional `unlink`/`rmdir` by inode; the installation
  lock excludes other x86QW operations, but does not claim to defend against
  hostile code running as the same user;
- the absence of cycles and reverse imports must stay green after the
  next extractions;
- the final snapshot still needs the PR's portable matrix;
- native smokes of clients and services belong to PR 11.

That is why issue #52 was closed by PR 62; the review and the native matrix
remain recorded as gates of the 1.0 stabilization. `0.7.3`, its
artifacts, hashes, tag, catalog and public bootstraps stay immutable.
This note neither authorizes nor records the publication of a new version: the
corrective checkout of the 1.0 stabilization stays separate from the public line.
