# Post-1.0 Master Plan — M3-first

MAIN: GREEN
TUF: HEALTHY
1.0.0 OWNER-ONLY: VALID_FOR_SINGLE_USER_M3
EXTERNAL-PUBLIC: NO-GO
FEATURE WORK: ALLOWED_AFTER_S0_M3

## Scope decision

**VERIFIED FACT:** there is a single user, and the only native lab available is the maintainer's Apple M3. The protected acceptance of the 1.0.0 candidate on the M3 passed 25/25 cases. Main passed the seven protected contexts after the deterministic fix to the DNS resolver.

**DECISION:** the current goal is not to prepare external-public. It is to keep a stable owner-only line on the M3 and release functional capability in small increments. Migrating third-party installations, a seven-day soak, acceptance by an external user, independent custody and support for other platforms no longer block the owner-only work.

**PROPOSAL:** the future first open publication may start from a new, clean baseline. Migration becomes a requirement again only if the communication promises an upgrade of a historical installation. Do not erase the history now: a history reset is a separate destructive operation, with a backup, an export of the evidence and a rollback.

## State and authorities

- `source`: current baseline `1.0.16`; `1.0.15`, `1.0.14`, `1.0.13`, `1.0.12`, `1.0.11`, `1.0.10`, `1.0.9`, `1.0.8`, `1.0.7`, `1.0.6`, `1.0.5`, `1.0.4`, `1.0.3`, `1.0.2` and `0.7.13` stay historical in the catalog.
- `candidate/release`: immutable 1.0.0 bytes, installer SHA-256 `d3274e6a...`, audience `owner-only`.
- `deployment`: site, bootstraps, product, catalog and TUF served; must converge with the exact candidate.
- `development`: the current HEAD and its checks; a green main is a precondition for any change.

The machine-readable projection is the authority for audience, support, CI state and functional state. README, site, product, catalog, CLI and PROJECT-STATUS are tested projections, not manual copies.

## S0 — owner-only stability on the M3

S0 is the only gate that blocks features:

1. `main` green, seven protected contexts, without depending on a rerun;
2. a deterministic DNS test with a controlled clock on both timeout branches;
3. the exact candidate accepted on the M3, including install, update, verify, changes, repair, cleanup and uninstall;
4. technical TUF authenticated, timestamp outside the alert window and renewal runnable;
5. converging deployment: bootstraps, product, catalog, trust and release-truth answer for the same bytes and audience;
6. no support claim bigger than the M3 evidence.

**Exit:** `owner-only` valid for the maintainer, feature work allowed, external-public still `NO-GO`.

Owner-only observation continues as a light, recurring operation. It does not need to be an external soak nor block the next functional increment while S0 stays green.

## Conditional external-public gates

These gates stay parked until there is an explicit decision to open to third parties:

| Gate | When it reopens | Criterion | Current state |
| --- | --- | --- | --- |
| EP-0 audience decision | intention to publish to third parties | ADR with baseline, support, contact and SLO | DEFERRED |
| EP-1 migration | promise to upgrade the old baseline | byte-for-byte preservation and rollback | NOT REQUIRED FOR FRESH BASELINE |
| EP-2 soak | the candidate that will be external-public | seven days of the exact digest | DEFERRED |
| EP-3 sustainable TUF | external audience | custody, backup, RTO and recovery | EXTERNAL_ONLY |
| EP-4 external acceptance | external audience | a non-maintainer user on a clean machine | DEFERRED |
| EP-5 platforms | each promoted platform | native evidence of the exact candidate | M3 ONLY |

QWLeague stays `BLOCKED_EXTERNAL`: without contact, authorization or a public contract there is no native integration.

## Owner-only delivery train

### O0 — immediate stabilization

- keep main green and do not reopen the time failure;
- finish and record the fix to the live projection (PASS, receipt 33136179763,
  artifact 9672118367);
- run an M3 acceptance after the projection converges;
- keep TUF renewable and technically monitored;
- close the audience and byte drift;
- keep releases, receipts and history until a separate destructive decision.

### F1 — diagnostics and first run

The first functional capability after S0:

- a read-only `x86qw doctor`;
- a sanitized, reviewable diagnostic bundle without secrets;
- status of installation, catalog, TUF, runtime, network, disk and permissions;
- first-run messages that explain owner-only without needless ritual.

### F2 — profiles and local discovery

- user-owned profiles kept apart from managed defaults, cache and ephemeral data;
- local favorites and recents;
- searching for and joining matches through the runtime contracts;
- offline fallback and the data's origin/freshness;
- no required dependency on QWLeague.

### F3 — local hosting

- declarative presets without versioned secrets;
- readiness, logs, stop, restart and crash recovery;
- MVDSV, QTV and QWFWD according to the M3 evidence;
- rollback and preservation of personal data.

### F4 — optional expansion

Only after real use justifies it: a demo library, training, a read-only local UI, official external adapters and additional native evidence. A persistent daemon, an online account, telemetry, a marketplace and a fleet control plane do not get in out of enthusiasm.

## Version rules

- tests, documentation or projection only: no artificial release;
- a compatible runtime, package or byte fix: 1.0.x;
- a new compatible capability in this phase: 1.0.x (1.1 retired until a new decision);
- a proven incompatible change: 2.0;
- a history reset: a destructive operational decision outside the product's SemVer.

## Definition of Done for each feature

- the issue separates outcome, non-scope, dependencies, security, privacy, rollback and docs;
- the runtime holds the reusable semantics; CLI, TUI and UI do not duplicate rules;
- a deterministic test covers success, timeout, interruption and recovery;
- a real run on the M3 records version, candidate, digest and result;
- personal data stays out of managed defaults and caches;
- an unavailable feature does not stop installing, local play or hosting;
- documentation does not raise preview to supported;
- main stays green.

## Priorities

**NOW:** S0 owner-only observation and the TUF lease on the M3.

**NEXT:** F4 only with real use (demos, adapters). FUNC-008 (read-only local UI) is already on main.

**LATER:** demos, external adapters and new platforms.

**NOT PLANNED for now:** historical migration for third parties, transactional QWLeague, a persistent daemon, online accounts, mandatory telemetry and a destructive reset without a backup.

## Minimum metrics without user telemetry

Measure in the harness and local receipts: fresh install success, lifecycle success, update/repair success, preservation incidents, time to first verified match, host readiness, diagnostic resolution, native evidence coverage, release reproducibility, mirror convergence, TUF lease reliability and human minutes per release.

## Accepted risks and controls

- **Too much process:** S0 is short; external gates are conditional.
- **Cost of a single maintainer:** small releases, explicit rollback and issue templates.
- **TUF without a successor:** technical operation continues now; independent custody is a requirement only before external-public.
- **Destructive reset:** keep a backup and the receipts before erasing any history.
- **External integration unavailable:** optional adapters and a complete local fallback.

## Next action

The current source is **1.0.16**; `1.0.15`, `1.0.14`, `1.0.13`, `1.0.12`, `1.0.11`, `1.0.10`, `1.0.9`, `1.0.8`, `1.0.7`, `1.0.6`, `1.0.5`, `1.0.4`, `1.0.3`, `1.0.2`, `1.0.1` and `0.7.13` are historical. F1–F3 and the
`host`/launcher fix stay in 1.0.x. Keep the TUF lease out of the 6 h
window. F4 and EP only with real use or an audience decision.
