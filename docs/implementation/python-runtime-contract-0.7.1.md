# Python runtime contract — 0.7.1 fix

This note records the implementation of issue
[#44](https://github.com/x86dx2/x86qw/issues/44), delivered in the public release
`0.7.1`.

> State: implementation published and verifiable. `0.7.0` stays immutable
> in the history.

## Baseline and defect

- published baseline: `57c7a06745d3d760a2ace70112beb6bbd391d633`;
- baseline of the corrective code: `8ba1f909fc8b4275c5a5001b0c2e1898b6f85c68`;
- public release: `x86qw-installer-0.7.1`;
- bundle: 157113 bytes, SHA-256
  `a0946ffcc8a4e1181dbc55ea08caf54691b18b12e901d12069eb2064b38c0d80`;
- PowerShell accepted `py -3`, `python3` or `python`, but `x86qw.cmd` used
  only `py -3`;
- Unix used only `python3` and did not reject Python 3.9 before the download;
- the Unix bootstrap loaded the whole bundle to compute the SHA-256.

## Decision

The minimal contract is in `dist/installer/bin/python_runtime.py`:

- minimum version `(3, 10)`;
- a probe through `sys.version_info`, without parsing localized text;
- launchers generated with the `sys.executable` that ran the installation;
- rejection of paths with control characters;
- shell- and CMD-specific quoting.

The wrappers must find Python before they can import that module. So
they implement minimal projections of the same contract:

- Unix: `python3`, then `python`;
- Windows: `py -3`, `python3`, then `python`;
- the persisted runtime is tried first and must pass the probe again;
- no compatible candidate means a failure before any network or mutation.

The `update`/`upgrade` handoff still uses an argument list with
`sys.executable`, without a shell. The final process installs the new CLI and generates the
launchers; there is no release/reacquisition in the middle of the mutation.

## Isolation of the PowerShell session

The `0.7.1` bootstrap is evaluated by `irm ... | iex` inside the current session,
but wraps its body in a script scope of its own. There is no child process
nor a new window. Internal variables, `$ErrorActionPreference` included, must not
escape that scope; the console and pipeline encodings are
restored in `finally`. The only intentional global effect is
`$global:LASTEXITCODE`, which receives exactly the Python installer's code.

The error stays visible in the terminal and the bootstrap does not run `exit`. The
evidence invokes the file through `Invoke-Expression` in a harness that already has
its own state and proves:

- the calling session stays alive after success and failure;
- `$ErrorActionPreference` and the encodings stay as before;
- no internal variable, such as `InstallerVersion`, leaks to the caller;
- `$global:LASTEXITCODE` keeps Python's code;
- the failure diagnosis stays visible and does not expose the probe's discarded
  output.

That evidence passed on the Windows runner's Windows PowerShell 5.1. A local
run in PowerShell outside Windows stays complementary and does not replace the
native matrix.

## Preserved contracts

- existing commands and exit codes;
- unlimited arguments in CMD;
- paths with spaces and Unicode;
- installation without administrative privileges;
- stable and nightly side by side;
- published bundles and tags, `0.7.0` included, immutable byte for byte.

## Contracts delivered in the fix

- Python below 3.10 fails before download or mutation;
- launchers no longer depend on a single global executable name;
- the Unix bootstrap's SHA-256 is computed in 1 MiB blocks;
- the zipapp has a version guard before importing the manager.

## Release evidence

- tests with only `python`, only `python3`, only `py`, all of them present
  and the persisted runtime removed;
- Python 3.9 rejected before the downloader;
- Python 3.10 and 3.13 on the three systems;
- paths with spaces/Unicode, public commands and long arguments;
- `git diff --check`, Git LFS and `manage.py verify`;
- 393 maintenance tests and four site tests;
- 7/7 Actions jobs: Quality gate and Python 3.10/3.13 on Ubuntu, macOS and
  Windows.

That matrix validates the portable contract and the native launchers. It is not
presented as a graphical or network smoke of ezQuake, MVDSV, QTV or QWFWD.

## Residual risks

- the resolution must stay duplicated in the wrappers until there is a Python
  runtime that can load the canonical module;
- the transport hardening and download limits belong to issue #45;
- the graphical and network smokes of the runtimes stay separate from the portable
  matrix, per the roadmaps.
