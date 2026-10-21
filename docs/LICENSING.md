# License of the project's own content

The Go code, presets, patches, configurations, documentation, tests,
metadata and other files the x86QW project creates are identified as
`MIT` when no more specific notice applies. The canonical license is in
[LICENSE](../LICENSE), and the distribution notice in [NOTICE](../NOTICE).

This does not relicense upstream components. Clients, engines, mods, tools,
base data and PAKs keep their own notices and terms; the provenance of each
component is in [maintenance/docs/provenance.md](../maintenance/docs/provenance.md)
and in the inventories. A component package's `license_url` points to the
license of its source, not to x86QW's. The entries of the `x86qw-cli` CLI
point to this repository's `LICENSE`.

Each `x86qw-cli-*` release publishes the binaries with `SHA256SUMS` and a
build provenance attestation on GitHub; content packages are verified by
size and SHA-256 against the signed catalog. These records document how the
bytes were produced; they do not change the license of the upstream payloads
nor replace their notices.

The historical `x86qw-installer-*` releases (the Python 0.x and 1.0 line)
also published `candidate.json`, `sbom.spdx.json` and `provenance.json`; they
stay immutable.
