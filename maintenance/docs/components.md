# Components and updates

x86QW separates **installable content** from **versioned origin**. Not every
nQuake folder matches a project with releases: skins, crosshairs, skyboxes,
maps and other resources are curated collections. In those cases, the right
version is the immutable commit of `nQuake/distfiles`, not an invented
number.

State verified on July 31, 2026:

## Layout of the versioned mods

Each external mod uses the same structure under `dist/mods/<mod>/<version>/`:

- `upstream/`: official binary packages the runtime consumes;
- `source/`: source code or the original distribution that holds the sources;
- `x86qw/`: configurations, profiles and overlays maintained by x86QW.

KTX has more than one kind of overlay and therefore splits x86QW's authorship
without changing the installed paths: `catalog/` holds declarative data,
`config/` holds the files projected into the game, `runtime/` holds the QVM
and its symbols, `source/` holds the numbered patches that reproduce them and
`policy/` holds the PK3's composition contract. The inventory is the explicit
bridge between those contexts and the runtime.

Folders are created only when there is matching content. That is why Pro-X,
whose public source code was not found, has `upstream/` and `x86qw/`, but not
an empty `source/` folder. `dist/mods/x86qw/` is reserved for the
distribution's own components and does not represent an upstream.

## Layout of the versioned services

Services use the same provenance contract under
`dist/services/<service>/<version>/`. The shape of the tree records the
origin of each artifact, and does not need to look identical across
components:

```text
<service>/<version>/
├── source/                         # kept upstream source
├── upstream/<platform>/            # official binary, when there is one
└── x86qw/
    ├── runtime/<platform>/         # binary built by x86QW
    ├── BUILD.json                  # origin, tool and hash of each runtime
    └── *.cfg                       # configuration maintained by x86QW
```

- `source/` holds the original source code or source archive, pinned by hash;
- `upstream/` holds only binaries published by the origin project and kept
  unchanged;
- `x86qw/runtime/` holds only binaries x86QW produced from the recorded
  source;
- `x86qw/BUILD.json` declares, per platform, whether the runtime is official
  or was reproduced by the project, and records tool, command and checksum
  when applicable;
- `x86qw/*.cfg` holds x86QW's operational configuration and is never
  presented as an upstream file.

Empty folders are not created. QTV has no official per-platform builds at the
pinned revision: Linux, Windows and macOS are reproduced builds and therefore
all live in `x86qw/runtime/`; there is no `upstream/`. QWFWD 1.30 provides
official binaries for Linux and Windows, kept in `upstream/`, while the macOS
arm64 runtime is produced by x86QW and lives in `x86qw/runtime/`.

The version folder also keeps the immutable identity available in the origin
project: QWFWD uses the `1.30` tag; QTV, which has no numbered release, uses
the abbreviation `025ca949aca0` of the full commit recorded in the inventory
and in `BUILD.json`. So the difference between those two trees is intentional
and auditable, not a second way of organizing.

