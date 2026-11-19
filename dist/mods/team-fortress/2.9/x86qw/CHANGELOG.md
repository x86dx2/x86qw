# Team Fortress 2.9 in x86QW

## Original rebuilt

- `upstream/tf28.zip`: complete official 2.8 client, 4204316 bytes, SHA-256 `56a6767b7944f2ce54423f18f1cd59239ae37a7c7456f684227d6f5310c76291`;
- `upstream/tf29qw.zip`: official QuakeWorld 2.9 update, server only;
- `source/tf_29src.zip`: preserved public source of version 2.9, byte for byte
  identical to the Internet Archive copy; the origin of its first publication
  could not be proven, so it is not labelled an official release;
- the useful assets and maps nQuake selected remain the base of the distribution.

## x86QW changes

- `source/0001-preserve-client-controls.patch` removes only the 20 remote bind commands of the MOTD and of the bindings menu;
- `runtime/qwprogs.dat` was recompiled with FTEQCC and promoted after a smoke test on stable and nightly;
- the consistent binds stay in the local x86QW profile and remain under the player's control;
- builds `cl_remote_capabilities` without replacing the client's safe list and
  adds only the legacy cvars TF needs;
- automatically removes from old configurations the invalid literal value
  `$cl_remote_capabilities,...` the previous profile produced;
- starts the mod directory only once with `-game` and publishes `*gamedir`
  separately, without reloading the filesystem during startup;
- leaves the console clean at startup and keeps the help on `F10`, on demand only;
- `runtime/sound/weapons/bounce2.wav` restores the sound `TRIGGERS.QC` references directly, using the official file shipped with KTX 1.47: 7960 bytes, SHA-256 `d2da688ec5fc64f24b798b645eb80b8cdd60c6e7d44cb68a7e05e2264e1e69d5`.

## Known gaps

- `clan1.wav` to `clan5.wav` are only played when the administrator turns on `localinfo clanmsgs on`; they were not found in the 2.8 client, in the 2.9 upgrade or in any verifiable official file;
- the historical build produces 187 warnings with FTEQCC; the runtime matrix passed,
  but their individual classification stays recorded in the roadmap for a
  future reduction without a broad modernization.

## Runtime validation

- 2026-07-30: ezQuake 3.6.9 and nightly `20260616-101233_a86996a` on `2fort5r`;
- PR1 gamecode loaded, the player joined and `TeamFortress QuakeWorld v2.9` was confirmed;
- no remote bind, blocked command, missing required file or mod error.

## Fixes sent to the client

- the false QVM error before the PR1 fallback was sent to ezQuake in
  [PR #1149](https://github.com/QW-Group/ezquake-source/pull/1149);
- the duplicate registration noise after `vid_restart` was sent separately
  in [PR #1150](https://github.com/QW-Group/ezquake-source/pull/1150).

## Deliberately unchanged

- the mod's opt-in executions and screenshots were not removed, because they do not happen automatically;
- no substitute sound, class, weapon, map or rule was invented.
