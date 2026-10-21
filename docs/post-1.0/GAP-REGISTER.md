# Gap register — M3-first

A record of what stands in the way of stability in the only environment supported now, and of what stands in the way only of a future external-public audience.

| ID | Priority | Gap | State | Evidence | Scope | Next proof |
| --- | --- | --- | --- | --- | --- | --- |
| G-001 | P0 | the Windows/Python 3.10 DNS contract depended on a real clock and an incomplete fake | RESOLVED | Gate 0A, 30,000 focused repetitions without a failure | owner-only | keep zero flakes |
| G-002 | P1 | the live projection still has to confirm the assembled tree | RESOLVED | receipt 33136179763; root 200 owner-only | owner-only | keep convergence after every overlay |
| G-003 | P1 | the historical public receipt references RC1 | ACCEPTED_RESIDUAL | separate final M3 receipt and bytes bound to the candidate | external-public | replace only if the audience changes |
| G-004 | P1 | ownership/SBOM holds items without an upstream classification | DEFERRED | release report | external-public/licensing | classify before wide redistribution |
| G-005 | P1 | the personal data cleanup does not cover every historical promise | DEFERRED | current single-user contract | conditional on an upgrade | widen only if migration is promised |
| G-006 | P1 | single maintainer and self-review | ACCEPTED_OWNER_ONLY | known limitation | external-public | reviewer/custodian before opening |
| G-007 | P1 | independent custody and production TUF RTO | DEFERRED | protected drill 31900793093; independent custody still missing | external-public | production custody, backup and drill |
| G-008 | P0_OPERATIONAL | the TUF lease must stay renewable | HEALTHY_WITH_OPERATION | timestamp v30 authenticated and technical monitor healthy | owner-only | keep the alert and renew before the window |
| G-009 | P2 | mirrors outside the installer need continuous verification | IN_PROGRESS | GitHub/GitLab installer E2 | owner-only/external | convergence after every projection |
| G-010 | P1 | only the M3 has E3 native evidence | ACCEPTED_SCOPE | 25/25 on the Apple M3 | owner-only | do not promote other platforms by inference |
| G-011 | P1 | soak and external acceptance do not exist | DEFERRED | current audience owner-only | external-public | reopen in EP-2/EP-4 |
| G-012 | P1 | QWLeague without an official contract | BLOCKED_EXTERNAL | no authorized API/OAuth/webhook | optional | official contact and documentation |
| G-013 | P2 | remote security flags not verified anonymously | DEFERRED | authenticated access needed | external-public | authenticated review |
| G-014 | P1 | the old process blocked features on external gates | RESOLVED_BY_REBASELINE | S0 M3 is the only functional block | owner-only | keep S0 green |
| G-015 | P1 | the historical migration from 0.7.13 was not reproduced | NOT_REQUIRED_FOR_FRESH_BASELINE | new baseline planned | conditional external-public | reopen if an old upgrade is promised |
| G-016 | P1 | the live source/projection was not confirmed on the new assembled path | RESOLVED | owner-only projection repair; receipt 33136179763 artifact 9672118367 | owner-only | the next overlay must keep TUF v30/v29 and the green contexts |

## Order of work

1. G-001 is already resolved.
2. G-002 and G-016 closed the owner-only projection convergence in receipt 33136179763.
3. G-008 stays operational and recurring, but does not block features while healthy.
4. S0 is PASS on the M3; F1–F3 and FUNC-008 are already on main.
5. G-004, G-006, G-007, G-011, G-013 and G-015 only become blockers when the audience changes.
6. G-012 stays `BLOCKED_EXTERNAL` in every scenario.

## Honesty rule

A gap can be accepted in the owner-only scope without being resolved for external-public. The status and the audience must appear together; no preview or artifact availability turns into support by inference.
