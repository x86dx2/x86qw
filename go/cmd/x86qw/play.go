package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/play"
)

// usageError is an argparse-style error (exit 2).
type usageError struct{ msg string }

func (e *usageError) Error() string { return e.msg }

type playRequest struct {
	game, target, mode, mapName, ruleset string
	options                              play.Options
	planJSON, menu, configureRuleset     bool
	verbose, noColor                     bool
}

var (
	valueFlags = map[string]bool{
		"--target": true, "--ruleset": true, "--mode": true, "--map": true, "--bots": true, "--bot-skill": true,
		"--bot-team": true, "--bot-weapon": true, "--bot-health": true, "--bot-names": true, "--ctf-hook": true,
		"--ctf-runes": true, "--race-style": true, "--race-scoring": true, "--race-pacemaker": true,
	}
	boolFlags = map[string]bool{
		"-v": true, "--verbose": true, "--no-color": true, "--menu": true, "--configure-ruleset": true,
		"--fill-bots": true, "--bot-break-on-death": true, "--no-bot-break-on-death": true,
		"--ctf-based-spawn": true, "--race-hide-players": true, "--plan-json": true,
	}
	choices = map[string][]string{
		"--ruleset":    {"x86qw", "qcon", "smackdown", "thunderdome", "mtfl", "smackdrive"},
		"--bot-weapon": {"random", "1", "2", "3", "4", "5", "6", "7", "8"}, "--bot-names": {"default", "x86qw", "one-piece", "personal"},
		"--ctf-hook": {"smooth", "fast", "classic", "crhook", "off"}, "--ctf-runes": {"on", "off"},
		"--race-style": {"solo", "simultaneous", "match"}, "--race-scoring": {"win", "scaled", "formula1"},
	}
	botTeam = regexp.MustCompile(`^[A-Za-z0-9_-]{1,9}$`)
)

func intRange(flag, value string, low, high int) (int, error) {
	number, err := strconv.Atoi(strings.TrimSpace(value))
	if err != nil || number < low || number > high {
		return 0, &usageError{fmt.Sprintf("argumento %s: valor inválido: %q", flag, value)}
	}
	return number, nil
}

