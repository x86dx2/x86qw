# Safe archive boundary — implementation of issue #49

**Initial baseline:** `b833ba45e08a9de644dc7368f82c905522a0a558`

**Preserved public version:** `0.7.1`

**State:** implementation, local regression and CI matrix done; PR 3 not
published

## Problems confirmed in the baseline

Before the change, the same ZIP/PK3 had different interpretations:

- `manager.py` extracted while still validating and restored modes coming from
  `external_attr`;
- `services.py` had its own stronger validator, but still extracted through an
  exclusive path;
- `gameplay.py` read maps, KTX assets and `qwprogs.dat` directly;
- component and release tools had their own readers;
- `unzip` and `Expand-Archive` ignored the Python contract in the bootstraps.

Undue acceptances were reproduced for case collisions, Unicode collisions,
`CON.txt`, a FIFO member, an `a` file + `a/b` conflict and backslashes. The risk
was a system-dependent failure, reading a semantically ambiguous archive or a
partial mutation before discovering the last invalid member.

## Implementation

The detailed contract is in
[ADR 0002](../adr/0002-fronteira-unica-de-arquivos.md). The change creates the
incremental package `x86qw_runtime.io.archive` and migrates:

- installation and verification of clients, components, base data and the
  CLI handoff;
- map discovery, the KTX inventory and gamecode reading;
- the scan and ephemeral materialization of PK3 for MVDSV;
- readers, rewrites and builders of components and releases;
- validation of the zipapp and the external bundle;
- site tests that inspect bundles;
- the Unix and PowerShell bootstraps.

The builders keep writing deterministic ZIPs with `zipfile` in `w` mode.
They are not an alternative reader: the file produced must pass
`scan_archive()`, `validate_installer_bundle()` or the equivalent versioned
historical contract before being accepted. Writing happens in staging under an
explicit root, followed by publication without replacement.

## Resulting flows

### Reading without extraction

```text
regular source
  → bounded copy to a private on-disk snapshot
  → pre-scan and full streaming over the same immutable snapshot
  → ArchivePlan bound to the source's identity and SHA-256
  → plan revalidation
  → bounded read of the requested member
```

A hostile member that was not selected also ends the flow before the requested
content is delivered.

### Payload extraction

```text
regular source
  → bounded private snapshot and source revalidation
  → full scan of the immutable bytes
  → nonexistent destination
  → private sibling staging
  → extraction + CRC/SHA-256 + fsync + canonical modes
  → final full revalidation of the source
  → exclusive rename
  → confirmation of the published identity (commit)
  → fsync of the parent directory
```

Any failure before the promotion leaves the destination absent and removes only the
staging whose identity can still be confirmed. Once the staging's identity is
confirmed at the destination, the commit is irreversible: a late failure
preserves the published directory and any personal file created in it. An
inconclusive promotion never authorizes walking or removing the public name.

### Service materialization

```text
PK3
  → full scan
  → canonical extraction into private staging
  → preflight of every conflict in the MVDSV context
  → journaled atomic copies
  → staging cleanup
```

This keeps the historical policy of never overwriting a different personal file
without keeping a decompressor inside `services.py`.

On Windows, the materialization holds handles to the ancestors without delete
sharing, refuses reparse points and records identity by volume +
`FILE_ID_128`. The file is written in exclusive staging, verified and promoted
by `MoveFileExW` without replacement. Cleanup and rollback act on the same
handle confirmed with `SetFileInformationByHandle`; they depend neither on a hardlink nor on
`DeleteFileW` by path. The portable tests use a fake API with the same
contract, and the native cases remain mandatory in the Windows job.

On POSIX, files created by the session go through an exclusive, atomic rename
into a quarantine in the same directory; there is no public sequence of
`stat` + `unlink`. The content is validated again through the open descriptor, and a
divergence restores the name without replacing concurrent content. Linux uses
`renameat2(RENAME_NOREPLACE)`, macOS uses `renameatx_np(RENAME_EXCL)` and any other
POSIX without the primitive fails closed. Cleanup hashes are bounded to the exact
size recorded in the journal; legacy journals without `expected_size` keep the
file. The boundary does not promise isolation from another process already
compromised under the same UID with a writable descriptor, a pre-opened `mmap` or direct
access to the quarantine's random name, an exclusion POSIX does not provide
portably.

### Bootstrap

The four bootstrap scripts hold the same Base64 projection of the canonical
module, checked byte for byte by the builder and by the tests. The helper is created
in a private temporary and validates the bundle's exact contract before running
`x86qw.pyz`; no call to `unzip` or `Expand-Archive` remains.

## Limits and modes

The canonical limits are 512 MiB for the compressed source file, 32 MiB for
the central directory metadata, 4,096 members, 128 MiB per member, 512 MiB
in total expanded size, depth 16, a path of 240 UTF-16 units and a maximum
compression ratio of 500:1. Stored and Deflate are the only accepted methods.

