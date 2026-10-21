# Stabilization plan `0.7.13 → 1.0`

> Current policy: `owner-only`, as set by
> [ADR 0008](../adr/0008-owner-only-release-gates.md). The historical reference
> `0.7.13 → 1.0` stays documented for the future external audience, but the
> first installation can be clean and needs no migration. The soak,
> migration and sustainable TUF operation gates below are conditional on
> `release_audience=external-public`.

> **Update of 2026-08-15:** the owner-only gate was completed and the release
> `x86qw-installer-1.0.0` was published. The sections below preserve the plan and
> the decisions of the preparation phase; the current state is in
> [PROJECT-STATUS.md](../PROJECT-STATUS.md) and the auditable closing is in
> [1.0.0-owner-only-publication-2026-08-15.md](../releases/1.0.0-owner-only-publication-2026-08-15.md).

| Field | Value |
|---|---|
| State | `1.0.0` owner-only published and verified; external gates parked |
| Public baseline | immutable release `x86qw-installer-0.7.13`, prepared at commit `04a55aed8711ec5466dc70f0e33a591d92e07ccb` |
| Canonical base | `origin/main` |
| Auditable checkpoint | product `e12ed081b968f820f47200e4be954a4f444056a1`; promotion `31849932133` (attempt 2 completed) |
| Target version | `1.0.0` |
| Audit date | 2026-08-13, final checks on 2026-08-14 UTC |
| Scope of this delivery | owner-only policy, durable evidence, clean acceptance, M3 coverage and promotion gates; external operation conditional |

This document records the approved sequence to turn the public baseline
`0.7.13` into a verifiable `1.0`. The current state is in
[PROJECT-STATUS.md](../PROJECT-STATUS.md), the strategic index in
[ROADMAP.md](../ROADMAP.md) and the baseline record in
[stabilization-baseline-2026-07-31.md](stabilization-baseline-2026-07-31.md). This plan does not promote the
checkpoint, does not authorize publication and does not replace human approval.

## 1. Boundary, authority and invariants

`origin/main` is the only canonical line. The public release `0.7.13` and all
earlier public bundles stay immutable. The checkpoint is only a
source to consult for selective extraction: **checkpoint for extraction, never
merge**. No commit of `codex/stabilize-1.0` will be incorporated as a unit.

The historical checkpoint that gave rise to this plan did not authorize publication. The
preparation snapshot confirmed that `1.0.0-rc.1` was a public
prerelease; that limitation was overcome by the protected owner-only
promotion workflow. The auditable closing is in
[`1.0.0-owner-only-publication-2026-08-15.md`](../releases/1.0.0-owner-only-publication-2026-08-15.md).
The private `1.0.0` preflight
of product revision `f2cadeff3261ce07f7c9490313db1aa69e417fa2` passed `25/25`
cases on the Apple M3 Pro, with `candidate.json` SHA-256
`c7357159df806b29d8c9eb715152ec6186c5d9edefd3bb5587dbf6c98a0a94c7`; it
still has to go through the remote flow, the signed handoff and the public endpoint
before it counts as release evidence. The later commits of this
sequence changed only release tooling and documentation, not the product
files covered by that preflight.

The invariants for every phase are:

- one structural front at a time, tied to an issue and a branch;
- implementation and release are different PRs;
- upstream bytes, x86QW customizations, trust metadata and native evidence
  keep explicit boundaries;
- declared compatibility is never presented as native execution;
- published artifacts are immutable and a promotion without evidence fails
  closed;
- TDD RED → GREEN → REFACTOR will be required in PRs A–G whenever there is a
  testable change; this documentation delivery is exempt from TDD.

## 2. Audit of the lines and the historical PRs

The audit was done on the local refs available on 2026-08-06. Before
any future cleanup action, ancestry must be revalidated against
`origin/main`; the list below is not an authorization to delete refs.

