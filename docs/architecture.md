# Architecture

The interactive map of the platform is at
[`diagrams/x86qw-platform.html`](diagrams/x86qw-platform.html). Its versioned
source sits next to it, in `x86qw-platform.architecture.json`.

## Public contracts

```text
https://qw.x86.com.br/                          project site
https://qw.x86.com.br/install.sh                macOS/Linux bootstrap
https://qw.x86.com.br/install.ps1               Windows bootstrap
https://qw.x86.com.br/api/v1/catalog.json       package catalog
https://qw.x86.com.br/api/v1/product.json       public product facts
https://qw.x86.com.br/api/v1/trust/             TUF repository (metadata/ and targets/)
https://github.com/x86dx2/x86qw/releases        CLI (x86qw-cli-*) and content packages
https://gitlab.com/x86dx2/x86qw/-/packages      fallback mirror of the packages
```

## Code

All the code lives in `go/`, in a single module:

- `cmd/x86qw`: the CLI and the app. Each command registers its group,
  synopsis and options; `internal/cliargs` is the only parser.
- `internal/setup` and `internal/setup/tui`: the installer and the app
  (Bubble Tea). The app talks to a `setup.Backend`; `setup.Real` uses the real
  installation and `setup.Demo` serves the demo and the tests.
- `internal/install`, `internal/plan`, `internal/maint`: installation,
  update/upgrade/repair plans, uninstall, cleanup, profile, library and
  migration.
- `internal/play`, `internal/services`, `internal/hub`: local matches and
  links, MVDSV/QTV/QWFWD with journal and lock, and the QuakeWorld Hub.
- `internal/trust`, `internal/download`, `internal/archive`,
  `internal/atomicfile`: the TUF-authenticated catalog, bounded downloads,
  safe extraction and atomic writes.
- `cmd/x86qw-content` and `internal/content`: verifying, updating, building
  and publishing `dist/`.
- `cmd/x86qw-release` and `internal/tufsign`: the CLI release, TUF signing,
  assembling the site and the monitor of the public chain.

## The installation

The published CLI is one static binary per platform. The bootstrap downloads
the one from the `x86qw-cli-*` release, pinned by size and SHA-256, and runs
`x86qw setup`. In an installation:

```text
x86qw / x86qw.exe        the CLI; links, shortcuts and handlers run it
.x86qw/cli/receipt       the CLI's version (before 2.5 the CLI lived here,
                         launched by x86qw.sh or x86qw.cmd)
.x86qw/state.json        state: profile, components and versions
.x86qw/components/       receipt and inventory of each component
.x86qw/clients/          ezQuake receipts by platform and channel
.x86qw/setup/            the installer's choices and the rcon password
.x86qw/sessions/         session locks and journals
ezQuake Stable.app,      stable and nightly client (an app on macOS, an AppImage on
ezquake-*.AppImage/.exe  Linux, an executable on Windows)
ezquake/configs/         client configuration
id1/                     registered PAKs
qw/ arena/ prox/         game content (KTX, Final Arena, Pro-X,
fortress/ td2/           Team Fortress and TD2)
mvdsv  qtv/  qwfwd/      server and services, when the installation hosts
docs/licenses/           component licenses
```

`.x86qw/` is the control plane. Packages carry per-platform variants in a
staging area; the CLI picks exactly one before copying. After any change of
components, it generates `qw/pak.lst` in a deterministic order, records the
file in a receipt of its own and rejects divergences on verification. ezQuake
always gets `-nohome`, so an outside `~/.ezquake` never overrides the
installation.

A newer CLI that opens an installation's app becomes that installation's CLI;
an older or development build never replaces the installed one.

## Repository

- `x86dx2/x86qw` keeps the complete distribution in `dist/`, the inventories,
  the catalog, the code, the site and the documentation. Large files live in
  Git LFS. The component packages are reproducible builds of the canonical
  sources and are not versioned again in the tree.
- The two registered PAKs live in `dist/game-data/id1`; the builder produces
  the `x86qw-core-id1` base data package, with size and SHA-256 in the
  catalog.
- The project's own customizations are part of the distribution: for TD2,
  they live in `dist/mods/td2/2.22/x86qw/` and are declared as
  `project_sources` in the BOM; the content bootstrap follows the same
  contract in `dist/mods/x86qw/core/`.

The canonical catalog is `site/public/api/v1/catalog.json`, exactly the file
served. Capabilities, runtimes, games and compatibility are declared in
`maintenance/inventory/`; the public projection
`site/public/api/v1/product.json` combines those contracts with the catalog,
and `x86qw-content verify` rejects a divergent projection. The command list in
`capabilities.json` is the same as `x86qw help`, and a test compares the two.

`x86qw-content publish` uploads the content packages to GitHub Releases and to
GitLab Generic Packages, downloads each public copy, checks size and SHA-256
and only then records the second URL. Content packages never get the
**Latest** badge; the CLI release is published as a prerelease and becomes
Latest when the site starts serving it.

