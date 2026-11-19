x86QW
=====

x86QW is a self-contained QuakeWorld distribution, based on ezQuake and on
selected nQuake components, with mods and configurations maintained by the
project.

START
-----

Use the launcher at the root of the installation:

  ./x86qw play

It lists only the installed mods, lets you choose the map and starts
ezQuake in the right gamedir. Each mod shows its controls when it loads.

PERSONAL CONFIGURATION
----------------------

Updates keep the files below:

  qw/x86qw-user.cfg
  qw/x86qw-ktx-user.cfg
  arena/x86qw-arena-user.cfg
  prox/x86qw-prox-user.cfg
  fortress/x86qw-fortress-user.cfg
  td2/x86qw-td2-user.cfg

Put global customizations in qw/x86qw-user.cfg and the settings of each mod
in its own file. Do not edit autoexec.cfg or the x86qw-*.cfg profiles, because
the distribution manages them.

MAINTENANCE
-----------

  ./x86qw verify
  ./x86qw update
  ./x86qw upgrade
  ./x86qw cleanup
  ./x86qw uninstall
  ./x86qw uninstall --purge

cleanup removes caches that can be rebuilt. Server downloads and personal
data are removed only when you ask for it explicitly.

The registered PAKs live in id1/ and are kept by updates and by a plain
uninstall. uninstall --purge removes the whole installation.

ezQuake documentation: https://ezquake.com/docs/
x86QW project: https://qw.x86.com.br/