| Ref | Observed state | Plan decision |
|---|---|---|
| `origin/main` | resolve with `git rev-parse origin/main` at the snapshot | the only canonical line |
| `codex/stabilize-1.0` | `30e9d5b`, checkpoint with preparation work | frozen; extract symbols/hunks, never merge/cherry-pick |
| `origin/codex/control-maps` | `362f705`, four commits ahead and 105 behind, four exclusive commits | keep until its own decision; do not delete with the checkpoint cleanup |
| `agent/bounded-downloader` | `a60a625`; 0 ahead/86 behind, integrated by PR #58 (`b833ba4`) | delete only after revalidating ancestry and with authorization |
| `agent/archive-boundary` | `3228865`; 0 ahead/74 behind, integrated by PR #59 (`b746d57`) | delete only after revalidating ancestry and with authorization |
| `agent/runtime-boundaries` | `e5a90a6`; 0 ahead/10 behind, historical extraction integrated by PR #62 and published by PR #64 | delete only after revalidating ancestry and with authorization |
| `origin/x86dx2/audit-ezquake-tournament-gameplay` | `6d659d4`; 0 ahead/5 behind, ancestor of `origin/main` | delete only after revalidating ancestry and with authorization |
| `gitlab/main` | `afb4f66` (`0.7.1`) | do not sync; the mirror's role still needs a decision |
| `fix/macos-stable-no-entitlements` | remote ref gone; historical local branch | do not recreate it or treat it as a canonical line |

The six historical stabilization fronts are closed as code/publication
deliveries that predate the 1.0 journey:

| Historical PR | Closed outcome | Reference evidence |
|---|---|---|
| PR 1 — Python bootstrap | published in `0.7.1` | PR #56, merge `8ba1f90`; [Python contract](python-runtime-contract-0.7.1.md) |
| PR 2 — bounded downloader | published in `0.7.3` | PR #58, merge `b833ba4`; [download boundary](bounded-download-boundary.md) |
| PR 3 — single ZIP/PK3/PYZ | published in `0.7.3` | PR #59, merge `b746d57`; [archive boundary](safe-archive-boundary.md) |
| PR 4 — private Windows DACL | published in `0.7.3` | PR #60, merge `206adc4`; [Windows implementation](windows-private-acl.md) |
| PR 5 — upstream macOS stable | published in `0.7.3` | PR #61, merge `0009833`, public fix PR #65 `3bbc7a0`; [ADR 0004](../adr/0004-preserve-upstream-ezquake-stable-macos-bundle.md) |
| PR 6 — runtime boundaries | integrated in `0.7.3`; auditable track closed as a delivery, without promoting the checkpoint | historical HEAD `29d76a4`, integration `daf5d0c`, publication PR #64 `15eef0b`; [PR 6 evidence](runtime-boundaries-pr6.md) |

The historical closing does not turn the checkpoint's artifacts into code
approved for `1.0`. Any remaining symbol needs an issue in the A–G
sequence, its own review and evidence from the exact candidate.

The relevant historical matrices remain as dated evidence, not as a
future gate: PR 3 (`30856293818`), PR 4 (`30866754435`) and PR 5
(`30871046055`). They prove the contracts of their commits, not native smokes of the
`1.0` candidate.

## 3. Checkpoint extraction rule

Extraction is selective and done by symbol or hunk, always comparing the result
with the `origin/main` baseline. The files shared between phases —
`manager.py`, `maintenance/manage.py`, `build_installer_bundle.py`,
`release_candidate.py`, `test_ci.py` and
`maintenance/inventory/installer-runtime-members.json` — cannot be
copied whole. Each hunk must declare its consumer, contract, test and
target issue.

### Extract when there is a contract and an issue

- governance, notices, ownership, Dependabot, the site lockfile, threat model and
  runbooks in PR B;
- SemVer, schemas, JSON envelopes, redaction and stable codes in PR C;
- one-way migration of real `0.7.0–0.7.13` fixtures in PR D;
- the approved trust ADR, library and ceremony in E1/E2;
- candidate preparation/verification, ownership, SBOM, provenance and mirrors
  in PR F;
- the native executor and M3 evidence in PR G;
- promotion without functional fixes in PR H.

### Reject in this journey

- gameplay, KTX, downloader, host, supervisor or incidental fixes without an
  issue of their own;
- workflows with reserved steps, rebuilds after approval or native claims
  without evidence;
- prospective `0.8.x`/`0.9.x` fixtures treated as published releases;
- private keys, production metadata, fixture keys/roles or the trust that
  expires on `2026-08-08`;
- synthetic native evidence, local promotion as a substitute for protected
  publication, or any change to bundles already published.

## 4. Platform and CI policy

