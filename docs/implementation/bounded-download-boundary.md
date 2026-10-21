# Bounded download boundary — corrective implementation

**Initial baseline:** `afb4f666095e37fe262b87b49339e18d25738522`

**Preserved public version:** `0.7.1`

**State:** corrective implementation under review; not published

This note records the implementation of
[issue #45](https://github.com/x86dx2/x86qw/issues/45). It does not change the version,
the `current` catalog, a bundle, a tag or a release already published.

## Reproduced defects

- `Content-Length: 100` with a three-byte body was accepted;
- a partial connection could leave the destination incomplete;
- a 2 MiB catalog response was read whole, without a limit;
- HTTP 404 was retried as a temporary failure;
- a corrupted HTTP 200 on the first mirror prevented a sound fallback in the bootstrap;
- maintenance could compute the SHA-256 of a stable, nightly or
  nQuake candidate during the transfer itself and promote it as the reference;
- `add --dry-run` returned before the semantic validation and printed the raw
  definition, allowing success without pins and exposing URL credentials;
- the controller's timeout could end while DNS or the header read
  still held residual work;
- runtime and maintenance had direct, diverging implementations of
  `urlopen`.

## Implementation

`maintenance/tools/downloader.py` became the Python boundary for HTTP
input. The public API of this unit is made of:

- `download(contract)`: one URL and a total deadline;
- `download_mirrors(contracts)`: equivalent URLs, validated before the network and
  bound to the same absolute deadline;
- typed errors for policy, redirect, protocol, limit, integrity,
  deadline, storage, permanent HTTP and transient failure.

The opener and its socket registry are private and new in each operation. There
is no public getter nor an argument to inject transport, clock, wait or
randomness into the production entrypoints; those seams live in private helpers
used only by the focused suite.

The contracts are:

| Contract | Use | Identity | Persistence |
|---|---|---|---|
| `PinnedArtifact` | bundle, client, component and declared remote file | size and SHA-256 known beforehand | atomic promotion |
| `BoundedMetadata` | catalog, Hub and discovery | limit and TLS; no versioned identity | memory or `HEAD` headers |

`PinnedArtifact` requires an HTTPS URL, a destination, the expected size, the expected SHA-256,
a maximum size, a deadline and a retry policy. It is the only contract authorized
to write a persistent remote result. Dynamic metadata always requires a
limit and a deadline, stays in memory and cannot be presented as authenticated
content.

The URLs persisted in the catalog, the manifest, the release inventory, the upstream
registry and the intake go through the same strict HTTPS parser before being
accepted: userinfo, fragments, queries, spaces and control characters are rejected without
echoing credentials in the exception.

Current discoveries only receive the already reviewed SHA-256 from `dist/manifest.json`
when path, URL and size match exactly. New candidates are
blocked before confirmation, staging, download or mutation and require a pinned
intake through `maintenance/manage.py add`.

The intake is also strict in `add --dry-run` mode: it validates HTTPS, size,
SHA-256, a portable destination, a local source without an intermediate symlink, case/Unicode
collisions, owner, consumer, package, the proposed inventories and the
match with the public package, but it downloads no bytes, creates no staging and does not
change the tree. Persistent URLs take no query. The summary uses only
redacted origins; the JSON definition is never echoed.

Every remote file of the intake declares `managed: true` and is bound
to exactly at least one authority: an artifact of the proposed release, a source
preserved in the upstream registry, a proposed public package or a pinned nQuake
reference. The `distribution_component` namespace, the consumer and the
logical identity of `package` must agree with that authority. Entries
already in the manifest are immutable at the same path, owner,
consumer and package included; a new installer bundle can only be born in its own
release flow. ezQuake packages must use exactly
`clients/ezquake/<channel>/<version>/<platform>-<architecture>/<file>` and their
component, channel, version, platform and architecture coordinates are validated
against the proposed catalog. Local sources can only take an exact `project_*`
of the proposed BOM. Their copy uses an exclusive temporary, computes the hash while
streaming against the already approved plan, runs `flush`/`fsync` and promotes with
`replace`, breaking the staging hardlink without writing to the live `dist/`. Mode
`0600` protects that temporary on POSIX. In PR 4's corrective candidate,
Windows temporaries go through the private boundary of
[ADR 0003](../adr/0003-dacl-privada-windows.md), validated in the Windows jobs with
Python 3.10 and 3.13.

A new revision of the nQuake snapshot can only be authorized by the intake itself.
The definition declares `reference` with `repository`, `revision` and `components`
(each component moved and the revision it leaves); the repository must be
exactly the one already inventoried, each previous revision must match the one
pinned by the component, and all of them are full Git hashes. The transition is applied
only to the in-memory inventories before validation, must incorporate the
files of each moved component under the new revision and can only be promoted
when the reviewed list rebuilds the files they consume. The other
components keep their revisions. It does not make `update` download a candidate
not yet reviewed.

Migrated consumers:

- `dist/installer/bin/manager.py`;
- `maintenance/manage.py`;
- `maintenance/tools/build_package.py`;
- `maintenance/tools/check_component_updates.py`;
- `maintenance/tools/public_upstreams.py`;
- `maintenance/tools/publish_gitlab_packages.py`;
- `maintenance/tools/sync_distribution.py`.

The builder embeds the module in `x86qw.pyz`. The canonical and public bootstraps
apply a minimal projection before the zipapp exists and revalidate size and
SHA-256 inside the mirror loop. The public Unix entrypoint uses `umask 077`,
`pipefail` and `head -c 262145`, checks the status and refuses more than 262144 bytes
before running the script; neither a prefix produced by a failed pipeline nor
an excessive stream without `Content-Length` reaches Bash.

## Deadline and fallback

The deadline is monotonic and absolute. In `download()`, it covers connection,
redirects, headers, streaming, retries and their pauses. In
`download_mirrors()`, the final instant is computed once before the first
mirror and shared by every origin and attempt.

The worker recomputes the budget right before opening. DNS is resolved
by an isolated subprocess, with bounded output and `kill` followed by reaping on
timeout. The resolver starts blocked on a `stdin` gate and only calls
`getaddrinfo` after the controller registers the handle and sends the start
token; TCP connections are non-blocking and share the absolute deadline.
If the deadline expires in TLS or headers, the controller runs `shutdown` on the registered
socket before closing it. The resolver and pending sockets belong to the same
controller and are also closed on `KeyboardInterrupt`; the budget reserves
250 to 500 ms, inside the total deadline, as a local reaping window.
Late responses are closed and never promote the temporary. The public API does not
accept alternative transport, opener, clock, wait or randomness
callbacks, and each operation builds its own private transport. Observability
callbacks for progress and retry stay in the public contract.

If less than 500 ms remain for the opening phase, it is refused before
creating a thread, subprocess or socket. Production deadlines are 60 to 300
seconds; the floor keeps an impractical budget from starting remote activity
without reserving a window for cancellation and reaping. It does not promise that a
pathologically slow reap by the operating system ends before the return.

The standard library offers no cancellation of a local call to
`subprocess.Popen` that is blocked inside the operating system. In that
exceptional case, the caller still returns at the deadline and the controller keeps
the late process from being attached: when `Popen` finally returns, the child is
terminated before resolving DNS or opening a socket. The daemon thread may remain
only until that local creation returns. This is a local availability
limitation; guaranteeing that thread's absolute absence would require an external supervisor,
not an arbitrary increase of the timeout.

Likewise, after `kill`, the operating system may take longer than the
reserved window to return the child's status. The caller keeps the hard
deadline; in that exceptional interval the daemon thread may finish reaping after
the return, but the DNS token is not sent and the cancellation has already interrupted
sockets and remote activity.

All the contracts of a set of mirrors are validated before the first
connection. Type, deadline, destination and identity must be compatible. Remote
transport or integrity failures allow fallback; protocol,
limit, storage, policy or deadline failures end the operation. A
`Retry-After` that does not fit the current mirror's budget does not block the attempt
at an equivalent origin within the same deadline.

The shared API does not automatically fix historical loops that still
call `download()` once per URL. Migrating the consumers to
`download_mirrors()` must be proven by integration tests and by the gate
that forbids parallel HTTP input routes.

## Limits and policy

- persistent artifacts: 512 MiB;
- catalog: 2 MiB;
- Hub: 1 MiB;
- HTTP discovery: 4 MiB;
- recursive GitHub tree: 4 MiB, 50,000 entries and a 60-second
  deadline shared with resolving the reference and the commit;
- three attempts by default;
- retry only for transient network errors and HTTP 408, 425, 429, 500, 502, 503 and 504;
- exponential backoff from 0.5 to 8 seconds, 20% jitter and `Retry-After` in
  seconds or HTTP-date;
- total monotonic deadline per contract or set of mirrors;
- timeout also applied to the read socket;
- `0600` temporary on POSIX and the order `flush` → `fsync` → close →
  `os.replace`.

## Adversarial tests

The dedicated suite covers:

- a never-ending response and a catalog above the limit, with and without `Content-Length`;
- missing, invalid, duplicated or diverging `Content-Length`;
- partial response, exceeded size and wrong SHA-256;
- connection/header timeout, a blocking read with `socketpair` and a deadline
  between chunks;
- a blocked DNS resolver receiving `kill` and being reaped on the normal path,
  with bounded output and candidates;
- the resolver's start gate without a token when `Popen` only returns the handle after
  the deadline, preventing late DNS;
- full download and the PowerShell bootstrap cancelling resolver, sockets and
  late responses with no residual remote activity;
- `KeyboardInterrupt` during DNS with no orphan subprocess;
- header cancellation proven with a real `shutdown` on `socketpair`;
- expiration between the worker's creation and the controller's wait, with
  mandatory cancellation of the connection;
- scheduling delay without reusing a stale connection budget;
- nominal 10 ms and 500 ms budgets rejected before starting transport
  when the remaining budget is already below the floor;
- one deadline shared between retries and mirrors;
- redirects to HTTP and private headers across origins;
- intermediate bodies of the five redirect statuses without unbounded reads and
  HTTP 308 compatibility with Python 3.10;
- a matrix of transient and permanent HTTP;
- `add --dry-run` intake without network or mutation, with pins, ownership and
  persistent URLs without query, redacted even on adversarial inputs;
- a static gate covering Python embedded in PowerShell, `.bat`, shell variants,
  shebangs, argv helpers and simple dynamic imports;
- numeric, HTTP-date, invalid and larger-than-deadline `Retry-After`;
- deterministic backoff and jitter;
- `EACCES`, `ENOSPC`, short writes and close, `flush`, `fsync` and
  `replace` failures;
- promotion order and preservation of the destination and the temporaries on failures.

The bootstraps have their own regressions for a corrupted HTTP 200 on the first
mirror, partial transfer and `IncompleteRead`, diverging `Content-Length`,
a shared budget, a real connection timeout without consuming the total
cleanup margin ahead of time, the Unix command's limit even without `Content-Length`,
a failed pipeline prefix never executed, redirects without draining the body,
HTTPS flags, pinned size, preservation of the PowerShell session and equivalence
between the canonical and public copies.

## Preserved contracts

- Python standard library;
- current GitHub and GitLab mirrors;
- progress and messages in Portuguese in the installer;
- bundles and releases already published stay immutable;
- fallback between mirrors;
- no runtime or gameplay change.

## Remaining limitations

Bounding bytes and requiring HTTPS does not authenticate the catalog, dynamic stable or nightly,
nor prevent rollback or freeze. A candidate without a previously reviewed digest is
only discovered: `update` neither downloads nor promotes its bytes. Versioned
authentication belongs to PR 9 and issue #48.

References, commits, recursive trees and GitHub release reads use
`BoundedMetadata`; truncated trees and entries with an unsafe schema are
rejected whole. `gh` and the remaining `curl` publish bytes to the
mirrors. The GitLab upload `curl` starts with `--disable`, requires HTTPS and does not
follow redirects; replacing it with immutable promotion belongs to PR 10.
The bootstrap projections are still needed before the zipapp exists;
Unix passes `--disable` as `curl`'s first argument to ignore an inherited
`.curlrc` configuration before restricting origin and redirects to HTTPS.

The static scanner inventories Python, GUI Python (`.pyw`), `.bat`, PowerShell,
shell variants and shebang scripts. The Python held in
`$DownloaderSource` in the bootstraps is also extracted and analyzed by AST. The gate
resolves concatenation/`join`, simple helpers with parameters, argv assignments
and basic dynamic imports; unresolved argv fails. Allowlists are limited
to explicit Python functions, exact dispatch expressions or line patterns
with a fixed count in the bootstraps.

It deliberately does not interpret arbitrary data flow, reflection through
`getattr`, `eval`/`exec`, generated code, native extensions or network libraries
not yet inventoried. Changes of that kind still require human review and a
new adversarial case in the gate; the test must not be described as formal proof of the
absence of any possible communication. External publication operations
also do not become consumers of the HTTP API because of the scanner. The code
[ADR 0003](../adr/0003-dacl-privada-windows.md). The native matrix of
[issue #47](https://github.com/x86dx2/x86qw/issues/47) proved the control on
Windows with Python 3.10 and 3.13; it still does not replace native smokes of the
clients, services, standard account or public transport.
An exclusive `mkstemp` and portable tests replace neither that control nor the
native smokes of the clients, services or public transport.

## Validation

This note describes corrective code not yet published. The implementation was
validated with 565 maintenance tests and five site tests; the eight local
skips are seven checks exclusive to the Windows runner and one opt-in network
smoke. The PR matrix passed on Ubuntu, macOS and Windows with Python 3.10 and 3.13.
The PR evidence records separately:

- the dedicated downloader suite;
- the full maintenance and site suite;
- `maintenance/manage.py verify`, `git diff --check` and `git lfs fsck`;
- the Worker dry-run;
- seven green jobs: the quality gate and Ubuntu, macOS and Windows with Python 3.10
  and 3.13;
- behavioral tests of both bootstraps.

None of these tests is presented as a native smoke of the runtimes. Version
`0.7.1`, its bundles and its public pointers stay immutable while
this implementation is under review.
