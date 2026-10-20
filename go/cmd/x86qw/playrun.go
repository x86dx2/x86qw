package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/launchguard"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/play"
)

const exitInterrupted = 130

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
	ktxOrUnset := request.game == "" || request.game == "ktx"
	if request.menu || request.configureRuleset || (request.mode == "" && ktxOrUnset) {
		// Something is left to choose: the match screen chooses it.
		if !interactive() {
			fmt.Fprintln(stderr, "x86qw play: sem terminal, informe o modo (--mode) ou o jogo")
			return output.ExitUsage
		}
		return runApp(realBackend("", install.HostPlatform()), appTarget(request.target), "match", stdout, stderr)
	}
	con := newConsole(stdout, stderr, request.noColor, request.verbose)
	if request.planJSON {
		con.out = io.Discard
	} else {
		fmt.Fprintf(stdout, "\n%s · launcher QuakeWorld\nDestino: %s\n", con.paint("x86-qw", paletteAccent+"m\033[1"), filepath.Clean(request.target))
	}
	target, _ := filepath.Abs(request.target)
	con.section("Jogo local")
	resolved, err := play.Resolve(target, play.Request{Game: request.game, Mode: request.mode, Map: request.mapName,
		Ruleset: request.ruleset, Channel: request.channel, Options: request.options})
	if err != nil {
		return con.failed(err)
	}
	con.success(fmt.Sprintf("Componente %s íntegro (%s; seleção %s).", resolved.Component, fileCount(resolved.Files), resolved.Selection))
	choice := &playChoice{game: resolved.Game, mode: resolved.Mode, mapName: resolved.Map, options: resolved.Options,
		assets: resolved.Assets, runtime: resolved.Runtime, ruleset: resolved.Ruleset}
	launch, err := play.Build(choice.game, choice.mode, choice.mapName, choice.assets, choice.options, choice.ruleset)
	if err != nil {
		return con.failed(err)
	}
	if request.planJSON {
		document := map[string]any{"argv": launch.Arguments, "startup_commands": launch.StartupCommands, "bot_name_settings": launch.BotNameSettings}
		encoder := json.NewEncoder(stdout)
		encoder.SetEscapeHTML(false)
		encoder.Encode(document)
		return output.ExitSuccess
	}
	selection := choice.game.Label
	if choice.mode != nil {
		selection += " · " + choice.mode.Label
	}
	con.info("Abrindo " + selection + " no mapa " + choice.mapName + "...")
	code := launchPlay(target, choice.runtime.Path, choice.runtime.BinarySHA, launch, stdout, stderr)
	if code == output.ExitSuccess {
		con.success(choice.runtime.Label + " aberto com " + selection + ".")
	}
	return code
}

func launchPlay(target, executable, binarySHA string, launch *play.Launch, stdout, stderr io.Writer) int {
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
	release, err := launchguard.Hold(executable, binarySHA)
	var process *os.Process
	if err == nil {
		process, err = play.Spawn(executable, argv, target)
		release()
	}
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

func init() {
	register("play", 70, "play                 abre uma partida local (menus para jogo, modo, mapa e regras)", noName(runPlay))
}
