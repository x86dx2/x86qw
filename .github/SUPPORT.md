# x86QW support

## Start with the local diagnosis

In any folder, with the `x86qw` command on the `PATH` (or `./x86qw` in the
installation folder; `x86qw.exe` on Windows):

```sh
x86qw version
x86qw verify
x86qw doctor
```

If verification finds managed content missing or wrong, review the repair
plan first:

```sh
x86qw repair --dry-run
```

`x86qw doctor --bundle` writes a diagnostic zip, outside the installation and with the
home folder replaced by `~`, to attach to a report once you have reviewed it.

## Where to ask for help

- **A reproducible failure or regression:** open a [bug report](https://github.com/x86dx2/x86qw/issues/new?template=bug-report.yml).
- **An idea or a new feature:** use the [improvement proposal](https://github.com/x86dx2/x86qw/issues/new?template=feature-request.yml).
- **A vulnerability:** do not open an issue; follow the [security policy](SECURITY.md).
- **Installing and using the CLI:** see the [installation section](../README.md#installation), `x86qw help` and `x86qw COMMAND --help`.
- **Server, QTV or QWFWD:** see the [hosting guide](../docs/HOSTING.md).

## Useful information in a diagnosis

Include the operating system and architecture, the x86QW version, the installed profile, the exact command, the full output without secrets and the result of `verify`. Also say whether the installation is new or updated and whether the problem happens with stable, nightly or both.

Remove passwords, tokens, private addresses and personal paths before publishing logs. Do not attach the contents of `.x86qw/sessions/` or `.x86qw/setup/` without reviewing every file.

## Limits

The project supports the packaging, the CLI and the x86QW customizations. Problems confirmed in upstream software can be forwarded to the corresponding maintainer with a reproducible case. The declared support matrix is in the [README](../README.md#compatibility).
