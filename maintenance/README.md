# Distribution maintenance

This folder is the control plane of `dist/`. It is not a second copy of the
product: inventories and recipes explain how to assemble the canonical tree,
the `x86qw-content` binary (code in `go/cmd/x86qw-content`) runs the process
and `build/` receives only temporary derived files.

## Single source of operation

Use only `x86qw-content` (`go -C go run ./cmd/x86qw-content ...` or the
compiled binary), which finds the checkout from the current folder:

```sh
x86qw-content check
x86qw-content update
x86qw-content add path/change.json
x86qw-content verify
x86qw-content build
x86qw-content publish
x86qw-content commit --push
```

- `check [COMPONENT...]`: queries the declared upstreams without writing to
  the project, component by component; returns 2 when it finds something new,
  1 when nothing changed but some upstream could not be queried and 0 when
  everything is current;
- `update [COMPONENT...]`: syncs only stable/nightly and nQuake bytes whose
  identity was already reviewed, requires a clean Git tree before mutating,
  uses staging, reconciles old files, updates the dependent metadata,
  validates and swaps the tree transactionally;
- `add`: brings in a new package, add-on or configuration from a reviewed
  local definition;
- `verify`: validates catalog, recipes, components, hashes, structure, TUF and
  Git LFS;
- `build`: validates the stable binaries and generates 21 component ZIPs plus
  the required `x86qw-core-id1` package in `maintenance/build/packages/`;
- `publish`: publishes/verifies GitHub Releases and GitLab Generic Packages in
  the `x86qw` repositories, which are the distribution's only destinations.
  Content packages never get the Latest badge, which belongs to the CLI
  release;
- `commit`: adds to Git only `dist/`, inventories, recipes and the catalog.

`update --commit --push` exists for automation, but the recommended routine
is to keep review and publication apart:

```sh
x86qw-content check
x86qw-content update --dry-run
x86qw-content update
x86qw-content verify
git diff --stat
x86qw-content commit --push
# after review, green checks and separate approval:
x86qw-content publish
```

