# ADR 0005 — Incremental boundaries in `x86qw_runtime`

- **Status:** accepted and implemented in PR 6's code; review open; not published
- **Date:** 2026-08-04
- **Issue:** [#52](https://github.com/x86dx2/x86qw/issues/52)
- **Baseline:** `00098330e5833ba2c83c7121272d644c2a204a7b`
- **Analyzed HEAD:** `29d76a48721190aad1203d0986a31d839d62070e`
- **Documented slice:** `23194fd..29d76a4`

## Context

The boundaries for safe archives, the private filesystem and the Windows DACL
already existed in `x86qw_runtime`, but the installed CLI still concentrated
download, catalog, persistence, gameplay, platform, supervision and UI rules in
the entrypoints. On some paths, the runtime depended on `maintenance.tools`, so
the public artifact had to embed maintenance code to run.

That dependency inversion grew the zipapp's surface, encouraged import cycles
and let sensitive contracts — HTTP, state, receipts, readiness and rollback —
diverge again between maintenance and the installed runtime.

## Decision

x86QW adopts an incremental migration, without a rewrite, toward the following
dependency direction:

```text
installed entrypoints ─┐
maintenance            ├──> x86qw_runtime
builders               ┘

x86qw_runtime -X-> maintenance
x86qw_runtime -X-> manager/gameplay/services/menu
```

The rules are:

1. `x86qw_runtime` owns the product's reusable contracts and does not import
   `maintenance`, `dist` or the installed entrypoints;
2. `manager.py`, `gameplay.py`, `services.py` and `menu.py` may remain as
   compatibility facades while they delegate to the runtime;
3. maintenance consumes the runtime; a maintenance module may be a temporary
   facade, but not a second implementation;
4. catalogs are loaded only by the first command that actually needs them;
   `--help` and `--version` do not depend on them;
5. covered mutations use an immutable plan, preflight, revalidation, ordered
   application and reverse rollback;
6. the zipapp includes only the runtime, the consumed entrypoints and the
   necessary declarative projections, without embedding `maintenance/`; each
   member must declare origin, consumer and contract in an independent
   manifest;
7. extraction keeps belonging to the single boundary
   `x86qw_runtime.io.archive`, accepted earlier in
   [ADR 0002](0002-single-archive-boundary.md); this PR does not create a
   parallel scanner nor change its limits;
8. codes, texts, persisted formats and public behavior stay stable during this
   refactoring. Public changes to SemVer, schemas, exit codes and JSON belong
   to PR 7.

## Ownership implemented in the analyzed HEAD

| Area | Canonical boundary | State at `29d76a4` |
|---|---|---|
| Version and errors | `x86qw_runtime.versioning`, `x86qw_runtime.errors` | consumers share the current types and parser |
| Download | `x86qw_runtime.io.downloader` | maintenance is a facade; the zipapp carries only the runtime |
| Archives | `x86qw_runtime.io.archive` | earlier safe contract preserved as the single boundary |
| Persistence and filesystem | `x86qw_runtime.io.atomic`, `io.metadata`, `io.paths`, `io.private_fs`, `io.managed_files`, `io.personal_files`, `io.quarantine` | durable writes, private objects, identity, personal files and reversible removal centralized |
| Catalogs | `x86qw_runtime.catalogs` | canonical models/loaders and late loading |
| Persisted state | `x86qw_runtime.state`, `receipts`, `migrations` | current parsing, serialization and migration extracted without changing the format |
| Transactions | `x86qw_runtime.transaction` | components, clients, CLI generation, PAKs, defaults, repair, migrations, cleanup and uninstall keep their inverses until the final logical result |
| UI | `x86qw_runtime.ui` | canonical menu, console and parser shared; manager stays the composition root of the command graph |
| Gameplay | `x86qw_runtime.gameplay` | models, catalogs and planning extracted; cycle with manager removed |
| Platform | `x86qw_runtime.platform.{display,host,locking,macos,processes,python_runtime,windows_acl}` | display, cache/variants, macOS preferences, Python, mutex, identity, shutdown and ACL belong to the runtime adapters |
| Installation session | `x86qw_runtime.session_control` | lock, ownership and conservative reclaiming are canonical |
| Supervisor | `x86qw_runtime.supervisor.{core,models,posix_guardian,readiness,sessions}` | lifecycle, readiness, journal, POSIX group gate and crash recovery centralized |
| Installed artifact | `maintenance/inventory/installer-runtime-members.json` | 56 members with origin, consumer and contract; the builder's projection is derived from the manifest and checked against the zipapp |

## Preserved contracts

- existing public commands, arguments, texts and exit codes;
- equivalent commands and planning of the current games;
- current formats of the catalog, `state.json`, receipts and migrations;
- coexisting stable and nightly, installed profiles and components;
- the standard library and Python 3.10 or later;
- temporary compatibility of the imports used by entrypoints and tests;
- the bundles, tag, catalog and public bootstraps of `0.7.1`, immutable;
- no change to gameplay, the distributed runtime or the content of the PAKs.

## Changed contracts

- ownership of reusable rules moves from the entrypoints and
  `maintenance.tools` to `x86qw_runtime`;
- maintenance and builders now import the runtime implementation;
- the zipapp no longer embeds maintenance modules and unused facades;
- a declarative manifest now declares each installed member and its consumer,
  generates the builder's projection and rejects any divergence from the ZIP;
- reading the catalog no longer happens during commands that do not use it;
- covered components and clients now expose composed transactions, which keep
  the staging until the parent state commits;
- a failure after an already promoted barrier is distinguished from a
  reversible failure, avoiding a misleading rollback of bytes whose durability
  was inconclusive.

## Consequences

A single implementation now defines each migrated contract. The tests can
compare symbol identity between facades and runtime, inspect the zipapp, prove
lazy loading and reject cycles or inverted imports through the AST.

The common transaction improves how changes compose: the parent state and the
final check can revert a completed subtransaction while its staging still
exists. That already covers CLI generation, preserved PAKs, defaults, repairs
and personal/metadata migrations. At the same time, a failure after `replace`
that does not allow proving `fsync` is reported as a committed effect,
preserved for recovery, not as a complete rollback.

The analyzed HEAD's zipapp has exactly 56 members: 44 canonical
`x86qw_runtime` modules, four top-level entrypoints/facades, two KTX
projections and six generated members. The manifest is the declarative source
the builder consumes, not a second list copied into the code. The test compares
the derived projection with the file actually produced.

## Residual risks and closing condition

This decision declares PR 6's code slice complete, but not its review. The
entrypoints remain extensive composition roots; that is allowed as long as they
do not again own HTTP, ZIP, persistence, platform or domain rules already
extracted. Execution logs are append-only and take no part in rollback; durable
mutations of payload, metadata and managed settings use plans, quarantine or
identified inverses. Finalizing the quarantine removes regular files and empty
directories after the descriptor, the identity and an exclusive rename to an
unpredictable private name on POSIX, and through the validated handle on
Windows. Links and special types are preserved with a diagnostic. Since POSIX
offers no conditional `unlink`/`rmdir` by inode, hostile code with the same
user identity stays outside the trust boundary, as already documented for
private ACLs.

At HEAD `29d76a4`, 1,190 maintenance tests passed with 37 explicit skips, and
the 5 site tests passed. They prove locally the ownership, the acyclic graph,
the 56-member zipapp and the adversarial races of rollback, quarantine, locks,
journals and personal files. The Linux, macOS and Windows matrix of the
snapshot sent to the PR is still needed and does not replace the candidate's
native smokes planned for PR 11. Issue #52 was closed by PR 62; the native
review of the 1.0 stabilization is still pending. Public release `0.7.1` is not
changed and this decision does not authorize publication.