| Component | Offered version | Strategy | State |
| --- | --- | --- | --- |
| x86QW client base and startup | `dfb2ac9299f7+x86qw.4` | nQuake snapshot with the x86QW bootstrap | assisted x86QW Ruleset, temporary aliases on demand and a maximum texture size consistent with Apple Silicon's OpenGL 4.1, over a base common to stable and nightly |
| nQuake interface and visuals | `e4cb23d40aa2` | nQuake snapshot | current in the reference repository |
| KTX x86QW | `1.47+x86qw.20` | official release over nQuake resources and x86QW integration | 24 modes, maps filtered by the actual resources, opt-in casual Quad Coach, 77 Frogbot routes, 54 Race routes, complete CTF/Race options, a contextual key plan, a manageable roster and a reproducible QVM |
| Player skins | `e4cb23d40aa2` | curated nQuake collection | current in the reference repository |
| Crosshairs | `e4cb23d40aa2` | curated nQuake collection | current in the reference repository |
| Skyboxes | `e4cb23d40aa2` | curated nQuake collection | current in the reference repository |
| Models | `e4cb23d40aa2` | curated nQuake collection | current in the reference repository |
| Scoreboard flags | `e4cb23d40aa2` | curated nQuake collection | current in the reference repository |
| External textures | `e4cb23d40aa2` | curated nQuake collection | current in the reference repository |
| Base textures | `e4cb23d40aa2` | curated nQuake collection | current in the reference repository |
| nQuake map selection | `e4cb23d40aa2` | curated nQuake collection | no bulk external download |
| Match information | `e4cb23d40aa2` | curated nQuake collection | optional; outside the recommended profile |
| Documentation and licenses | `e4cb23d40aa2+x86qw.2` | the snapshot's licenses and x86QW manuals | historical shortcuts and readmes do not go into the runtime |
| QRP high resolution | `e4cb23d40aa2+x86qw.2` | nQuake snapshot with a UTF-8 x86QW manual | holds maps 1.00 and items 0.73; explicit order in `pak.lst` |
| Final Arena | `1.20+nquake.e4cb23d40aa2+x86qw.5` | nQuake snapshot and x86QW's own gameplay | queue, statistics and the mod's options accessible; optional x86QW Ruleset before the personal layer; original 1.20 files and base source kept |
| Pro-X | `1.1+x86qw.6` | public 1.1 release and x86QW gameplay | complete official runtime; the ENT fixes only four obsolete fields; optional x86QW Ruleset and personal configuration loaded last |
| Team Fortress | `2.9+nquake.e4cb23d40aa2+x86qw.7` | 2.9 gamecode recompiled over nQuake assets | 2.8 gamecode removed from `misc.pak`; nQuake media kept; optional x86QW Ruleset before the personal layer |
| Total Destruction 2 | `2.22+x86qw.6` | independent package recompiled from upstream | complete runtime with optional x86QW Ruleset, spells, special, runes, voting, audio and personal configuration loaded last |
| Dedicated MVDSV | `1.11+x86qw.4` | official release and a reproduced build per platform | KTX dedicated server for macOS arm64, Linux amd64 and Windows x64; a minimal patch fixes QVM argument passing in 64 bits on Apple Silicon |
| QTV broadcast | `0+025ca949aca0+x86qw.3` | immutable upstream commit and reproduced builds | optional HTTP/MVD relay, loopback and upload off by default |
| QWFWD proxy | `1.30+x86qw.4` | official release and builds per platform | optional proxy, with no automatic master queries in the managed profile |

## Update contract

- `reference-snapshot`: follows the latest approved nQuake payload;
- `reference-payload-current`: the consumed content stays current even when
  the observed branch only moved in files outside the distribution;
- `reference-overlay`: keeps nQuake assets and replaces a runtime with a
  proven independent release;
- `upstream-overlay`: an official artifact replaces only declared members over
  the nQuake base;
- `upstream-package`: an independent component is validated and packaged
  without pretending to belong to the nQuake snapshot;
- `upstream-composed`: a curated nQuake base is merged member by member with a
  complete official release under a versioned conflict policy;
- every download has a size and SHA-256 before it enters the collection;
- the internal package records origin, replaced members and the resulting
  hashes;
- the catalog keeps only the current version, while old releases stay
  immutable on the delivery mirrors;
- `x86qw-content check` detects what is new; `update` prepares only the safe
  updates and `publish` stays a separate action.

The bootstrap keeps the original nQuake snapshot, changes its `gl_max_size`
default from 32768 to 16384 and installs an x86QW `autoexec.cfg` with
temporary aliases. The common base keeps options recognized by stable and
nightly; when an installation is updated, the installer migrates the same
value only when the personal configuration still holds exactly the old
default. The global file `qw/x86qw-user.cfg` is loaded last and never
overwritten.

KTX is an independent project and a server mod. The x86QW package combines
the `ktx.pk3` found in the nQuake snapshot with the `qwprogs.qvm` and the
`qwprogs.map` symbol map recompiled from the official 1.47 sources with
isolated x86QW extensions. The unchanged official artifact is kept in
`dist/mods/ktx/1.47/upstream/` and the sources in
`dist/mods/ktx/1.47/source/`. It is neither run by nor a replacement for the
ezQuake client. The MVDSV dedicated server is described separately in the
ecosystem.

## Characteristic composition of each mod

All mods inherit the player's general configuration, but do not pretend to
share the same provenance. The pipeline is declared according to the actual
content:

