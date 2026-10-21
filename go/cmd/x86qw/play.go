package main

import (
	"regexp"
	"strconv"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/play"
)

type playRequest struct {
	game, target, mode, mapName, ruleset string
	channel                              string
	options                              play.Options
	planJSON, menu, configureRuleset     bool
	verbose, noColor                     bool
}

// playFlags collects what play's flags set before validation.
type playFlags struct {
	bots, health, pacemaker                        int
	skill, team, weapon, names                     string
	hook, runes, raceStyle, raceScoring            string
	botsGiven, healthGiven, skillGiven, teamGiven  bool
	pacemakerGiven, breakOn, breakOff, fill, based bool
	hide                                           bool
}

var botTeam = regexp.MustCompile(`^[A-Za-z0-9_-]{1,9}$`)

func playParser(r *playRequest, f *playFlags) *cliargs.Parser {
	p := cliargs.New()
	targetFlag(p, &r.target)
	p.String(&r.mode, "MODO", "modo KTX: duel, 2on2, 4on4, ffa, ctf, race...", "--mode")
	p.String(&r.mapName, "MAPA", "mapa", "--map")
	p.Choice(&r.ruleset, play.RulesetKeys, "ruleset do cliente", "--ruleset")
	p.Choice(&r.channel, []string{"stable", "nightly"}, "canal do ezQuake", "--channel")
	p.Value(func(value string) error {
		f.botsGiven = true
		return intFlag(value, 1, 31, &f.bots)
	}, "N", "quantos Frogbots entram (1 a 31)", "--bots")
	p.Bool(&f.fill, "completa as vagas com Frogbots", "--fill-bots")
	p.Exclusive("--bots", "--fill-bots")
	p.Value(func(value string) error {
		f.skillGiven = true
		if strings.EqualFold(value, "random") {
			f.skill = "random"
			return nil
		}
		var skill int
		if err := intFlag(value, 1, 20, &skill); err != nil {
			return err
		}
		f.skill = strconv.Itoa(skill)
		return nil
	}, "1-20|random", "habilidade dos bots", "--bot-skill")
	p.Value(func(value string) error {
		if !botTeam.MatchString(value) {
			return cliargs.Usagef("aceita 1 a 9 letras, números, _ ou -")
		}
		f.team, f.teamGiven = value, true
		return nil
	}, "TIME", "time dos bots", "--bot-team")
	p.Choice(&f.weapon, []string{"random", "1", "2", "3", "4", "5", "6", "7", "8"}, "arma inicial dos bots", "--bot-weapon")
	p.Value(func(value string) error {
		f.healthGiven = true
		return intFlag(value, 1, 300, &f.health)
	}, "N", "vida dos bots (1 a 300)", "--bot-health")
	p.Bool(&f.breakOn, "bots largam a mochila ao morrer", "--bot-break-on-death")
	p.Bool(&f.breakOff, "bots não largam a mochila", "--no-bot-break-on-death")
	p.Exclusive("--bot-break-on-death", "--no-bot-break-on-death")
	p.Choice(&f.names, []string{"default", "x86qw", "one-piece", "personal"}, "nomes dos bots", "--bot-names")
	p.Choice(&f.hook, []string{"smooth", "fast", "classic", "crhook", "off"}, "gancho do CTF", "--ctf-hook")
	p.Choice(&f.runes, []string{"on", "off"}, "runas do CTF", "--ctf-runes")
	p.Bool(&f.based, "CTF nasce na base", "--ctf-based-spawn")
	p.Choice(&f.raceStyle, []string{"solo", "simultaneous", "match"}, "estilo da Race", "--race-style")
	p.Choice(&f.raceScoring, []string{"win", "scaled", "formula1"}, "pontuação da Race", "--race-scoring")
	p.Value(func(value string) error {
		f.pacemakerGiven = true
		return intFlag(value, 1, 10, &f.pacemaker)
	}, "N", "pacemaker da Race (1 a 10)", "--race-pacemaker")
	p.Bool(&f.hide, "esconde os outros corredores", "--race-hide-players")
	p.Bool(&r.menu, "abre a tela de partida do app", "--menu")
	p.Bool(&r.configureRuleset, "escolhe o ruleset na tela de partida", "--configure-ruleset")
	p.Bool(&r.planJSON, "mostra o comando do ezQuake em JSON sem abrir", "--plan-json")
	p.Bool(&r.verbose, "detalhes técnicos", "-v", "--verbose")
	p.Bool(&r.noColor, "sem cores", "--no-color")
	return p
}

