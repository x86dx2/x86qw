package setup

// The inventory speaks English; portuguese gives the Portuguese interface
// its components. The games and the KTX modes come from signed content in
// Portuguese; english gives the English interface their words. Both are
// keyed by kind and ID ("component/ktx", "mode/duel.d"), so the data files
// stay as they are. What is missing shows as the data has it.
var english = map[string]string{
	"game/ktx.d":           "Competitive QuakeWorld with the KTX 1.47 QVM, built reproducibly by x86QW.",
	"game/final-arena.d":   "Queued one-on-one duels: the winner stays in the arena.",
	"game/pro-x.d":         "Rounds and teams with ready, break and votes.",
	"game/team-fortress.d": "Classic Team Fortress for QuakeWorld.",
	"game/td2.d":           "Weapons, spells, runes and powers.",
	"mode/duel.d":          "Competitive duel between two players.",
	"mode/2on2.d":          "Competitive teamplay between two pairs.",
	"mode/4on4.d":          "Classic teamplay between two teams of four.",
	"mode/3on3.d":          "Competitive teamplay between two teams of three.",
	"mode/10on10.d":        "Large scale teamplay for twenty players.",
	"mode/ffa.d":           "Everyone against everyone, with continuous respawn.",
	"mode/clan-arena.d":    "Elimination rounds without item pickups.",
	"mode/wipeout.d":       "Team rounds with a growing team respawn.",
	"mode/tot.d":           "Survival combat with progression and Frogbots.",
	"mode/blitz-2v2.d":     "Quick 2on2 rounds with instant equipment.",
	"mode/blitz-4v4.d":     "Quick 4on4 rounds with instant equipment.",
	"mode/2on2on2.d":       "Three teams of two players.",
	"mode/3on3on3.d":       "Three teams of three players.",
	"mode/4on4on4.d":       "Three teams of four players.",
	"mode/xonx.d":          "Flexible teamplay sized by the number of players.",
	"mode/hoony.d":         "Duel in rounds with alternating spawns.",
	"mode/ctf.d":           "Two teams defend their flag and capture the other.",
	"mode/midair.d":        "Air combat with rockets, scored by height.",
	"mode/dmm4.d":          "Every weapon and unlimited ammunition.",
	"mode/instagib.d":      "Instant kills with the shaft on DMM4.",
	"mode/lgc.d":           "Lightning gun duel under LGC rules on DMM4.",
	"mode/rocket-arena.d":  "Rocket duel without item pickups.",
	"mode/race.d":          "Timed runs on the official KTX routes.",
	"mode/practice.d":      "An unlocked server to practise items, weapons and movement.",
	"group/recommended":    "Recommended",
	"group/individual":     "Individual competition",
	"group/teams":          "Teams",
	"group/arena":          "Arena and alternatives",
	"group/training":       "Training and movement",
}

var portuguese = map[string]string{
	"component/x86qw-core-id1":             "Dados do Quake",
	"component/cli":                        "CLI x86QW",
	"component/x86qw-client-bootstrap":     "Base e inicialização do cliente x86QW",
	"component/x86qw-client-bootstrap.d":   "Compõe a base nQuake com a inicialização, os defaults e as camadas globais do cliente x86QW.",
	"component/nquake-visual-core":         "Interface e recursos visuais nQuake",
	"component/nquake-visual-core.d":       "Pacotes principais do ezQuake e da identidade visual nQuake.",
	"component/ktx.d":                      "KTX 1.47 executado pelo servidor local integrado do ezQuake, com diversão assistida por padrão e regras competitivas opcionais.",
	"component/nquake-player-skins":        "Skins de jogadores",
	"component/nquake-player-skins.d":      "Skins GPL e comunitárias para jogadores e equipes.",
	"component/nquake-crosshairs":          "Miras",
	"component/nquake-crosshairs.d":        "Coleção de miras incluída pelo nQuake.",
	"component/nquake-skyboxes.d":          "Céus de alta resolução e documentação correspondente.",
	"component/nquake-models":              "Modelos",
	"component/nquake-models.d":            "Modelos atualizados empacotados pelo nQuake.",
	"component/nquake-scoreboard-flags":    "Bandeiras do placar",
	"component/nquake-scoreboard-flags.d":  "Bandeiras de países usadas no placar.",
	"component/nquake-external-textures":   "Texturas externas",
	"component/nquake-external-textures.d": "Texturas individuais mantidas pelo nQuake.",
	"component/nquake-base-textures":       "Texturas base",
	"component/nquake-base-textures.d":     "Pacote de texturas base selecionado pelo nQuake.",
	"component/nquake-maps":                "Mapas selecionados nQuake",
	"component/nquake-maps.d":              "Somente os mapas, ENT e textos que fazem parte da distribuição nQuake.",
	"component/nquake-matchinfo":           "Informações de partidas",
	"component/nquake-matchinfo.d":         "Demos históricas e de treinamento mantidas pelo nQuake; conteúdo opcional, não necessário para jogar.",
	"component/nquake-documentation":       "Documentação e licenças",
	"component/nquake-documentation.d":     "Licenças upstream e documentação atual da distribuição x86QW.",
	"component/qrp-hires":                  "QRP alta resolução",
	"component/qrp-hires.d":                "Texturas e brush models em alta resolução do Quake Retexturing Project.",
	"component/final-arena.d":              "Final Arena 1.20, com duelos individuais em fila e perfil próprio x86QW.",
	"component/pro-x.d":                    "Pro-X QW 1.1, com rounds, equipes, votações e perfil próprio x86QW.",
	"component/team-fortress.d":            "Team Fortress 2.9 com assets e mapas nQuake harmonizados pelo x86QW.",
	"component/total-destruction-2.d":      "TD2QW 2.22 com armas, magias, runas, modelos, sons e gamecode; fontes e manuais permanecem preservados no dist.",
	"component/mvdsv":                      "MVDSV dedicado",
	"component/mvdsv.d":                    "Servidor QuakeWorld MVDSV 1.11 para os gamecodes declarados, MVD e transmissão QTV.",
	"component/qwfwd.d":                    "Proxy QuakeWorld QWFWD 1.30, local por padrão e exposição externa opcional.",
	"component/qtv.d":                      "QTV HTTP fixado por commit para streams locais ou upstream remoto e demos MVD.",
}

// Localize is a data text in lang, when its words are known, else the text
// as the data has it. kind is component, game, mode or group; id is the ID,
// with ".d" for a description.
func Localize(lang, kind, id, text string) string {
	words := english
	if lang == LangPT {
		words = portuguese
	}
	if word, ok := words[kind+"/"+id]; ok && word != "" {
		return word
	}
	return text
}
