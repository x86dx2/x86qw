# ADR 0002 — Single boundary for ZIP, PK3 and PYZ archives

**Status:** accepted in PR 3's corrective code; CI matrix complete and not
published

**Date:** 2026-08-03

**Issue:** [#49](https://github.com/x86dx2/x86qw/issues/49)

## Context

The installer, the services, gameplay and the maintenance tools read or
extracted ZIP/PK3 through independent paths. The guarantees diverged: some
consumers validated only the selected member, others wrote while validation was
still running, and the bootstraps delegated extraction to `unzip` or
`Expand-Archive`.

This fragmentation let the same archive be accepted by one consumer and refused
by another. It also let late failures — name collisions, a special member, a
ZIP bomb or a mismatched CRC at the end — happen after the first write. Issue
#49 requires a full preflight and a single portable interpretation for every
ZIP, PK3 and PYZ archive.

## Decision

`x86qw_runtime/io/archive.py` is the canonical stdlib-only source. It exposes:

- `ArchiveLimits`, with the product's closed limits;
- `scan_archive()`, which reads the whole archive and only then produces an
  immutable `ArchivePlan`;
- `read_archive_member()` and `read_archive_members()`, which revalidate the
  plan before reading;
- `extract_archive()`, which extracts into private staging, syncs and promotes
  the complete directory without replacing an existing destination;
- `validate_installer_bundle()`, which also validates the exact identity and
  layout of the installer's public bundle.

The module does not import `manager.py`, `services.py` or `maintenance`.
Consumers convert `ArchiveError` into their own layer's error. Deterministic
writers may keep using `zipfile` explicitly in write mode, but the artifact
they produce goes back through the canonical boundary before it is accepted.

### Preflight and plan binding

`scan_archive()` happens before the first visible extraction or payload write
and walks every member, even when the consumer wants to read only one of them.
The private, bounded snapshot is part of the preflight. The plan records the
SHA-256 of the source and of each member, the size, CRC, compression method,
type and canonical mode. A source on disk is also bound to the identity
obtained from `lstat`/the descriptor; it must be a regular file and not a
symlink.

Each operation copies the source, with a strict limit of one unit past the
ceiling, into a private temporary snapshot on disk. The source is revalidated
before and after use, while the pre-scan, `ZipFile` and member streaming see
exactly the same immutable bytes. Reading and extraction reopen the source
without following symlinks and check again the identity, SHA-256, envelope,
metadata, real size, CRC and SHA-256 of the member. Swapping, continuous growth
or mutation of the source between scan and use invalidates the plan without an
unbounded read. The ZIP envelope must take up the whole file: an executable
prefix, a suffix after the EOCD, multi-volume archives and a central directory
outside the declared region are refused.

### Name and type semantics

Internal names use `ZipInfo.orig_filename` and explicit POSIX semantics. These
are refused:

- an empty name, an absolute path, UNC, a drive, a colon or a backslash;
- an empty component, `.` or `..`;
- characters in the Unicode C categories, including controls and surrogates,
  and non-canonical paths;
- a component ending in a dot or a space;
- reserved Windows names, including with an extension and in any case:
  `CON`, `PRN`, `AUX`, `NUL`, `CLOCK$`, `CONIN$`, `CONOUT$`, `COM1`–`COM9` and
  `LPT1`–`LPT9`;
- characters forbidden in Win32 names: `<`, `>`, `"`, `|`, `?` and `*`;
- an exact duplicate, a collision after NFC + `casefold`, a prefix collision
  and a file used as an ancestor directory;
- a symlink, a special type, a type incompatible with the name and a Unix extra
  field able to represent a link;
- a DOS device or reparse point attribute and a directory attribute
  incompatible with the member's name;
- an encrypted member or a method other than Stored/Deflate.

This contract uses the most restrictive semantics common to the supported
systems. An archive valid on the source filesystem but ambiguous on Windows or
macOS is not a valid x86QW package.

### Canonical limits

| Resource | Limit |
|---|---:|
| Members | 4,096 |
| Compressed source archive | 512 MiB |
| Central directory metadata | 32 MiB |
| Uncompressed member | 128 MiB |
| Total uncompressed | 512 MiB |
| Depth | 16 components |
| Path | 240 UTF-16 units |
| Compression ratio | 500:1 per member |
| Methods | Stored and Deflate |

Issue #49's initial inventory found 59 ZIP/PK3/PYZ files in `dist/`. The
observed maximums were 102,995,518 bytes in the source, 216,156 bytes in the
central directory, 2,830 members, 22,784,504 bytes per member, 103,139,351
bytes in total, depth 5, path 54 and ratio 83.81: all well within the limits.
Before creating `ZipInfo` objects, a structural pre-scan walks the central
directory records inside the declared envelope in the snapshot, bounds their
bytes and counts the real records. That way, an underestimated count in the
EOCD or a concurrent swap of the source does not widen the memory budget. The
other limits keep being applied to both the declared metadata and the actual
streaming.

### Modes, staging and promotion

Permissions stored in `external_attr` are not restored. The product applies
only:

- `0644` to regular files;
- `0755` to directories;
- `0755` to files the consumer declared by name as executables.

Setuid, setgid, sticky and other bits of the source archive are never
propagated.

`extract_archive()` requires a destination that does not exist. Extraction
happens in a private `0700` sibling directory, created exclusively and without
following symlinks. Each file is written with `0600`, revalidated, given its
canonical mode and passed through `fsync`. Directories are synced too. Only
after full success is the staging promoted by an exclusive rename and the
parent directory synced.

Every revalidation of the source ends before promotion. A failure before the
rename removes only the staging whose identity can still be proven. Confirming
that the destination has the exact identity of the staging is the irreversible
commit point: a later durability or path stability failure preserves the
complete destination and any personal content created in it. A promotion with
an inconclusive result also never walks the public name during rollback. The
previous destination is never overwritten.

The services keep one extra step: the PK3 is extracted in full into private
staging and only then are its valid files materialized in a conflict-safe,
journaled way in the MVDSV context. A different personal file stays
preserved.

On Windows, this materialization does not rely on hardlinks or on a `stat` +
remove-by-path sequence. Each ancestor stays open without
`FILE_SHARE_DELETE`, reparse points are refused and the persisted identity uses
`VolumeSerialNumber` + `FILE_ID_128`. The file goes through exclusive staging,
`MoveFileExW` without replacement and a check through the handle. Cleanup
marks for deletion the same handle whose identity and hash were confirmed; a
diverging identity is preserved.

On POSIX, cleanup no longer runs `hash` followed by `unlink` directly on the
public name. It moves the name into a quarantine through an exclusive, atomic
rename (`renameat2(RENAME_NOREPLACE)` on Linux and `renameatx_np(RENAME_EXCL)`
on macOS), confirms the identity, computes the hash again through the
still-open descriptor and only then removes the private name. If content,
size, identity or link count diverge, the original name is restored by another
rename without replacement. POSIX systems without that primitive fail closed
and preserve the file. The journal also records the exact size; legacy records
without that field are preserved instead of causing a read up to EOF.

This protection assumes the integrity of the same user's processes. POSIX does
not offer, portably, mandatory exclusion against a process of the same UID that
already holds a writable descriptor or an `mmap` before the quarantine. That
actor has the same authority as the installation and could change or delete it
directly. The children controlled by x86QW are stopped before cleanup;
interference by name, inode swaps and detectable changes between the two reads
stay covered and preserved.

### Bootstrap before the zipapp

The bootstrap must extract the bundle before it can import the runtime
contained in `x86qw.pyz`. To avoid keeping a second extractor, the candidate's
Unix and PowerShell scripts embed a byte-for-byte Base64 projection of the
canonical source. They materialize the helper into a private temporary file
through the already validated Python and invoke its CLI with the version, the
required members and the declared executables.

The builder regenerates and validates that projection; tests require exact
equality between the four bootstraps and `x86qw_runtime/io/archive.py`.
`unzip` and `Expand-Archive` are no longer part of the flow. The projection
exists only to break the bootstrap cycle and is not a second authorized API.

The bundle keeps the exact public layout of seven members. VERSION, the two
`installer.json` documents and the identity inside `x86qw.pyz` must agree with
the requested version before the extracted CLI runs.

## Preserved contracts

- bytes, hashes and installed structure of current valid archives;
- public commands, gameplay, five games, KTX and Frogbots;
- content and strategy of the PAKs;
- personal files and existing destinations;
- bundles, tags, checksums and pointers already published, including `0.7.1`;
- the two immutable historical installer layouts: six members up to `0.1.19`
  and seven members from `0.1.20` on;
- deterministic writers, as long as the result is produced in staging,
  validated by the boundary and published without replacing an existing name.

## Changed contracts

- an invalid member in any position invalidates the whole archive, even if the
  consumer did not mean to read it;
- archives with portable ambiguity, a special type, an extra envelope, a
  disallowed compression or an exceeded limit fail before any mutation;
- executability no longer comes from the ZIP and is declared by the product;
- canonical extraction requires a destination that does not exist and the
  promotion of the complete directory;
- bootstraps no longer depend on external extractors;
- builders refuse symlink/reparse parents, concurrent targets and the automatic
  promotion of a diverging `latest` pointer.

## Consequences and residual risks

The review surface for reading and extraction becomes a single module, and a
static gate prevents reintroducing `extract`, `extractall`, `testzip`,
`is_zipfile`, `unzip`, `Expand-Archive` or reading through `ZipFile` outside
the boundary. The exception is explicit writing in `w` mode, followed by a
scan.

These stay outside this decision:

- the private DACL policy of staging and Windows temporary files, defined
  separately in [ADR 0003](0003-private-windows-dacl.md) and validated natively
  in [issue #47](https://github.com/x86dx2/x86qw/issues/47);
- authentication, expiration, rollback and freeze of metadata, handled in PR 9
  and [issue #48](https://github.com/x86dx2/x86qw/issues/48);
- native runtime evidence and smokes, which portable archive tests do not
  replace;
- transactional promotion of the candidate, including a deliberate swap of
  `latest`, handled in PR 10 and
  [issue #51](https://github.com/x86dx2/x86qw/issues/51);
- TAR: there is no TAR extraction to the filesystem in the flows this PR
  covers. If a future consumer needs it, it must come in through its own
  contract before first use, not as an exception to the ZIP/PK3/PYZ boundary.

Public version `0.7.1` does not contain this decision. Promoting the candidate
requires a PR, a green matrix and a separate release step; this ADR does not
authorize publication.
