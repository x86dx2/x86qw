# ADR 0003 — Private DACL on Windows

- **Status:** accepted in PR 4's corrective code; native Win32 cases passed on
  Windows with Python 3.10 and 3.13; not published
- **Date:** 2026-08-03
- **Issue:** [#47](https://github.com/x86dx2/x86qw/issues/47)

## Context

The POSIX modes `0600` and `0700` do not define an equivalent access policy on
Windows. An exclusive creation through `mkstemp`, `O_EXCL` or `CreateFileW`
prevents a name collision, but it can still inherit from its directory a DACL
that grants access to `Users`, `Everyone` or another local principal.

This difference affects the `.x86qw/` control plane, locks, journals,
background logs, stop requests, temporary settings that may hold passwords,
download temporaries and the files the bootstrap materializes before the
zipapp exists. An external file given through `--password-file` must also be
proven private before it is read.

The covered risk is reading or changing by another ordinary local user while
the object is created, used or recovered. The contract does not try to protect
data from code already running as the same user, nor from an administrator who
uses native privileges to take ownership or bypass the DACL.

## Decision

### Canonical DACL

A private object managed by x86QW on Windows uses:

- an owner equal to the current user on new creations; a pre-existing managed
  object may keep the `LOCAL SYSTEM` owner when its identity is proven;
- a protected DACL, without inheritance from the parent directory;
- exactly one `Allow/Full Control` ACE for the current user's SID;
- exactly one `Allow/Full Control` ACE for `LOCAL SYSTEM` (`S-1-5-18`);
- `Object Inherit` and `Container Inherit` inheritance only on directories;
- no explicit ACE for `Administrators`, `Users`, `Everyone` or any other
  principal.

The implementation does not ask for elevation. The current user owns the new
objects and `LOCAL SYSTEM` stays authorized for compatibility with normal
system operations. End-to-end execution with a standard user token still has
to be proven in PR 11.

### Creation before the first write

Sensitive files and directories must be born with the canonical DACL. Applying
the protection only after creating or writing the object would leave an
exposure window. The Windows boundary uses `SECURITY_ATTRIBUTES` in
`CreateFileW` and `CreateDirectoryW`, keeps handles open during validation and
rejects reparse points along the path.

Pre-existing managed objects may be hardened when found, as long as identity
and owner are proven. The implementation does not do a recursive, inferential
migration of arbitrary files in the installation.

### Managed scope

The contract applies to the private objects x86QW creates or controls,
including:

- `.x86qw/`, `.x86qw/sessions/` and session directories;
- the active lock, the reclaimed lock, journals and stop requests;
- background process logs;
- ephemeral settings and other files that hold secrets;
- temporaries and staging of the downloader, the installer and the
  ZIP/PK3/PYZ boundary;
- the PowerShell bootstrap's working directory and its files before
  promotion.

The bootstrap creates the private working directory before materializing the
helper, the bundle or extracted content. The implementation before the zipapp
is a minimal projection of the same descriptor; it does not create a second
access policy.

The named mutex that serializes lock reclaiming uses the Windows global
namespace to span different sessions and gets an equivalent ACL, limited to the
current user and `LOCAL SYSTEM`. If it cannot be created or acquired, the
operation fails closed and does not try an alternative concurrent path.

### External files with secrets

A file pointed to by the user is outside x86QW's ownership. The CLI:

- opens and validates the file without following symlinks or reparse points;
- requires a filesystem with persistent ACLs, a protected DACL and an owner
  equal to the current user or `LOCAL SYSTEM`;
- allows ACEs only for the current user and `LOCAL SYSTEM`, requires that the
  current user can read and rejects duplicates or inheritance flags;
- bounds the read and does it through the same validated handle;
- never changes the DACL of, moves, deletes or rewrites the external file.

A failure or an inconclusive result while reading the DACL stops the operation
before the secret is used. Messages and journals do not record its content.

### Conservative failure

A filesystem without proven persistent ACL support, an API error, an
unexpected owner, a reparse point, an unknown ACE, active inheritance or a
difference between the expected and observed DACL produce an error. The
implementation keeps a private leftover whose identity can no longer be proven
instead of deleting by pathname and risking the removal of a concurrent
replacement.

## Preserved contracts

- the install contract without administrative privileges, still subject to the
  standard user smoke in PR 11;
- public paths and names of the `.x86qw/` control plane;
- read compatibility of lock formats 1 and 2; new lock format 3 with
  `private_filesystem`; journal format 1 with an additive extension; receipt
  and state unchanged;
- cleanup semantics and conservative recovery;
- prompt options and password files;
- bundles, hashes, tags and pointers already published, including `0.7.1`.

## Changed contracts

- on Windows, a managed private object can only be used after the canonical
  DACL has been created or proven;
- external password files must satisfy the private policy before they are
  read;
- an ACL failure or ambiguity becomes terminal, even if the file would have
  been accepted by a POSIX check that is a no-op on Windows;
- the PowerShell bootstrap's temporary directory is born private before the
  first write.

## Validation

The portable tests cover descriptor generation, routing through the private
boundary, conservative failure and no mutation of external files. PR 4's
native Windows jobs, on Python 3.10 and 3.13, proved:

- creation under a parent that grants inheritable access to `Users` or
  `Everyone`;
- a protected DACL with only the current user and `LOCAL SYSTEM`;
- no creation window with a broad ACL;
- rejection of an external file with broad access;
- bootstrap, temporaries, locks, journals, logs and sensitive settings;
- a bootstrap that does not ask for administrative elevation.

The evidence is recorded in run
[`30866754435`](https://github.com/x86dx2/x86qw/actions/runs/30866754435).
This does not prove the runtime smoke under a standard account without
elevation, which remains mandatory in PR 11. This ADR does not authorize
publication.

## Consequences and residual risks

The access policy becomes explicit, reviewable and single across runtime,
maintenance and bootstrap. In exchange, volumes without persistent ACLs and
environments that prevent inspecting them fail closed, even when the operation
could work without proven privacy.

These stay outside this decision: defense against the same user, against an
administrator or a compromised kernel, secure physical erasure of secrets and
authentication/expiration of remote metadata. Public release `0.7.1` does not
contain this decision; a future `0.7.2` requires a separate PR, green checks
and an immutable promotion.
