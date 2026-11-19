package setup

// The inventory and the KTX mode catalog speak English; portuguese gives
// the Portuguese interface its components, its games, its modes and the
// words of the update plan, and english the few words the data does not
// have. Both are keyed by kind and ID ("component/ktx", "mode/duel.d"), so
// the data files stay as they are. What is missing shows as the data has it.
var english = map[string]string{
	"game/td2.d": "Weapons, spells, runes and powers.",
}

var portuguese = map[string]string{
	"game/ktx.d":                           "QuakeWorld competitivo com o QVM KTX 1.47 derivado e reproduzível do x86QW.",
	"game/final-arena.d":                   "Duelos individuais em fila: o vencedor permanece na arena.",
	"game/pro-x.d":                         "Rounds e equipes com ready, break e votação.",
	"game/team-fortress.d":                 "Team Fortress clássico para QuakeWorld.",
	"game/td2.d":                           "Armas, magias, runas e poderes.",
	"plan/Installation state":              "Estado da instalação",
	"plan/Redundant nQuake sounds":         "Sons redundantes nQuake",
	"plan/format 2":                        "formato 2",
	"plan/full screen":                     "tela inteira",
	"plan/merged into KTX":                 "incorporado ao KTX",
	"plan/intact upstream bundle":          "bundle upstream integral",
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
	"mode/duel.d":                          "Duelo competitivo entre dois jogadores.",
	"mode/2on2.d":                          "Teamplay competitivo com duas duplas.",
	"mode/4on4.d":                          "Teamplay clássico com duas equipes de quatro.",
	"mode/3on3.d":                          "Teamplay competitivo com duas equipes de três.",
	"mode/10on10.d":                        "Teamplay de grande escala para vinte jogadores.",
	"mode/ffa.d":                           "Todos contra todos com respawn contínuo.",
	"mode/clan-arena.d":                    "Rounds eliminatórios sem coleta de itens.",
	"mode/wipeout.d":                       "Rounds em equipe com respawn coletivo progressivo.",
	"mode/tot.d":                           "Combate de sobrevivência com progressão e bots Frogbot.",
	"mode/blitz-2v2.d":                     "Rounds rápidos de 2on2 com equipamento imediato.",
	"mode/blitz-4v4.d":                     "Rounds rápidos de 4on4 com equipamento imediato.",
	"mode/2on2on2.d":                       "Confronto entre três equipes de dois jogadores.",
	"mode/3on3on3.d":                       "Confronto entre três equipes de três jogadores.",
	"mode/4on4on4.d":                       "Confronto entre três equipes de quatro jogadores.",
	"mode/xonx.d":                          "Teamplay flexível dimensionado pelo número de jogadores.",
	"mode/hoony.d":                         "Duelo em rounds com alternância de spawns.",
	"mode/ctf.d":                           "Duas equipes defendem sua bandeira e capturam a adversária.",
	"mode/midair.d":                        "Combate aéreo com rockets e pontuação por altura.",
	"mode/dmm4.d":                          "Combate com todas as armas e munição ilimitada.",
	"mode/instagib.d":                      "Eliminação instantânea com shaft em DMM4.",
	"mode/lgc.d":                           "Duelo de shaft com regras LGC sobre a base DMM4.",
	"mode/rocket-arena.d":                  "Duelo de rockets sem coleta de itens.",
	"mode/race.d":                          "Percursos cronometrados usando rotas oficiais do KTX.",
	"mode/practice.d":                      "Servidor destravado para praticar itens, armas e movimento.",
}

// Localize is a data text in lang, when its words are known, else the text
// as the data has it. kind is component, game, mode or plan; id is
// the ID, with ".d" for a description.
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
