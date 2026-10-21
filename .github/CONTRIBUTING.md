# Contributing to x86QW

Thank you for helping keep QuakeWorld playable, verifiable and understandable. x86QW is a distribution, a CLI, a catalog and a portal at the same time; that is why a small change can cross more than one contract.

## Before you start

- look for an [existing issue](https://github.com/x86dx2/x86qw/issues);
- for new runtimes, games, mods or architecture changes, open a proposal first;
- vulnerabilities follow the [security policy](SECURITY.md), never a public issue;
- do not take an artifact's presence in the catalog as proof that it was tested on that platform.

## Repository principles

1. **Provable origin.** Every byte brought in needs a declared origin, version and consumer.
2. **Intact upstream.** Original files and x86QW customizations stay in separate layers.
3. **Reproduction before convenience.** Published packages are immutable and verified by size and SHA-256.
4. **Personal state is personal.** The player's settings and saves are not treated as managed content.
5. **Explicit network.** `x86qw host` listens on loopback until you ask for another interface; the server the installer builds says that it accepts the network and offers "Only this machine".

## Recommended flow

```sh
git clone https://github.com/x86dx2/x86qw.git
cd x86qw
git lfs pull
git switch -c type/short-description
```

Make one change per context. Before sending it:

```sh
go -C go test ./...
go -C go run ./cmd/x86qw-content verify
go -C go run ./cmd/x86qw help
cd site && npm test && npm run deploy:dry-run
```

`x86qw-content verify` validates the distribution, inventories, recipes, catalog, TUF and Git LFS. On every PR, CI runs the Go tests on Linux (with the race detector, staticcheck and govulncheck) and on Windows, builds the CLI for the five platforms, checks the distribution and validates the site. A nightly e2e installs for real on Linux, macOS and Windows. None of that replaces using the change on a real machine; say in the PR which system you used it on.

## Where to change things

| Intent | Main directory | Related contract |
|---|---|---|
| Client, mod or distributed content | `dist/` | `maintenance/inventory/` |
| Origin, version or recipe | `maintenance/` | `dist/manifest.json` |
| CLI, app or bootstrap | `go/` | catalog and Go tests |
| Portal or public API | `site/` | `site/tests/` |
| Architecture and operation | `docs/` | public product facts |

Do not edit published packages to "fix" an existing version. Produce a new version through the maintenance flow. A change to the catalog needs a new TUF generation (`go -C go run ./cmd/x86qw-release sign`), which only the maintainer does; see the [release runbook](../docs/runbooks/release.md).

## Adding content

A content proposal needs to answer:

- what the upstream and the exact version are;
- which license or terms allow the intended use;
- which component consumes each file;
- how future updates will be detected;
- on which platforms the result was used;
- whether it conflicts with content already preserved.

See [distribution maintenance](../maintenance/README.md), [provenance](../maintenance/docs/provenance.md) and [architecture](../docs/architecture.md).

## Pull requests

A good PR has:

- the problem and the result in plain language;
- what was left out, when relevant;
- the commands run and what they gave;
- origin and license of new assets;
- screenshots when something visual changes;
- compatibility notes when applicable.

PRs are squash-merged, with signed commits and the required checks green. Merges that touch `site/public` go live right away.

## Commits

Prefer imperative, specific messages:

```text
Keep QTV on loopback by default
Show the profile summary before installing
```

Do not include caches, local builds, installation folders or credentials. Build test binaries outside the repository (`go build -o /tmp/x86qw ./cmd/x86qw`).
