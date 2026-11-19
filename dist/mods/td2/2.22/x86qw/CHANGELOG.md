# Total Destruction 2 2.22 in x86QW

## Original preserved

- `source/quakeworld-TD2.22QW-server_PTBR.tar.gz`: complete original distribution with source, manuals, gamecode, models and sounds, 567029 bytes, SHA-256 `b0b7632debe931e435008df939bade3791b5d21abfbb66f828790f1996beca93`.

## x86QW changes

- restores `saw_down.wav` by copying `saw.wav`, the only verifiable original bytes for the missing reference;
- `source/0001-runtime-compatibility.patch` restores the standard `wad` worldspawn field;
- removes `easyrecord`, `stop` and hundreds of `wait` commands, leaving recordings under the player's control;
- removes remote binds and `scr_centertime`, which the local profile already provides;
- removes persistent gamma changes and keeps the visual duration of the flash bomb with the original entity effects;
- removes the dead `monster_vomit` branch that called `vomitus/v_sight1.wav`, a file that does not exist in the Quake 1.06 code base either;
- starts the mod directory only once with `-game` and publishes `*gamedir`
  separately, without reloading the filesystem during startup;
- leaves the console clean at startup and keeps the help on `F10`, on demand only;
- `runtime/qwprogs.dat` was recompiled with FTEQCC and promoted after a smoke test on stable and nightly.

## Known gaps

- the voting list names 30 external maps not included in the preserved original distribution;
- the build produces 14 historical warnings that still need to be classified.

## Runtime validation

- 2026-07-30: ezQuake 3.6.9 and nightly `20260616-101233_a86996a` on `dm6` with `temp1 65560`;
- the player joined, weapons and effects loaded and `td2qw 2.22` was preserved;
- no automatic demo, `stop`, persistent gamma, remote bind or `wad`/`vomitus` error.

## Fixes sent to the client

- the false QVM error before the PR1 fallback was sent to ezQuake in
  [PR #1149](https://github.com/QW-Group/ezquake-source/pull/1149);
- the duplicate registration noise after `vid_restart` was sent separately
  in [PR #1150](https://github.com/QW-Group/ezquake-source/pull/1150).

## Deliberately unchanged

- `temp1 65560` and the rules for weapons, spells, runes and powers were kept;
- no external map, menu, mode, sound or mechanic was added.
