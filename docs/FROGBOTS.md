# Frogbot names

x86QW offers four identity profiles for the KTX Frogbots. The profile is
chosen on the app's Play screen or with `--bot-names`; names are declarative
data and do not change the bots' rules, attributes or intelligence.

| Profile | Behavior |
|---|---|
| `default` — KTX Default | Sets no identity; KTX keeps its original names and colors. |
| `x86qw` — random x86QW | Shuffles the distribution's maps and modes once per launch. |
| `one-piece` — One Piece (optional) | Shuffles One Piece character names once per launch. |
| `personal` | Uses the names of the installation's personal list, in the declared order. |

Examples:

```sh
x86qw play ktx --mode duel --map dm6 --bots 1 --bot-names x86qw
x86qw play ktx --mode duel --map dm6 --bots 1 --bot-names one-piece
x86qw play ktx --mode 2on2 --map dm6 --bots 2 --bot-names personal
x86qw host ktx --mode 2on2 --map dm6 --bots 2 --bot-names x86qw
```

The app starts on `KTX default`, and `random x86QW`, `One Piece` and
`personal list` stay available. In the CLI, `default` is also the default, so
a run that does not give `--bot-names` has no customization.

## x86QW list

The canonical source is:

```text
dist/mods/ktx/1.47/x86qw/catalog/frogbots/names.json
```

The first ten names are classic maps of the distribution: dm6, dm4, dm2, dm3,
aerowalk, e1m2, schloss, povdmm4, death32c and cmt3. The following names
gather other shipped maps and KTX mode labels. The selection is the product's
own vocabulary, not a list of third-party characters.

## One Piece list (optional)

The canonical source is:

```text
dist/mods/ktx/1.47/x86qw/catalog/frogbots/names.one-piece.json
```

The first ten names are the members of the Straw Hat Pirates: Luffy, Zoro,
Nami, Usopp, Sanji, Chopper, Robin, Franky, Brook and Jinbe. That line-up
follows the [official One Piece character cast][straw-hats].

The following names gather figures the work presents as legends, great powers
or holders of relevant titles. The selection is editorial — it is not an
official power ranking. Among the references used are Gol D. Roger, the King
of the Pirates; Edward Newgate, presented as the strongest man in the world;
Shanks and Marshall D. Teach, Emperors; and Dracule Mihawk, the world's
strongest swordsman. See the official pages of [Roger][roger],
[Whitebeard][whitebeard], [Shanks][shanks], [Blackbeard][blackbeard] and
[Mihawk][mihawk].

## Personal list

The bootstrap creates, without overwriting later edits:

```text
quake-world/qw/x86qw-frogbot-names.json
```

Edit only the `characters` list, keeping the rest of the contract:

```json
{
  "format": 1,
  "game": "ktx",
  "profile": "personal",
  "prefix": "/",
  "color": "quake-high-bit",
  "characters": [
    {"name": "Luffy"},
    {"name": "Zoro"}
  ]
}
```

Each name takes 1 to 13 ASCII characters: letters, digits, space, period,
apostrophe and hyphen. The comparison ignores case, so there can be no
duplicates such as `Luffy` and `luffy`. The list must hold at least as many
names as the number of bots requested; `--fill-bots` uses up to eight in open
modes and fills the declared roster in fixed modes. Old lists made only of
strings in `names` are still accepted.
Each item takes only `name`. Appearance fields are rejected so that no option
seems to take effect when, by contract, shirt, pants and team colors belong to
KTX's default rules and settings.

Do not write `/` or color codes in the values. The launcher adds the `/`
prefix automatically and turns each character into Quake's classic colored
variant. The RGB `&cRGB` format is not used because
[ezQuake does not allow it in player names][ezquake-charsets], for
compatibility with old QuakeWorld clients.

The generic, ally and enemy names get the three families of `k_fb_name_*`
cvars, but the same index keeps the same identity in all of them. That way
KTX's relational classification neither swaps nor duplicates the bot's name.
The launcher sends no appearance cvars and the x86QW QVM extension registers
no shirt or pants cvars of its own; KTX keeps its color rules entirely. In the
random profile, the draw happens once before the runtime opens; restarting the
game produces another order. In the personal profile, the JSON order is kept.

The launcher does not change `teamskin`, `enemyskin`, `teamcolor`,
`enemycolor` or the `r_*skincolor` variants, even when the selection includes
Frogbots. Rendering follows KTX's default profile and the player's personal
settings, the same way in local matches and on real servers.

