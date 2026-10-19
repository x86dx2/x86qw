package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/play"
)

// usageError is an argparse-style error (exit 2).
type usageError struct{ msg string }

func (e *usageError) Error() string { return e.msg }

type playRequest struct {
	game, target, mode, mapName, ruleset string
	options                              play.Options
	planJSON                             bool
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
	request := &playRequest{target: values["--target"], mode: values["--mode"], mapName: values["--map"], ruleset: values["--ruleset"], planJSON: flags["--plan-json"]}
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

func runPlay(args []string, stdout, stderr io.Writer) int {
	games, err := play.LoadGames()
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	request, err := parsePlay(args, games)
	if err != nil {
		fmt.Fprintf(stderr, "x86qw play: erro: %v\n", err)
		return output.ExitUsage
	}
	if request.target == "" {
		request.target = defaultTarget()
	}
	target, _ := filepath.Abs(request.target)
	launch, err := planPlay(target, request, games)
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	if request.planJSON {
		document := map[string]any{"argv": launch.Arguments, "startup_commands": launch.StartupCommands, "bot_name_settings": launch.BotNameSettings}
		encoder := json.NewEncoder(stdout)
		encoder.SetEscapeHTML(false)
		encoder.Encode(document)
		return output.ExitSuccess
	}
	return launchPlay(target, request, launch, stdout, stderr)
}

// planPlay runs every precondition and builds the launch for a
// non-interactive play request.
func planPlay(target string, request *playRequest, games []play.Game) (*play.Launch, error) {
	if info, err := os.Lstat(target); err != nil || !info.IsDir() {
		return nil, fmt.Errorf("diretório de destino não existe: %s", target)
	}
	if request.mapName == "" {
		return nil, errors.New("esta versão exige --map (e --mode para KTX); use a CLI Python para o menu interativo")
	}
	ruleset := request.ruleset
	if ruleset == "" {
		saved, err := readRuleset(target)
		if err != nil {
			return nil, err
		}
		ruleset = saved
	}
	if ruleset == "" {
		ruleset = "x86qw"
	}
	installGames, err := install.LoadGames()
	if err != nil {
		return nil, err
	}
	available := install.AvailableGames(target, installGames)
	if len(available) == 0 {
		return nil, errors.New("nenhum jogo local está instalado")
	}
	if request.game == "" {
		if len(available) != 1 {
			return nil, errors.New("informe o jogo (ktx, final-arena, pro-x, team-fortress ou td2)")
		}
		request.game = available[0].ID
	}
	var game *play.Game
	for index := range games {
		if games[index].ID == request.game {
			game = &games[index]
		}
	}
	installed := false
	for _, item := range available {
		installed = installed || item.ID == request.game
	}
	if game == nil || !installed {
		return nil, fmt.Errorf("%s não está instalado nesta instalação", request.game)
	}
	if err := install.VerifyForPlay(target, game.Component, available); err != nil {
		return nil, err
	}
	var mode *play.Mode
	var assets map[string]bool
	options := request.options
	label := game.Label
	var requirements []play.Requirement
	if game.UsesModeCatalog() {
		if request.mode == "" {
			return nil, errors.New("esta versão exige --mode para KTX; use a CLI Python para o menu interativo")
		}
		modes, err := play.LoadModes()
		if err != nil {
			return nil, err
		}
		found, err := play.FindMode(modes, request.mode)
		if err != nil {
			return nil, err
		}
		mode = &found
		label = "KTX · " + found.Label
		if options, err = play.ResolveNames(target, *game, options, mode); err != nil {
			return nil, err
		}
		if assets, err = play.KtxAssets(target); err != nil {
			return nil, err
		}
		requirements = play.ActiveRequirements(found, options)
	}
	mapName, err := play.ResolveMap(target, *game, request.mapName, requirements, assets, label)
	if err != nil {
		return nil, err
	}
	return play.Build(*game, mode, mapName, assets, options, ruleset)
}

func launchPlay(target string, request *playRequest, launch *play.Launch, stdout, stderr io.Writer) int {
	executable, err := install.ClientRuntime(target)
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	arguments := launch.Arguments
	var config *play.RuntimeConfig
	if len(launch.StartupCommands) > 0 {
		payload, err := play.Payload(launch.BotNameSettings, launch.StartupCommands)
		if err == nil {
			config, err = play.Create(target, payload)
		}
		if err != nil {
			fmt.Fprintf(stderr, "✗ %v\n", err)
			return output.ExitFailure
		}
		replaced := make([]string, len(arguments))
		for index, argument := range arguments {
			replaced[index] = strings.ReplaceAll(argument, play.RuntimeConfigPlaceholder, config.Name)
		}
		arguments = replaced
	}
	argv := append([]string{"-nohome", "-basedir", target}, arguments...)
	process, err := play.Spawn(executable, argv, target)
	if err != nil {
		if config != nil {
			config.Release()
		}
		fmt.Fprintf(stderr, "✗ não foi possível abrir o ezQuake: %v\n", err)
		return output.ExitFailure
	}
	if config != nil {
		if err := config.Transfer(process.Pid); err != nil {
			process.Kill()
			config.Release()
			fmt.Fprintf(stderr, "✗ %v\n", err)
			return output.ExitFailure
		}
		if exited, code := play.ExitsWithin(process, 15*time.Second); exited {
			config.Release()
			fmt.Fprintf(stderr, "✗ O ezQuake encerrou antes de carregar a configuração KTX (código %d).\n", code)
			return output.ExitFailure
		}
	}
	fmt.Fprintf(stdout, "✓ Partida aberta no mapa %s.\n", launch.Arguments[indexOf(launch.Arguments, "+map")+1])
	return output.ExitSuccess
}

func indexOf(values []string, wanted string) int {
	for index, value := range values {
		if value == wanted {
			return index
		}
	}
	return -1
}
