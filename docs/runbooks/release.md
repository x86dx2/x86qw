# CLI release and TUF operations

Everything here runs with the repository's Go tools. Commands start from the
root of the checkout.

## What is automatic

| What | When | Workflow |
| --- | --- | --- |
| Site deploy | on every merge into `main` that touches `site/public` | `site-deploy.yml` |
| Timestamp renewal (30-day lease) | Monday, 06:41 UTC | `tuf-timestamp-auto-renewal.yml` |
| Monitor of the public chain | every hour | `tuf-monitor.yml` |

- The deploy publishes what is in `main`, never rolls the TUF timestamp back
  to an earlier version and refuses a version already published with other
  bytes. When it starts serving a new CLI, it marks that CLI's release as
  *latest*.
- The renewal uses the timestamp role's online key, publishes, checks and
  opens a PR that records the new timestamp in `main`. It refuses to run if
  production and `main` diverge. Every failure opens a P0 issue.
- The monitor fails (and opens a P0 issue) when the timestamp has less than 72
  hours left, snapshot or targets less than 14 days, the root less than 60
  days, when production has not served `main` for more than an hour or when
  the monitor itself stopped.

## Publish a CLI version

1. Publish the binaries:

   ```sh
   gh workflow run go-release.yml --ref main -f version=X.Y.Z
   ```

   The `cli-release` environment asks for approval (reviewer `x86dx2`). The
   workflow tests, builds the five binaries, generates `SHA256SUMS`, attests
   the provenance and publishes `x86qw-cli-X.Y.Z` as a *prerelease*.

2. Prepare the checkout, on the Mac that keeps the offline keys:

   ```sh
   git switch -c release/cli-X.Y.Z origin/main
   go -C go run ./cmd/x86qw-release prepare --version X.Y.Z
   ```

   `prepare` downloads the binaries, checks each one against
   `SHA256SUMS` and, with `gh attestation verify`, that `go-release.yml`
   built it, then rewrites the catalog (with a new `generated_at`),
   `product.json`, the `install.sh` and `install.ps1` bootstraps and the
   version named in `README.md` and in `index.html`. Then it signs the next
   TUF generation (the higher of checkout and production, plus one), removes
   the previous one from the checkout and runs `x86qw-content verify`.

3. Review the diff, commit, open the PR and wait for CI.
4. Merge (squash). The deploy runs by itself, and the release becomes
   *latest*.
5. Check: `go -C go run ./cmd/x86qw-release monitor --checkout`.

The `TestCheckoutNamesOneRelease` test fails if the catalog, the bootstraps,
`README.md` and the page name different CLI versions.

## Publish content

After `x86qw-content update`, `add` or `build --register` and
`x86qw-content publish`, the catalog changed and needs a new TUF generation
before the PR:

```sh
go -C go run ./cmd/x86qw-release sign
go -C go run ./cmd/x86qw-content verify
```

Without it, `verify` reports "the local catalog differs from the TUF target;
sign it with x86qw-release sign".

## Renew snapshot and targets

Snapshot and targets are valid for 90 days from signing and are only renewed
with the offline keys. Every CLI or content release already renews them.
Without a release, on the same Mac:

```sh
git switch -c ops/tuf-renew origin/main
go -C go run ./cmd/x86qw-release sign
go -C go run ./cmd/x86qw-content verify
```

After that: PR, merge and automatic deploy. The monitor warns 14 days before
expiry.

## Root

Root v1 expires on 2027-08-10, and the monitor warns 60 days before. Renewing
the root needs the offline root keys and a `2.root.json` signed by the
current root and the new one; the client follows the chain of published roots
from the one it embeds (`go/internal/trust/root.json`). There is no command
for it yet: plan the work before June 2027.

## When something fails

- **The renewal refused because production diverges from `main`.** Run
  `gh workflow run site-deploy.yml` and, when it finishes,
  `gh workflow run tuf-timestamp-auto-renewal.yml`.
- **A deploy failed and `main` has site changes that are not live.** The next
  renewal also publishes `main`'s `site/public`, but do not wait for it: run
  `gh workflow run site-deploy.yml`.
- **The deploy refused: "production serves timestamp N and main M".** The
  renewal published and the PR that records the timestamp has not merged
  yet. Wait for its merge; the deploy runs again.
- **The renewal published, but the record PR never opened** (the run fails at
  "Record the published timestamp on main"; the `bot/tuf-timestamp-vN` branch
  exists without a PR). Production stays ahead of `main` and every deploy
  refuses until the record merges. Open the PR by hand, wait for CI and
  merge:

  ```sh
  gh pr create --base main --head bot/tuf-timestamp-vN \
    --title "chore(trust): record published TUF timestamp vN" --body "Manual record."
  ```

  Since #383 the PR is opened by the job's own token (the repository lets
  Actions create PRs) and `RELEASE_GH_TOKEN` only pushes the branch, whose
  required checks run on `push`. Before that, the missing PR permission on
  that token left v56 unrecorded (#355).
- **The renewal published, but failed at "Verify the public chain and the
  published timestamp"** (no `bot/tuf-timestamp-vN` branch). Production
  serves vN and `main` the one before, so every deploy refuses. Record what
  production serves on a branch from `main`, then open the PR and merge it;
  the merge deploys `main` again:

  ```sh
  go -C go run ./cmd/x86qw-release live-timestamp \
    --out site/public/api/v1/trust/metadata/timestamp.json
  ```

  The check now waits up to five minutes for the edge to serve the new
  timestamp; before, one read right after the deploy left v65 unrecorded.
- **The renewal PR (`bot/tuf-timestamp-vN`) failed CI and stayed open.**
  Production stays ahead of `main` until it merges. Fix what CI pointed out on
  the branch itself and merge; the deploy and the monitor go back to normal.
- **The deploy refused: "main and production have different timestamps N".**
  A PR signed the same version the renewal published. On the PR's branch, run
  `go -C go run ./cmd/x86qw-release sign` again and update the PR.
- **The production timestamp is about to expire.** Run the renewal by hand.
  If the online key fails, `x86qw-release sign` produces a new generation with
  the offline keys.
- **The monitor says production does not serve `main`.** Look at the last
  *Deploy site* run; run it again through `workflow_dispatch`.
- **Compromised key.** Follow the [compromise runbook](trust-compromise.md).

## Keys and environments

- The offline keys (root, targets, snapshot, timestamp) live in
  `.tuf-production/keys`, only on the maintainer's Mac and outside Git.
- The timestamp role's online key is the repository secret
  `TUF_TIMESTAMP_KEY_B64`. It does not sign root, targets or snapshot.
- The `cli-release` environment requires approval and only accepts protected
  branches; it publishes binaries.
- The `release` environment asks for no approval, also only accepts protected
  branches and keeps `CLOUDFLARE_API_TOKEN` and `RELEASE_GH_TOKEN`; the deploy
  and the renewal use that environment.
