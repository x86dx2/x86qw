# Dedicated hosting, QTV and QWFWD

x86QW distributes MVDSV `1.11+x86qw.4`, QTV `0+025ca949aca0+x86qw.3` and QWFWD
`1.30+x86qw.4`. They come in when the installation hosts (the Host or Play and
host intent) or in the complete profile of someone who hosts. Each runtime
has its own package, receipt and verification. The commands download no
content: if a component is missing, add it in **Components and setup** in the
app or with `x86qw setup`.

## Two starting points

**The server the installer builds.** When you choose Host or Play and host,
the installer records the choices and the rcon password in `.x86qw/setup/`
and one preset per instance (`x86qw-1`, `x86qw-2`…) in
`qw/x86qw-host-presets.json`. By default there are two KTX instances (Duel on
`dm6` and 4on4 with Frogbots on `dm3`), starting at UDP port 27500, with QTV
on 28000 and QWFWD on 30000. That server accepts players from the network on
every interface; choose **This machine only** (or `--local` in the recipe) to
restrict it to `127.0.0.1`. The app's **Host** screen starts and stops the
session in the background and shows what is running. The equivalent command
appears at the end of an install by recipe.

**The `host` command directly.** It picks a game and starts MVDSV on
`127.0.0.1:28501`, attached to the terminal:

```sh
x86qw host ktx --mode duel --map dm6
x86qw host final-arena --map 23ar-a
x86qw host pro-x --map proxmap1
x86qw host team-fortress --map 2fort5r
x86qw host td2 --map dm6
x86qw host --menu
```

Without a game and without `--mode`, the command asks for the game; `--menu`
opens the app's Host screen. `--instance MODE:MAP:PORT[:bots]` adds more KTX
servers to the same session (repeatable); QTV and QWFWD stay with the first:

```sh
x86qw host ktx --mode duel --map dm6 --port 27500 --instance 4on4:dm3:27501:bots
```

Local layouts, without passwords, live in `qw/x86qw-host-presets.json` and go
into the profile backup:

```sh
x86qw host ktx --mode duel --map dm6 --save-preset local-duel
x86qw host --preset local-duel
```

Each game uses its own gamecode, personal configuration and maps. Since MVDSV
does not read PK3, payloads such as KTX and Final Arena are materialized only
during the session and removed when it stops.

## Network

The default bind of `host` is `127.0.0.1:28501`. To accept external
connections, choose the interface deliberately and adjust the firewall and
NAT outside x86QW:

```sh
x86qw host ktx --bind 0.0.0.0 --port 28501 --mode ctf --map e2m2
```

## Passwords

`--password`, `--spectator-password`, `--rcon-password` and `--qtv-password`
are accepted, but the value may stay in the shell history and in the process
list of the CLI itself. Prefer no-echo input or a file:

```sh
x86qw host ktx --prompt-password --prompt-rcon-password
x86qw host ktx --password-file ~/.config/x86qw/player-password
x86qw qtv --prompt-qtv-password --upstream quake.example:28501
```

There are also `--spectator-password-file`, `--rcon-password-file` and
`--qtv-password-file`. No-echo input needs a terminal. On Unix, the file must
be regular, cannot be a symlink and must be restricted to its owner
(`chmod 600`). A single trailing line break is removed; multi-line content is
refused. Passwords never go into the child process's arguments or into the
logs. An external bind without a password produces an explicit warning,
without blocking an intentional choice.

## Frogbots

The distributed KTX includes 77 Frogbot routes and 54 Race routes. The CLI
crosses those resources with the installed BSPs; regular personal routes in
the folders documented in [Frogbot names](FROGBOTS.md) are recognized too. On
the dedicated server, bots are added automatically when the first player
joins; skill and limit are set by the server's native cvars. Weapon, health
and break on death belong only to ToT:

```sh
x86qw host ktx --mode duel --bots 1 --bot-skill 8 --bot-names x86qw
x86qw host ktx --mode 4on4 --fill-bots --bot-skill 6
x86qw host ktx --mode ffa --fill-bots --bot-skill random
x86qw host ktx --mode ctf --ctf-hook smooth --ctf-runes off
x86qw host ktx --mode race --race-style match --race-scoring formula1
```

A fixed bot team, the pacemaker and hiding racers are client controls and
stay available in `play`, not in `host`.

The dedicated server takes the same four name profiles: `default` does not
customize KTX, `x86qw` draws the distribution's maps and modes, `one-piece`
draws the optional One Piece list and `personal` reads
`qw/x86qw-frogbot-names.json`. The CLI applies the `/` prefix and the Quake
color. With `--bot-skill random`, each new bot gets its own skill from 1 to
20.

When the host's bind is external and there are bots, x86QW reapplies
`k_fb_admin_only 1` after the personal configuration. Remote players cannot
change the roster; a host restricted to loopback keeps the KTX default.

## QTV

To start QTV with the dedicated server:

```sh
x86qw host ktx --mode 4on4 --map dm3 --with-qtv
```

HTTP is on `127.0.0.1:28000` by default. You can also start QTV on its own:

```sh
x86qw qtv --upstream 127.0.0.1:28501
```

HTTP upload stays off in the managed profile. Use `--bind 0.0.0.0` only if
you really want to expose the page and the stream. On any non-loopback bind,
the CLI warns that the HTTP/QTV interface will be exposed and that the
upstream password does not authenticate HTTP access, even when
`--qtv-password` was given.

When composing `host --with-qtv`, the upstream uses a reachable address of
MVDSV's bind: the IPv4 wildcard becomes `127.0.0.1`, the IPv6 wildcard becomes
`[::1]`, specific addresses are kept and IPv6 is always written in brackets.
Upstreams accept only `IPv4:port`, `hostname:port` or `[IPv6]:port`. Spaces,
control characters, chained commands, out-of-range ports and IPv6 without
brackets are refused.

On the app's **Servers** screen, `ctrl+w` watches a server through its QTV;
on the command line, `x86qw open qw://N@host:port/qtvplay` does the same.

## QWFWD

```sh
x86qw proxy
x86qw proxy --bind 0.0.0.0 --port 30000
x86qw proxy --background
x86qw host ktx --mode duel --with-proxy
```

The proxy uses `127.0.0.1:30000` by default and does not promise to lower
latency. The managed configuration does not query public masters
automatically.

## See and stop the running stack

Services stay attached to the terminal or are detached with `--background`.
To look at or stop the stack:

```sh
x86qw status
x86qw status --stop
```

The app's **Host** screen shows the same information. The output gathers the
running operation and session and, for each registered MVDSV, QTV or QWFWD,
shows the identity state, PID, runtime, executable, endpoint and effective
parameters such as game, mode, map, bots, bind, ports, clients, upstream and
MVD recording. Password values never show; the view only says which kinds of
secret were set.

`status` without options is read-only: it does not take the lock, does not
recover a session, does not stop processes and does not change files.
`status --stop` asks on the terminal and, without one, needs `--yes`; it sends
the identified controller a private request for a coordinated stop. An
inconclusive journal or one without a lock is kept for inspection, without
signaling a PID by inference.

When a stack is detached, the resolved password values are sent once to the
new controller over an anonymous pipe. They never go into the arguments, the
journal, the status or the log. The runtimes get a closed `stdin`; secrets
exist only in the private ephemeral configurations covered by the session's
cleanup.

## Start, stop and locks

Before creating any process, the CLI detects duplicate ports, tries the
requested binds and validates folders, executables, configurations and
components. The order is MVDSV, readiness through `status`, post-map
configuration over local RCON, QTV with HTTP and upstream readiness and,
last, QWFWD with a proof of life and the port taken. A failure at any step
stops the processes already started, in reverse order.

