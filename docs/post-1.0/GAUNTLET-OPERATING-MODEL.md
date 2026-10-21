# Gauntlet operating model

The gauntlet is the minimum path for a proposal to get through evidence,
security and release. It does not turn the documentation executor into a
product authority.

## Roles

| Role | Responsibility | May do in this issue |
| --- | --- | --- |
| Maker | prepares the diff and the evidence | create/edit the approved local artifacts |
| Checker | repeats the checks and points out failures | later independent review |
| Release owner | decides advance, hold or rollback | record the decision; never implicit |
| Custodian | keeps trust/recovery | missing for production in this snapshot |
| Orchestrator | picks the scope and chains agents | receive PLAN_DEVIATION/Evidence Pack |

One maintainer can hold both Maker and owner-only operator, but self-review is
not an independent Checker for trust or an external audience.

## Passes

1. **Intake:** confirm the issue, branch, contract and path allowlist.
2. **Baseline:** record status, branch, HEAD, dirty files, limitations and
   prior evidence.
3. **Maker:** change only the scope; use the labels `VERIFIED FACT`,
   `INFERENCE`, `PROPOSAL`, `BLOCKED`.
4. **Static:** parse JSON, relative links, `git diff --check`, allowlist and
   absence of secrets/unobserved URLs.
5. **Semantic:** compare hashes, gates, audience, dependencies and claims with
   the baseline.
6. **Adversarial:** look for unproven site/CWV/native claims, a receipt
   pointing at RC, audience drift and incomplete fields.
7. **Decision:** the Checker returns findings; the owner chooses
   `advance`/`hold`; the executor fixes only authorized findings.
8. **Handoff:** Evidence Pack with baseline, changed files, diff summary,
   tests/results, limitations and deviations.

## Gauntlet invariants

- do not create a remote issue for a backlog item;
- do not publish code, a workflow, a release, trust metadata or a deploy in
  this materialization; the commit/push of this branch's documentation happens
  only after the Checker and the Finalizer approve the diff;
- do not run an authenticated external mutation;
- do not raise E1/E2 to E3/E4 through wording;
- do not close a risk for lack of data;
- do not change `quake-world/` or files outside the allowlist.

## Minimum artifact per item

Every backlog item must have title, problem, outcome, scope, non-scope,
dependencies, acceptance, gates, tests, platform, security, privacy, rollback,
docs, Maker, Checker, target version and audience. [backlog.json](backlog.json)
is the source of structure; [ISSUE-BACKLOG.md](ISSUE-BACKLOG.md) is the human
reading.

## Rollback

A documentation rollback removes only this issue's diff in an authorized
revision, keeping the historical snapshots. A rollback of product, workflow,
release, trust or site is out of scope and needs a new contract.

## Gauntlet evidence ledger

| Evidence | Level | State | Rule of use |
| --- | --- | --- | --- |
| HEAD/main and Validate run | E1/E2 | MAIN RED | blocks 0A and feature work |
| focused DNS with a controlled clock | E2 local | test-only hypothesis | still needs Windows 3.10/3.13 |
| authenticated TUF root/roles | E1/E2 | HEALTHY at the observed moment | does not prove custody/recovery |
| GitHub/GitLab installer byte equality | E2 | converging | not an E4 rebuild |
| final candidate on the Apple M3 | E3 | 25/25 | does not promote non-M3 |
| final receipt/public acceptance | E1/E2 | RC1 mixed in | does not approve external-public |
| final ownership/SBOM | E1 | 87/87 NOASSERTION | gate 0C open |
| QWLeague discovery | E1 public | BLOCKED_EXTERNAL | no scraping, auth or claim |

Every row keeps the commit, digest, run/endpoint, UTC and Checker. Missing
evidence is `BLOCKED`, never `PASS`.
