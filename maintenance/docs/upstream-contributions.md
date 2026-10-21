# Contributions to upstreams

x86QW keeps upstreams without mixing in independent fixes. A PR is only sent when there is an official public Git repository, the change is generic for the upstream and it can be shown outside the x86QW distribution.

| Project | Public Git | x86QW change | Destination |
| --- | --- | --- | --- |
| ezQuake | `QW-Group/ezquake-source` | skip the QVM attempt when `sv_progtype` explicitly selects PR1 | [PR #1149](https://github.com/QW-Group/ezquake-source/pull/1149), draft, waiting for CI/review |
| ezQuake | `QW-Group/ezquake-source` | make registering the same object/function idempotent during `vid_restart` | [PR #1150](https://github.com/QW-Group/ezquake-source/pull/1150), draft, waiting for CI/review |
| KTX | `QW-Group/ktx` | early definition of `k_defmap` | do not send; the fix belongs to the x86QW launcher |
| nQuake | `nQuake/distfiles` | `bounce2.wav` missing after modularization | do not send; the file already exists in the snapshot's KTX and the fix belongs to the x86QW composition |
| Final Arena | not found | future source patches | keep in x86QW until an official upstream appears |
| Pro-X | no public source exists | compatibility ENT | do not send as code; the author withheld the source deliberately |
| Team Fortress 2.9 | not found | removal of forced remote binds | keep the patch ready and documented |
| TD2 2.22 | not found | QuakeC compatibility fixes | keep the patch ready and documented |

## Submission rules

- create one branch per problem, from the upstream's current default branch;
- include no x86QW branding, configuration or paths in a generic fix;
- describe version, environment, reproduction steps, behavior before and after;
- attach an automated test when the codebase allows it; otherwise, record a manual matrix;
- do not combine cleanup, formatting and a functional change;
- refer to the mod's changelog in x86QW as history, not as the only justification;
- follow CI and review until merge or an explicit decision by the maintainer.

## State on July 30, 2026

- both PRs start from the current `master` `a86996a3` and hold no x86QW
  branding, configuration or files;
- both went through diff inspection and `git diff --check`;
- the reproduction host has no CMake, so the build will be validated by the
  upstream's CI; GitHub had not started checks yet at the time of
  submission;
- the other mods got no PR because no public Git repository was found that
  represents exactly the distributed version.
  `QW-Group/Quake-Custom-Team-Fortress` is Custom TF, not the Team Fortress
  2.9 kept here.
