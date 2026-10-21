package setup

// The inventory, the games and the KTX modes describe themselves in
// Portuguese; english gives the English interface their words, keyed by
// kind and ID ("component/ktx", "mode/duel.d"), so the data files stay as
// they are. What is missing here shows in Portuguese.
var english = map[string]string{
	"component/x86qw-core-id1":             "Quake data",
	"component/cli":                        "x86QW CLI",
	"component/x86qw-client-bootstrap":     "x86QW client base and startup",
	"component/x86qw-client-bootstrap.d":   "Builds on the nQuake base with the x86QW client's startup, defaults and global layers.",
	"component/nquake-visual-core":         "nQuake interface and visuals",
	"component/nquake-visual-core.d":       "ezQuake's main packages and the nQuake look.",
	"component/ktx":                        "KTX x86QW",
	"component/ktx.d":                      "KTX 1.47 run by ezQuake's built-in local server, with assisted fun by default and optional competitive rules.",
	"component/nquake-player-skins":        "Player skins",
	"component/nquake-player-skins.d":      "GPL and community skins for players and teams.",
	"component/nquake-crosshairs":          "Crosshairs",
	"component/nquake-crosshairs.d":        "The crosshair collection nQuake ships.",
	"component/nquake-skyboxes":            "Skyboxes",
	"component/nquake-skyboxes.d":          "High resolution skies and their notes.",
	"component/nquake-models":              "Models",
	"component/nquake-models.d":            "Updated models packaged by nQuake.",
	"component/nquake-scoreboard-flags":    "Scoreboard flags",
	"component/nquake-scoreboard-flags.d":  "Country flags for the scoreboard.",
	"component/nquake-external-textures":   "External textures",
	"component/nquake-external-textures.d": "Single textures kept by nQuake.",
	"component/nquake-base-textures":       "Base textures",
	"component/nquake-base-textures.d":     "The base texture pack nQuake selects.",
	"component/nquake-maps":                "nQuake map selection",
	"component/nquake-maps.d":              "Only the maps, ENT files and texts of the nQuake distribution.",
	"component/nquake-matchinfo":           "Match information",
	"component/nquake-matchinfo.d":         "Historical and training demos kept by nQuake; optional, not needed to play.",
	"component/nquake-documentation":       "Documentation and licenses",
	"component/nquake-documentation.d":     "Upstream licenses and the current x86QW documentation.",
	"component/qrp-hires":                  "QRP high resolution",
	"component/qrp-hires.d":                "High resolution textures and brush models from the Quake Retexturing Project.",
	"component/final-arena":                "Final Arena",
	"component/final-arena.d":              "Final Arena 1.20: queued one-on-one duels, with its own x86QW profile.",
	"component/pro-x":                      "Pro-X",
	"component/pro-x.d":                    "Pro-X QW 1.1: rounds, teams and votes, with its own x86QW profile.",
	"component/team-fortress":              "Team Fortress",
	"component/team-fortress.d":            "Team Fortress 2.9 with nQuake assets and maps fitted by x86QW.",
	"component/total-destruction-2":        "Total Destruction 2",
	"component/total-destruction-2.d":      "TD2QW 2.22: weapons, spells, runes, models, sounds and game code.",
	"component/mvdsv":                      "Dedicated MVDSV",
	"component/mvdsv.d":                    "The MVDSV 1.11 QuakeWorld server for the declared game codes, MVD and QTV.",
	"component/qwfwd":                      "QWFWD proxy",
	"component/qwfwd.d":                    "The QWFWD 1.30 QuakeWorld proxy, local by default, open on request.",
	"component/qtv":                        "QTV broadcast",
	"component/qtv.d":                      "QTV pinned by commit, for local streams, a remote upstream and MVD demos.",
	"game/ktx.d":                           "Competitive QuakeWorld with the KTX 1.47 QVM, built reproducibly by x86QW.",
	"game/final-arena.d":                   "Queued one-on-one duels: the winner stays in the arena.",
	"game/pro-x.d":                         "Rounds and teams with ready, break and votes.",
	"game/team-fortress.d":                 "Classic Team Fortress for QuakeWorld.",
	"game/td2.d":                           "Weapons, spells, runes and powers.",
	"mode/duel.d":                          "Competitive duel between two players.",
	"mode/2on2.d":                          "Competitive teamplay between two pairs.",
	"mode/4on4.d":                          "Classic teamplay between two teams of four.",
	"mode/3on3.d":                          "Competitive teamplay between two teams of three.",
	"mode/10on10.d":                        "Large scale teamplay for twenty players.",
	"mode/ffa.d":                           "Everyone against everyone, with continuous respawn.",
	"mode/clan-arena.d":                    "Elimination rounds without item pickups.",
	"mode/wipeout.d":                       "Team rounds with a growing team respawn.",
	"mode/tot.d":                           "Survival combat with progression and Frogbots.",
	"mode/blitz-2v2.d":                     "Quick 2on2 rounds with instant equipment.",
	"mode/blitz-4v4.d":                     "Quick 4on4 rounds with instant equipment.",
	"mode/2on2on2.d":                       "Three teams of two players.",
	"mode/3on3on3.d":                       "Three teams of three players.",
	"mode/4on4on4.d":                       "Three teams of four players.",
	"mode/xonx.d":                          "Flexible teamplay sized by the number of players.",
	"mode/hoony.d":                         "Duel in rounds with alternating spawns.",
	"mode/ctf.d":                           "Two teams defend their flag and capture the other.",
	"mode/midair.d":                        "Air combat with rockets, scored by height.",
	"mode/dmm4.d":                          "Every weapon and unlimited ammunition.",
	"mode/instagib.d":                      "Instant kills with the shaft on DMM4.",
	"mode/lgc.d":                           "Lightning gun duel under LGC rules on DMM4.",
	"mode/rocket-arena.d":                  "Rocket duel without item pickups.",
	"mode/race.d":                          "Timed runs on the official KTX routes.",
	"mode/practice.d":                      "An unlocked server to practise items, weapons and movement.",
	"group/recommended":                    "Recommended",
	"group/individual":                     "Individual competition",
	"group/teams":                          "Teams",
	"group/arena":                          "Arena and alternatives",
	"group/training":                       "Training and movement",
}

// Localize is the English of a data text when lang is English and one is
// known, else the text as the data has it. kind is component, game, mode or
// group; id is the ID, with ".d" for a description.
func Localize(lang, kind, id, text string) string {
	if lang == LangEN {
		if english, ok := english[kind+"/"+id]; ok && english != "" {
			return english
		}
	}
	return text
}
