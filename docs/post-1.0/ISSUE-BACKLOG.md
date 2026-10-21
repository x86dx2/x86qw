# Proposed issue backlog — M3-first

This backlog is local and workable by a single maintainer. It separates what stabilizes the owner-only installation on the Apple M3 from what only makes sense once there is an external audience. No item below implies automatically creating a remote issue.

## Prioritized top 20

| ID | Class | Title | Dependencies | Version/audience |
| --- | --- | --- | --- | --- |
| S0-001 | NOW/P0 | Keep main green and preserve the deterministic DNS contract | none | no release if test-only / owner-only |
| S0-002 | DONE/P1 | Verify the convergence of the site, bootstraps, product and release-truth projection | S0-001 | documentation/protected deploy / owner-only (receipt 33136179763) |
| S0-003 | DONE/P1 | Acceptance of the exact candidate on the Apple M3 | S0-001, S0-002 | 1.0.x if bytes change / owner-only (E3 25/25) |
| S0-004 | NOW/P1 | Renewal and technical monitor of the TUF lease | none | continuous operation / owner-only |
| S0-005 | NEXT/P1 | Owner-only operational observation and incident log | S0-003, S0-004 | 1.0.x / owner-only (CLI 1.0.2 zipapp `cd37e868…`; host/ui PASS; conservative `uninstall` PASS 2026-08-17T02:09Z; `--purge` after leftovers refused the identity; reinstall complete + `--purge` PASS 2026-08-17T02:26Z — target and Darwin cache removed; 25/25 of the 1.0.2 digest `35e77723…`) |
| FUNC-001 | DONE/P1 | Implement a read-only `x86qw doctor` | S0-003 | 1.0.x / owner-only (PR #191) |
| FUNC-002 | DONE/P1 | Generate a sanitized, reviewable diagnostic bundle | FUNC-001 | 1.0.x / owner-only (PR #191) |
| FUNC-003 | DONE/P1 | Separate profiles, defaults, cache and personal data | S0-003 | 1.0.x / owner-only (PR #193) |
| FUNC-004 | DONE/P1 | Local favorites and recents with origin and freshness | FUNC-003 | 1.0.x / owner-only (PR #194) |
| FUNC-005 | DONE/P1 | Match discovery and joining with a local fallback | FUNC-004 | 1.0.x / owner-only (PR #194) |
| FUNC-006 | DONE/P1 | Declarative local hosting presets | S0-003 | 1.0.x / owner-only (PR #195) |
| FUNC-007 | DONE/P1 | Server readiness, logs, stop and crash recovery | FUNC-006 | 1.0.x / owner-only (`status` / `--stop` / journal) |
| FUNC-008 | DONE/P2 | Read-only local UI over the runtime contracts | FUNC-001, FUNC-005 | 1.0.x / owner-only (PR #199) |
| OPS-001 | LATER/P1 | Independent TUF custody, backup and RTO | S0-004 | external-public only |
| OPS-002 | LATER/P1 | Seven-day soak of the exact external-public candidate | EP-0 decision | external-public only |
| OPS-003 | LATER/P1 | Acceptance by a non-maintainer user | OPS-002 | external-public only |
| OPS-004 | LATER/P1 | Real historical migration | promise of an old upgrade | external-public only, conditional |
| OPS-005 | LATER/P1 | Native evidence per additional platform | native lab | external-public per platform |
| EXT-001 | BLOCKED_EXTERNAL | Official QWLeague RFC and adapter | official documentation/authorization | optional / 1.3 |

## Order of execution

1. S0 is PASS on the M3; keep main green and the TUF lease.
2. F1–F3, FUNC-008 and the polished TUI go into the **1.0.4** source (no 1.1 line in this phase).
3. The published `1.0.0` and the earlier versions stop being current; they stay historical in the catalog.
4. Continue the owner-only observation (S0-005) without turning it into a soak.
5. Reopen OPS-001 to OPS-005 only when the audience changes to external-public.

## Migration rule

The 0.7.13 migration is not a requirement of the first open publication if that publication declares a new baseline and a clean install. It returns to the backlog only if the product promises to update historical installations or preserve third-party data. The maintainer's own personal data stays protected in any upgrade that is offered.

## Disposition of the existing issues

- `#143`: RC history; not an approval for new bytes.
- `#146`: issue closed for the recorded gate; migration stays conditional on external-public.
- `#148`: issue closed after the technical drill; custody, backup, RTO and succession remain external-public requirements.
- `#150`: issue closed after preserving the evidence refs; do not delete new refs without revalidating ancestry.
- `#151`: issue closed as a future assessment of host-level immutability; does not block M3 features.
- `#152`: historical alert closed; keep the recurring technical monitor.
- `#175`: use for owner-only observation on the M3, without turning it into an external soak.

## Definition of Ready

Every concrete issue must state its outcome, non-scope, dependencies, platform, security, privacy, deterministic tests, rollback, docs, Maker, Checker, version and audience. Implementation, publication and audience promotion stay separate.

## Common Definition of Done

- evidence tied to the right commit, run, digest or endpoint;
- the Checker tries to refute the result;
- no claim above the native evidence;
- a diagnosable rollback;
- personal data kept apart from managed defaults and cache;
- external unavailability does not stop installing, local play or hosting;
- main green without depending on a rerun;
- no artificial release change when only tests or documentation changed.
