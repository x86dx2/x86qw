# ADR 0006 — Catalog authentication with TUF

- **Status:** approved and implemented in the `0.7.5` hotfix candidate
- **Date:** 2026-08-06
- **Issue:** [#48](https://github.com/x86dx2/x86qw/issues/48)
- **Analyzed baseline:** `3bbc7a01faf8d472c5ccbab9233e05e9abadc379`
- **Implementation:** E2 merged in PR #69; production root and metadata come in
  through the `0.7.5` hotfix, under ADR 0007's solo-maintainer waiver
- **Owner-only amendment (2026-08-16):** maximum `timestamp` expiration = 7 days
  while the audience is one maintainer and `external-public=NO-GO`. The 24-hour
  ceiling applies again before any public catalog announcement.
- **Owner-only amendment (2026-08-27):** `snapshot` expiration = 90 days and
  `timestamp` = 30 days, under the same owner-only regime. The previous
  amendment left both roles expiring at the same instant, which defeated the
  project's only protected renewal flow: `tuf-timestamp-publish` can only change
  `metadata/timestamp.json`, and a renewed timestamp keeps pointing to an
  already expired snapshot. From here on, `snapshot` outlives `timestamp` by
  construction. The 7-day and 24-hour ceilings apply again before any public
  catalog announcement, along with the custody separation in the table below;
  INC-001 of 2026-08-27 is the evidence that prompted the amendment.

## Context

The current public catalog uses HTTPS, records size and SHA-256 of the payloads
and keeps multiple mirrors. These controls detect corruption of an artifact
after the catalog has already been accepted, but they do not authenticate the
catalog itself. An attacker who controls the origin or a mirror can present a
malicious catalog and payload consistent with each other, replay old metadata
or keep clients frozen on a version still valid for them.

The CLI also has fallbacks to files on mutable `main` branches. A branch is not
a release identity and cannot take part in the new trust boundary.

This E1 decides only architecture, custody and operation. It does not create
keys, metadata, fixture roles, installed dependencies, verification code,
workflows, releases or publication. In particular, no material from the
checkpoint expiring on `2026-08-08` is promoted or used as a root of trust.

## Decision

x86QW will adopt The Update Framework (TUF), following specification 1.0, with
consistent snapshots. No equivalent protocol or in-house cryptographic verifier
will be created.

### Library and primitives

- the client will use `tuf.ngclient.Updater` from `python-tuf` 7.0.0 to run the
  root → timestamp → snapshot → targets flow and validate targets;
- preparing and signing the repository will use the metadata APIs of
  `python-tuf` and `securesystemslib[crypto]`, in an isolated ceremony
  environment;
- E2 will pin versions and hashes of every dependency, record licenses and an
  SBOM and package the client without depending on `pip` or a global package on
  the host;
- every role will use Ed25519; payloads and references between metadata will
  use SHA-256;
- serialization, key IDs, counting distinct signatures and threshold
  verification belong to the libraries, not to x86QW code;
- in-house RSA-PSS, in-house canonicalization and a fallback to a "signed hash"
  outside TUF are forbidden.

The exact library versions must be reconfirmed when E2 opens. A change of
major version, algorithm or format requires a new approval of this ADR.

### Roles, thresholds and custody

The public target column is the final policy. The owner-only column is what
applies while `external-public` is NO-GO, following the amendments recorded at
the top.

| Role | Authorized keys | Threshold | Custody | Public target | Owner-only in force |
|---|---:|---:|---|---:|---:|
| root | 3 | 2 | three distinct custodians; offline, non-exportable keys | 365 days | 365 days |
| targets | 3 | 2 | three release authorities; offline use and human approval | 90 days | 90 days |
| snapshot | 2 | 1 | isolated online signer; one active key and one sealed spare | 7 days | 90 days |
| timestamp | 2 | 1 | isolated online signer; one active key and one sealed spare | 24 hours | 30 days |

The `snapshot` expiration is always strictly greater than the `timestamp` one,
in both columns. Making them equal removes the ability to recover the chain
through the protected flow, and the test
`test_initializes_root_and_generates_refreshable_catalog_repository` locks that
order.

Each key belongs to a single role. No private key goes into Git, the bundle,
logs, CI artifacts or secrets of pull request jobs. Root and targets do not
live on runners nor with the provider hosting the repository. People may hold
several functions only if two independent human custodians remain for any
2-of-3 threshold; the same person never provides two signatures for the same
threshold.

Before the first ceremony, the security owner must record outside the
repository: the custodian of each key ID, the physical medium, the location,
recovery access and the substitute. The repository gets only public keys,
fingerprints and evidence without secrets.

### Operational windows

- in owner-only mode, `timestamp` expires in 30 days and is renewed before 7
  days remain; a critical alert starts when 72 hours remain. The alert window
  must comfortably exceed the worst interval observed between monitor runs,
  which on 2026-08-27 was 10.25 hours; the previous 6-hour window was shorter
  than that interval and therefore did not warn. The 24-hour ceiling and
  renewal at most every 12 hours apply again before a public catalog is
  authorized, to bound the online key's freeze;
- in owner-only mode, `snapshot` expires in 90 days. It is renewed on every
  targets change and, without a change, before 14 days remain. It never expires
  before `timestamp`, so that an isolated timestamp renewal can recover the
  chain without a full ceremony;
- `targets` is renewed on every promotion and, without a promotion, before 30
  days remain;
- `root` is reviewed quarterly and renewed before 120 days remain;
- the expiration date is always computed from the moment of signing, never
  copied from earlier metadata;
- a client pins the clock at the start of each refresh, as TUF requires.
  Expired metadata ends the update; remote content is not promoted.

Expiration blocks installing, updating or repairing from the network. It does
not prevent running an already validated local installation and does not
authorize going back to an unsigned catalog.

### Endpoints and signed objects

The canonical origins will be versioned under:

```text
https://qw.x86.com.br/api/v1/trust/metadata/
https://qw.x86.com.br/api/v1/trust/targets/
```

Approved mirrors expose the same paths and bytes. The layout is TUF's
consistent snapshot layout:

```text
metadata/<N>.root.json
metadata/timestamp.json
metadata/<N>.snapshot.json
metadata/<N>.targets.json
targets/catalog/<sha256>.catalog.json
```

Every root version stays available. `timestamp.json` is the only mutable
"current" pointer: it is signed by the timestamp role and references a
versioned, immutable snapshot by version, size and hash. There will be no
symlink, `latest` file or parallel `current` pointer in the trust boundary.

The TUF snapshot is signed by the snapshot role and pins the version and hash
of `targets.json`. The targets role authenticates the catalog as a TUF target
by size and SHA-256. The domain field `current` that already exists inside the
catalog keeps indicating the installer's current version, but it is only used
after the whole catalog has been authenticated. So both the snapshot and the
current decision are covered by the signed chain without a cryptographic
envelope invented by the project.

Payload URLs recorded in the catalog keep using multiple mirrors. The payload
keeps its current verification by size and SHA-256, now anchored in an
authenticated catalog. A refresh cycle uses a single metadata origin; if it
fails, the cycle is discarded before trying another mirror. Metadata from
different origins is never mixed in the same cycle.

### Trusted client state

The first approved public root is embedded as bytes in the CLI bundle and
passed in `Updater`'s `bootstrap` argument; a writable cache is never a root of
trust. E2 must keep those bytes as a declared member of the zipapp and validate
their identity during the build.

The client persists the latest trusted metadata in a private directory. Lower
versions are a rollback and are rejected. The same version with different
bytes is equivocation and is also rejected. The client does not delete the
last valid state when a refresh fails, but does not use expired metadata for a
new remote mutation.

Root updates go strictly from `N` to `N+1`. Each new root is verified by the
old root's threshold and by the new one's. Jumps, unanchored roots and removal
of intermediate versions fail closed.

### Rotation and revocation

- a planned root rotation produces `N+1.root.json` signed by 2-of-3 of root `N`
  and 2-of-3 of root `N+1`;
- every intermediate root version is published immutably before
  `timestamp.json` is updated;
- replacing targets, snapshot or timestamp requires a new root that removes the
  old key and authorizes the new one;
- after a snapshot or timestamp rotation, clients discard those roles' cache
  following the TUF flow, preventing fast-forward after a compromise;
- revocation never temporarily lowers a threshold below the policy. If there is
  no quorum, publication stops and recovery is escalated;
- a compromise of the root threshold has no silent recovery: it requires a new
  anchor distributed outside the affected chain and public communication of the
  incident.

The full procedure is in the
[release and TUF runbook](../runbooks/release.md).

### Compatibility and cutover

E2 will remove from the authenticated CLI any read of
`raw.githubusercontent.com/.../main` and `gitlab.com/.../raw/main`. Clients
that support TUF query only trust endpoints and authenticated targets.

Legacy clients cannot be fixed retroactively. Before the cutover, a separate
corrective release must replace their mutable fallbacks with an immutable
compatibility snapshot pinned by SHA-256. That endpoint may keep the old
schema, but it gets no new promotions after the cutover. There is no automatic
downgrade from TUF to the legacy catalog.

`/api/v1/catalog.json` may remain as a public projection for the site and for
human observation; the authenticated CLI does not treat it as an authority.

## Threats and failure behavior

| Event | Required result |
|---|---|
| missing, invalid or repeated signature | abort before accepting metadata |
| unanchored root or version jump | abort and keep the last trusted root |
| lower version | classify as rollback and abort |
| same version with different content | classify as equivocation and abort |
| expired metadata | classify as a possible freeze and block remote mutation |
| inconsistent snapshot/targets | classify as mix-and-match and abort |
| mirror unavailable | discard the cycle before trying another mirror |
| payload with a mismatched size or SHA-256 | discard the staging and keep the previous destination |
| unreliable local clock | do not renew trust nor promote content |

Trust errors are distinct from transport failures and must be visible in human
and structured output, without including private keys, tokens or URLs with
credentials.

## Gate for E2

E2 can only start after recording in issue #48:

1. approval of this ADR by the maintainers and the security owner;
2. an independent cryptographic review of the model, thresholds and windows;
3. designation of the custodians and operators, outside the repository;
4. a rehearsal of the ceremony with disposable material that will never be
   promoted;
5. an approved strategy to package dependencies, licenses and the SBOM in the
   zipapp;
6. a cutover plan for legacy clients without a mutable `main` branch.

The implementation must start with RED adversarial tests for signature,
threshold, expiration, rollback, freeze, equivocation, mix-and-match, rotation,
revocation, unanchored root and divergence between mirrors. Only then does
production code come in. Production keys and metadata are created in the
approved ceremony, not in fixtures nor during tests.

## Consequences

The chain now tolerates an isolated compromise of timestamp or snapshot without
authorizing arbitrary payloads, and a compromise of fewer than two root or
targets keys without reaching quorum. Expiration bounds freezes, monotonic
versions bound rollbacks and consistent snapshots prevent mixing generations.

The cost is operational: audited dependencies go into the artifact, there is a
persistent trusted cache, continuous metadata renewal, offline custodians and
incident procedures. Unavailability or expiration fails closed for updates;
availability never prevails over the chain of trust.

## References

- [The Update Framework Specification 1.0](https://theupdateframework.github.io/specification/latest/)
- [`tuf.ngclient.Updater`](https://theupdateframework.readthedocs.io/en/stable/api/tuf.ngclient.updater.html)
- [Deploying a trusted root with python-tuf](https://theupdateframework.readthedocs.io/en/stable/INSTALLATION.html#application-deployment)
- [TUF client conformance](https://theupdateframework.github.io/tuf-conformance/)
