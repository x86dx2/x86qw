package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"path/filepath"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/play"
)

func runPlay(args []string, stdout, stderr io.Writer) int {
	games, err := play.LoadGames()
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	request, err := parsePlay(args, games)
	if errors.Is(err, cliargs.ErrHelp) {
		fmt.Fprint(stdout, commandHelp("play"))
		return output.ExitSuccess
	}
	if err != nil {
		return usageFailure("play", err, stderr)
	}
	if request.target == "" {
		request.target = install.DefaultTarget()
	}
	target, _ := filepath.Abs(expandHome(request.target))
	ktxOrUnset := request.game == "" || request.game == "ktx"
	if request.menu || request.configureRuleset || (request.mode == "" && ktxOrUnset) {
		// Something is left to choose: the match screen chooses it.
		if !interactive() {
			return usageFailure("play", cliargs.Usagef("without a terminal, give the mode (--mode) or the game"), stderr)
		}
		return runApp(realBackend("", install.HostPlatform()), target, "match", stdout, stderr)
	}
	con := newConsole(stdout, stderr, request.noColor, request.verbose)
	if request.planJSON {
		con.out = io.Discard
	} else {
		fmt.Fprintf(stdout, "\n%s · partida local\nDestino: %s\n", con.paint("x86QW", paletteAccent+"m\033[1"), target)
	}
	resolved, err := play.Resolve(target, play.Request{Game: request.game, Mode: request.mode, Map: request.mapName,
		Ruleset: request.ruleset, Channel: request.channel, Options: request.options})
	if err != nil {
		return con.failed(err)
	}
	con.success(fmt.Sprintf("Component %s intact (%s; selection %s).", resolved.Component, fileCount(resolved.Files), resolved.Selection))
	launch, err := play.Build(resolved.Game, resolved.Mode, resolved.Map, resolved.Assets, resolved.Options, resolved.Ruleset)
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
	selection := resolved.Game.Label
	if resolved.Mode != nil {
		selection += " · " + resolved.Mode.Label
	}
	con.info("Starting " + selection + " on map " + resolved.Map + "...")
	if err := play.Start(target, resolved.Runtime.Path, resolved.Runtime.BinarySHA, launch); err != nil {
		return con.failed(err)
	}
	con.success(resolved.Runtime.Label + " started with " + selection + ".")
	return output.ExitSuccess
}

func init() {
	register(command{name: "play", group: "Play", order: 10, synopsis: "[GAME] [options]",
		summary: "starts a local match; without a mode, the app's match screen",
		about: "Starts a local match in ezQuake. KTX, the default, needs --mode; without it, on a terminal, opens the app's match screen. " +
			"Bots, CTF and Race are KTX options.",
		flags: func() *cliargs.Parser { return playParser(&playRequest{}, &playFlags{}) },
		run:   noName(runPlay)})
}
