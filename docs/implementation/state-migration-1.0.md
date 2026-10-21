# State migration to 1.0

This document records the migration contract that belongs to the runtime. It
does not publish a version nor authorize changing game content.

## Supported path

The canonical destination is `1.0.0`. The boundary accepts the `0.7.x` family; the
public fixtures `0.7.0` through `0.7.13` are backed by the exact ZIPs
of the corresponding public releases and carry the origin of the tag, the
commit, the file, the size and the SHA-256 in `manifest.json`. Each fixture
keeps the ZIP file and a `bundle/` tree extracted by the canonical archive
boundary. The legacy metadata layout stays deterministic and includes no
PAKs, installed runtime or real personal data; the smokes write temporary
payload bytes only to prove preservation.

There is no public x86QW `0.8.x` or `0.9.x` release in this repository. Those
directories appear only as `prospective` contract fixtures, marked
`synthetic-contract-only`, and the plan blocks them with `prospective-source`.
As soon as one of those lines exists, the tag's manifest and fixture must
be added before enabling the migration; a manually created tree
is not release evidence. The decision to publish real 0.8/0.9 releases or
formally narrow acceptance to the published versions is a product approval,
not an inference of this code.

Each public manifest declares `source_tag`, `source_commit`, `source_path`,
`source_version`, `payload_kind`, `layout` and `source_archive` with `url`,
`path`, `size`, `sha256` and `extracted_path`. The test compares the local file
with the closed digest and validates each materialized member against the `ArchivePlan`;
no network is needed during the normal suite. The prospective manifests declare
the family, `payload_kind: synthetic-contract-only`,
`public_release_available: false` and the reason for the block. The fixture
test compares the public fields with the checkout's real tag and `VERSION`.

## State and receipts

`x86qw_runtime.state` still accepts the persisted formats 1 and 2. Reading
is pure and bounded; a migration plan normalizes the semantics of both
formats, including legacy IDs, fingerprint and historical aliases. The optional marker
`installation_version` identifies the contract version without invalidating historical
states that did not have it. Booleans are not accepted as format
numbers.

`x86qw_runtime.receipts` keeps the historical codecs and adds
`inspect_receipt`, `receipt_sha256` and `validate_receipt_inventory`. A file
is considered managed only when the receipt, the inventory, the identity and
the exact hash of the pair are valid. A file name alone never proves ownership.

## Plan and execution

`plan_migration(root, source_version=..., target_version="1.0.0")` does not write.
The optional `source_version` argument only serves fixtures without historical
metadata; when a valid CLI receipt or state marker exists, it is
compared and any divergence blocks the plan. A `1.0.x` state marker
still accepts the old argument so that idempotent replanning of an
already migrated installation is not blocked.
The result is a `MigrationPlan` with operations, preservations, snapshot and
conflicts. `MigrationPhase` exposes the phases `preflight`, `stage`, `verify`,
`commit` and `finalize`. `migrate_installation(..., dry_run=True)` is the shortcut to
get the same plan.

During execution, the bytes are copied to private staging, verified by
size and SHA-256, promoted by an atomic write that does not replace an unknown
destination, and only then are the legacy names collected. A failure or crash in
any phase raises `MigrationExecutionError` and restores the previous bytes
when possible. The `MigrationResult` keeps the inverse until the final verification and
offers `rollback()`. A destination with matching bytes, but without a pending ownership
journal, is still a collision: equal bytes do not authenticate a
previous run. A non-dry-run execution recovers the pending journal under the
exclusive lock before rebuilding the plan.

Commit and cleanup are distinct boundaries: once the destinations have been
promoted and the legacy names finalized, the journal persists
`cleanup-pending`. A failure to remove the private tree neither reverts bytes already
committed nor claims `rolled_back`; the pending state stays visible for a
resumption that only removes authenticated staging/backup. Swapping the tree for a
symlink, a directory or bytes from another origin blocks that cleanup and preserves the
material for inspection. The resumption uses a closed allowlist derived from the
journal (`journal.json`, the declared backups and the numbered stages), checks
hash/size and captures the identities of the leaves and directories before
removing. Extra files, directories, symlinks or paths are never discovered
recursively nor deleted; an identity swap between validation and
removal fails closed. If a failure leaves part of the tree removed, the journal
is still accepted so that the next attempt only finalizes the authenticated
objects that still exist.

The legacy layout of component receipts (`.x86qw/<id>.receipt` and
`.inventory`), of the CLI (`.x86qw/cli.receipt`) and of the ezQuake clients
(`.x86qw/ezquake-<platform>-<channel>.receipt`) is converted to the contextual
paths. Stable and nightly are independent operations. The legacy pair
`nquake-ktx` is normalized to `ktx`: the receipt's internal identity, the
inventory path `qw/ktx.pk3`, the binding hash and the contextual destination
must agree. An `nquake-ktx` and `ktx` pair is only deduplicated when the normalized
bytes are equal; a divergence blocks before the first write. The legacy
aggregate pair `.x86qw/nquake.receipt` + `.x86qw/nquake.inventory` is not
moved, but it is validated (hash, format, ownership and portable paths) before
being preserved; a partial or corrupted pair blocks. The retired component
`nquake-sounds` is taken out of the active state, but its validated pair stays at the
legacy path for diagnosis and explicit disposal;
`MigrationPlan.retired_components` records that condition.

## Preservation and blocks

The plan never touches PAKs, runtimes, personal settings, demos, logs or
files without proven ownership. A personal collision at any managed
destination, a symlink/reparse point (including the canonical root
`.x86qw/components`), a partial metadata pair, a corrupted state, a diverging
hash or a concurrent change blocks the plan before the first write.
There is no inference by name, automatic removal, downgrade or cleanup of personal
data.

## Validation run

The contract is exercised by `maintenance/tests/test_migration_1_0.py`, including
byte-for-byte public fixtures, coexisting stable/nightly, a custom profile, a dry-run without
writes, a personal collision, a symlink, a corrupted receipt, a full disk, a crash in
each phase, byte-for-byte rollback, normalized legacy pairs, a collision of identical
bytes without a journal, crash recovery through the CLI and idempotent re-execution.

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest maintenance.tests.test_migration_1_0
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest maintenance.tests.test_migration_manager
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest maintenance.tests.test_runtime_state maintenance.tests.test_runtime_receipts
```

The CLI command already acquires the maintenance lock before recovering journals and
rebuilding the plan in a real run; `--dry-run` still runs without the lock and without
recovery. Native smokes and real `0.8.x`/`0.9.x` fixtures remain
separate gates; this contract does not claim them.
