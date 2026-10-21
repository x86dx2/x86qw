# Metrics and observation criteria

The metrics below measure gates, not product vanity. Every value needs a UTC
timestamp, commit, run/artifact, origin and Checker. A missing measurement is
`BLOCKED`, not zero.

## Current snapshot — 2026-08-28T02:32:21Z

| Metric | Value at the audit | Level | Next threshold |
| --- | --- | --- | --- |
| main CI | `GREEN`; Validate `33135951867`, 5/5 required jobs | E2 | keep zero reds in 0A |
| Windows portable-contract | Python 3.10 and 3.13 green | E2 | keep the protected matrix |
| installer mirror equality | `E2`, GitHub/GitLab equal | E2 | keep it and add redundancy |
| exact native candidate | `25/25` Apple M3 | E3 | do not extrapolate to non-M3 |
| independent rebuild | not run | E0 | explicit E4 or an exception |
| TUF root | v1 | E2 | observable rotation/recovery |
| TUF timestamp/snapshot/targets | v30/v29/v29 | E2 | lease without a critical alert |
| timestamp expiry | `2026-09-27T02:15:12Z` | E2 | 6 h alert and proven renewal |
| SBOM/ownership classified | `0/87` classified; `87/87` unclassified/NOASSERTION | E1 | 87/87 classified or exceptions |
| package mirrors | GitHub/GitLab byte-equal for the installer | E2 | keep verifying after every projection |
| QWLeague contract | no verified API/OAuth/webhook | E1 | official contract |
| site headers/CWV | not measured | E0 | independent measurement before a claim |

## Gate metrics

### 0A — CI

`ci_red_jobs = 0`, with an explicit Windows 3.10/3.13 matrix. Record the fake's
duration with a virtual clock; do not use real sleeps as proof of a timeout.

### 0B — TUF

Record authenticated fetch successes/failures, hours to expiry, metadata
divergence, time to alert and time to recovery. One healthy query does not
replace the operating window.

### 0C — release truth

Measure size/digest equality between each mirror, the receipt's references to
the final candidate, SBOM/ownership completeness and the presence of an E4
rebuild. The result is a boolean only when every field has a source.

### EP-1/EP-2 — external

Count the real fixture versions migrated, preservation/rollback, consecutive
soak days and the HTTPS references observed. Any gap restarts the seven-day
counter.

### EP-5 — platform

Count the platforms with E3 of the exact candidate. Until then, the public
matrix must show `preview`/`conditional`; do not invent success for the rest.

## What not to measure in this delivery

Do not run additional native tests, a public monitor, a browser, load, a
deploy, a TUF change or a QWLeague integration. This package has no evidence
of site headers, CWV, OAuth or webhook.

## Minimum observation format

```text
metric=<name>
observed_at=<UTC>
commit=<SHA>
run=<run-or-local>
artifact=<id-or-none>
value=<value>
evidence_level=E0|E1|E2|E3|E4
checker=<role>
```

See [risk-register.json](risk-register.json) and [release-truth.json](release-truth.json)
for the machine-readable values.
