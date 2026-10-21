# x86QW roadmap

## Where we are

The current line is the 2.x CLI in Go, in `owner-only` mode
([ADR 0008](adr/0008-owner-only-release-gates.md)). Today's state is in
[PROJECT-STATUS.md](PROJECT-STATUS.md). The road to 1.0 and the post-1.0
plans of the Python line remain recorded in [releases/](releases/),
[post-1.0/](post-1.0/) and [implementation/](implementation/), as history.

## Next steps

1. **Recorded native validation:** a versioned record of installation and use
   on Linux and Windows, to take those platforms out of preview.
2. **TUF root:** a command in `x86qw-release` to prepare and sign a new root,
   before v1 expires on 2027-08-10.
3. **Key custody:** keep root and targets on their own offline media, with a
   backup, before any opening to third parties.
4. **macOS:** decide between keeping the ezQuake stable upstream bundle
   (conditional) or seeking a signed and notarized bundle.

## Opening to third parties

`external-public` only comes with an explicit decision by the maintainer.
Before it:

- native validation recorded on the announced platforms;
- key custody separated from the everyday machine;
- support and a fix policy defined for external users;
- public texts reviewed so they promise no more than what was validated.

## Out of current scope

A demo center, formal MVD/QWD validation, training as a command, new clients
or engines, external mods and maps, persistent system services and additional
operational profiles need their own proposals, contracts, tests and platform
evidence. They do not come in as a side effect of `play`, `host`, `update` or
`upgrade`.

For the long-term view of the ecosystem, see
[ROADMAP-QUAKE-ECOSYSTEM.md](ROADMAP-QUAKE-ECOSYSTEM.md).