func parsePlay(args []string, games []play.Game) (*playRequest, error) {
	values := map[string]string{}
	flags := map[string]bool{}
	var positional []string
	for index := 0; index < len(args); index++ {
		arg := args[index]
		name, inline, hasInline := strings.Cut(arg, "=")
		switch {
		case valueFlags[name]:
			value := inline
			if !hasInline {
				if index+1 >= len(args) {
					return nil, &usageError{"argumento " + name + ": esperado um valor"}
				}
				index++
				value = args[index]
			}
			if allowed, ok := choices[name]; ok {
				found := false
				for _, choice := range allowed {
					found = found || choice == value
				}
				if !found {
					return nil, &usageError{fmt.Sprintf("argumento %s: escolha inválida: %q", name, value)}
				}
			}
			values[name] = value
		case boolFlags[arg]:
			flags[arg] = true
		case strings.HasPrefix(arg, "-"):
			return nil, &usageError{"argumentos não reconhecidos: " + arg}
		default:
			positional = append(positional, arg)
		}
	}
	if len(positional) > 1 {
		return nil, &usageError{"argumentos não reconhecidos: " + strings.Join(positional[1:], " ")}
	}
	request := &playRequest{target: values["--target"], mode: values["--mode"], mapName: values["--map"], ruleset: values["--ruleset"], planJSON: flags["--plan-json"],
		menu: flags["--menu"], configureRuleset: flags["--configure-ruleset"], verbose: flags["-v"] || flags["--verbose"], noColor: flags["--no-color"]}
	if len(positional) == 1 {
		selection := positional[0]
		for _, game := range games {
			if strings.EqualFold(game.ID, selection) {
				request.game = game.ID
			}
		}
		if request.game == "" {
			if request.target != "" {
				return nil, &usageError{"jogo desconhecido: " + selection}
			}
			request.target = selection
		}
	}
	o := play.DefaultOptions()
	var err error
	if value, ok := values["--bots"]; ok {
		if o.Bots, err = intRange("--bots", value, 1, 31); err != nil {
			return nil, err
		}
	}
	o.FillBots = flags["--fill-bots"]
	if o.Bots > 0 && o.FillBots {
		return nil, &usageError{"argumento --fill-bots: não permitido com o argumento --bots"}
	}
	skillGiven := false
	if value, ok := values["--bot-skill"]; ok {
		skillGiven = true
		if strings.EqualFold(value, "random") {
			o.BotSkill = "random"
		} else {
			skill, err := intRange("--bot-skill", value, 1, 20)
			if err != nil {
				return nil, err
			}
			o.BotSkill = strconv.Itoa(skill)
		}
	}
	if value, ok := values["--bot-team"]; ok {
		o.BotTeam = &value
	}
	if value, ok := values["--bot-weapon"]; ok {
		o.BotWeapon = &value
	}
	if value, ok := values["--bot-health"]; ok {
		health, err := intRange("--bot-health", value, 1, 300)
		if err != nil {
			return nil, err
		}
		o.BotHealth = &health
	}
	if flags["--bot-break-on-death"] && flags["--no-bot-break-on-death"] {
		return nil, &usageError{"argumento --no-bot-break-on-death: não permitido com o argumento --bot-break-on-death"}
	}
	if flags["--bot-break-on-death"] || flags["--no-bot-break-on-death"] {
		value := flags["--bot-break-on-death"]
		o.BotBreakOnDeath = &value
	}
	if value, ok := values["--bot-names"]; ok {
		o.BotNamesProfile = value
	}
	for flag, target := range map[string]**string{"--ctf-hook": &o.CTFHook, "--ctf-runes": &o.CTFRunes, "--race-style": &o.RaceStyle, "--race-scoring": &o.RaceScoring} {
		if value, ok := values[flag]; ok {
			copied := value
			*target = &copied
		}
	}
	o.CTFBasedSpawn = flags["--ctf-based-spawn"]
	o.RaceHidePlayers = flags["--race-hide-players"]
	if value, ok := values["--race-pacemaker"]; ok {
		pacemaker, err := intRange("--race-pacemaker", value, 1, 10)
		if err != nil {
			return nil, err
		}
		o.RacePacemaker = &pacemaker
	}
	// resolve_ktx_launch_options
	if request.mode != "" {
		if request.game != "" && request.game != "ktx" {
			return nil, &usageError{"--mode só pode ser usado com o jogo KTX"}
		}
		request.game = "ktx"
	}
	modifiers := skillGiven || o.BotTeam != nil || o.BotWeapon != nil || o.BotHealth != nil || o.BotBreakOnDeath != nil || o.BotNamesProfile != "default"
	if modifiers && !(o.Bots > 0 || o.FillBots) {
		return nil, &usageError{"opções --bot-* exigem --bots ou --fill-bots"}
	}
	if o.BotTeam != nil && !botTeam.MatchString(*o.BotTeam) {
		return nil, &usageError{"--bot-team aceita 1 a 9 letras, números, _ ou -"}
	}
	ktxSpecific := o.Bots > 0 || o.FillBots || modifiers || o.CTFHook != nil || o.CTFRunes != nil || o.CTFBasedSpawn ||
		o.RaceStyle != nil || o.RaceScoring != nil || o.RacePacemaker != nil || o.RaceHidePlayers
	if ktxSpecific {
		if request.game != "" && request.game != "ktx" {
			return nil, &usageError{"opções de bots, CTF e Race só podem ser usadas com o jogo KTX"}
		}
		request.game = "ktx"
	}
	request.options = o
	return request, nil
}

// readRuleset loads .x86qw/launcher/client-ruleset.json when present.
func readRuleset(target string) (string, error) {
	path := filepath.Join(target, ".x86qw", "launcher", "client-ruleset.json")
	payload, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return "", nil
	}
	if err != nil || len(payload) > 4096 {
		return "", fmt.Errorf("Preferência de ruleset inválida: %s", path)
	}
	var document map[string]any
	if json.Unmarshal(payload, &document) != nil || len(document) != 3 || document["project"] != "x86qw" {
		return "", fmt.Errorf("Preferência de ruleset inválida: %s", path)
	}
	format, _ := document["format"].(float64)
	ruleset, _ := document["ruleset"].(string)
	if format != 1 || play.Rulesets[ruleset] == "" {
		return "", fmt.Errorf("Preferência de ruleset inválida: %s", path)
	}
	return ruleset, nil
}
