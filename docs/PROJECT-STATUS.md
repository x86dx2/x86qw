# Current state of the project

## Operating mode

The project is `owner-only`: a single user, the maintainer, with clean
reinstalls allowed. The decision is in
[ADR 0008](adr/0008-owner-only-release-gates.md). The `external-public`
audience was not authorized; opening it requires deciding again on migrating
old installations, a period of use by third parties and key custody.

## What is published

- **CLI:** in Go, published as `x86qw-cli-X.Y.Z` for macOS (arm64 and amd64),
  Linux (amd64 and arm64) and Windows (x64). The current version is the one
  the [README](../README.md#installation) and the
  [product catalog](https://qw.x86.com.br/api/v1/product.json) name; the
  site's bootstrap pins it by size and SHA-256.
- **Content:** 21 components in 28 packages, five games, ezQuake stable and
  nightly, MVDSV, QTV and QWFWD.
- **Trust:** a catalog authenticated by TUF. Root v1 (expires 2027-08-10),
  snapshot and targets signed offline (expire 2027-01-19) and a timestamp
  renewed every week with a 30-day lease.

The Python `0.x` and `1.0.x` installers were retired with CLI 2.0 and remain
historical on the mirrors; their records are in [releases/](releases/).

## How to check the live state

Nothing in this document replaces looking at what is live:

```sh
go -C go run ./cmd/x86qw-release monitor --checkout   # public TUF chain and production = main
x86qw catalog                                          # the catalog the CLI authenticates
```

The `tuf-monitor.yml` workflow runs the first check every hour and opens a P0
issue when a role enters its warning window (timestamp 72 hours, snapshot and
targets 14 days, root 60 days), when production stops serving `main` for more
than an hour or when the workflow itself stops running.

## Operations

- **Site:** every merge that changes `site/public` goes live through the
  `site-deploy.yml` workflow, which refuses to roll the TUF timestamp back.
- **TUF renewal:** `tuf-timestamp-auto-renewal.yml` runs every Monday without
  manual approval and opens an issue when it fails.
- **CLI release:** `go-release.yml` publishes the binaries as a prerelease,
  with approval in the `cli-release` environment; `x86qw-release prepare`
  brings the release into the checkout. The step by step is in the
  [release runbook](runbooks/release.md).
- **CI:** Go tests on Linux (with the race detector, staticcheck and
  govulncheck) and on Windows, distribution and site checks on every PR; a
  nightly e2e that installs for real on Linux, macOS and Windows; content
  parity and corruption checks every week.

## Known limits

- Real use is the maintainer's, on an Apple M3 Mac. There is no versioned
  record of native validation of the 2.x versions on Linux or Windows; those
  platforms stay in preview.
- The macOS ezQuake stable keeps the upstream bundle, without a Developer ID
  or proven notarization; it stays conditional.
- The offline keys live on a single machine. Separate custody and a backup
  are requirements for opening to third parties.
- Renewing the root (before 2027-08-10) still has no command of its own in
  `x86qw-release`.

## History

The documents in [releases/](releases/), [post-1.0/](post-1.0/),
[implementation/](implementation/) and [contracts/1.0.md](contracts/1.0.md)
record the Python 0.x/1.0 line and its decisions. They do not describe the
current CLI.
