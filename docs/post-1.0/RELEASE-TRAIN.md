# Post-1.0 release train — M3-first

## Canonical order

| Station | Content | Exit gate | State |
| --- | --- | --- | --- |
| S0.1 | main and the deterministic DNS contract | seven protected contexts green | PASS |
| S0.2 | exact candidate in a single-user M3 installation | install/lifecycle/verify/repair/cleanup/uninstall | PASS |
| S0.3 | technical TUF and live projection | healthy timestamp; bootstraps/product/release-truth converging | PASS |
| S0.4 | owner-only observation | the maintainer's incidents and receipts | OBSERVED |
| F1 | doctor and diagnostics | JSON contracts, sanitization and M3 tests | PASS |
| F2 | profiles, favorites and local discovery | personal data preserved and fallback | PASS |
| F3 | hosting presets and health | readiness, logs and rollback | PASS |
| EP-0 | audience decision | explicit external-public ADR | DEFERRED |
| EP-1 | historical migration | only if an old upgrade is promised | CONDITIONAL |
| EP-2 | soak of the exact candidate | seven full days | DEFERRED |
| EP-3 | TUF with independent custody | production RTO and recovery | EXTERNAL_ONLY |
| EP-4 | acceptance by an external user | clean machine and support | DEFERRED |
| EP-5 | additional platforms | native evidence per platform | DEFERRED |

## Rule for advancing

S0 is the only gate that blocks features. After S0, F1, F2 and F3 advance in small PRs on the M3. EP-0 to EP-5 are not prerequisites for working on the owner-only product; they reopen only when the audience changes.

Any change to product bytes creates a candidate and repeats S0. A change only to tests, documentation or projection does not create an artificial release. TUF metadata stays last when there is an authorized publication.

## State of the load

The load is `x86qw-installer-1.0.0`, product commit `e12ed081...`, installer SHA-256 `d3274e...`, candidate `0bde05...`, E2 mirror and E3 25/25 native M3 evidence. It is valid for the maintainer and is not external-public.

## Migration and a new baseline

The first open publication may declare a new baseline and start from a clean install. In that case EP-1 does not exist for the first opening. If the product promises an upgrade from 0.7.13 or another baseline, EP-1 returns to the train before the promotion.

## Handoff

The Maker records state, commit, digest, environment and rollback. The Checker tries to refute it. The maintainer decides advance, hold or rollback. No handoff holds secrets, cookies or claims without evidence.

## References

- [master plan](MASTER-PLAN.md)
- [issue backlog](ISSUE-BACKLOG.md)
- [dependency graph](DEPENDENCY-GRAPH.md)
- [release truth](RELEASE-TRUTH.md)
