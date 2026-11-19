# KTX 1.47 in x86QW

## Layers

1. nQuake provides the historical selection and the unique resources that are still useful.
2. KTX 1.47 replaces the shared files with official configurations, bots, LOCs and resources.
3. x86QW applies the integration, a local profile and a minimal, reproducible QVM extension for Frogbot identities.

## x86QW changes

- adds the optional Quad Coach `k_x86qw_quadcoach`, turned on only by the
  local x86QW Ruleset, with global warnings at 15 and 5 seconds and at respawn;
- recompiles the 1.47 QVM with isolated patches that cache Frogbot identities
  and spread automatic additions over the usermode's number of teams;
- fixes the upstream Frogbot name table and uses the bot's global index in the
  ally and enemy lists, avoiding repeated identities in the modes with
  three teams;
- removes the experimental shirt and pants cvars from the x86QW QVM; names stay
  customizable, but colors, skins and team colors follow KTX alone;
- pins the build metadata to the date of the 1.47 tag, removing the variation of
  `__DATE__` and `__TIME__` between rebuilds of the same QVM;
  when it gets no profile, the draw and the original names stay intact;
- sets the local server to `sv_progtype 2`;
- leaves `k_defmap` under the launcher's control, avoiding the automatic switch to
  `dm3`, the second QVM load and the `SV_PreSpawn_f from different level` warning;
- leaves `k_defmode` under the launcher's control and starts directly in the
  selected usermode, without applying the 4on4 default first;
- declares in `catalog/modes.json` the 17 native KTX 1.47 usermodes and seven official
  variations: Midair, DMM4, Instagib, LGC, Rocket Arena, Race and Practice;
- turns the seven variations on through static single-entry profiles, only
  after KTX confirms the local connection; removing the alias at once avoids
  toggling or reloading in a loop;
- exposes the Frogbot bots in the launcher by count or by filling, skill
  1-20 and team; weapon, health and stop on death are limited to ToT;
  enables the subsystem before the map and only offers maps that have an
  official packaged route or a regular personal route;
- keeps KTX's native naming: `3on3` forms two teams of three and
  `2on2on2` forms three teams of two; the aliases `3v3` and `2v2v2` are still
  accepted by the CLI; modes with
  a fixed roster offer exactly the remaining slots and spread the bots to
  complete each team;
- offers Frogbot identities in three profiles: the KTX default without customization, the
  x86QW One Piece catalog shuffled per launch and a personal list the installer
  preserves; keeps the `/` prefix and leaves shirt/pants under KTX's control;
- filters Race by the 54 official routes or personal routes and exposes solo, simultaneous or
  match racing, the three scoring systems, pacemaker and hiding racers;
- enables CTF with the six official ENTs of the KTX rotation (`e2m2`, `e1m5`,
  `e1m3`, `e2m5`, `e1m4` and `e3m3`), each validated with one flag per
  team, and sets up loading the ENTs before opening the map;
- declares Frogbot, CTF and Race as map resource policies in the catalog; the
  menu matches these policies against the BSPs actually installed before
  showing a choice;
- uses `dm4` as the Essential fallback for Midair, DMM4, Instagib and LGC, and limits
  ToT to the three maps that have a specific configuration upstream;
- exposes in CTF the five hook styles, hook off, runes, team
  swapping and base spawns; Race and CTF refuse bots, as the QVM requires;
- when KTX starts, applies to the KTX profile alone the competitive nQuake key
  map, including quick weapons, team messages, timers
  and match commands;
- reserves `F5`, `F6`, `F11` and, depending on the mode, `H`, `I`, `M`, `X` and `Z` for
  contextual actions; `INS`, `DEL`, `HOME` and `END` manage Frogbot sessions;
  loads the personal file at the end of the profile and reapplies after it only the
  universal `F12` bind to quit, preserving every other control;
- keeps the examples of the personal file independent of internal aliases;
- keeps the current number of bots and the skill of the next bots in session
  aliases: the mode's capacity cannot be exceeded, `DEL` reopens the slot and
  `HOME`/`END` change the skill cumulatively;
- replaces nQuake's generic message with contextual help made only of
  keys that are actually bound;
- shows on `F10`, in colored multi-line blocks, every control of the
  nQuake key map KTX uses, opening the console so that the complete reference
  stays visible;
- records the preset chosen in the launcher and offers `ktx_mode` to confirm it
  again in the console and at the end of the `F10` help;
- prints the mode's key plan automatically as soon as the map opens;
- keeps the official commands in the catalog as validation data, but shows the
  player only the keys that run them; `F10` combines the common map with
  the active mode's plan;
- builds the contextual help from short aliases, removing 24 duplicate CFGs;
- standardizes the key and command column at 26 characters and widens the console
  to 80% of the screen when the help is requested;
- applies the KTX profile on the entry event matching the usermode, after
  the QVM completes the local connection, making the binds and `F10` deterministic;
- forces the first local connection as a player; this avoids the spectator path
  that is incompatible between the current QVM and ezQuake 3.6.9's integrated server;
- applies the KTX timing and jump values to KTX alone and restores the nQuake
  default when another mod opens, preventing the leak through the personal `config.cfg`;
- preserves the deterministic package order.
- fully preserves the skins and relational colors set by KTX and by the
  personal configuration, including in sessions that contain Frogbots;
- does not send individual colors from the name profiles, keeping shirt, pants and
  team colors under KTX's native rules;
- forces `k_fb_admin_only 1` only on hosts with bots bound to an
  external interface, after the personal configuration;
- organizes the layer the project maintains into `catalog/`, `config/`, `runtime/`,
  `source/` and `policy/`, without changing the installed paths.

## Known pending items

- `1.48-dev` is development on master, not a promoted release.

## Runtime validation

- 2026-07-30: ezQuake 3.6.9 and nightly `20260616-101233_a86996a` on `dm6`;
- the 1.47 QVM loaded once, the chosen map was kept and the player joined;
- no `SV_PreSpawn_f from different level`, QVM error or unknown command.
- 2026-07-30: Duel started directly in `1on1`; Midair was turned on once
  on `povdmm4`; Race loaded the `dm6` route without a toggling loop; Practice
  confirmed the unlocked server. Every process ended without orphans.
- 2026-07-30: two crashes reproduced on ezQuake 3.6.9 were identified
  as `SIGSEGV` in `SV_SpawnSpectator`, on receiving a null `classname` from the
  QVM runtime. The explicit local connection with `spectator 0` stayed up in the same
  Duel scenario that used to end after about eight seconds.
- 2026-07-31: CTF started on ezQuake 3.6.9 in a `1280x720` window, loaded
  `e2m2`, ran `configs/usermodes/ctf/default.cfg` and entered `standby`
  with one player and 16 slots. The test used `cfg_save_onquit 0` and kept the
  hash of the personal configuration.
- 2026-08-01: the 24 modes were opened on ezQuake 3.6.9 in a
  `1280x720` window; the 22 Frogbot-compatible modes received the chosen bot
  during the first entry. CTF and Race stayed without bots, as the
  QVM contract requires. Practice no longer waits for a second entry that never comes.

## Fix sent to the client

- the duplicate registration noise after `vid_restart` does not belong to KTX; the
  generic fix was sent to ezQuake in
  [PR #1150](https://github.com/QW-Group/ezquake-source/pull/1150).

## Deliberately unchanged

- no mode, vote, weapon, rule, bot or map was created in the gamecode; the
  extensions only read declarative identities, draw the skill when
  asked and fix automatic balancing in the native usermodes with
  two or three teams; colors and skins stay entirely under KTX;
- the launcher only exposes usermodes and commands that already exist in KTX 1.47.