Before `zipfile` materializes the central list, a structural pre-scan bounds
the metadata bytes, walks each record and compares its real count with the
EOCD. This prevents both unbounded allocation and a bypass through an
understated count. Pre-scan and `ZipFile` work on the same private snapshot; a
concurrent source does not swap the bytes between the two steps and every read of the
source is cut off at the ceiling plus one byte. Forbidden Win32 characters and
DOS device or reparse point attributes are also refused on every
platform.

Declared directories and executables get `0755`; other files get
`0644`. ZIP modes are not restored. During staging, directories start
private and files are created exclusively with `0600`.

## Change record

| Area | Before | Corrective candidate |
|---|---|---|
| Scan | independent readers | a single `scan_archive()` |
| Plan | implicit, partial validation | immutable `ArchivePlan` |
| Reading | a selected member could ignore a hostile neighbor | the whole archive validated first |
| Extraction | write during validation | full staging before promotion |
| Modes | could come from `external_attr` | only declared `0644`/`0755` |
| Services | their own validator and extraction | canonical scan/extract + journaled materialization |
| Windows services | fallback without reversible cleanup | handles, persistent identity and exact unlink |
| Bootstrap | `unzip`/`Expand-Archive` | exact projection of the canonical boundary |
| Builders | direct write to the final name | stable snapshot, staging under an explicit root, validation and hardlink without replacement |
| Regression | prohibition by convention | static gate against parallel readers/extractors |

## Compatibility and migration

There is no state or payload migration. The 59 existing ZIP/PK3/PYZ in the
initial inventory fit the new limits and pass the scan. The installed
structure, the hashes of valid files, the commands and the gameplay are
preserved.

The installer's immutable history keeps its two real layouts: six members
between `0.1.0` and `0.1.19`, and seven from `0.1.20` on. Both require the exact
set of members, coherent external identities and the internal identity of
`x86qw.pyz`; the modern layout stays mandatory for new bundles.

The builders write to a private file under an explicit output root,
reject a symlink or reparse point in the managed parents, validate the snapshot and
publish by hardlink without replacing an existing name. A failure after the
link's creation preserves the target for inspection and prevents manifest, registry and
publication. The `latest` pointer is only created when absent, or accepted when it already
holds exactly the expected value; promoting a diverging value stays
blocked until PR 10's immutable transaction.

Archives previously tolerated only through incomplete validation now fail before
the first extraction write or visible payload. That is the only deliberately
incompatible contract.

## Current evidence

In this candidate's local validation, the maintenance suite reported
`Ran 695 tests` and `OK (skipped=15)` on both Python 3.10 and Python 3.14;
the five site tests also reported `OK`. Of the 15 local skips, fourteen
require the Windows runner and one is the opt-in network smoke. `verify`, Git LFS,
`git diff --check`, the bootstrap parsers and the Worker dry-run also
passed.

Run
[#30856293818](https://github.com/x86dx2/x86qw/actions/runs/30856293818)
successfully repeated:

- positive, negative and adversarial tests of the boundary;
- regression of the installer, services, gameplay, components, recipes,
  launchers and site;
- a scan of every distributed archive;
- the static gate without a parallel reader/extractor;
- `verify`, Git LFS, `git diff --check` and the Worker dry-run;
- the Ubuntu, macOS and Windows matrix with Python 3.10 and 3.13, including the
  native Windows cases.

All seven jobs passed. In both Windows jobs, the native cases accepted `.exe`/`.cmd`
sources, rejected reparse points and validated the real identities of
Python 3.10 and 3.13. This evidence classifies the archive contract as fully
cross-platform, but does not replace a native smoke of ezQuake, MVDSV, QTV
or QWFWD.

## Residual risks

- the private DACL on Windows was implemented in PR 4's candidate per
  [ADR 0003](../adr/0003-dacl-privada-windows.md) and validated natively on the
  Windows runners with Python 3.10 and 3.13; the runtime smoke under a standard account
  is still pending;
- trust metadata belongs to PR 9;
- transactional candidate promotion and a safe `latest` update belong
  to PR 10; this candidate fails closed when facing a diverging pointer;
- native smokes and candidate evidence belong to PRs 10 and 11;
- TAR is not extracted to the filesystem by the current flows and stays out of this
  PR's contract;
- the build-time sync of the bootstraps detects swaps, does not follow symlinks and
  publishes by atomic replace, but the standard library offers no portable CAS
  over an existing pathname; the build requires an exclusive checkout, and that
  operational guarantee will be formalized by PR 10's immutable promotion;
- `0.7.1` remains the public release and does not receive this implementation.

No version, catalog, deployed public bootstrap or published bundle was
changed by this note.
