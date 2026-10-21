<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/assets/hero-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset=".github/assets/hero-light.svg">
    <img alt="x86QW — QuakeWorld, reassembled" src=".github/assets/hero-dark.svg" width="100%">
  </picture>

  <br>

  **A modern, reproducible and self-contained QuakeWorld distribution.**<br>
  Client, games, dedicated server, relay and proxy — installed by a single verifiable CLI.

  [![Release](https://img.shields.io/github/v/release/x86dx2/x86qw?display_name=tag&sort=semver&style=for-the-badge&label=release&color=ef6a57)](https://github.com/x86dx2/x86qw/releases/latest)
  [![Quality gate](https://img.shields.io/github/actions/workflow/status/x86dx2/x86qw/validate.yml?branch=main&style=for-the-badge&label=quality%20gate&color=4767ff)](https://github.com/x86dx2/x86qw/actions/workflows/validate.yml)
  [![Portal](https://img.shields.io/website?url=https%3A%2F%2Fqw.x86.com.br%2F&style=for-the-badge&label=portal&up_message=online&down_message=offline&color=28a890)](https://qw.x86.com.br/)
  [![Platforms](https://img.shields.io/badge/macOS_·_Linux_·_Windows-catalogued-181b2a?style=for-the-badge)](#compatibility)

  [**Install**](#installation) · [The app](#the-app) · [Commands](#commands) · [Hosting](#hosting) · [Architecture](#architecture) · [Contributing](#contributing)
</div>

---

## QuakeWorld without the archaeology

x86QW turns a historical ecosystem into a current product: it selects known versions, preserves their origins, applies isolated customizations and ships everything through a catalog signed with size and SHA-256. The result is an organized, updatable installation that can play locally, join servers or run your own infrastructure.

<table>
  <tr>
    <td width="33%" valign="top"><h3>⚡ Jump into a match</h3>ezQuake stable or nightly, curated nQuake content and five games ready for the same installation.</td>
    <td width="33%" valign="top"><h3>🧩 Build your profile</h3>Pick the essential, recommended or complete profile, or your own selection of components.</td>
    <td width="33%" valign="top"><h3>🛡️ Verify the bytes</h3>A TUF-authenticated catalog, immutable packages, local receipts and explicit repair that never mistakes personal files for managed content.</td>
  </tr>
  <tr>
    <td width="33%" valign="top"><h3>🎯 Master KTX</h3>Duel, teams, CTF, Race, Clan Arena, Wipeout, Frogbots and dozens of declarative combinations.</td>
    <td width="33%" valign="top"><h3>📡 Host the arena</h3>MVDSV, QTV and QWFWD in the same folder, with several instances per session, passwords kept off the command line and a coordinated stop.</td>
    <td width="33%" valign="top"><h3>♻️ Update under control</h3>See the plan before the change. On a terminal the CLI asks; in automation, <code>--yes</code> confirms and <code>--dry-run</code> only shows.</td>
  </tr>
</table>

## Installation

> [!IMPORTANT]
> x86QW organizes and distributes software and content from different origins. Read [Provenance and licenses](#provenance-and-licenses) before redistributing the set.

### macOS and Linux

```sh
curl -fsS https://qw.x86.com.br/install.sh | bash
```

### Windows

In PowerShell or the Command Prompt:

```powershell
powershell -c "irm https://qw.x86.com.br/install.ps1 | iex"
```

The bootstrap downloads the `x86qw` native CLI from the `x86qw-cli-2.4.1` release on
GitHub, checks the size and SHA-256 pinned in the script itself and runs
`x86qw setup`. On a terminal, that opens the app's installer; it queries the
public TUF catalog and asks what to install and where. Nothing beyond the
system is needed: macOS and Linux use `curl`, and Windows uses the PowerShell it ships with.
On Unix, the body of the script only runs on its last line, so a truncated
download fails to parse and runs nothing. On Windows, the bootstrap does not end
the PowerShell session; if the install fails, the message stays on screen and
the code in `$LASTEXITCODE`.

The default folder is `~/Games/x86qw`. In the installer's **Integration**
section you choose shortcuts, the command on the `PATH` and the file types:

- **Command on the `PATH`:** on macOS and Linux, a `~/.local/bin/x86qw` link; if
  that folder is not on your `PATH`, use the full path
  `~/Games/x86qw/x86qw.sh` or add it to your shell. On Windows, the
  installation folder joins the user's `PATH`.
- **Shortcuts:** Start Menu and Desktop on Windows, a menu entry on
  Linux.
- **`qw://` links and demos:** open servers and `.mvd`/`.qwd` demos in the
  installation's ezQuake, through the `x86qw open` command.

To prepare the folder for another system from macOS or Linux:

```sh
curl -fsS https://qw.x86.com.br/install.sh | bash -s -- --platform windows
```

The accepted values are `macos`, `linux` and `windows`. The CLI binaries are also
in the [`x86qw-cli-2.4.1` release](https://github.com/x86dx2/x86qw/releases/tag/x86qw-cli-2.4.1),
with `SHA256SUMS` and a build provenance attestation
(`gh attestation verify FILE --repo x86dx2/x86qw`).

The audience is still `owner-only`. That is neither an `external-public`
authorization nor a promise of support for outside users.

## What comes with x86QW

### Five games, one installation

| Game | Version | Experience |
|---|:---:|---|
| **KTX** | 1.47 | Competitive play, teams, CTF, Race and Frogbots |
| **Final Arena** | 1.20 | Its own arsenal and rules in QuakeWorld arenas |
| **Pro-X** | 1.1 | A full conversion, preserved and harmonized |
| **Team Fortress** | 2.9 | Classic classes, teams and objectives |
| **Total Destruction 2** | 2.22 | Expanded gameplay with an isolated x86QW customization |

### Profiles for every kind of installation

| Profile | Content | Best for |
|---|---|---|
| **Essential** | Visual base + KTX | Getting in fast and playing competitive QuakeWorld |
| **Recommended** | Essential + maps, skins, models, textures and documentation | The balanced x86QW experience (the installer's default) |
| **Complete** | All 21 components, five games, MVDSV, QTV and QWFWD | Players, hosts and explorers of the ecosystem |
| **Custom** | An explicit selection of components | Minimal or specialized installations |

Players who only play never get the server programs, not even with the complete
profile; they come in when the installation also hosts.

<details>
<summary><strong>KTX modes and advanced options</strong></summary>

The catalog covers 17 native usermodes: Duel, 2on2, 3on3, 4on4, 10on10, FFA, CTF, HoonyMode, Blitz 2v2/4v4, 2on2on2, 3on3on3, 4on4on4, XonX, Wipeout, Clan Arena and ThunderWalker ToT. `3on3` means two teams of three players; `2on2on2`, three teams of two. The CLI still accepts the `3v3` and `2v2v2` aliases. It also includes Midair, DMM4, Instagib, LGC, Rocket Arena, Race and Practice.

```sh
x86qw play ktx --mode duel --map dm6 --bots 1 --bot-skill 8 --bot-names x86qw
x86qw play ktx --mode ffa --map dm6 --bots 4 --bot-skill random
x86qw play ktx --mode ctf --ctf-hook smooth --ctf-runes off
x86qw play ktx --mode race --map slide1 --race-style match --race-scoring formula1
```

`x86qw play --help` lists Frogbots, CTF rules and Race formats. The CLI
validates maps, combinations and QVM limits before starting the client. With
bots, only maps with a Frogbot route are offered; CTF and Race match the installed maps
against their ENTs and routes. Regular personal routes are recognized too.

On the app's **Play** screen, the 24 modes appear grouped (recommended,
individual, teams, arena/alternatives and training) and each option only shows
when it makes sense: Race asks for format, scoring, pacemaker and whether the
racers are visible; CTF asks for hook, runes and spawn; modes that support them
offer Frogbots, skill and names. There are four name profiles
(`--bot-names`): `default` (stock KTX), the original names; `x86qw`, the
distribution's maps and modes, shuffled on every launch; `one-piece`, the One
Piece list, shuffled; and `personal`, an editable personal list. See the
[Frogbot names guide](docs/FROGBOTS.md).
Fixed-size modes limit the selection to the free slots: Duel offers at
most one bot when there is one human player.

On entering the map, the console prints the keys of the active mode. In Duel, `F5`
marks ready, `F6` breaks and `F11` shows the rules; with Frogbots, `INS`,
`DEL`, `HOME` and `END` manage bots and skill. The named profiles also
keep the shirt, pants and team colors KTX sets.
`--bot-skill random` draws an independent skill from 1 to 20 every time
a bot joins. `INS` respects the mode's capacity, `DEL` frees the slot and
`HOME`/`END` change the skill of the next bots cumulatively. Weapon, health
and break on death are ToT-only options. `F12` quits
QuakeWorld directly in all five games.

</details>

## The app

After the install, `x86qw` — or `x86qw.sh` in the installation folder, and
`x86qw.cmd` on Windows — opens the app on a terminal. `x86qw --target FOLDER` opens
another installation. Without a terminal, `x86qw` only prints the help.

The home screen brings together:

- **Play:** game, mode, map, bots and rules, with the last match remembered;
- **Servers:** the public list from QuakeWorld Hub, favorites first.
  Enter joins, `ctrl+f` marks a favorite, `ctrl+w` watches through QTV, `ctrl+o`
  joins as a spectator and `ctrl+r` refreshes. Without the Hub, the local favorites and
  recents show;
- **Host:** starts and stops the configured server, with its instances,
  QTV and QWFWD, and shows what is running;
- **Update**, **Components and setup**, **Repair and verify**,
  **Tools** (diagnosis, verification, upgrade, changes, favorites,
  profile, cleanup and the HTML panel) and **Uninstall**.

The installer follows the same logic. First you say what you want — **Play**,
**Host** or **Play and host** —; then you adjust components, your
setup (nick, keys, mouse, video, sound and ruleset), the server, the
integration and the folder. A preflight checks folder, disk, catalog and ports before
writing anything, and canceling midway puts the folder back the way it was.

The **Recipe** section shows the command and the JSON equivalent to what you
chose, without passwords. With them, the same install repeats without the app:

```sh
x86qw setup --recipe recipe.json --yes
x86qw setup --intent both --nick player --content recommended --servers duel:dm6,4on4:dm3+bots --yes
```

The app follows the system language (English or Portuguese) and switches language and theme
from the top corner. On terminals without Unicode glyphs, `X86QW_GLYPHS=ascii`
forces plain characters; `X86QW_GLYPHS=unicode` forces the full ones.

## Commands

`x86qw help` lists the commands by task; `x86qw COMMAND --help` shows each
one's usage and options. Options can come before or after the folder.

| Command | What it does |
|---|---|
| `play [GAME]` | Starts a local match; without `--mode` (or with `--menu`), on a terminal, opens the app's Play screen; `--channel nightly` uses the nightly ezQuake |
| `open ADDRESS\|DEMO` | Opens a `qw://` server (including `qw://N@host:port/qtvplay`) or an `.mvd`/`.qwd` demo in ezQuake |
| `hub` | Lists the public servers; without the Hub, uses the local favorites and recents |
| `host [GAME]` | Hosts an MVDSV server; `--instance` adds servers to the session, `--with-qtv` and `--with-proxy` start those services with it |
| `qtv` | Starts a server's QTV relay |
| `proxy` | Starts the QWFWD proxy |
| `status` | Shows the running services; `--stop` stops the stack |
| `setup` | Installs (through the app, `--profile` or a recipe) or adopts an existing installation |
| `install` | Installs ezQuake and a content profile, like `setup --profile` |
| `update` | Updates the installed ezQuake, components and CLI |
| `upgrade` | Like `update`, and adds what your profile gained |
| `verify` | Checks every managed file against the catalog and the receipts |
| `repair` | Restores what `verify` reports, without touching what you edited |
| `uninstall` | Removes the installation and keeps settings, PAKs and demos; `--purge` deletes everything |
| `library` | Favorites and recents; `--add` and `--remove` |
| `profile` | Shows settings, cache and personal data; `--backup` and `--restore` |
| `changes` | Shows files that are new, modified or removed compared with the installation |
| `ui` | Builds a local, read-only HTML panel |
| `cleanup` | Deletes caches; `--downloads` and `--personal-data` go further |
| `doctor` | Diagnoses the installation without changing files; `--bundle` writes a zip to share |
| `catalog` | Authenticates the public catalog over TUF and shows the role versions |
| `migrate` | Converts the metadata of old installations to the current format |
| `version` | Shows the version |

Whatever changes or stops something — `update`, `upgrade`, `repair`, `uninstall`,
`status --stop` and recipes — shows what it will do and asks on the terminal.
Without a terminal, it needs `--yes` and exits with code 2 when it is missing. `--dry-run` only
shows the plan. `verify`, `doctor`, `hub`, `catalog`, `version`, `status` and the
`update`, `upgrade` and `repair` plans with `--dry-run` accept `--json` and
write a JSON envelope on one line.

Without `--target` or a folder, each command uses the installation that holds the running
CLI; outside an installation, `~/Games/x86qw`. The exit codes are 0
(success), 1 (failure), 2 (wrong usage or missing confirmation) and 130
(interrupted).

## Hosting

```sh
x86qw host ktx --mode 4on4 --map dm3
x86qw host ktx --mode duel --map dm6 --instance ffa:dm4:28502:bots --with-qtv
x86qw host team-fortress --map 2fort5r
x86qw host td2 --map dm6 --bind 0.0.0.0
x86qw qtv --upstream 127.0.0.1:28501
x86qw status --stop
```

There are two starting points, with different defaults:

- **`x86qw host` directly:** listens on `127.0.0.1:28501`, QTV on
  `127.0.0.1:28000` and QWFWD on `127.0.0.1:30000`, and follows the terminal. The
  network only comes in with an explicit `--bind`; `--background` detaches the session.
- **The server the installer builds** (the Host or Play and host intent,
  or `setup --intent host`): starts at port 27500, with one port per
  instance, QTV on 28000 and QWFWD on 30000, and accepts players from the network on all
  interfaces. Choose **Only this machine** in the installer, or `--local` in the
  recipe, to restrict it to `127.0.0.1`. It starts in the background from the
  app or from the command the installer shows at the end.

Passwords can come from a prompt without echo or from a private file; the CLI keeps them out of the
command line of child processes and out of the logs. Locks and journals prevent
concurrent maintenance, coordinate the stop and allow a conservative
recovery after a crash. See the [hosting guide](docs/HOSTING.md).

The CLI inspects ZIP and PK3 files before any extraction, with limits, canonical
modes and atomic promotion. On Windows, the installation's private state is created
with a protected DACL, limited to the current user and `LOCAL SYSTEM`; external password
files are only accepted with that same restriction and are never rewritten.
See [ADR 0002](docs/adr/0002-fronteira-unica-de-arquivos.md) and
[ADR 0003](docs/adr/0003-dacl-privada-windows.md).

## Architecture

<a href="docs/diagrams/x86qw-platform.html">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/assets/architecture-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset=".github/assets/architecture-light.svg">
    <img alt="The x86QW platform flow: pinned sources, distribution, catalog, installer and runtimes" src=".github/assets/architecture-dark.svg" width="100%">
  </picture>
</a>

The repository is the canonical source. `dist/` preserves the inputs; inventories declare consumers and dependencies; the catalog projects the public packages and is signed with TUF; the CLI materializes only the chosen profile and platform. GitHub Releases is the main channel for packages and GitLab Generic Packages keeps the fallback mirror.

All the code lives in `go/`: the `x86qw` CLI (app, installation, games, hub,
services and installation maintenance), `x86qw-content` (verification,
update, build and publication of `dist/`) and `x86qw-release` (CLI release,
TUF signing, site assembly and monitor). The repository holds no Python.

```text
dist/           canonical product, preserved upstreams and x86QW customizations
go/             the x86qw CLI and the maintenance tools
maintenance/    inventories, recipes and the trust root
site/           portal, public catalog API and Cloudflare deploy
docs/           architecture, hosting, runbooks and decisions
```

[Detailed architecture](docs/architecture.md) · [Interactive diagram](docs/diagrams/x86qw-platform.html) · [Provenance](maintenance/docs/provenance.md) · [Roadmap](docs/ROADMAP.md) · [Status](docs/PROJECT-STATUS.md)

## Project status

The current CLI is the one from the `x86qw-cli-2.4.1` release, in `owner-only` mode: one user,
the maintainer, who uses it on an Apple M3 Mac. The macOS, Windows
and Linux binaries are in the catalog and in the release; the CI tests run on Linux and
Windows, but there is no versioned record of a native smoke test of the 2.x versions, so
Windows and Linux stay in preview. See the
[operational status](docs/PROJECT-STATUS.md).

## Compatibility

| Component | macOS | Linux | Windows |
|---|:---:|:---:|:---:|
| ezQuake stable + nightly | Universal · conditional/preview | x86-64 · preview | x64 · preview |
| MVDSV | arm64 · preview | amd64 · preview | x64 · preview |
| QTV | arm64 · preview | amd64 · preview | x64 · preview |
| QWFWD | arm64 · preview | amd64 · preview | x64 · preview |
| `x86qw` CLI | arm64 + amd64 | amd64 + arm64 | x64 |

> [!NOTE]
> The required CI runs the Go tests on Linux (with the race detector, staticcheck and govulncheck) and on Windows, builds the CLI for the five platforms and validates the distribution and the site. A nightly e2e installs for real on Linux, macOS and Windows. That is not the same as validating real use on each system.

The macOS ezQuake stable keeps the upstream bundle without changing `Info.plist`,
sandbox, entitlements or signature. It has no Developer ID and no proven
notarization, so it stays conditional; see
[ADR 0004](docs/adr/0004-preservar-bundle-upstream-ezquake-stable-macos.md).

## Developing

Anyone who cloned the repository uses the local sources:

```sh
git lfs pull
go -C go run ./cmd/x86qw help
go -C go run ./cmd/x86qw play --help
```

The full validation:

```sh
go -C go test ./...
go -C go run ./cmd/x86qw-content verify
cd site && npm test && npm run deploy:dry-run
```

To view the portal locally:

```sh
cd site
npm run dev
```

Open `http://127.0.0.1:8787`. The versioned production configuration holds no
assets directory: the **Deploy site** workflow assembles the site and publishes it
on every merge that changes `site/public`, and `wrangler deploy` without `--assets` fails
closed. The CLI release and the TUF operation are in the
[release runbook](docs/runbooks/release.md); content maintenance, in
[maintenance/README.md](maintenance/README.md); the site's operation, in
[site/README.md](site/README.md).

## Contributing

Contributions are welcome when they preserve provenance, reproducibility and the boundary between upstream and x86QW customization. Before opening a PR:

1. read the [contributing guide](.github/CONTRIBUTING.md);
2. keep each change small and traceable;
3. run `go -C go test ./...` and `go -C go run ./cmd/x86qw-content verify`;
4. explain the origin and the consumer of any new distributed file.

[Report a bug](https://github.com/x86dx2/x86qw/issues/new?template=bug-report.yml) · [Suggest an improvement](https://github.com/x86dx2/x86qw/issues/new?template=feature-request.yml) · [Get support](.github/SUPPORT.md) · [Security policy](.github/SECURITY.md)

## Provenance and licenses

x86QW is a composite distribution, not a single relicensing of all its components. Clients, engines, mods, tools and content keep their original licenses and terms; our own customizations stay separate from the upstream bytes. The registered base data is treated as an independent component.

See [provenance and licenses by component](maintenance/docs/provenance.md) before copying, publishing or redistributing artifacts. QuakeWorld, Quake and related marks belong to their respective owners; this community project is neither affiliated with nor endorsed by them.

---

<div align="center">
  <strong>READY // CONNECT // FRAG</strong><br>
  <sub>Made to preserve QuakeWorld — and keep playing.</sub><br><br>
  <a href="https://qw.x86.com.br/">Portal</a> ·
  <a href="https://github.com/x86dx2/x86qw/releases">Releases</a> ·
  <a href="https://github.com/x86dx2/x86qw/issues">Issues</a>
</div>