func intFlag(value string, low, high int, target *int) error {
	number, err := strconv.Atoi(strings.TrimSpace(value))
	if err != nil || number < low || number > high {
		return cliargs.Usagef("use um número de %d a %d, não %q", low, high, value)
	}
	*target = number
	return nil
}

// parsePlay reads play's arguments; the one positional is a game or, when
// it names none, the installation.
func parsePlay(args []string, games []play.Game) (*playRequest, error) {
	r := &playRequest{}
	f := &playFlags{}
	p := playParser(r, f)
	if err := p.Parse(args); err != nil {
		return nil, err
	}
	positional := p.Positional()
	if len(positional) > 1 {
		return nil, cliargs.Usagef("argumentos a mais: %s", strings.Join(positional[1:], " "))
	}
	if len(positional) == 1 {
		selection := positional[0]
		for _, game := range games {
			if strings.EqualFold(game.ID, selection) {
				r.game = game.ID
			}
		}
		if r.game == "" {
			if r.target != "" {
				return nil, cliargs.Usagef("jogo desconhecido: %s", selection)
			}
			r.target = selection
		}
	}
	o := play.DefaultOptions()
	if f.botsGiven {
		o.Bots = f.bots
	}
	o.FillBots = f.fill
	if f.skillGiven {
		o.BotSkill = f.skill
	}
	if f.teamGiven {
		o.BotTeam = &f.team
	}
	if f.weapon != "" {
		o.BotWeapon = &f.weapon
	}
	if f.healthGiven {
		o.BotHealth = &f.health
	}
	if f.breakOn || f.breakOff {
		value := f.breakOn
		o.BotBreakOnDeath = &value
	}
	if f.names != "" {
		o.BotNamesProfile = f.names
	}
	for _, choice := range []struct {
		value  string
		target **string
	}{{f.hook, &o.CTFHook}, {f.runes, &o.CTFRunes}, {f.raceStyle, &o.RaceStyle}, {f.raceScoring, &o.RaceScoring}} {
		if choice.value != "" {
			copied := choice.value
			*choice.target = &copied
		}
	}
	o.CTFBasedSpawn, o.RaceHidePlayers = f.based, f.hide
	if f.pacemakerGiven {
		o.RacePacemaker = &f.pacemaker
	}
	if r.mode != "" {
		if r.game != "" && r.game != "ktx" {
			return nil, cliargs.Usagef("--mode só pode ser usado com o jogo KTX")
		}
		r.game = "ktx"
	}
	modifiers := f.skillGiven || o.BotTeam != nil || o.BotWeapon != nil || o.BotHealth != nil || o.BotBreakOnDeath != nil || o.BotNamesProfile != "default"
	if modifiers && !(o.Bots > 0 || o.FillBots) {
		return nil, cliargs.Usagef("opções --bot-* exigem --bots ou --fill-bots")
	}
	ktxSpecific := o.Bots > 0 || o.FillBots || modifiers || o.CTFHook != nil || o.CTFRunes != nil || o.CTFBasedSpawn ||
		o.RaceStyle != nil || o.RaceScoring != nil || o.RacePacemaker != nil || o.RaceHidePlayers
	if ktxSpecific {
		if r.game != "" && r.game != "ktx" {
			return nil, cliargs.Usagef("opções de bots, CTF e Race só podem ser usadas com o jogo KTX")
		}
		r.game = "ktx"
	}
	r.options = o
	return r, nil
}
