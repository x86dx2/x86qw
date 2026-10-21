# TUF — SLO and recovery

## Current state

At 2026-08-28T02:32:21Z, the public chain authenticated root v1, timestamp v30,
snapshot/targets v29 and 75 packages. The timestamp expires at
2026-09-27T02:15:12Z, outside the 6-hour alert window. The snapshot expires
after the timestamp, at 2026-11-26T02:07:54Z. The public monitor came back
healthy on the canonical and alias domains. The historical correction of the
root version is recorded in the [TUF erratum](ERRATA-TUF-ROOT-VERSION.md).

The owner-only v29/v30 ceremony renewed snapshot and timestamp without changing
catalog, root or targets:

- handoff/source run: 33135065604; artifact 9671745396;
- renewal run: 33135314707; artifact 9671800710;
- projection verification run: 33136179763; artifact 9672118367;
- timestamp: v29 → v30;
- snapshot/targets: v29;
- renewal report SHA-256:
  fe90b29ca4aa49f3b3c5a33897edd67b7069685d1622f3ae8d85f348a172e7cb.

The technical recovery drill was recorded in run 31900793093, artifact 9251029392,
with status drill-passed, expiry failure detected and recovery verified. The
timestamp-only publication passed in run 31900914825, artifact 9251063517, and
the public verification confirmed TUF, bootstraps and product. Issue #152 was
closed as a resolved operational alert.

In owner-only mode the timestamp lives 30 days and the snapshot 90 days, so the
snapshot outlives the timestamp. The 6-hour alert and the fail-closed for remote
mutation do not change. Before an `external-public` catalog the policy goes back
to a 7-day snapshot and a 24-hour timestamp, with separate keys.

## Current/proposed SLO

The numbers below remain a PROPOSAL until a formal operational decision:

| Signal | Proposed target | Alert | Action |
| --- | --- | --- | --- |
| timestamp within validity | 100% of checks | 6 h before expiry | open an incident and freeze promotion |
| authenticated chain | 100% per valid query | any failure | fail closed, compare mirror and root |
| endpoint reachability | measure from an independent vantage | transient failure | observe, record and use the planned fallback |
| recovery | periodic exercise with independent custody | missing evidence | keep external-public=NO-GO |
| byte divergence | zero | first divergence | do not overwrite; keep the receipts |
| production recovery | RTO defined and exercised | missing owner/backup | keep external-public=NO-GO |

## Residual operational gap

The technical/local drill and the protected drill proved the protocol for
expiry, bounded renewal and recovery without improper publication. They do not
yet prove independent human custody, a backup custodian, a production RTO
owner, a recurring exercise cadence or succession. These gaps remain EP-3
requirements and keep external-public=NO-GO; issue #148 was closed after
recording the technical capability, without proving independent custody or
authorization to open the audience.

The monitors that observed old leases inside the 6-hour window stay archived as
history. The current v28 lease and the current projection were verified
independently after the deploy.

## Short runbook

1. Confirm the alert from an independent network and save the timestamp,
   endpoint, root and UTC time.
2. Freeze promotion/publication; do not change the URL nor disable TUF.
3. Compare timestamp, snapshot, targets and root with the custodied receipt.
4. If only the lease expired, run the approved bounded path for the
   timestamp; if root/targets diverge, stop and require a rotation ceremony.
5. Restore the metadata from the custodied artifact, verify signatures,
   versions and expirations before publishing.
6. Repeat the public verification, record the Checker, incident and RTO.
7. Keep external-public=NO-GO until the post-incident review and independent
   custody close the causes.

No renewal may be run outside the protected workflow without equivalent
operational authorization. Sustainability and repeatable recovery remain
external-public gates; owner-only work may advance while S0-M3 stays green.