`Ctrl+C`, `SIGINT`, `SIGTERM`, a startup error and a normal stop end the
children in reverse order, wait for them to exit and force only processes that
do not answer. On Unix, the CLI checks the whole group after `SIGTERM` and
applies `SIGKILL` if a descendant remains. On Windows, each process starts
suspended, joins a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` and
only then has its initial thread resumed; a failure to join stops the process
while it is still suspended, without running the runtime.

Only one service stack or operation that changes the installation is active
at a time. `host`, `qtv`, `proxy`, `setup`, `install`, `update`, `upgrade`,
`repair`, `cleanup`, `migrate` and `uninstall` share the atomic lock
`.x86qw/sessions/active.lock`. If the registered controller is alive, the
second run fails without touching journal, processes or files. `status`,
`version`, `verify`, `doctor`, `hub` and `play` do not use the lock; nor do
the plans with `--dry-run`.

If the identity cannot be proven, the CLI fails conservatively and points to
inspecting the lock. An abandoned lock is only reclaimed when PID, creation
token and executable confirm that the previous controller died, and recovery
also validates the controller recorded in the journal: removing only
`active.lock` never authorizes recovering a stack that is still alive.
Observing, reclaiming and replacing an abandoned lock are serialized per
installation (`flock` on POSIX and a private global mutex on Windows).

Each run keeps a private journal in
`.x86qw/sessions/<session-id>/session.json` (`0700` for folders and `0600` for
the file on Unix). It records processes, ephemeral configurations,
materialized files, non-sensitive hashes and created folders. Children use a
group of their own and record PID, group, creation token and executable.
After a confirmed crash, recovery stops only the process whose identity
matches exactly; a reused PID is left alone. An inconclusive identity blocks
the new stack and the removal of files the process may depend on.

On Windows, `.x86qw/`, sessions, locks, journals, stop requests, logs and
sensitive configurations use a protected DACL with access only for the current
user and `LOCAL SYSTEM`. The object is born private, before the first write;
an inconclusive volume or ACL makes the operation fail closed. External
password files are only validated and read through the same handle, never
reconfigured by x86QW. See [ADR 0003](adr/0003-private-windows-dacl.md).

Non-sensitive temporary files the session created are removed when they still
match their hash; modified files and pre-existing data are kept and reported.
Ephemeral configurations with passwords are classified as sensitive and
removed on stop or recovery even when modified, with no hash, backup or
content in the journal. If the path was replaced by a folder or a special
file, it is kept and finalization fails without recursive removal. A symlink
is removed without touching its target. This is a logical removal, not a
promise of physical erasure on the device.

## ZIP and PK3 limits

Every extraction goes through the same boundary: the archive is walked
entirely before the first read handed to the consumer or the first write.
Internal names use POSIX semantics, whatever the host system. Traversal,
absolute paths, drives, backslashes, control characters, forbidden Win32
characters (`<`, `>`, `"`, `|`, `?`, `*`), Windows reserved names, names
ending in a period or space, links, special members and exact, case or Unicode
collisions are refused.

Only Stored and Deflate are accepted, with these limits:

- 4,096 members;
- 512 MiB for the compressed archive;
- 128 MiB per member;
- 512 MiB uncompressed in total;
- 16 folder levels;
- 240 UTF-16 units per path;
- a maximum compression ratio of 500 to 1.

Extraction checks each member's size and CRC while writing, uses a private
sibling folder, applies only `0644` or `0755` and promotes the result by
rename; the destination must not exist beforehand. A failure before promotion
removes only the staging area.

When materializing for the dedicated server, the PK3 goes through that
extraction into a staging folder beside MVDSV's context, and its members are
published there as hard links, which never replace a file. The journal records
the whole payload in one write before any member is published, so a session
that dies halfway is cleaned up on the next run. A different personal file is
never overwritten. Personal
configurations, logs and demos are not treated as managed content. See the
[file boundary ADR](adr/0002-single-archive-boundary.md).
