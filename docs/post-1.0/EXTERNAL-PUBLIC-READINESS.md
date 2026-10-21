# Readiness for external-public

**Verdict:** NO-GO.

Live snapshot: `2026-08-28T02:32:21Z`; `MAIN=GREEN`, `TUF=HEALTHY` and
`1.0.0 owner-only=VALID_FOR_SINGLE_USER_M3`. The converging public projection
was verified in run `33136179763`; the `main` line's Validate passed in run
`33135951867`. These facts close the owner-only scope, but they do not replace
the conditional gates for opening to third parties.

The x86qw-installer-1.0.0 tag and the M3 smoke do not remove the prerequisites
of availability, release truth, migration, soak, trust/recovery, acceptance and
platform.

## Entry checklist

| Gate | Requirement | Current evidence | State |
| --- | --- | --- | --- |
| 0A | green main and the Windows 3.10/3.13 matrix | Validate 33135951867, 5/5 required jobs | PASS |
| 0B | sustainable TUF, custody and recovery | root v1, timestamp v30, snapshot/targets v29; independent custody/RTO missing | BLOCKED_EXTERNAL |
| 0C | coherent receipt, candidate, audience and digests | projection 33136179763; site/product/catalog/release-truth converge | PASS_OWNER_ONLY |
| 0D | complete backlog, Maker/Checker and rollback | documentation tied to #164; external limits recorded | PASS_OWNER_ONLY |
| 0E | owner-only observation | single-user acceptance and full lifecycle on the M3 | PASS_OWNER_ONLY |
| EP-1 | real migration from the published baseline | the exact 0.7.13 installer does not authenticate the historical state; only conditional if an upgrade is promised | CONDITIONAL |
| EP-2 | seven consecutive days of soak | not started for an external audience | BLOCKED |
| EP-3 | production TUF operation/recovery | custody and recovery missing | BLOCKED |
| EP-4 | external user | not authorized/run | BLOCKED |
| EP-5 | platform decision | only E3 M3 of the candidate | BLOCKED |

## Real migration

The public baseline used in EP-1 must be the installer actually published as
0.7.13 (SHA-256 114604400e1fd18c4180624314d4bc8ca9b6d4559ed26cfe8d0a767287f2aa32).
In this audit, that installer was installed into a throwaway target and the
final 1.0.0 CLI ran migrate; the command ended blocked because the historical
state had no authenticated version.

The signed native evidence declares the migration-0.7.13-real case as passed,
but the harness's public code writes installation_version into the fixture
before the migration call (maintenance/native_case_entrypoint.py). That proves
the fixture's contract, not the path of a user who has only the published
0.7.13 installer. EP-1 must settle that difference with an independent
decision/reproduction, without fabricating authenticated state.

## Promotion criteria

A promotion is eligible only when every row above has a verifiable receipt, an
independent Checker, a reference to the commit and the bytes, and no high risk
without an approved exception. The exact candidate must stay immutable between
EP-1 and EP-5; any change restarts the train.

QWLeague stays BLOCKED_EXTERNAL: no adapter, authentication, scraping, cookie,
deep link or partnership claim is allowed without an official contract and
authorization.
