# Decision log

| ID | Decision | State | Evidence/authority |
| --- | --- | --- | --- |
| D-01 | 1.0.0 exists as an owner-only release | decided | release page, receipt, PROJECT-STATUS |
| D-02 | external-public is not authorized | decided | release audience contract |
| D-03 | green main is a precondition; Gate 0A is closed | decided | Validate 33135951867, SHA 962fb2b…, 5/5 required jobs |
| D-04 | the DNS failure was test-only; no byte/runtime impact was observed | decided | deterministic tests, 87 tests, 30,000 repetitions, green matrix |
| D-05 | the TUF lease authenticates technically; 6 h monitor healthy under the 7-day owner-only policy | decided | timestamp v30 expires 2026-09-27T02:15:12Z; current monitor healthy |
| D-06 | production TUF custody/recovery are external-public gaps | deferred | protected drill recorded; independent custody/RTO not yet proven |
| D-07 | final acceptance on the M3 is E3 25/25 | decided | release-evidence of the exact candidate |
| D-08 | GitHub/GitLab equality is E2, not E4 | decided | byte comparison; no independent rebuild |
| D-09 | the final receipt must not reuse the RC1 acceptance | accepted_owner_only | historical receipt kept apart from the final evidence; reopen only for external-public |
| D-10 | cleanup --personal-data must decide on covering qw/demos | deferred | contract versus observed coverage; reopen only if a historical upgrade is promised |
| D-11 | QWLeague stays BLOCKED_EXTERNAL | decided | no verified official contract/authorization |
| D-12 | no 1.1 capability starts before 0A–0E | decided | Master Plan and stop conditions |
| D-13 | issue #164 comes before the documentation branch | done | CONTRIBUTING/ROADMAP policy |
| D-14 | this work changes only approved documents; it does not change product/publication | done | issue #164 and documentation allowlist |
| D-15 | the release-truth source was merged; the live deployment converges on the candidate | done | receipt 33136179763; root 200 owner-only; public TUF v30/v29 |
| D-16 | Gate 0A passed; Gate 0B stays external-only; 0C owner-only converged | decided | green main; receipt 33136179763; healthy TUF monitor |
| D-17 | the exact public 0.7.13 migration is not approved by inference from the harness | decided | installer SHA 11460440… blocked; the harness injects historical metadata |
| D-18 | a timestamp inside the warning window blocks feature work | decided | stop condition; 6h monitor at 2026-08-15T15:38:22Z |
| D-19 | source and deployment cannot answer incompatible versions/audiences | resolved | product/catalog/site/release-truth converge; receipt 33136179763 |
| D-20 | the owner-only timestamp expires in 7 days at most | decided | ADR 0006 amendment; the 24 h cap returns before a public catalog |

## Pending human decisions

- name an independent TUF owner and backup, custody and RTO before
  `external-public`;
- decide on an external audience and the platforms it will cover;
- settle the 0.7.13 installer migration contract without a privileged fixture
  if a historical upgrade is promised;
- decide the final cleanup/purge semantics for historical installations;
- review and classify the SBOM/ownership exceptions before wide redistribution;
- provide an official contract and authorization before any QWLeague adapter;
- keep `external-public=NO-GO` until EP-0–EP-5 have protected evidence.
