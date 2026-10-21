# Release truth

This document defines which authority answers each question about the
release. When two sources disagree, the state is `BLOCKED`; the last source
consulted does not take precedence for being the most recent.

## Authority hierarchy

1. published bytes and a digest checked in more than one source (`E2`);
2. candidate and product commit bound by the workflow;
3. final receipt and M3 evidence of the exact candidate (`E3`);
4. TUF metadata authenticated by the embedded root;
5. CI run/artifact and derived documentation.

No release note or issue replaces a byte authority. The
[JSON of this historical snapshot](release-truth.json) is an index, not a new
TUF catalog. The current authority is the live endpoint
`https://qw.x86.com.br/api/v1/release-truth.json`; the file
[`release-truth-current.json`](release-truth-current.json) is its pointer and
[`release-truth-projection-seed.json`](release-truth-projection-seed.json) is
only the last versioned offline seed.

## Observed identity

| Field | Value | Evidence |
| --- | --- | --- |
| tag | `x86qw-installer-1.0.0` | audited release |
| product commit | `e12ed081b968f820f47200e4be954a4f444056a1` | candidate/promotion |
| installer | `600825` bytes; `d3274e6aa2f1e3078ac5000ffae8b97c9efd329f3c2a87499bf1c57e5f388cb8` | E2 GitHub/GitLab |
| candidate | `17405` bytes; `0bde0550895cab24abf8a3ee974da011e031fea11279148a41635e173cbdcc21` | exact candidate |
| native | `25/25` on the Apple M3 | E3 |
| rebuild | not observed | E4 missing |
| mirror | GitHub/GitLab equal for the installer | E2; operational redundancy not yet proven |

## State per dimension

| Dimension | State | Rule |
| --- | --- | --- |
| artifact | VERIFIED FACT | use the tag, commit, bytes and digests above |
| validation | VERIFIED FACT on the M3; BLOCKED on non-M3 platforms | do not extrapolate E3 |
| owner-only audience | VALID_FOR_SINGLE_USER_M3 | exact candidate, M3 and single-user lifecycle verified |
| external-public audience | NO-GO | needs EP-0–EP-5 and explicit authorization |
| technical TUF | HEALTHY | root v1, timestamp v30, snapshot/targets v29 and healthy monitor |
| operational release | CONVERGED_CANDIDATE_DEPLOYMENT | projection 33136179763, bootstraps/product/catalog/release-truth verified |

## Open contradictions

| ID | Contradiction | How to resolve | State |
| --- | --- | --- | --- |
| RT-01 | the final receipt's `public_acceptance` points at RC1; the final evidence is in a separate file | keep it separate in owner-only; reconcile only in an external-public promotion | ACCEPTED_OWNER_ONLY |
| RT-02 | source/release/deployment/development and audience drift | the current projection and the site were updated and verified | RESOLVED_DEPLOYMENT_VERIFIED |
| RT-03 | ownership/SBOM `87/87` `unclassified`/`NOASSERTION` | classify every item, or declare an approved exception with an expiry | DEFERRED_EXTERNAL |
| RT-04 | a single package mirror does not prove operational redundancy | keep GitHub/GitLab equality and record the risk | DEFERRED_CONDITIONAL_UPGRADE |
| RT-05 | historical snapshots deliberately differ from the current state | re-audit current documents without erasing dated snapshots | DOCUMENTED_HISTORICAL |

## Rules for consumers

- `latest` is not a sufficient authority: resolve through the TUF catalog and
  the verified receipt/candidate;
- `owner-only`, `external-public`, `supported`, `conditional` and `preview` are
  not synonyms;
- do not report HTTP headers, Core Web Vitals, OAuth, webhook or native support
  without specific evidence;
- byte changes need a new candidate and invalidate the earlier chain;
- a documentation-only change does not create `1.0.1`.

## Related evidence

- [baseline](AUDIT-BASELINE.md);
- [existing final release](../releases/1.0.0-owner-only-publication-2026-08-15.md);
- [M3 acceptance](../releases/1.0.0-owner-only-public-acceptance-m3-2026-08-15.json);
- [release runbook](../runbooks/release.md);
- [JSON](release-truth.json).
