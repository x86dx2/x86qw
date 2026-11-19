# Pro-X 1.1 in x86QW

## Original preserved

- `upstream/prox_11.zip`: complete public 1.1 package, 9095898 bytes, SHA-256 `8f68563cd5abec1a1fbf2b1ec2288a96693c7bad12b2ab6ce977451c1ee72010`;
- it holds the gamecode, media, six maps `proxmap1` to `proxmap6` and the optional ENT for `q1_q3dm13`.

The author stated publicly that he would not release the source. x86QW preserves the original binary and does not try to decompile or reimplement it.

## x86QW changes

- `runtime/maps/proxmap1.ent` reproduces exactly the entity lump of the original BSP, removing only four obsolete `"noname" "1"` keys;
- the fix removes four `noname is not a field` messages without changing entities, arena, geometry or gameplay;
- forces external entities to be read only when Pro-X starts, even if
  an old personal configuration left `sv_loadentfiles` turned off;
- starts the mod directory only once with `-game` and publishes `*gamedir`
  separately, without reloading the filesystem during startup;
- separates the client, server and user configurations;
- leaves the console clean at startup and keeps the help on `F10`, on demand only.

## Optional dependencies

- `q1edge.bsp` and `q1_q3dm13.bsp` are conditional external arenas and are not needed for the package's six maps;
- these maps were not added automatically.

## Runtime validation

- 2026-07-30: ezQuake 3.6.9 and nightly `20260616-101233_a86996a` on `proxmap1`;
- 1.1 gamecode loaded, the player joined and the four `noname is not a field` messages were gone;
- no missing required file, blocked command or mod error.

## Fixes sent to the client

- the false QVM error before the PR1 fallback was sent to ezQuake in
  [PR #1149](https://github.com/QW-Group/ezquake-source/pull/1149);
- the duplicate registration noise after `vid_restart` was sent separately
  in [PR #1150](https://github.com/QW-Group/ezquake-source/pull/1150).

## Deliberately unchanged

- no code, rule, map or visual content was created.
