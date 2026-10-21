# ADR 0007 — External cryptographic review waiver for a solo maintainer

- **Status:** approved by the owner; operational waiver; not an independent review
- **Date:** 2026-08-07
- **Issue:** #48
- **Scope:** allow the documented continuation of E1/E2 when the repository has
  a single owner and there is no second eligible reviewer

## Context

The stabilization plan requires an independent cryptographic review for trust
and explicit human approval for the final promotion. The repository
`x86dx2/x86qw` is maintained by a single owner, `x86dx2`, who is also the
author of the PRs. GitHub does not let an author approve their own PR, and no
other collaborator, team or `CODEOWNERS` is available at this time.

The lack of a second reviewer is a real operational limitation. It must not be
turned into a fictitious GitHub review, an auxiliary account or a claim of
independent review.

## Decision

The owner explicitly approves this waiver and accepts the risk of a missing
independent review to let the plan continue. The exception has the following
limits:

1. The owner's approval is recorded in comments on the PRs and in this ADR. It
   is a governance decision, not an independent cryptographic review.
2. No part of the history may state that there was an external review,
   multi-operator custody or an independent threshold.
3. The waiver does not authorize promoting fixtures, test keys, expired
   metadata, in-house RSA-PSS or a checkpoint root as production trust.
4. The implementation must prefer `python-tuf`/`securesystemslib` and the
   corresponding official vectors; no new cryptographic primitive is added just
   to make up for the lack of a reviewer.
5. Thresholds, rotation, expiration, rollback, freeze, equivocation and an
   unanchored root keep failing closed and need adversarial tests.
6. A single person may hold the material only as a declared exception.
   Multiple keys controlled by the same person are not equivalent to
   independent custodians and do not raise the threshold's guarantee.
7. The waiver does not replace the key ceremony, fingerprints, signed metadata,
   native M3 evidence, hashes, mirrors or the other release gates.

## Mandatory evidence before any production trust

Even with the waiver, promoting trust remains conditional on:

- pinned dependencies and versions, with verified hashes;
- tests of the official vectors and of the rollback, freeze, expiration,
  equivocation and rotation cases;
- key generation outside the workspace and a record of the public
  fingerprints;
- signed metadata with bound bytes, versions and digests;
- an authenticated `release-evidence.json` for the exact candidate;
- real M3 evidence and an RC usage period;
- independent validation of each mirror, without overwrite or rebuild;
- documentation consistent with what is actually published.

Until those artifacts exist, the verifier must keep failing closed and no 1.0
version may be announced as production trust.

## Future review and expiration

This waiver must be revisited as soon as an independent collaborator with
review permission is available. The future review must compare the
implementation with the official vectors, confirm the custody, validate the
ceremony and record a separate approval.

The waiver does not make the solo policy a permanent state: it is a
single-maintainer exception for this run of the plan and must be removed or
replaced by an external review before any change that raises the
cryptographic, custody or publication risk.

## Consequences

The plan can move forward with an honest audit trail and the explicit approval
of the sole owner. The guarantee of an independent review remains absent and is
treated as an accepted risk, not as a requirement met. Any release that does
not satisfy the mandatory evidence stays blocked by failing closed, even though
the solo governance was approved.
