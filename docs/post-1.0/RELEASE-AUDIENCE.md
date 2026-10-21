# Audience and support claims

An audience is an authorization decision, not a synonym for published bytes.
The `x86qw-installer-1.0.0` candidate exists and the chain of truth was
reconciled for the owner-only scope: one maintainer on the Apple M3, with
`MAIN=GREEN`, `TUF=HEALTHY` and a converging deployment. That does not
authorize `external-public`.

## Current matrix

| Audience | State | What may be said | What may not be said |
| --- | --- | --- | --- |
| `owner-only` | VALID_FOR_SINGLE_USER_M3 | final publication, E3 M3 and single-user lifecycle verified | external support or an unvalidated historical upgrade |
| `external-public` | NO-GO | it is a future intention subject to EP-1–EP-5 | that the release is ready for external users |
| `preview` per platform | VERIFIED POLICY | the artifact/contract may be consulted | that the candidate ran natively |
| `supported` | BLOCKED where there is no E3 | only after native evidence of the exact candidate | inferring it from portable CI or the ZIP being present |

## Limited owner-only

The owner-only scope allows one maintainer, a clean throwaway install and
acceptance on the Apple M3. The final recorded public acceptance uses
`single-user`; it does not prove historical migration nor the experience of
external users. The 0E observation must record version, catalog/TUF, lifecycle,
update, repair, uninstall/purge and any incident without changing the bytes
already published.

## Moving to `external-public`

The maintainer must record an explicit EP-0 decision. Without it, the external
capabilities stay parked. With it, the required order is:

1. EP-1: real migration with `0.7.0–0.7.13` fixtures, preservation and rollback;
2. EP-2: an exact seven-day soak, with one HTTPS reference and hardware per day;
3. EP-3: production TUF custody, renewal and recovery;
4. EP-4: acceptance by an external user on the exact endpoint and candidate;
5. EP-5: platform decision, keeping `preview` until there is a specific E3.

The summary of each step must hold the commit, digests, run, artifact, observed
endpoint, timestamp and Checker. A failure or missing evidence keeps `NO-GO`.

## Native evidence matrix

| Platform | Artifact | Portable contract | Native evidence of the exact candidate | State |
| --- | --- | --- | --- | --- |
| macOS arm64/M3 | yes | yes | E3, 25/25 | conditional |
| macOS Intel | yes | yes | not observed | preview |
| Linux x86_64 | yes | yes | not observed | preview |
| Windows x64 | yes | yes | not observed; portable contract 3.10/3.13 green | preview |
| macOS nightly | yes | yes | insufficient | preview |

Artifact availability is not support. Each promotion needs an issue, owner,
Checker, candidate/digest and a native run in the declared environment.

## Claims forbidden in this phase

- do not claim site headers, Core Web Vitals or latency without a reproducible
  capture;
- do not call Linux, Windows, macOS Intel or nightly `supported` because of the
  physical installer;
- do not call a package mirror a redundant operation;
- do not treat QWLeague as an official integration without a verified
  contract/API/OAuth/webhook;
- do not present the RC as final evidence when the receipt points at it.

## Sources

- [release truth](RELEASE-TRUTH.md);
- [external-public readiness](EXTERNAL-PUBLIC-READINESS.md);
- [platform policy](../PROJECT-STATUS.md);
- [owner-only ADR](../adr/0008-owner-only-release-gates.md).