Published artifact, support and validation are three different dimensions. A
platform record can only use the states below:

| State | Rule |
|---|---|
| `supported` | requires native evidence from the exact candidate |
| `conditional` | keeps the artifact, but documents the contract still missing |
| `preview` | availability/contract exists, without a native smoke of the candidate |
| `deprecated` | the artifact stays while its removal is carried out |
| `unavailable` | derives from the absence of an artifact; it is not a synonym for “not tested” |

Linux, Windows, macOS Intel and nightly start as `preview`. The physical
universal macOS artifact is preserved, but support is projected separately
for `macos-arm64` and `macos-x64`. M3 evidence only changes the state after PR G:
services can become `supported`; the stable client stays `conditional`
if signing/notarization is still a limitation. The portable matrix is not a
native smoke.

The workflows now exist as executable gates: portable validation is
separate from the self-hosted M3 runner, and no portable job claims graphical,
network or Gatekeeper execution. Promotion requires approval, mirrors,
durable evidence, public acceptance for the final and metadata-last; missing
external configuration fails closed.

## 5. Sequence A–H

Each phase starts with an issue, creates its branch only after the issue, closes
its contract gate and only then unblocks the next phase. The version targets
are publication suggestions, not versions prepared in this delivery.

### PR A — platform truth

Create an issue of its own before the branch. Separate published artifact, support and
validation; keep the physical universal macOS; classify Linux, Windows,
macOS Intel and nightly as `preview`; keep the CI with the
`portable-contract / ...` names; and record the exact-candidate evidence rule.
macOS M3 is not promoted before G. The public stable baseline `0.7.13` is not
rewritten; any future fix must be a new release, separate from the
implementation PR and justified by a P0/P1 gap.

**Gate:** catalog and documentation use the five states without mixing presence
with execution; portable CI stays intact; the `0.7.4` release issue is
separate from the implementation.

### PR B — governance (`issue #55`)

Extract license, notices, ownership, Dependabot, the site lockfile, threat model
and runbooks. Do not remove workflows and do not mix in trust, promotion or publication.

**Gate:** every new file has an owner and a license; notices carry over to the
modern distribution; runbooks point to a single authority.

### PR C — contracts (`issue #53`, target `0.8.0`)

Extract SemVer, state/receipt schemas, JSON envelopes, redaction and codes
by symbol/hunk. The real writers must emit the frozen versions. Before
declaring a publishable RC, builders, archives, catalog, bootstraps and ordering
must accept `1.0.0-rc.1`.

**Gate:** schemas, writers and consumers agree; negative fixtures cover
redaction and codes; no 0.7.x bootstrap breaks.

### PR D — migration (`issue #46`, target `0.8.1` or `0.8.2`, post-public)

Update the issue to support only versions actually published. Use
real `0.7.0–0.7.13` fixtures; remove synthetic
`0.8.x`/`0.9.x` claims; ensure
the migrated state satisfies the schemas frozen in C and that rollback and
ownership stay diagnosable.

**Gate when `external-public`:** one-way migration, preservation of personal
files, coherent receipts and no invented future version. In
`owner-only` mode, the issue stays a future capability and does not block the installation.

### PRs E1/E2 — trust (`issue #48`, target `0.9.0`)

Keep the issue open across both PRs. E1 approves the ADR, library, algorithm,
thresholds, custody, endpoints, expiration, rotation and ceremony. E2 only
implements after approval and an external cryptographic review.

Never promote a home-made RSA-PSS, fixture keys/roles or checkpoint
metadata that expires on `2026-08-08`.

**Gate:** the production key was created in an approved ceremony, the independent
review was recorded and the verifier fails closed on rollback,
freeze, equivocation, expiration and an unanchored root.

### PR F — immutable candidate (`issue #51`)

Preserve and redesign `validate.yml` and `release.yml`. Decouple candidate
preparation from trust/evidence through optional imports: a rehearsal can exist without
evidence, but 1.0 promotion/publication fails closed without M3 evidence. Build
once, pin Actions by SHA, check ownership/SBOM/provenance/mirrors and
publish metadata last. Fix the prototype's ownership and sources/licenses;
every runtime the smoke uses must belong to the candidate or be bound
by an immutable digest.

**Gate:** bytes, checksums, ownership, SBOM and provenance are identical between
preparation and promotion; no destination is overwritten; trust is not imported
implicitly.

