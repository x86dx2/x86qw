# Private DACL on Windows — corrective implementation

**Initial baseline:** `b746d577fab5ffd95f170feb28b8a4bd4d4ce76c`

**Preserved public version:** `0.7.1`

**Issue:** [#47](https://github.com/x86dx2/x86qw/issues/47)

**Pull request:** [#60](https://github.com/x86dx2/x86qw/pull/60)

**State:** native Win32 cases passed; not published

This note records PR 4's corrective code. It does not change the version, the
`current` catalog, the public bootstrap, a bundle, a tag or a release already published.

## Problem and risk

POSIX modes such as `0600` and exclusive creation did not keep a Windows object
from inheriting ACEs from `Users` or `Everyone`. Locks, journals, logs, sensitive
settings, staging, downloads and the bootstrap could end up readable or
writable by another local user between creation and a late hardening.

The adversarial review also reproduced races on `DELETE_CHILD`, promotion
by pathname after closing the temporary, a hostile pre-existing mutex, recovery
of legacy journals and a window in which the Windows process could run
before being assigned to the Job Object.

## Preserved contracts

- standard library, no external dependency;
- installation without an elevation request;
- reading formats 1 and 2 of locks;
- journal format 1, receipts, state and personal settings;
- password prompts and external password files, without rewriting their owner, DACL, name or
  content;
- conservative recovery and cleanup semantics;
- the existing runtimes, games, mods, maps, services and gameplay;
- the bytes, hashes, tag, release and public pointers of `0.7.1`.

## Changed contracts

- private managed objects on Windows are born with a protected DACL, limited
  to the current user and `LOCAL SYSTEM`;
- an external password file must have a private owner and DACL proven
  through the same handle used for reading;
- a new lock uses format 3 with `private_filesystem`; journal format 1 gets an
  additive extension to record the private boundary;
- a failure, ambiguity, reparse point, inheritance or additional principal stops
  the operation before the object is consumed;
- a Windows process is born suspended, joins the Job Object and only then runs.

## Implementation

`x86qw_runtime/platform/windows_acl.py` holds the Win32 adapter and declares
explicit `ctypes` signatures. Creation uses `SECURITY_ATTRIBUTES` with the canonical
descriptor from the first moment of visibility. Validation and reading stay bound
to the same handle, and leases without delete sharing block concurrent rename and
removal.

Download promotion uses `SetFileInformationByHandle` with
`FILE_RENAME_INFO`, keeping the temporary open until the replacement. The ABI uses
the one-byte `BOOLEAN` the API requires and an absolute path on the same volume.
No shell takes part in the operation.

The control plane holds the root's lease only after acquiring the lock.
Observation and claiming are serialized by `flock` on POSIX and a private global
mutex on Windows. Clean legacy objects can migrate conservatively;
interrupted or inconclusive objects are preserved or quarantined without
`kill` or `unlink` by inference.

The PowerShell bootstrap creates `WorkDir`, the helper and the bundle under the same DACL before
the first write. The supervisor starts Windows processes suspended, assigns them
to the Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and resumes the thread only
after a successful assignment.

## TDD cycle and evidence

The adversarial tests were introduced before the fixes. Run
[`30864723157`](https://github.com/x86dx2/x86qw/actions/runs/30864723157)
recorded the RED of the remaining races. Intermediate runs exposed the guard
applied to the wrong handle, an incompatible `FILE_RENAME_INFO` ABI and a relative
destination on another volume. Each case got a focused regression before the smallest
matching fix.

| Validation | Result | Evidence |
|---|---|---|
| private boundary, downloader and delete guards | 107 tests passed; 19 expected native skips outside Windows | focused local run |
| Windows / Python 3.10 | 745 maintenance tests and five site tests passed | [run 30866754435](https://github.com/x86dx2/x86qw/actions/runs/30866754435) |
| Windows / Python 3.13 | 745 maintenance tests and five site tests passed | [run 30866754435](https://github.com/x86dx2/x86qw/actions/runs/30866754435) |
| `maintenance/manage.py verify` | 745 maintenance tests and five site tests passed | full local run |
| explicit maintenance and site suites | 745 + 5 passed; 35 explicit local skips | full local run |
| Git LFS, diff and Worker | `git lfs fsck`, `git diff --check` and dry-run passed | full local run |

The Windows jobs cover creation under a parent with broad inheritance, the canonical DACL,
reparse points, an unsafe external file, bootstrap, lock, journal, log,
sensitive settings, staging, download, rename/delete guards, a hostile mutex,
legacy migration and the Job Object.

## Residual risks

- the native Win32 tests are not the same as installing and running the runtimes under
  a standard Windows account without elevation; that stays with PR 11;
- real ezQuake, MVDSV, QTV and QWFWD were not run by this PR;
- an administrator, a compromised kernel and code running as the same user
  stay outside the threat boundary;
- a filesystem without persistent ACLs or an inconclusive API fails closed;
- this evidence is of corrective code, not of an immutable release candidate.

## Next item unblocked

After a green review and full CI, PR 5 can remove the ad hoc signature of the
stable macOS client in a separate issue and branch. This document does not authorize
publication.
