# State v2 and execution without mutation

> Historical document of the 0.2.x line. The current clean bootstrap uses `.x86qw/`
> and does not convert old trees; see `clean-bootstrap-layout.md`.

This migration separates preparing the installation from running the runtimes.

## Installation state

Format 1 of `.install/state.json` recorded the profile, the custom selection,
the observed components and the known catalog. Format 2 keeps those
fields and adds:

- `capabilities`: explicitly recorded capabilities, initially empty
  for historical installations;
- `component_fingerprint`: a deterministic fingerprint of the components actually
  recorded.

`install`, `update`, `upgrade` and `repair` perform the one-way migration by
a temporary write followed by a rename. The `custom` selection, the installed
components and the recognized historical identifiers are preserved. An
`update` simulation shows the migration in the plan before any write.

The operational rollback is restoring the previous `state.json`; no
payload or personal file is removed by the state conversion. The installer
still accepts format 1 throughout this compatibility line.

Technical capabilities of runtimes stay in the declarative catalog and are not
selectable capabilities of the installation. Since no operational profile was
enabled in this line, the only value accepted in `state.json` is
`"capabilities": []`; arbitrary identifiers are rejected.

## Support derived from the games

`play-support` formalizes gamecodes and managed settings derived from the
installed components. It is prepared by `install`, `update`, `upgrade` or
`repair`. `verify` detects absence or divergence and points to one of those actions.

`play` and `host` only validate that support. They create no receipts, do not
change `.install/components/`, do not repair executables and do not write to
personal settings. The only materialization allowed during `host` is the
ephemeral PK3 content MVDSV needs, protected by the session journal and
reconciled at shutdown or on the next run.

Diverging personal files are never overwritten during repair. A
non-sensitive ephemeral file changed during the session is also preserved and
reported. Ephemeral settings classified as sensitive are removed
even when modified, since they may hold passwords; the journal records only
redacted metadata.

## Operational dependencies

MVDSV, QTV and QWFWD are independent installable components. The game
selection determines the component `host` requires; QTV accepts a remote upstream without
a local MVDSV; QWFWD can run on its own. The historical profiles still
hold exactly the choices already published.
