# Risk register — M3-first

Owner-only risks and the conditional risks of a future external-public audience are kept apart. No external risk becomes an artificial block on features on the M3.

| ID | Risk | P | I | Score | State | Scope | Mitigation |
| --- | --- | ---: | ---: | --- | --- | --- | --- |
| R-001 | red CI masks a regression in the Windows contract | high | high | high | MITIGATED | owner-only | deterministic protocol and zero flakes |
| R-002 | an authority communicates the wrong bytes or audience | medium | critical | high | CONTROLLED | owner-only | single projection, hashes and live verification; receipt 33136179763 |
| R-003 | the TUF lease expires without renewal | medium | critical | high | CONTROLLED | owner-only | technical monitor, alert and renewal before the window |
| R-004 | transient reachability interrupts an update | medium | high | high | ACCEPTED_WITH_FALLBACK | owner-only | retry, cache, mirror and diagnostics |
| R-005 | unclassified SBOM/ownership | high | high | high | DEFERRED | external-public | classify before wide redistribution |
| R-006 | cleanup removes or abandons personal data | medium | high | high | CONTROLLED | owner-only | contract, dry-run, backup and explicit purge |
| R-007 | self-review fails on release/trust | medium | critical | high | ACCEPTED_OWNER_ONLY | external-public | custodian and reviewer before opening |
| R-008 | a single mirror becomes a point of failure | medium | high | high | CONTROLLED | owner-only | fallback and verification after deploy |
| R-009 | a support claim exceeds the M3 evidence | high | medium | high | MITIGATED | owner-only | the M3 as the only native support; the rest preview |
| R-010 | external migration/soak fails after opening | medium | critical | high | DEFERRED | external-public | EP-1/EP-2 only when the audience changes |
| R-011 | QWLeague uses an unauthorized protocol | low | high | medium | BLOCKED_EXTERNAL | optional | no scraping, auth or required dependency |
| R-012 | too much process freezes features | high | high | high | MITIGATED_BY_S0 | owner-only | short S0; conditional external gates |
| R-013 | a destructive reset loses evidence or data | low | critical | high | DEFERRED | future publication | backup/export/rollback before erasing history |

## Treatment policy

- **Accept:** an explicit risk in the owner-only scope, without claiming external support.
- **Mitigate:** a small issue with Maker, Checker, test and rollback.
- **Transfer:** only with real custody or a real contract.
- **Avoid:** do not start an external integration without authorization.
- **Close:** only with evidence matching the acceptance.

Git history is not erased as part of stabilization. A possible publication with a new baseline must be planned as a separate destructive operation, with a verifiable copy of the evidence.

The machine-readable data is in [risk-register.json](risk-register.json).