In `external-public`, the final gate also requires a TUF operation handoff recorded by
`.github/workflows/tuf-operation-drill.yml`. The report must be bound to the
candidate's catalog, hold operator/host/SLA context and prove
renewal, simulated expiration and recovery; its coordinates go into the
durable `1.0.0` receipt.

In `external-public`, the final workflow also checks the current public lease with
`monitor_public_tuf.py` and a six-hour window. The historical drill does not replace
that check: while alert issue #152 is open or the lease
is inside the window, promotion stays `NO-GO`.

The bounded renewal path is prepared in
`.github/workflows/tuf-timestamp-renewal.yml`: it consumes an already
verified TUF artifact, requires `TUF_TIMESTAMP_KEY_B64` in the protected environment and produces only
an unpublished handoff with a renewed `timestamp.json`. The second workflow,
`.github/workflows/tuf-timestamp-publish.yml`, verifies that handoff against the
source TUF and the candidate, publishes the timestamp-only generation under the same
protected environment, validates the receipt with
`maintenance/tools/verify_tuf_timestamp_publication.py` and records the
public post-deploy check. The existence of the workflows proves neither custody
nor public health; without a configured secret, a green run and protected
approval, the TUF gate stays pending.

In `external-public`, the final gate also requires the RC usage period through
`.github/workflows/rc-soak.yml`, dispatched on the ref of the candidate's exact commit.
The report must prove seven full days, green daily observations,
the five operational conditions and the canonical issue closed. `verify-soak`
checks the run's provenance and the immutable artifact; its coordinates go into the
`soak` section of the durable `1.0.0` receipt.

### PR G — Mac M3/arm64 and RC (`issue #54`; operational closing `#147`)

The `native_macos_harness.py` executor and the
`.github/workflows/native-m3.yml` workflow require a closed plan supplied for the
exact candidate. The checkpoint holds no signed release evidence; on the
exact candidate, run the
full set of clients, games, services and lifecycle. Linux, Windows and
macOS Intel stay `not-run`/`preview`, with non-blocking harnesses.

The public RC was produced and promoted in run `31752738047`. The implementation
that closes this front adds the remaining M3 cases, the clean acceptance and
the durable evidence. The local recheck downloaded the same bytes through the public
endpoints and completed the lifecycle; the real migration
`0.7.13 → 1.0.0-rc.1` is archived as an external capability in the receipt
[`1.0.0-rc.1-public-acceptance-migration-local-2026-08-14.json`](../releases/1.0.0-rc.1-public-acceptance-migration-local-2026-08-14.json).
The local technical TUF drill, format 2, also passed renewal, simulated expiration and
recovery, with operator/host/SLA context, per-role versions and ephemeral keys, in
[`1.0.0-tuf-drill-local-2026-08-14.json`](../releases/1.0.0-tuf-drill-local-2026-08-14.json).
The local candidate `1.0.0-rc.2`, distinct from the public RC's bytes, also passed
the 25 real M3 cases on the Apple M3 Pro; the unsigned/pending receipt is in
[`1.0.0-rc.2-native-preflight-local-2026-08-14.json`](../releases/1.0.0-rc.2-native-preflight-local-2026-08-14.json).
The protected workflow's handoff/artifact, production custody and the opening of the
soak are still pending.

**Owner-only gate:** implementation closed; authenticated M3 evidence for a
new candidate, clean installation and recorded smokes. The usage period is only a
gate when `external-public` is declared.

### PR H — `1.0.0` promotion (`issue #149`)

Create its own issue after the candidate meets the gates of the chosen
audience. The PR only does promotion/release, without functional fixes. Require
valid trust, M3 evidence, immutable bytes, converging mirrors and
coherent documentation.

**Owner-only final gate:** recorded approval, clean acceptance, M3 evidence,
metadata published last, documented rollback and no platform claim
beyond the existing evidence. **External-public final gate:** adds soak,
migration, sustainable TUF operation and external handoffs.

## 6. Intermediate releases and dependencies

| Milestone | Minimum dependency | Release type |
|---|---|---|
| new `0.7.x` | confirmed P0 only | corrective, separate release PR |
| `0.8.x`/`0.9.x` | not a requirement of this cycle | do not create out of documentary inertia |
| `1.0.0-rc.1` | F and G executed; public acceptance v2 and operational evidence recorded | public RC available; soak not yet formally opened |
| `1.0.0` | H and owner-only gates | published; external-public gates parked |

