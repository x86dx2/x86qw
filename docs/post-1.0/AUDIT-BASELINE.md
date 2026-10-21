# Post-1.0 audit baseline

**State:** VERIFIED FACT for the observed values; INFERENCE for
classifications that still depend on confirmation; BLOCKED for gates without
evidence; PROPOSAL only for what is still to be decided.

## Scope and snapshot

This is the snapshot approved for issue #164. The audit took place in UTC
between `2026-08-15T01:28:37Z` and `2026-08-15T01:55:48Z`, on commit
`dc080acacba7d70ced0cb311f08a81259bf5a9bd`. The snapshot is dated: it does not
replace a fresh check of the network, the runners or the releases.

The historical claim of root v2 below was corrected by the
[TUF erratum](ERRATA-TUF-ROOT-VERSION.md), without rewriting the dated snapshot.

| Area | Observed state | Evidence | Allowed reading |
| --- | --- | --- | --- |
| Canonical line | `origin/main`, snapshot `dc080ac...` | git and audit | VERIFIED FACT; revalidate before a promotion |
| Main/CI | `RED` | run `31853649373` | BLOCKED for gate 0A |
| TUF | `HEALTHY` at the time of the audit | root v2; timestamp/snapshot/targets v18; valid until `2026-08-15T21:09:01Z` | technical VERIFIED FACT, with a 6 h lease warning |
| TUF reachability | transient incident observed | canonical public endpoint | INFERENCE about availability; do not claim headers/CWV |
| Custody/recovery | missing for production | audit | BLOCKED for sustainable operation |
| `1.0.0` owner-only | `AT-RISK` at the audit | conflicts and P1s below | do not reclassify as GO without settling the release truth |
| `external-public` | `NO-GO` | gates 0A–EP-5 | VERIFIED DECISION |
| Feature work | blocked | depends on gates | do not start product implementation |

## Observed release identity

- tag: `x86qw-installer-1.0.0`;
- product commit: `e12ed081b968f820f47200e4be954a4f444056a1`;
- installer: `600825` bytes, SHA-256
  `d3274e6aa2f1e3078ac5000ffae8b97c9efd329f3c2a87499bf1c57e5f388cb8`;
- `candidate.json`: `17405` bytes, SHA-256
  `0bde0550895cab24abf8a3ee974da011e031fea11279148a41635e173cbdcc21`;
- GitHub/GitLab comparison of the installer: `E2`, byte-for-byte equal;
- independent rebuild `E4`: not run in this snapshot;
- exact candidate on Apple M3: `E3`, `25/25` cases.

The existing durable references are the
[owner-only publication record](../releases/1.0.0-owner-only-publication-2026-08-15.md)
and the [M3 acceptance](../releases/1.0.0-owner-only-public-acceptance-m3-2026-08-15.json).
They are dated sources, not an authorization to change the audience.

## Known limitations and conflicts

1. The only red in run `31853649373` is Windows/Python 3.10 in the portable
   contract. The explanation that the fake takes no arguments and that there is
   an uncontrolled 10 ms `sleep` is a **test-only INFERENCE**, until the
   protocol is reproduced with a deterministic clock. Gate 0A requires
   Windows Python 3.10 and 3.13 green.
2. The final receipt still points `public_acceptance` at RC1, while the exact
   final evidence is kept separately. That is a contradiction of authority,
   not proof that the final bytes differ.
3. Ownership/SBOM reports `87/87` items as `unclassified`/`NOASSERTION`.
4. The promise of `cleanup --personal-data` includes `qw`/demos, but the
   observed cleanup does not cover those paths.
5. There is a single maintainer and self-review; GitHub's security controls are
   the ones recorded in the audit and need independent verification.
6. The observed package mirror is a single one; no redundancy is proven.
7. Linux, Windows, macOS Intel, nightly and any native run that is not the
   exact candidate on the M3 must stay `preview`. Do not claim smoke, headers,
   Core Web Vitals or native support that was not observed.
8. The QWLeague assessment is `BLOCKED_EXTERNAL`: only the public home/sitemap
   were observed; there is no verified official contract for an API, OAuth or
   webhook.

## Executor baseline

Before this documentation was written, the checkout was on branch
`docs/164-post-1-0-master-plan`, tracking the remote of the same name, at
`dc080acacba7d70ced0cb311f08a81259bf5a9bd`. There were no tracked changes
outside this scope; the current `git status --short --untracked-files=all`
finds no `quake-world/`, and no file under that path was created or changed by
this work. `docs/post-1.0/` did not exist before this work. The earlier
`git diff --check` exited with code `0`.

This execution baseline is distinct from the audit snapshot: the first
describes the checkout before this diff; the second describes the release state
observed in the UTC interval above.

## Evidence levels

| Level | Meaning |
| --- | --- |
| `E0` | proposal or no observation |
| `E1` | documentary source, fixture or local check |
| `E2` | independent comparison of sources/bytes, without a rebuild |
| `E3` | native run/acceptance of the exact candidate on the M3 |
| `E4` | independent rebuild showing the same bytes; not observed |

## Operating references

- [PROJECT-STATUS](../PROJECT-STATUS.md) — earlier operating source, to be
  reconciled with this snapshot where they conflict;
- [release runbook](../runbooks/release.md);
- [ADR 0008 — owner-only](../adr/0008-owner-only-release-gates.md);
- [machine-readable release truth](release-truth.json);
- [risk register](risk-register.json).
