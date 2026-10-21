# Historical note on the stabilization up to 1.0

This file preserves the context of the initial stabilization checkpoint. It
is not the current source of status, release or trust. For the executable state,
see [PROJECT-STATUS.md](../PROJECT-STATUS.md) and, for the execution
sequence, [stabilization-1.0-plan.md](stabilization-1.0-plan.md).

## Preserved baseline

The work started on the public `0.7.3` line. The current public version is
`0.7.13`, prepared at commit
`04a55aed8711ec5466dc70f0e33a591d92e07ccb` and followed by
`origin/main@d4a92c0fe29786fdc6ec5c7d978813cb634be62c`. No earlier public
bundle is rewritten by this local line.

The six historical fronts below were incorporated before this cycle:

| Historical front | Observed release | Limit of the evidence |
|---|---|---|
| Python bootstrap | 0.7.1 | portable contracts and launchers of that commit |
| bounded downloader | 0.7.3 | limits, deadline and retry of that commit |
| ZIP/PK3/PYZ | 0.7.3 | archive boundary of that commit |
| private Windows DACL | 0.7.3 | historical code and tests; not current Windows execution |
| upstream macOS stable | 0.7.3 | bundle preservation; not notarization/M3 execution |
| runtime boundaries | 0.7.3 | historical integration; not 1.0 approval |

A portable matrix or a release note does not prove a native smoke, publication,
production custody or mirror convergence.

## Current corrective state

The local checkpoint holds incremental fixes for:

- SemVer, schemas, launchers and the `changes`/`migrate` commands;
- a migration based on real fixtures, including the public release 0.7.13;
- standard TUF with an embedded Ed25519 root, a private cache and fail-closed;
- a candidate built once, a publisher without silent rebuilds and explicit
  byte verification;
- separate workflows for portable validation, candidate, M3 evidence,
  approval, mirrors and metadata-last;
- an M3 harness that requires a real host, a closed plan, the exact candidate and an artifact for
  each assertion;
- regressions of macOS processes and the projection of runtime dependencies.

These changes are local and were not published in this cycle. The RSA trust
fixture in `maintenance/inventory/trust/` stays only as historical material
of the legacy tests; the installed runtime uses the standard TUF root in
`maintenance/trust/root.json` and receives no private keys from the repository.

## Gates still open

- run the harness's closed plan on the Mac M3 against the exact candidate;
- set up custody, a human threshold, a scheduled signer, monitoring and expiration
  recovery for production metadata;
- verify idempotent publication to the mirrors and publish TUF metadata only
  after the assets;
- repeat the critical suite over the final bytes and align the documentation after the
  authorized publication.

Linux, Windows, macOS Intel and nightly stay `preview`/`not-run` in this
scope. The presence of an artifact is not a claim of native execution.

## Reading rule

Dates, test counts, CI runs and error messages that appeared in the
previous version of this note were observations of that checkpoint and must not be
used as current evidence. Current evidence must point to a command,
hash, endpoint, fixture or result reproduced in the audit report.