The RC `1.0.0-rc.1` was created, tagged and published; the final release
`1.0.0` was also published for `owner-only`. Materializing this
documentation does not change published bytes. Any product change requires a
new candidate; the soak becomes a gate again when the audience is
`external-public`. The GitLab mirror of the final release was confirmed in run
`31849932133`.

## 7. Current governance and future cleanup

The historical control instructions below were superseded by the operational
state of the owner-only final release. The cleanup actions still require a new check and
cannot remove evidence:

1. do not create the historical tag `audit/stabilize-1.0-2026-08-06` nor treat it as
   an authority;
2. revalidate ancestry and delete only refs already integrated, after
   recording the SHAs and preserving refs tied to the RC, the evidence and TUF;
3. keep the operational backlog in issues [#143–#152](https://github.com/x86dx2/x86qw/issues/143),
   with branch cleanup in [#150](https://github.com/x86dx2/x86qw/issues/150)
   and the host-level immutability assessment in
   [#151](https://github.com/x86dx2/x86qw/issues/151);
4. keep `codex/control-maps` and the historical checkpoints out of the
   release line until their own decision.

The historical issues #68 and #70 were closed because they were already done; they
must not be used again as a 1.0 backlog.



## 8. Risks, rollback and approval boundaries

| Risk | Control | Required evidence |
|---|---|---|
| the checkpoint being treated as a merge | hunk-by-hunk comparison and an `origin/main` base | each PR's diff and revalidated ancestry |
| compatibility turning into support | separate states and candidate evidence | matching catalog, status and smoke |
| fixture trust reaching production | mandatory custody and external review | ceremony, fingerprints and expiration |
| a rebuild changing bytes | build once, hashes and promotion without overwrite | checksums, SBOM, provenance and mirrors |
| docs diverging from the code | status/roadmap/plan have distinct roles | `verify`, links and human review |
| a release mixing in implementation | separate release PR | issue, approval and changelog |

Rolling back a documentation phase is just reverting its PR before the merge;
bundles are not rewritten and evidence is not deleted. A promotion of an artifact already
published is immutable and requires an incident procedure, not a `git revert` of
bytes. Any remote, cryptographic or destructive action outside the phases
described stops until explicit authorization is obtained.

## 9. Definition of done

The journey can only be declared done when:

- A–G have approved issues, branches, contracts, evidence and gates;
- `1.0.0-rc.1` passed the acceptance and the gates required by the chosen audience;
- H promotes only identical bytes, converging mirrors and coherent metadata;
- support per platform matches the candidate's native evidence;
- `PROJECT-STATUS.md`, `ROADMAP.md`, README and release notes point to the
  same state;
- in `external-public`, there is a protected soak artifact, a closed issue and
  coordinates checked in the final durable receipt;
- no functional change is hidden in a promotion PR.

For the `owner-only` scope, completion was reached: the local contracts
pass, the publisher does not recompile, the public 0.7.13 fixture stays
archived, the clean public acceptance went through the public endpoints, the
durable evidence was published, the final candidate was promoted and the release
was verified on the M3. Sustainable TUF operation, external soak and historical
migration stay parked for `external-public`.

## 10. Validation of this materialization

The local implementation gate runs in the worktree; it does not replace the
remote and native gates:

1. confirm that the canonical base still resolves to
   `origin/main`; record the returned SHA in the audit report; if it moved, repeat the
   audit before editing;
2. run `git diff --check` and review the local Markdown links, resolving
   each target from the document's directory and ignoring only external URLs
   and anchors;
3. confirm the distinction between public baseline, local checkout, technical TUF and
   TUF operation;
4. run `git diff --check`, `manage.py verify --no-tests`, the portable
   tests and the native tests with the appropriate permissions;
5. review the local `HEAD` against `origin/main`, keeping the historical work already
   published separate from the current candidate; record the local
   public acceptance and TUF drill receipts without treating them as protected
   handoffs.

That gate does not promote `1.0.0` nor change the existing RC release. Native
M3 execution, TUF custody, the public catalog and the soak still depend on
protected inputs and are not inferred locally.