## Rule for admitting components

`maintenance/inventory/component-policy.json` is the general boundary of the
distribution. `maintenance/inventory/components.json` breaks the content down
into BOM, profiles, dependencies, origins and destinations. The collector
rejects paths outside those declarations and validation rejects files without
a consumer or component. Researching or evaluating a resource does not make it
part of x86QW.

`maintenance/inventory/component-releases.json` keeps the update layer
separate: current version, strategy, upstream, consumed artifacts and hashes.
That way a release such as KTX can move forward without renaming or rebuilding
unrelated components.

All canonical content lives under `dist/`: `clients/ezquake/stable/` holds the
official releases, `clients/ezquake/nightly/` holds the development
snapshots, `distributions/nquake/` holds only the content taken from the
reference distribution, `mods/` keeps upstreams and the project's own
adjustments and `game-data/id1/` keeps the registered PAKs. Derived ZIPs live
temporarily in `maintenance/build/packages/`, outside Git.

When a component is proposed, the order is: implement the consuming action,
declare the component in the policy, add tests and only then enable its
download. Removing the consuming action also requires removing its files from
the collection.

Updates are detected by `x86qw-content check`. `update` applies only
mechanically safe updates; independent releases need a reviewed definition in
`add`, and `publish` stays explicit. Each mod chooses a verifiable
composition: a file merge for KTX, a full snapshot for Final Arena, a complete
replacement for Pro-X, reference assets with updated gamecode for Team
Fortress and an independent package for TD2. The x86QW harmonizations are
always applied last. In KTX, every shared divergence must be listed, with
hashes and resolution, in `dist/mods/ktx/1.47/x86qw/policy/merge-policy.json`;
in Team Fortress, the builder strips the 2.8 gamecode and the strictly
duplicated media out of the assembled PAK before adding the 2.9
`qwprogs.dat` extracted from the original ZIP. An undeclared change stops the
build.

## Publication flow

```text
pinned source -> dist/ -> Git LFS -> catalog -> x86qw-release sign -> TUF
                       -> maintenance/build/packages/ -> GitHub + GitLab
go/cmd/x86qw -> go-release.yml -> x86qw-cli-X.Y.Z -> x86qw-release prepare -> bootstraps + catalog
merge into main -> site-deploy.yml -> qw.x86.com.br

HTTPS metadata -> limit + deadline -> memory
HTTPS package  -> size + SHA-256 -> private staging -> atomic promotion
```

The step by step for release, TUF renewal and recovery is in the
[release runbook](runbooks/release.md).

## Remote byte boundary

`internal/download` is the only entry for persistent HTTP bytes, in the CLI
and in the tools. Persistent artifacts are pinned by size and SHA-256; dynamic
metadata uses a bounded read and never becomes managed content. Discovered
stable, nightly and nQuake builds only inherit an identity when path, URL and
size match the reviewed manifest; new candidates need a reviewed definition
before the persistent download. ezQuake packages must also match exactly
`clients/ezquake/<channel>/<version>/<platform>-<architecture>/<file>`, and
paths that come from upstream are validated as relative and portable before
any folder is created.

The limits are 512 MiB for artifacts, 2 MiB for the catalog and 1 MiB for the
Hub. Every initial, redirected and final URL uses HTTPS; one deadline covers
attempts, reads and backoff, and retries are limited to transient failures.
Temporary files get mode `0600` on POSIX and are synced before an atomic
replacement, keeping the previous destination until promotion. Persistent
URLs in the catalog, manifest and inventories go through the same HTTPS
validator and accept no credentials, query, fragment, spaces or control
characters.

## Trust

The catalog is authenticated by TUF with Ed25519 keys. The root, embedded in
the CLI and versioned in `maintenance/trust/root.json`, defines four roles:
root and targets with a threshold of 2 of 3, snapshot and timestamp with a
threshold of 1 of 2. The CLI keeps the trusted metadata in the local cache and
rejects rollback, expired metadata and a timestamp that reuses a version with
other bytes. The details are in the [threat model](THREAT-MODEL.md) and in the
[key runbook](runbooks/key-management.md).

## Security and recovery

- no persistent artifact is accepted without a prior size and SHA-256;
- every response has an explicit limit and a total deadline;
- bytes only reach the final destination after validation and atomic
  promotion;
- published versions are never replaced, they only stop being the current one
  in the catalog;
- service passwords come from a no-echo prompt or a private file and never go
  on the children's command line;
- on Windows, private objects use a protected DACL with access only for the
  current user and `LOCAL SYSTEM`;
- the port preflight happens before the first process; readiness and rollback
  stop partial startups;
- `.x86qw/sessions/` records private journals and, after a crash, removes only
  the files the session created whose hash still matches.
