# Final Arena 1.20 in x86QW

## Original rebuilt

- `upstream/fasrv12.zip`: official 1.20 server, 278932 bytes, SHA-256 `a250816fc3ffa461e21fc0c85cb286751b2bb22de2fd7d7bc42add7d0fd6ca70`;
- `upstream/farena12.zip`: official 1.20 client, 7018988 bytes, SHA-256 `88dac7c3b7982deaddd3970a5c3293eeee8a30e18ddf9fd404c1504c6ab519dd`;
- `source/qwrasrc12.zip`: official QuakeWorld source extracted from the server, 70227 bytes, SHA-256 `b83684390e6c68e2cdb39e9d22312df798b4c4b27ff3d816f75cfb27a7f67de7`.

The 93 members of the official `pak0.pak` were compared with the nQuake content and are preserved byte for byte. nQuake's `qwprogs.dat` is a later derivative attributed to Rakk; its public source was not found.

## x86QW changes

- keeps the Rakk gamecode nQuake already uses, so voting, backpacks, airgib and statistics without an equivalent source are not lost;
- separates the client profile, the server configuration and the personal file;
- starts the mod directory only once with `-game` and publishes `*gamedir`
  separately, without reloading the filesystem during startup;
- exposes controls and help without changing the mod's rules;
- leaves the console clean at startup and keeps the help on `F10`, on demand only.

## Known gaps

- the official 1.20 source can rebuild the base, but not the Rakk derivative;
- `sprites/s_aball.spr` appears only in `amtest.qc`, a test entity, and does not exist in the official client.

## Runtime validation

- 2026-07-30: ezQuake 3.6.9 and nightly `20260616-101233_a86996a` on `23ar-a`;
- Rakk gamecode loaded, the right map opened and the player joined the queue;
- no missing required file, blocked command or mod error.

## Fixes sent to the client

- the false QVM error before the PR1 fallback was sent to ezQuake in
  [PR #1149](https://github.com/QW-Group/ezquake-source/pull/1149);
- the duplicate registration noise after `vid_restart` was sent separately
  in [PR #1150](https://github.com/QW-Group/ezquake-source/pull/1150).

## Deliberately unchanged

- no Rakk change was reimplemented by inference;
- no new sprite, map or mode was added.
