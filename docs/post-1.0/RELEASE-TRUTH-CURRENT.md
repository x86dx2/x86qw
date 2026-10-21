# Release truth — current M3-first scope

The current deployment state is exclusively
`https://qw.x86.com.br/api/v1/release-truth.json`.
[`release-truth-current.json`](release-truth-current.json) holds
only that pointer and the audience invariants. Check the live observation
with:

```sh
python3 maintenance/tools/verify_live_release_truth.py
```

[`release-truth-projection-seed.json`](release-truth-projection-seed.json) is the
versioned offline seed used by the static projection and the tests. It is not
the authority of the current deployment. The rest of this document records the
last audited snapshot, without turning it into permanent live truth.

## Last snapshot verified at 2026-08-28T02:32:21Z

- `MAIN=GREEN`: the `main` line passed Validate in run
  [33135951867](https://github.com/x86dx2/x86qw/actions/runs/33135951867), on
  commit `962fb2b2cc27560e982c2255d9299a55f16acdd1`.
- `TUF=HEALTHY`: root v1, timestamp v30 and snapshot/targets v29 authenticate
  the public catalog of 75 packages. The timestamp expires at
  `2026-09-27T02:15:12Z`, outside the six-hour alert window at the time of the
  observation.
- `1.0.0 owner-only=VALID_FOR_SINGLE_USER_M3`: the exact candidate stays
  valid for one maintainer on the Apple M3, with installer SHA-256
  `d3274e6aa2f1e3078ac5000ffae8b97c9efd329f3c2a87499bf1c57e5f388cb8`.
- `external-public=NO-GO`: historical migration, external soak, independent
  custody/RTO and acceptance by an external user stay conditional on an
  explicit decision to open the audience.
- `FEATURE WORK=ALLOWED` while S0-M3 stays green.

## Authorities

| Authority | Value in the 2026-08-28 snapshot |
| --- | --- |
| source | current baseline `1.0.16` in `dist/installer/VERSION` |
| candidate/release | `x86qw-installer-1.0.0`, commit `e12ed081b968f820f47200e4be954a4f444056a1`, audience `owner-only` |
| deployment | site, bootstraps, product, catalog, trust and release-truth converging |
| development | `main` green in Validate `33135951867`; exact candidate preserved by the receipt and the M3 evidence |
| scope | one user, one maintainer and the Apple M3 native lab |

## Public deployment

Run [33136179763](https://github.com/x86dx2/x86qw/actions/runs/33136179763)
repaired and verified the owner-only projection. The loaded candidate is the
same at the canonical endpoint `https://qw.x86.com.br/`: product and catalog report `1.0.0`, the HTTP 200
root shows `owner-only`, and `/api/v1/release-truth.json` answers 200.

The public catalog has SHA-256
`a03a8b0e3dcd97a66d338891dacd6ca80befdbee907ed9b83007a538bb97646a`; the
release-truth projection had SHA-256
`601e30eb9025a76782e75fe417723fda404539d065d5c1bd338a4a4b382a6cf7` in that
public observation.
GitHub and GitLab stay byte-equal for the owner-only installer. The final
release has 600825 bytes and candidate SHA-256
`0bde0550895cab24abf8a3ee974da011e031fea11279148a41635e173cbdcc21`.

The projection receipt records the same state: `CONVERGED_CANDIDATE_DEPLOYMENT`,
root probe `200_OWNER_ONLY`, repair artifact `9672118367` and site publication
`projection-only`. The run used the observed commit
`962fb2b2cc27560e982c2255d9299a55f16acdd1`; it did not rebuild the candidate nor
change its immutable bytes.

## Technical TUF and operation

In the snapshot, the public TUF was authenticated by the Ed25519 v1 root. Timestamp v30 was
renewed in run `33135314707`, with artifact `9671800710` and report SHA-256
`fe90b29ca4aa49f3b3c5a33897edd67b7069685d1622f3ae8d85f348a172e7cb`.
Snapshot and targets are at v29 and expire after the timestamp. The public
monitor passed the six-hour and one-hour thresholds on both domains.

The technical recovery drill is recorded in run `31900793093`; it proves
the operational contract with ephemeral keys, but does not prove independent
human custody. Custody, backup, RTO and succession remain `external-public`
gates. The historical correction of the root version is in
[`ERRATA-TUF-ROOT-VERSION.md`](ERRATA-TUF-ROOT-VERSION.md).

## Gates and limits

### Done for `owner-only`

- candidate built once, with an immutable digest and installer;
- M3 evidence of the exact candidate and a throwaway lifecycle;
- `single-user` public acceptance, including clean install, verify, update,
  repair, changes, uninstall and purge;
- mirrors, bootstraps, product, catalog, TUF, site and release-truth verified;
- feature work released while `S0-M3` stays green.

### Parked until `external-public`

- real migration of the public `0.7.13` installation and preservation of upgrades;
- a protected seven-day soak and acceptance by an external user;
- independent custody, backup and TUF RTO observed in production;
- native evidence outside the Apple M3 and any additional support claim;
- QWLeague or another external integration without explicit contact and contract.

The historical issues and their artifacts stay preserved; a closed issue is not
a synonym for `external-public` authorization. The decision in force remains
that of [`ADR 0008`](../adr/0008-owner-only-release-gates.md), and the detailed
historical release is in
[`1.0.0-owner-only-publication-2026-08-15.md`](../releases/1.0.0-owner-only-publication-2026-08-15.md).