In a local game, the launcher writes those cvars into a private session CFG
(`0600` on Unix), has ezQuake load it before the map and removes the file
right after startup. The file is unique per process, does not change the
personal list and avoids the console's size and alias limits. On a dedicated
server, the same choices go into the ephemeral configuration already protected
by the session journal.

In modes with a fixed number of players, the app only offers to fill the
remaining slots after the human player. Duel adds one bot; 2on2 adds three;
3on3 adds five; 2on2on2 adds five; 4on4 adds seven. The CLI applies the same
validation.
The launcher puts the player on the first declared team and lets the QVM run
its native automatic team assignment. The reproducible patch uses the bot's
global index when choosing ally and enemy names, so identities do not repeat
when the usermode has three teams. An explicit team in the CLI is still
respected both at startup and for bots added later with `INS`.

`3on3` keeps KTX's conventional meaning: two teams of three players.
`2on2on2` is the line-up of three teams of two players. The CLI still accepts
the `3v3` and `2v2v2` aliases. `3on3on3` and `4on4on4` keep three teams of
three and four players. The line-up lives in `catalog/modes.json`, in the
`bot_teams` field, and is used by the app, the local match and the dedicated
server.

Open modes, such as FFA and Practice, keep the fill and custom amount options.
Several bots join spaced out by server frames.
Pick `random` for the skill in the app, or use `--bot-skill random`, to draw
an independent skill from 1 to 20 for each bot added, including by `fill`.

## Maps and resources

The app does not confuse "installed map" with "map usable by the current
settings". The list shown is the intersection of the BSPs present and the
resources the mode declares:

- with Frogbots, the map needs `qw/bots/maps/<map>.bot`;
- CTF needs `id1/maps/ctf/<map>.ent`;
- Race needs `qw/race/routes/<map>.route`;
- without bots, a regular mode still offers maps that have no bot route.

The KTX package holds 77 Frogbot routes and 54 Race files. Those numbers are
not treated as a list of BSPs: the launcher also requires the map to be
installed. CTF offers exactly the six maps for which the component installs
ENTs. ToT offers `dm4`, `e1m2` and `schloss`, the three maps with specific
upstream configuration; `dm4` is the default because it is available in the
Essential profile.

Regular personal routes can be added without rebuilding the component:

```text
quake-world/qw/bots/maps/mymap.bot
quake-world/qw/race/routes/mymap.route
quake-world/id1/maps/ctf/mymap.ent
```

The file name must match the BSP. Symlinks and unsafe names are not taken
into the session catalog. The project's validator also inspects the
directives and structure of the official routes before building a package.

The `--bot-weapon`, `--bot-health` and `--[no-]bot-break-on-death` options
belong only to ThunderWalker ToT. When they are not given, the map's specific
configuration prevails; the app lets you pick the default, on or off
explicitly.

## Session keys

Each KTX mode declares its own key plan. The summary is printed automatically
as soon as the map opens and can be shown again with `F10`:

- `F5`, `F6` and `F11`: the mode's main actions; in Duel they are ready,
  break and rules;
- `H`, `I`, `M`, `X` and `Z`: extra actions only when the mode needs them;
  Race uses queue, times, route and camera, while CTF uses status and dropping
  the flag or rune;
- `INS` and `DEL`: add and remove a Frogbot when the session started with
  bots; `INS` respects the mode's roster and `DEL` frees a slot again;
- `HOME` and `END`: lower or raise cumulatively, between 1 and 20, the skill
  of the next bots. With random skill, the keys only confirm that the
  individual draw is still on;
- `F12`: close QuakeWorld directly.

The managed binds are loaded before `x86qw-ktx-user.cfg`. That way a personal
configuration can override them; the launcher only changes the contextual
aliases and does not rewrite the personal file.

When hosting is bound to an external address, a session with bots forces
`k_fb_admin_only 1` after the personal configuration. That stops remote
players from changing the Frogbot roster; hosts on loopback only keep KTX's
default behavior.

[straw-hats]: https://one-piece.com/character/luffy/index.html
[roger]: https://one-piece.com/character/Gold_Roger/index.html
[whitebeard]: https://one-piece.com/character/edward_newgate/index.html
[shanks]: https://one-piece.com/character/Shanks/index.html
[blackbeard]: https://one-piece.com/character/Marshall_D_Teech/index.html
[mihawk]: https://one-piece.com/character/Dracule_Mihawk/index.html
[ezquake-charsets]: https://ezquake.com/docs/charsets.html
