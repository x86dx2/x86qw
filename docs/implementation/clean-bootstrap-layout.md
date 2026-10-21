# Clean bootstrap layout

The current layout assumes an installation created from scratch. Earlier trees can
be renamed and kept outside the destination only as a reference; there is no
automatic conversion.

## Contract

- `.x86qw/`: CLI, state, receipts, inventories, lock and journals;
- `mvdsv` or `mvdsv.exe`: dedicated server in the QuakeWorld basedir;
- `qtv/`: QTV's runtime, resources, configuration and operational data;
- `qwfwd/`: QWFWD's runtime and configuration;
- `docs/licenses/`: licenses distributed with the services.

The MVDSV, QTV and QWFWD packages stay reproducible and can carry
artifacts for the three systems. Before the overlay, the installer selects the
variant declared for the destination and drops the staging of the others. So a
macOS arm64 installation gets no ELF or PE; Linux amd64 gets no Mach-O or
PE; Windows x64 gets no Mach-O or ELF.

`BUILD.json`, sources and patches are maintenance evidence. They take part in the
component's validation, but are not player payload.
