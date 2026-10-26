package main

import (
	"os"
	"regexp"
	"strconv"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/catalog"
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
	p.String(&r.mode, "MODE", "KTX mode: duel, 2on2, 4on4, ffa, ctf, race...", "--mode")
	p.String(&r.mapName, "MAP", "map", "--map")
	p.Choice(&r.ruleset, play.RulesetKeys, "client ruleset", "--ruleset")
	p.Choice(&r.channel, catalog.Channels, "ezQuake channel", "--channel")
	p.Value(func(value string) error {
		f.botsGiven = true
		return intFlag(value, 1, 31, &f.bots)
	}, "N", "how many Frogbots join (1 to 31)", "--bots")
	p.Bool(&f.fill, "fills the free slots with Frogbots", "--fill-bots")
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
	}, "1-20|random", "bot skill", "--bot-skill")
	p.Value(func(value string) error {
		if !botTeam.MatchString(value) {
			return cliargs.Usagef("takes 1 to 9 letters, digits, _ or -")
		}
		f.team, f.teamGiven = value, true
		return nil
	}, "TEAM", "bot team", "--bot-team")
	p.Choice(&f.weapon, []string{"random", "1", "2", "3", "4", "5", "6", "7", "8"}, "bots' starting weapon", "--bot-weapon")
	p.Value(func(value string) error {
		f.healthGiven = true
		return intFlag(value, 1, 300, &f.health)
	}, "N", "bot health (1 to 300)", "--bot-health")
	p.Bool(&f.breakOn, "bots drop their backpack when they die", "--bot-break-on-death")
	p.Bool(&f.breakOff, "bots keep their backpack", "--no-bot-break-on-death")
	p.Exclusive("--bot-break-on-death", "--no-bot-break-on-death")
	p.Choice(&f.names, []string{"default", "x86qw", "one-piece", "personal"}, "bot names", "--bot-names")
	p.Choice(&f.hook, []string{"smooth", "fast", "classic", "crhook", "off"}, "CTF hook", "--ctf-hook")
	p.Choice(&f.runes, []string{"on", "off"}, "CTF runes", "--ctf-runes")
	p.Bool(&f.based, "CTF players spawn at their base", "--ctf-based-spawn")
	p.Choice(&f.raceStyle, []string{"solo", "simultaneous", "match"}, "Race style", "--race-style")
	p.Choice(&f.raceScoring, []string{"win", "scaled", "formula1"}, "Race scoring", "--race-scoring")
	p.Value(func(value string) error {
		f.pacemakerGiven = true
		return intFlag(value, 1, 10, &f.pacemaker)
	}, "N", "Race pacemaker (1 to 10)", "--race-pacemaker")
	p.Bool(&f.hide, "hides the other racers", "--race-hide-players")
	p.Bool(&r.menu, "opens the app's match screen", "--menu")
	p.Bool(&r.configureRuleset, "picks the ruleset on the match screen", "--configure-ruleset")
	p.Bool(&r.planJSON, "shows the ezQuake command as JSON without starting it", "--plan-json")
	p.Bool(&r.verbose, "technical details", "-v", "--verbose")
	p.Bool(&r.noColor, "no colors", "--no-color")
	return p
}

func intFlag(value string, low, high int, target *int) error {
	number, err := strconv.Atoi(strings.TrimSpace(value))
	if err != nil || number < low || number > high {
		return cliargs.Usagef("use a number from %d to %d, not %q", low, high, value)
	}
	*target = number
	return nil
}

// parsePlay reads play's arguments; the one positional is a game, a KTX
// mode or, when it names neither, the installation.
func parsePlay(args []string, games []play.Game) (*playRequest, error) {
	r := &playRequest{}
	f := &playFlags{}
	p := playParser(r, f)
	if err := p.Parse(args); err != nil {
		return nil, err
	}
	positional := p.Positional()
	if len(positional) > 1 {
		return nil, cliargs.Usagef("unexpected arguments: %s", strings.Join(positional[1:], " "))
	}
	if len(positional) == 1 {
		selection := positional[0]
		for _, game := range games {
			if strings.EqualFold(game.ID, selection) {
				r.game = game.ID
			}
		}
		modes, _ := play.LoadModes()
		_, notFolder := os.Stat(selection)
		_, modeErr := play.FindMode(modes, selection)
		switch {
		case r.game != "":
		case r.mode == "" && notFolder != nil && modeErr == nil:
			r.mode = selection // `x86qw play duel`
		case r.target != "":
			return nil, cliargs.Usagef("unknown game: %s", selection)
		default:
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
			return nil, cliargs.Usagef("--mode only works with the KTX game")
		}
		r.game = "ktx"
	}
	modifiers := f.skillGiven || o.BotTeam != nil || o.BotWeapon != nil || o.BotHealth != nil || o.BotBreakOnDeath != nil || o.BotNamesProfile != "default"
	if modifiers && !(o.Bots > 0 || o.FillBots) {
		return nil, cliargs.Usagef("--bot-* options need --bots or --fill-bots")
	}
	ktxSpecific := o.Bots > 0 || o.FillBots || modifiers || o.CTFHook != nil || o.CTFRunes != nil || o.CTFBasedSpawn ||
		o.RaceStyle != nil || o.RaceScoring != nil || o.RacePacemaker != nil || o.RaceHidePlayers
	if ktxSpecific {
		if r.game != "" && r.game != "ktx" {
			return nil, cliargs.Usagef("bot, CTF and Race options only work with the KTX game")
		}
		r.game = "ktx"
	}
	r.options = o
	return r, nil
}
