# ADR 0001 — Bounded boundary for remote bytes

**Status:** proposed; corrective implementation under review and not published

**Date:** 2026-08-03

**Issue:** [#45](https://github.com/x86dx2/x86qw/issues/45)

## Context

The installer and the maintenance tools had independent downloaders. Some
accepted a partial body, read metadata without a limit, retried permanent HTTP
responses or promoted the destination before full validation. A mirror that
answered HTTP 200 with corrupted bytes could also prevent the fallback to an
intact mirror.

The decision must bound resources, preserve the previous destination and apply
a single transport policy without adding an external Python dependency. This
protection must not be confused with authentication of the catalog or of the
upstream.

## Decision

HTTP downloads made by the application and by the Python tools go through
`maintenance/tools/downloader.py`. The boundary exposes data contracts and two
operations: `download()` for one origin and `download_mirrors()` for equivalent
origins under a single monotonic budget.

The public operations create a private HTTPS opener and cancellation registry
per call. There is no public singleton/getter and no argument to inject
transport, clock, sleep or randomness. The zipapp embeds only the sealed
entrypoints; deterministic seams stay in private helpers of the suite.

### API contracts

| Contract | Mandatory security fields | Allowed result |
|---|---|---|
| `PinnedArtifact` | HTTPS URL, destination, expected size, expected SHA-256, maximum size, total deadline and retry policy | validated file promoted atomically |
| `BoundedMetadata` | HTTPS URL, maximum size, total deadline and retry policy | ephemeral bytes in memory or `HEAD` headers |

`PinnedArtifact` is mandatory for any remote byte that may persist, both in the
installation and in the distribution tree. `BoundedMetadata` serves the
catalog, the Hub and discovery, but it does not turn dynamic metadata into
authenticated information, nor does it authorize its consumers to promote the
response to a managed file.

A stable, nightly or nQuake discovery cannot use the SHA-256 computed during
the transfer itself as the expected identity. `update` only inherits an
already reviewed identity when path, URL and size match `dist/manifest.json`
exactly. A new candidate stays in `review_required` and must come in through
an `add` definition that declares size and SHA-256 before any persistent
download.

For a new nQuake revision, the definition must also declare an explicit
`reference` transition: a repository identical to the inventoried one, a
previous revision equal to the current state and a new revision as a full Git
hash. This stale-safe transition only changes the in-memory projection used by
the intake, must incorporate a file under the new snapshot and is only
promoted if the reviewed list rebuilds the complete consumed snapshot; `update`
gets no authority to download or promote the candidate on its own.

The definition's pin is not self-sufficient: a remote URL requires
`managed: true` and an exact match with at least one canonical authority — an
artifact of a proposed release, a source preserved in the upstream registry, a
proposed public package or a pinned nQuake reference. The namespace of
`distribution_component`, the operational consumer and the logical identity of
`package` must also match that authority. An already registered path cannot
receive new bytes or metadata in place, and installer bundles belong
exclusively to the immutable release flow. For ezQuake, the path is derived
exactly from
`clients/ezquake/<channel>/<version>/<platform>-<architecture>/<file>` and must
match those coordinates in the package. Local sources must be consumed by the
proposed BOM and are copied through a temporary file with hashing while
streaming, `fsync` and `replace`, so that hardlink staging cannot modify the
live tree before the transaction.

The retry policy, headers and label are part of each contract. Mirror
contracts are fully validated before the first connection and must have the
same type, deadline and destination identity. Artifact mirrors must also share
the expected size and SHA-256.

Catalog, manifest, release inventory, upstream registry and intake reuse the
boundary's HTTPS validator. Persistent URLs reject userinfo, fragments,
queries, spaces and control characters before an origin is registered.

## Deadline, transport and mirrors

`download()` creates an absolute deadline for one origin. That same budget
covers connection, redirects, headers, reads, attempts and retry pauses. Each
read gets on the socket only the remaining monotonic time.

DNS resolution happens in an isolated Python subprocess, with a bounded number
of addresses and bounded output. The subprocess is killed, with one attempt to
reap it, if the budget runs out. The child waits for a token on `stdin`; the
parent only releases it to `getaddrinfo` after attaching the handle to the
controller. TCP attempts use non-blocking sockets and the same absolute
deadline. During TLS and headers, the controller registers the connection; on
cancel, it ends the resolver and the pending sockets and runs `shutdown` before
closing. The same cleanup happens when the controller is interrupted. The
worker recomputes the budget right before opening the connection, so a
scheduling delay does not reuse a stale timeout. Remaining budgets under
500 ms are refused before creating a worker, resolver or socket, because they
do not reserve a large enough cancellation window. Accepted operations reserve
250 to 500 ms of their own deadline for local reaping. A response that ends
after the controller is closed by the worker and can never promote the
destination.

`subprocess.Popen` is a local call without cancellation provided by the
standard library. If creating the resolver itself blocks past the deadline,
the caller returns on time and the child stays at the gate without starting
DNS; as soon as creation returns, the canceled state kills the process without
sending the token. The daemon thread may wait only for that local return.
Removing even that wait would require an external process supervisor and is
out of scope for this incremental change.

If the operating system takes longer than the reserved window to finish
reaping an already dead child, the daemon thread may finish reaping after the
return. The public deadline stays strict and there is no new DNS or socket
after cancellation; the window is not an absolute guarantee of the OS reap
time.

`download_mirrors()` creates a single absolute deadline before the first
mirror and reuses it across all attempts and origins. Transport or integrity
failures may move on to the next mirror. Protocol, limit, storage, policy or
deadline failures are terminal and are not masked by a remote fallback. When a
`Retry-After` does not fit in that mirror's budget, the controller may move
immediately to another origin without exceeding the shared deadline.

A caller that makes several independent calls to `download()` does not
automatically get that shared budget. Historical mirror loops must be migrated
to `download_mirrors()` and stay covered by integration tests; the mere
existence of the API does not prove that every consumer uses it.

## Limits

- persistent artifact: at most 512 MiB;
- installer catalog: at most 2 MiB;
- Hub response: at most 1 MiB;
- maintenance HTTP discovery: at most 4 MiB;
- GitHub recursive tree: at most 4 MiB and 50,000 entries under a deadline
  shared between resolving the reference, the commit and reading the tree.

The catalog also rejects up front packages whose declared size exceeds the
global limit. Responses without `Content-Length` stay bounded while
streaming.

## Transport, retry and storage

- the initial, redirected and final URLs must stay on HTTPS;
- cross-origin redirects cannot forward private headers;
- intermediate 301/302/303/307/308 bodies are closed without reading and do
  not get around the byte limit;
- `Content-Length`, when present, must be single, decimal and consistent;
- SHA-256 and size are computed while streaming, without a second read;
- the expected size is exceeded by at most one byte before the interruption;
- the default is three attempts, exponential backoff from 0.5 to 8 seconds and
  20% jitter;
- only transient network errors and HTTP 408, 425, 429, 500, 502, 503 and 504
  are retried;
- `Retry-After`, in seconds or as an HTTP-date, is accepted only when it fits
  in the deadline;
- temporary files use mode `0600` on POSIX;
- validation, `flush`, `fsync` and close come before `os.replace`;
- a network, integrity or storage failure preserves the previous destination.

## Bootstraps

Before the zipapp exists, the common boundary cannot be imported. The
bootstraps keep minimal, isolated projections:

- Unix uses `curl --disable` as the first argument to ignore `.curlrc`,
  followed by `--proto '=https' --proto-redir '=https'`, a connection timeout,
  a single budget for retries and mirrors, a limit while receiving and
  validation of `Content-Length`, size and SHA-256;
- the truncation boundary of the Unix bootstrap is structural in the script,
  not in the caller: the body lives in `x86qw_install_main` and only runs on
  the last line `x86qw_install_main "$@"`; a prefix at 25%, 50%, 75% or 99%
  fails to parse and does not run the installer. The public command is
  `curl -fsS https://qw.x86.com.br/install.sh | bash`;
- PowerShell embeds a Python downloader with HTTPS, a limit, a deadline,
  `Retry-After`, a private temporary file on POSIX and atomic promotion;
- in PowerShell, connection and attempt timeouts use the full budget when the
  total deadline still leaves the later reaping margin; only the missing part
  of that margin is reserved before the limiter;
- a test requires the canonical and public copies to stay identical.

In PR 4's corrective code, the Windows bootstrap creates its working directory
with a DACL protected for the current user and `LOCAL SYSTEM` before the first
write. The decision and the conservative failure behavior are in
[ADR 0003](0003-private-windows-dacl.md). The contract was validated in the
Windows jobs with Python 3.10 and 3.13; the runtime smoke under a standard
account stays separate, and this change has not been published yet.

These projections exist only to obtain the pinned zipapp; they do not
authorize a new downloader for clients, components or product metadata.

## External boundaries and risks accepted in this ADR

- `BoundedMetadata` bounds transport and memory, but catalogs still have no
  expiration, rollback/freeze protection, key rotation or pinned root of
  trust. That contract belongs to PR 9 and
  [issue #48](https://github.com/x86dx2/x86qw/issues/48).
- GitHub references, commits, trees and release reads go through
  `BoundedMetadata`. Truncated recursive trees and entries with an invalid
  schema, type, mode, SHA-1, size or path are rejected before use. The
  remaining `gh` commands and the GitLab upload `curl` are outgoing
  publication operations, not alternative ingestion routes. That `curl` uses
  `--disable` as the first argument, plus mandatory HTTPS and disabled
  redirects, but the immutable promotion still needs to be redesigned in
  PR 10;
- the bootstrap's `curl` is the bounded projection described above;
- DACL privacy on Windows was incorporated into PR 4's corrective code by
  [ADR 0003](0003-private-windows-dacl.md) and validated by the native Windows
  checks of [issue #47](https://github.com/x86dx2/x86qw/issues/47); running
  the runtimes under a standard account stays in PR 11;
- portable tests of the boundary do not replace native network or runtime
  smokes.

These exceptions are not authorized alternative routes to install content
without a prior identity. They must be eliminated or formally absorbed by the
PRs that deal with trust metadata, release automation and ACLs before version
1.0.0.

The proposed architecture for trust metadata, still without an implementation
or keys, is in [ADR 0006](0006-tuf-trust-metadata.md).

## Consequences

A new direct Python consumer of `urlopen` or `urlretrieve` fails the static
test. HTTP boundary URLs shown in diagnostics omit credentials, path, query and
fragment; GitHub discovery accepts only a previously validated public
repository. Boundary errors are typed, the previous final destination is
preserved until promotion and mirror orchestration can tell a remote failure
from a local one. In exchange, every new consumer must declare a limit, a
deadline and, for persistent content, a cryptographic identity before
receiving bytes.

The gate walks `.py`, `.pyw`, `.bat`, shell variants, extensionless scripts and
launchers. The Python embedded in `$DownloaderSource` is also extracted and
analyzed through its AST. The scanner resolves literal concatenation, `join`,
simple argv helpers and basic dynamic imports; unresolved argv fails unless
there is an exact suppression per file, API, function and expression. It is not
a formal flow analysis: general reflection, `getattr`, `eval`, `exec`,
generated code, native extensions and libraries not yet inventoried require a
review and a new adversarial case.