The catalog that `update`, `add` or `build --register` changed only counts
once signed: run `go -C go run ./cmd/x86qw-release sign` and
`x86qw-content verify` before the PR. The merge publishes the site; the step
by step is in the
[release runbook](../docs/runbooks/release.md#publish-content).

Comparisons with public upstreams use GitHub's official HTTPS API and public
release and download URLs. `check` and `update` need no GitHub account, token
or authentication. Credentials are needed only by the `publish` command,
because it writes the artifacts to the project's mirrors.

## Remote byte boundary

`go/internal/download` is the only boundary, for maintenance and the CLI, that
receives persistent HTTP bytes. An artifact requires HTTPS, a destination,
size, SHA-256, a deadline and retry; dynamic metadata uses a bounded read and
never writes a managed payload. There is no contract to promote unpinned
content: the SHA-256 observed in the transfer itself cannot become the
reference that validates those same bytes.

## Independent updates

Each component of the distribution (`ezquake`, `ktx`, `mvdsv`,
`nquake-maps`...) is queried and updated on its own:

```sh
x86qw-content check                 # all
x86qw-content check mvdsv ktx       # only these
x86qw-content update --dry-run mvdsv
x86qw-content update mvdsv
```

- an upstream that is down (HTTP 429, timeout) shows as
  `[UNAVAILABLE] component: reason` and does not stop the others from being
  queried;
- without a selection, `update` skips (`[SKIPPED]`) whatever is unavailable
  or needs a reviewed definition and updates the rest; with an explicit
  selection, a requested component that cannot be updated makes the command
  fail;
- a component outside the selection or skipped keeps exactly the files the
  manifest records, without changing or disappearing.

The 17 components that consume nQuake files (the 13 of the snapshot, the
Final Arena and Team Fortress overlays, KTX's `ktx.pk3` and the Pro-X files)
each pin their own revision in `reference_revision`
(`component-releases.json`). The consumed files all live in a single tree,
`dist/distributions/nquake/`, with the same layout as the nQuake repository;
each file's revision is its owner's and shows in the URL recorded in
`dist/manifest.json` (`verify` requires that match).

nQuake is the base of the distribution. `maintenance/inventory/nquake.json`
records every file of the last reviewed revision (path, size, SHA-256 and Git
hash) and, in `unused`, the reason for each part the distribution does not
use. Every nQuake file is consumed by a component (a `reference` source in
`components.json`) or declared unused; `verify` requires that classification
to be complete and without overlap.

When nQuake moves:

- `check` reads the Git tree and the `manifest.json` nQuake publishes (the
  SHA-256 of each file, checked against the tree) and marks for update only
  the components whose consumed files changed; the others stay on the revision
  they already pin. Changes in unused parts show as
  `[NQUAKE] unused by the distribution, <part>: N new, N changed, N removed`
  and a file nobody classified shows as `[CLASSIFY]`;
- `update` moves each affected component to the new revision on its own: it
  downloads only the files that changed (identical ones are reused from
  `dist`), checks SHA-256 and Git hash, swaps the revision in the version (or
  increments `+x86qw.N` when the version does not carry the revision) and
  re-pins the x86QW customizations (`text_replacements`,
  `archive_text_replacements`) whose text still applies to the new file. A
  customization that no longer applies, or the composition layer of
  `ktx.pk3`, stops only that component (`[SKIPPED]`/`[REVIEW]`) for review;
- without a selection, `update` also moves `nquake.json` to the new revision,
  as long as every file is classified.

To adopt a part that is unused today, create the component with `add` and
remove the matching rule from `unused`.

The nQuake snapshot already recorded can be repaired mechanically because
path, URL, size and SHA-256 are pinned in the manifest. A new nQuake revision,
a new stable/nightly or a new release of KTX, TD2 or another mod needs a
reviewed intake: members, identity, compatibility, version and customizations
cannot be inferred from the transfer itself. The manager detects what is new,
marks `review_required` and stops `update` before confirmation, staging or
download, pointing to `add`; it never overwrites a published version.

KTX is assembled in three layers: `gpl/qw/ktx.pk3` from the nQuake snapshot,
the official release's resources and QVM and, last, the x86QW adjustments.
The policy in `dist/mods/ktx/<version>/x86qw/policy/merge-policy.json` keeps
exclusive files and records each shared conflict with the hashes of both
origins. Any new or changed conflict stops the build for human review.

The convention for `source/`, `upstream/` and `x86qw/`, including the
intentional asymmetry between the QTV and QWFWD runtimes, is documented in
[`docs/components.md`](docs/components.md#layout-of-the-versioned-services).

## Inclusion definition

`add` takes a temporary JSON with the `distribution-change` identity. The
file does not need to be versioned: the permanent result is recorded in
`dist/`, in the BOM, in the release inventory and in the catalog.

`add --dry-run` validates the same input identity before any network or
mutation: destinations, local sources, HTTPS, a positive size and a
previously reviewed SHA-256, managed metadata, component, profile and package.
The dry run never prints the raw definition; URLs show only with a redacted
origin, without credentials, path, query or fragment.

A new remote must match exactly at least one authority: an artifact of a
proposed release, a source kept in the upstream registry, a proposed public
package or a pinned nQuake reference. The `distribution_component` namespace,
the operational consumer and the logical identity of `package` must agree with
that authority. For ezQuake, the path is derived from
`clients/ezquake/<channel>/<version>/<platform>-<architecture>/<file>` and
must match the proposed package's component, channel, version, platform,
architecture and name.

```json
{
  "format": 1,
  "project": "x86qw",
  "kind": "distribution-change",
  "files": [
    {
      "url": "https://arenacamper.ddns.net/repo/quakeworld/quakeworld-TD2.22QW-server_PTBR.tar.gz",
      "destination": "dist/mods/td2/2.22/source/quakeworld-TD2.22QW-server_PTBR.tar.gz",
      "size": 567029,
      "sha256": "b0b7632debe931e435008df939bade3791b5d21abfbb66f828790f1996beca93",
      "managed": true,
      "distribution_component": "td2",
      "consumer": "development:td2",
      "package": "total-destruction-2"
    }
  ]
}
```

The example uses a canonical identity that is already in the inventory, so
the contract is executable and not just illustrative. A file uses `source`
relative to the definition or an HTTPS `url`, never both. Every persistent URL
requires `managed: true`, accepts no credentials, fragment or query and must
match exactly one of the authorities described above. A remote path that
already exists in the manifest is immutable: URL, size, SHA-256, owner,
consumer and package cannot change in place. The CLI comes in only through the
`x86qw-cli` release, never through `add`.

Moving components to a new nQuake revision uses an explicit transition,
resistant to stale plans, in the same definition, which names each moved
component and the revision it leaves:

```json
{
  "reference": {
    "repository": "https://github.com/nQuake/distfiles",
    "revision": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "components": {
      "x86qw-client-bootstrap": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  }
}
```

The previous revision of each component must be exactly the one in the
inventory at the time it is applied, the repository cannot change and each
URL is derived from the canonical GitHub origin, the new revision and the
declared path. The `files` list must bring every file the moved components
consume at the new revision (with `package` equal to the component); each
one's version then carries the new revision. The files they leave are only
removed in the transactional workspace, after the plan is validated, and the
other components change neither revision nor bytes.

Local sources must be declared exactly in the proposed BOM's
`project_sources`, `project_inputs`, `project_overrides` or
`project_archive_overrides`. The copy uses an exclusive temporary file, mode
`0600` on POSIX, hashing while streaming, `fsync` and an atomic `replace`;
that way staging by hardlink does not change the live `dist/` and a change of
the source after the plan is rejected. On Windows, that temporary file
inherits the ACL of the checkout's folder. Destinations use portable names,
and collisions by case or Unicode normalization and intermediate symlinks are
rejected.
The common contract uses `/` only as a separator, rejects invalid characters
and Win32 device names (including `CONIN$`, `CONOUT$`, `COM¹` and `LPT¹`),
components ending in a period/space and limits the full relative path to 240
UTF-16 units. That margin fits the usual checkout prefix of the Windows runner
under the classic Win32 limit; local checkouts should still use a short root.

To create or replace a component, the same definition must also bring the
complete `component` and `release` objects, `replace: true` when applicable
and the `profiles` list. `--dry-run` assembles and validates those
inventories in memory before any network. When a profile changes, `add`
automatically keeps its previous signature and records the new one in
`inventory/components.json`. That history lets `x86qw upgrade` recognize
installations that skipped intermediate releases.

To update an existing component, send the complete object with
`"replace": true` and a new immutable release. x86QW configurations must live
in an `x86qw/` subfolder of the component and be declared as
`project_sources`; the original upstream copy stays intact.

## Contents of the subfolders

```text
inventory/component-policy.json    namespaces accepted in dist
inventory/components.json          BOM, profiles, dependencies and destinations
inventory/component-releases.json  versions, strategies, artifacts and hashes
inventory/upstreams.json           versions, kept sources and build recipes
recipes/                            recipes of the kept stable clients
recipes/sources/                    build instructions per upstream
build/                              derived artifacts ignored by Git
docs/                               components and provenance
```

There is no longer a parallel summary inventory: `dist/manifest.json` is the
single source of truth for the upstream bytes actually kept.
