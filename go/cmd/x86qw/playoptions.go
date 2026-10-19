package main

import (
	"fmt"
	"strconv"

	"github.com/x86dx2/x86qw/go/internal/menu"
	"github.com/x86dx2/x86qw/go/internal/play"
)

func option(key, label, description string) menu.Option {
	return menu.Option{Key: key, Label: label, Description: description}
}

func stringPointer(value string) *string { return &value }

// chooseOptions mirrors choose_ktx_launch_options for activity "Jogar":
// only the options that matter for the selected KTX mode.
func (f *playFlow) chooseOptions(mode play.Mode, selected play.Options) (play.Options, bool, error) {
	ui := f.ui
	crumb := "x86QW › Jogar › KTX › " + mode.Label
	switch mode.Key {
	case "race":
		for {
			style, back, err := ui.SelectOne("Formato da corrida", []menu.Option{
				option("solo", "Solo", "um corredor por vez"),
				option("simultaneous", "Largada simultânea", "todos percorrem a rota juntos"),
				option("match", "Match competitivo", "rodadas com sistema de pontuação"),
			}, menu.Settings{Breadcrumb: crumb, AllowBack: true})
			if err != nil || back {
				return selected, back, err
			}
			var scoring *string
			if style == "match" {
				chosen, back, err := ui.SelectOne("Sistema de pontuação", []menu.Option{
					option("win", "Vitória simples", "vence quem ganhar a rodada"),
					option("scaled", "Escalonada", "pontua conforme a colocação"),
					option("formula1", "Fórmula 1", "pontuação no estilo automobilismo"),
				}, menu.Settings{Breadcrumb: crumb + " › Match", AllowBack: true})
				if err != nil {
					return selected, false, err
				}
				if back {
					continue
				}
				scoring = stringPointer(chosen)
			}
			for {
				pacemakers := []menu.Option{option("none", "Nenhum", "correr sem referência")}
				for rank := 1; rank <= 10; rank++ {
					pacemakers = append(pacemakers, option(strconv.Itoa(rank), fmt.Sprintf("Posição %d", rank), "usar esse tempo do ranking"))
				}
				pacemaker, back, err := ui.SelectOne("Pacemaker", pacemakers, menu.Settings{Breadcrumb: crumb, Searchable: true, AllowBack: true})
				if err != nil {
					return selected, false, err
				}
				if back {
					break
				}
				hide, back, err := ui.Confirm("Ocultar os demais corredores?", menu.Settings{Breadcrumb: crumb, AllowBack: true}, "", false)
				if err != nil {
					return selected, false, err
				}
				if back {
					continue
				}
				result := selected
				result.RaceStyle, result.RaceScoring, result.RacePacemaker, result.RaceHidePlayers = stringPointer(style), scoring, nil, hide
				if pacemaker != "none" {
					rank, _ := strconv.Atoi(pacemaker)
					result.RacePacemaker = &rank
				}
				return result, false, nil
			}
		}
	case "ctf":
		for {
			hook, back, err := ui.SelectOne("Estilo do gancho", []menu.Option{
				option("smooth", "Suave", "movimento progressivo"),
				option("fast", "Rápido", "resposta imediata"),
				option("classic", "Clássico", "comportamento tradicional"),
				option("crhook", "CRHook", "variação competitiva"),
				option("off", "Desativado", "partida sem gancho"),
			}, menu.Settings{Breadcrumb: crumb, AllowBack: true})
			if err != nil || back {
				return selected, back, err
			}
			for {
				runes, back, err := ui.SelectOne("Runas", []menu.Option{
					option("on", "Ativadas", "regras completas de CTF"),
					option("off", "Desativadas", "CTF sem powerups de runa"),
				}, menu.Settings{Breadcrumb: crumb, AllowBack: true})
				if err != nil {
					return selected, false, err
				}
				if back {
					break
				}
				based, back, err := ui.Confirm("Usar spawn baseado na base?", menu.Settings{Breadcrumb: crumb, AllowBack: true}, "", false)
				if err != nil {
					return selected, false, err
				}
				if back {
					continue
				}
				result := selected
				result.CTFHook, result.CTFRunes, result.CTFBasedSpawn = stringPointer(hook), stringPointer(runes), based
				return result, false, nil
			}
		}
	}
	if !mode.Bots {
		return selected, false, nil
	}
	return f.chooseFrogbots(mode, selected, crumb)
}

