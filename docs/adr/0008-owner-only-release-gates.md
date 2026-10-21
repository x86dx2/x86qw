# ADR 0008 — Gates of the owner-only phase

**Status:** accepted

**Date:** 2026-08-14

**Decision maker:** x86QW's sole maintainer (`x86dx2`)

## Context

x86QW is being born from scratch and, in this phase, has a single user and a
single maintainer. The user can delete the whole destination and install again.
There is no commitment yet to preserve installations, data or settings of
external users.

The repository already contains migration contracts, historical fixtures and
the `migrate` command. Those components stay available, but they must not turn
a historical compatibility that is not needed yet into a blocker for the
product's first install.

## Decision

The current cycle runs with `release_audience=owner-only`.

This cycle's mandatory acceptance gate proves:

- a clean install on a disposable destination;
- `version`, `changes`, `verify` and the launcher lifecycle;
- an applied, idempotent update on a disposable destination;
- a conservative uninstall and an explicit purge;
- native Apple M3 evidence for the exact candidate;
- portable contracts, build-once, immutable bytes, ownership, SBOM and
  provenance;
- convergence of the published assets when a release is published.

The `single-user` gate does not require:

- installing `0.7.13`;
- historical migration or preserving data from an old installation;
- a seven-day soak aimed at external users;
- a production TUF operations drill for continuous availability;
- acceptance by external users.

The migration code and its fixtures are kept as a `post-public` capability,
not as a requirement of the owner-only version.

## Transition to external users

When the maintainer declares that the product will be released to external
users, the audience must change explicitly to
`release_audience=external-public`. From that decision on, the workflow
requires:

1. acceptance with `acceptance_scope=external-users`;
2. real migration of the supported baseline;
3. a protected soak of the candidate;
4. sustainable TUF operations and proven recovery;
5. durable public evidence bound to the external bytes.

Changing the audience is a release decision and must be recorded in new minutes
or a PR before the promotion. An omitted value is still `owner-only` in the
workflow, but the promotion still needs that mode's own gates.

## Accepted risks

In owner-only mode, an old installation may be discarded instead of migrated.
That is a deliberate limitation of the product in this phase, not a hidden
failure. The documentation must not advertise historical compatibility while
the transition to `external-public` is not recorded.

## Evidence and audit

Each promotion records in the durable receipt:

- `release_audience`;
- the candidate's commit and identity;
- the acceptance scope;
- the M3 evidence and the evidence root;
- assets and mirrors;
- external gates, when applicable.

The `owner-only` receipt holds no soak or external TUF operations handoffs.
The `external-public` receipt keeps requiring those handoffs and the byte
divergence between the final candidate and the RC under soak.