| Mod | nQuake reference | Independent update | x86QW harmonization |
| --- | --- | --- | --- |
| KTX | `ktx.pk3`, including exclusive content and Clan Arena sounds | the official 1.47 resources, QVM and symbols replace declared conflicts | local server compatibility, gameplay and personal configuration |
| Final Arena | the complete Final Arena 1.20 package with maps, media and gamecode | no newer release proven in the collection | local server, binds, queue, statistics and help |
| Pro-X | 0.8b is kept only as a historical reference, without entering the runtime | the complete 1.1 package replaces nQuake's version | `qw_server.cfg`, local server, binds and personal configuration |
| Team Fortress | maps, models, sounds and 34 LOCs; the 2.8 gamecode is taken out of the assembled `misc.pak` and the identical `detpack.wav` stays only in `pak1.pak` | the official 2.9 `qwprogs.dat` is extracted and validated directly from `tf29qw.zip` | compatible capabilities, local server, binds and personal configuration |
| Total Destruction 2 | uses no nQuake content | complete 2.22 distribution; the missing `saw_down.wav` is rebuilt from the validated identical sound | local server, weapons, spells, runes, voting and personal configuration |

Byte-identical files are only removed when they also have the same role and
the same runtime path. Similar sounds inside `arena.pk3`, `prox/` and
`ktx.pk3` stay when each gamedir's gamecode looks for them in distinct
namespaces. That is why the loose `qw/sound/ca` WAVs could be removed, but the
internal media of Final Arena and Pro-X was not discarded.

## Contract of the gameplay profiles

The configuration order is fixed and applied separately to the chosen mod:

```text
nQuake -> upstream of the selected mod -> x86QW of the same mod -> personal configuration of the same mod
```

The launcher runs only the profile matching the selected game. The KTX
profile is not loaded by Final Arena, Pro-X, Team Fortress or TD2, just as no
profile of those mods is loaded by KTX. The nQuake base provides the client's
general configuration; the upstream defines the mod's own semantics; the
x86QW layer harmonizes only that combination; and that mod's personal file
runs last and is never overwritten.

The x86QW layer replaces only binds that conflict with the mod's mechanics,
keeps the coherent resources of the composition above and adds access to the
functions confirmed in the manual, the original configuration or the matching
gamecode.

Movement keys, sensitivity, network and visual preferences still belong to
the player. The managed profiles are limited to weapons, mod actions, HUD
compatibility adjustments and contextual help. `F10` repeats the automatic
help shown when each mod loads.

The catalog declares a single `common-baseline` policy: the gameplay
components use common configuration files proven both with ezQuake stable
3.6.9 and with nightly `20260616-101233_a86996a`. No nightly-only command is
written globally; future specific optimizations must come as an explicit
client overlay, never as a silent change of the base.

Final Arena uses the matching subfolder of the nQuake snapshot. Pro-X uses
the public 1.1 release; the historical 0.8b subfolder is kept only as a
migration origin. Both produce independent packages, versions, inventories
and receipts.

TD2 has two public numberings that do not form a simple chronology. Arena
Camper's active page documents the `2.12` line, from 2023, but that version's
ZIP is no longer available. The incorporated package is the complete
QuakeWorld distribution `2.22`, by Spinal with a patch by Vegetous, still
available with `qwprogs.dat`, models, sounds, documentation and sources. It is
optional, belongs to the `complete` profile and downloads no maps outside
x86QW's already approved collection. The complete original distribution stays
kept in `dist/mods/td2/2.22/source/`; the runtime receives only gamecode,
models, sounds and the x86QW profiles. Binds, HUD and server parameters come
from the versioned layer in `dist/mods/td2/2.22/x86qw/` and go into the
component's own package. Only the isolated copy of the gamecode belongs to
`.x86qw/components/play-support/`, while `td2/x86qw-td2-user.cfg` stays
personal and outside the immutable inventory.
The 2.22 package omits `saw_down.wav`, although the gamecode preloads it. The
builder restores that name from the byte-identical `saw.wav` that already
exists in the upstream itself, validating size and SHA-256 before assembling
the package.

## Catalog quirks

- **ezQuake without `package`.** The entries of the `ezquake` component have
  `"package": null`: the client is chosen by component, channel (`stable` or
  `nightly`), platform and architecture (`catalog.ClientReleases`), and
  nothing looks it up by package id. The other components and the CLI are
  looked up by `package`.
- **CLI without a GitLab mirror.** The `x86qw-cli` binaries only have the
  GitHub release URL, where the provenance attestation also lives
  (`gh attestation verify`). The content packages have the GitHub release and
  the GitLab package mirror.
- **nQuake revisions per component.** Each nQuake consumer pins its own
  `reference_revision` in `component-releases.json`, so components can be on
  different revisions. `update` moves only what changed; `verify` shows the
  revisions in use.
