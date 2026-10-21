# CI health and Gate 0A

**Current state:** `MAIN=GREEN`; Gate 0A stays closed. The `main` line's
Validate passed in run
[33135951867](https://github.com/x86dx2/x86qw/actions/runs/33135951867),
on commit `962fb2b2cc27560e982c2255d9299a55f16acdd1`, with 5/5 required jobs
green; the preview job was skipped and not used as support.

## Closed diagnosis

The historical run 31853649373 failed only in
portable-contract / Windows / Python 3.10. The fix was confined to
maintenance/tests/test_downloader.py: both time branches use a controlled
clock, the fake process protocol is explicit, and the kill, bounded reap,
detach and timeout scenarios are deterministic regressions.

Checker verification:

- downloader module: 87 tests OK (1 skip);
- 30,000 repeated runs of the three new cases: zero failures;
- adversarial cases of kill, wait, TimeoutExpired and partial output
  kept the budget and the expected error;
- manage.py verify --no-tests and git diff --check: OK;
- Windows/Python 3.10 and 3.13 and the protected matrix: green in run
  33135951867.

The classification is **test-only**. There is no evidence of a needed change in
the runtime/package and no reason for 1.0.1 from this finding. If a future
product reproduction changes bytes/behavior, the decision must be reopened with
a new candidate, receipt and observation.