func (f *playFlow) chooseFrogbots(mode play.Mode, selected play.Options, crumb string) (play.Options, bool, error) {
	ui := f.ui
	limit, fixed := play.BotLimit(mode)
	var botOptions []menu.Option
	if !fixed {
		botOptions = []menu.Option{
			option("none", "Sem bots", "iniciar somente com jogadores humanos"),
			option("1", "1 bot", "adicionar um oponente"),
			option("2", "2 bots", "partida pequena"),
			option("4", "4 bots", "partida intermediária"),
			option("fill", "Preencher servidor", "até oito Frogbots neste modo aberto"),
			option("custom", "Quantidade personalizada", "escolher de 1 a 31"),
		}
	} else {
		label := fmt.Sprintf("%d bots", limit)
		if limit == 1 {
			label = "1 bot"
		}
		botOptions = []menu.Option{
			option("none", "Sem bots", "iniciar somente com jogadores humanos"),
			option(strconv.Itoa(limit), label, play.RosterDescription(mode)),
		}
	}
	maximum := 31
	if fixed && limit > 0 {
		maximum = limit
	}
	quantity := func() (string, bool, error) {
		options := make([]menu.Option, maximum)
		for number := 1; number <= maximum; number++ {
			options[number-1] = option(strconv.Itoa(number), strconv.Itoa(number), "")
		}
		return ui.SelectOne("Quantidade de Frogbots", options, menu.Settings{Breadcrumb: crumb + " › Frogbots", Searchable: true, AllowBack: true})
	}
	frogbots := crumb + " › Frogbots"
	for {
		choice, back, err := ui.SelectOne("Adicionar Frogbots?", botOptions, menu.Settings{Breadcrumb: crumb, AllowBack: true})
		if err != nil || back {
			return selected, back, err
		}
		if choice == "none" {
			return play.WithoutFrogbots(selected), false, nil
		}
		custom := choice == "custom"
		if custom {
			if choice, back, err = quantity(); err != nil {
				return selected, false, err
			}
			if back {
				continue
			}
		}
	skill:
		for {
			skills := []menu.Option{option("random", "Aleatória", "cada bot recebe habilidade de 1 a 20")}
			for level := 1; level <= 20; level++ {
				tier := "avançada"
				if level <= 4 {
					tier = "iniciante"
				} else if level <= 10 {
					tier = "intermediária"
				}
				skills = append(skills, option(strconv.Itoa(level), strconv.Itoa(level), tier))
			}
			skill, back, err := ui.SelectOne("Habilidade dos Frogbots", skills, menu.Settings{Breadcrumb: frogbots, Default: 5, Searchable: true, AllowBack: true})
			if err != nil {
				return selected, false, err
			}
			if back {
				if custom {
					if choice, back, err = quantity(); err != nil {
						return selected, false, err
					}
					if back {
						break skill
					}
					continue
				}
				break
			}
			names, back, err := ui.SelectOne("Nomes dos Frogbots", []menu.Option{
				option("default", "KTX Default", "nomes originais sem customização"),
				option("x86qw", "x86QW aleatório", "mapas e modos da distribuição, embaralhados"),
				option("one-piece", "One Piece (opcional)", "Chapéus de Palha, lendas e grandes potências"),
				option("personal", "Lista pessoal", "usar qw/x86qw-frogbot-names.json na ordem declarada"),
			}, menu.Settings{Breadcrumb: frogbots, AllowBack: true})
			if err != nil {
				return selected, false, err
			}
			if back {
				continue
			}
			configured := selected
			configured.FillBots = choice == "fill"
			configured.Bots = 0
			if !configured.FillBots {
				configured.Bots, _ = strconv.Atoi(choice)
			}
			configured.BotSkill, configured.BotNamesProfile = skill, names
			if mode.Key != "tot" {
				return configured, false, nil
			}
			tot := frogbots + " › ToT"
			weapons := []menu.Option{
				option("default", "Padrão do mapa", "usar a configuração ToT carregada"),
				option("random", "Aleatória", "sortear entre as armas"),
			}
			for number := 1; number <= 8; number++ {
				weapons = append(weapons, option(strconv.Itoa(number), fmt.Sprintf("Arma %d", number), ""))
			}
			weapon, back, err := ui.SelectOne("Arma dos Frogbots no ToT", weapons, menu.Settings{Breadcrumb: tot, AllowBack: true})
			if err != nil {
				return selected, false, err
			}
			if back {
				continue
			}
			healths := []menu.Option{option("default", "Padrão do mapa", "usar a configuração ToT carregada")}
			for number := 1; number <= 300; number++ {
				healths = append(healths, option(strconv.Itoa(number), strconv.Itoa(number), "HP"))
			}
			health, back, err := ui.SelectOne("Vida inicial dos Frogbots no ToT", healths, menu.Settings{Breadcrumb: tot, Searchable: true, AllowBack: true})
			if err != nil {
				return selected, false, err
			}
			if back {
				continue
			}
			death, back, err := ui.SelectOne("Encerrar tentativa quando o jogador morrer?", []menu.Option{
				option("default", "Padrão do mapa", "preservar dm4, e1m2 ou schloss"),
				option("on", "Sim", "encerrar a tentativa"),
				option("off", "Não", "continuar a tentativa"),
			}, menu.Settings{Breadcrumb: tot, AllowBack: true})
			if err != nil {
				return selected, false, err
			}
			if back {
				continue
			}
			configured.BotWeapon, configured.BotHealth, configured.BotBreakOnDeath = nil, nil, nil
			if weapon != "default" {
				configured.BotWeapon = stringPointer(weapon)
			}
			if health != "default" {
				value, _ := strconv.Atoi(health)
				configured.BotHealth = &value
			}
			if death != "default" {
				value := death == "on"
				configured.BotBreakOnDeath = &value
			}
			return configured, false, nil
		}
	}
}
