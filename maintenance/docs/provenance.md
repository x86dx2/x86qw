# Provenance and redistribution

A file being available for download does not mean it may be redistributed.
No component enters the mirror before its license, attribution, immutable
origin and checksum are recorded.

## Initial audit — updated on July 29, 2026

| Component | Evidence | Mirror state |
| --- | --- | --- |
| ezQuake stable | [repository](https://github.com/QW-Group/ezquake-source) identified as GPL-2.0 | 3.6.9 kept in `dist/clients/ezquake/stable/3.6.9` and replicated on the mirrors |
| ezQuake nightly | binaries on `builds.quakeworld.nu`, derived from the same project | build `20260616-101233_a86996a` kept in `dist/clients/ezquake/nightly/20260616-101233_a86996a` |
| nQuake reference components | [distfiles](https://github.com/nQuake/distfiles) pinned by commit and broken down by `maintenance/inventory/components.json` | originals in `dist/distributions/nquake`; delivery packages are temporary reproducible builds |
| KTX | `1.46-dev` identified in the shipped QVM; [official 1.47 release](https://github.com/QW-Group/ktx/releases/tag/1.47) with checksums | the 1.47 QVM applied over the nQuake resources and published as an independent package |
| MVDSV | [official 1.11 release](https://github.com/QW-Group/mvdsv/releases/tag/1.11), sources and binaries kept by SHA-256 | official Linux/Windows runtimes; macOS arm64 reproduced from source with a minimal, recorded x86QW patch |
| QTV | [pinned upstream commit](https://github.com/QW-Group/qtv/commit/025ca949aca06cad6777de0075148ac06a15f4f0), with no numbered release | source and per-platform runtimes kept; the x86QW version derives from the immutable commit |
| QWFWD | [official 1.30 release](https://github.com/QW-Group/qwfwd/releases/tag/1.30), official sources and builds | runtimes and a safe configuration published as an independent optional component |
| Pro-X | [public 1.1 release](https://www.quakeworld.nu/forum/topic/3741/prox-11-released) recovered from the author's historical archive | original ZIP in `dist/mods/pro-x/1.1/upstream/`; public source code not found |
| Team Fortress | [2.9 runtime and public copy of the sources](https://www.frag-net.com/mod_quake_fortress.html) | runtime in `upstream/`; public source kept in `source/`, identical to the Internet Archive copy, but without proof of the original publication; the 2.9 gamecode applied over nQuake assets |
| Total Destruction 2 | complete [TD2QW 2.22 distribution](https://arenacamper.ddns.net/repo/quakeworld/), with source code; the historical `saw_down.wav` is byte-identical to the kept `saw.wav` | complete original in `dist/mods/td2/2.22/source/` and an independent installable package, without maps or a default password |
| maps and LOCs | `maps.quakeworld.nu` and individual authors | outside the mirror; add only items incorporated into x86QW one by one |
| x86QW presets | authored by this project | released once x86QW's own license is defined |
| `id1` PAKs | registered copy provided by the maintainer, pinned by SHA-256 | canonical sources in `dist/game-data/id1`; separate public package `x86qw-core-id1`, never built into the installer |

"Candidate" does not authorize publication yet. The license of the source
code does not prove, on its own, that every file added to the binary package
uses compatible terms. The `nQuake/distfiles` repository shows why the audit
must happen per artifact, and not just by the project's name. Clients other
than ezQuake are outside the active scope and are not captured in this phase.

### Initial ezQuake 3.6.9 recipe

On July 25, 2026, the three official assets of release 3.6.9 were downloaded
into a temporary area. Size, SHA-256 and the minimum members of the ZIPs
matched `checksums.txt` and the digests published by the GitHub API:

| Platform | File | Size | SHA-256 |
| --- | --- | ---: | --- |
| macOS | `ezQuake-macOS-universal.zip` | 8,560,464 | `2ccea8f214c91e5fc92b5cb195c81ac584f75a551d11a58de56e5e7951eec7ed` |
| Linux | `ezQuake-linux-x86_64.zip` | 22,268,939 | `6d5707bf9be1a8338441265f9bf03154d107488a4fed9eab4c989c29de6573ee` |
| Windows | `ezQuake-windows-x64.zip` | 4,135,630 | `1814d2c9df12a732a5b2efb8720e67bb94094b422794e5f2550821f17a377f4d` |

The three stable recipes are `ready`; their artifacts live in
`dist/clients/ezquake/stable/3.6.9` and are published only in the `x86qw`
projects. The catalog records the three nightly binaries and the full commit
`a86996a3d33dc1bc3fb15bfe7bcadd662b822557`. The exact source tarballs of
stable and nightly are also kept in `dist/` for reference and building.

### Independent KTX update

The official `qwprogs-qvm.zip` 1.47 release has SHA-256
`e7b6382197a31b8cbf010b1e3a3b20c19dc56d57290015ebba306f0e7ab5c6ed`.
Its `qwprogs.qvm` member has 1,578,544 bytes and SHA-256
`a1987546d3b00453e5a0e1e7e89d75edd2a6779997436d5dac6a7d741bdcc79c`;
`qwprogs.map` has 112,973 bytes and SHA-256
`71b3b9798ff91aa9d168ce8e8a28c2f2d23d54ba4c5ad84839dd4844b37e9c96`.
The packager keeps the other members of the reference `ktx.pk3`, swaps the
QVM and adds its official symbol map. The resulting x86QW package is
`1.47+x86qw.20`; sources, override, mode catalog, gameplay and hashes live in
the versioned inventory and in the ZIP's internal metadata.

The recompiled x86QW QVM has 1,580,404 bytes and SHA-256
`32cfd1e70d1b88abb591872cd92f3eeeee03ea3d4d0097d6b82f30cf2fb6ed91`;
its map has 113,316 bytes and SHA-256
`91414e05db60aace163ccae7e7ba27bdd6b89b9f347fe7619018ae6f1390c356`.
The recipe pins the `ec-/q3lcc` revision, applies separately build metadata,
identity, random skill, balancing and per-role Frogbot names, plus the
optional Quad Coach, and reproduces those two hashes from the 1.47 tarball.

The full install, verify and uninstall flow was run with ezQuake stable 3.6.9
and with nightly `20260616-101233_a86996a`. That proves the package
management in the client; the QVM is still server code, officially meant for
MVDSV according to KTX's upstream.

Every current installable artifact is also published in the GitLab Generic
Package Registry. The catalog lists GitHub first and GitLab second; the
publishing tool downloads both copies and requires the same size and SHA-256
before recording.

## Required record per artifact

- component, version, channel, system and architecture;
- immutable original URL, project and author;
- declared license and the URL of the kept license text;
- URLs of the matching sources and attribution obligations;
- name, size and SHA-256 of the received file;
- ingestion date and the tool used;
- URLs and SHA-256 of the package published by x86QW.

If redistribution is not clear, the catalog may point to the origin, but
x86QW will not store a copy until it gets authorization.
