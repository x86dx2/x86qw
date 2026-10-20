package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/menu"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/play"
)

const exitInterrupted = 130

func runPlay(args []string, stdout, stderr io.Writer) int {
	code, _ := playMain(args, stdout, stderr, false)
	return code
}

// playMain mirrors gameplay.main. With propagateExit (the main menu), a
// MenuExit is returned instead of being reported.
func playMain(args []string, stdout, stderr io.Writer, propagateExit bool) (int, error) {
	games, err := play.LoadGames()
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure, nil
	}
	request, err := parsePlay(args, games)
	if err != nil {
		fmt.Fprintf(stderr, "x86qw play: erro: %v\n", err)
		return output.ExitUsage, nil
	}
	if request.target == "" {
		request.target = defaultTarget()
	}
	con := newConsole(stdout, stderr, request.noColor, request.verbose)
	if request.planJSON {
		con.out = io.Discard
	} else {
		fmt.Fprintf(stdout, "\n%s · launcher QuakeWorld\nDestino: %s\n", con.paint("x86-qw", paletteAccent+"m\033[1"), filepath.Clean(request.target))
	}
	target, _ := filepath.Abs(request.target)
	stop := trapInterrupt(stderr, "Operação cancelada. O jogo não foi iniciado.")
	choice, err := choosePlay(con, target, request, games)
	stop()
	switch {
	case err == nil:
	case errors.Is(err, errBack):
		return output.ExitSuccess, nil
	case errors.Is(err, menu.ErrInterrupt):
		con.failure("Operação cancelada. O jogo não foi iniciado.")
		return exitInterrupted, nil
	case errors.Is(err, menu.ErrExit):
		if propagateExit {
			return output.ExitSuccess, err
		}
		con.info("Menu encerrado; o jogo não foi iniciado.")
		return output.ExitSuccess, nil
	case errors.Is(err, menu.ErrCancelled):
		con.info("Operação cancelada; o jogo não foi iniciado.")
		return exitInterrupted, nil
	default:
		return con.failed(err), nil
	}
	if choice == nil {
		return output.ExitSuccess, nil
	}
	launch, err := play.Build(choice.game, choice.mode, choice.mapName, choice.assets, choice.options, choice.ruleset)
	if err != nil {
		return con.failed(err), nil
	}
	if request.planJSON {
		document := map[string]any{"argv": launch.Arguments, "startup_commands": launch.StartupCommands, "bot_name_settings": launch.BotNameSettings}
		encoder := json.NewEncoder(stdout)
		encoder.SetEscapeHTML(false)
		encoder.Encode(document)
		return output.ExitSuccess, nil
	}
	selection := choice.game.Label
	if choice.mode != nil {
		selection += " · " + choice.mode.Label
	}
	con.info("Abrindo " + selection + " no mapa " + choice.mapName + "...")
	code := launchPlay(target, choice.runtime.Path, launch, stdout, stderr)
	if code == output.ExitSuccess {
		con.success(choice.runtime.Label + " aberto com " + selection + ".")
	}
	return code, nil
}

// choosePlay runs every precondition and picker up to the launch; a nil
// choice with a nil error means --configure-ruleset finished.
func choosePlay(con console, target string, request *playRequest, games []play.Game) (*playChoice, error) {
	if info, err := os.Lstat(target); err != nil || !info.IsDir() {
		return nil, fmt.Errorf("diretório de destino não existe: %s", target)
	}
	ui := interactiveUI(request.noColor)
	if request.configureRuleset {
		saved, err := readRuleset(target)
		if err != nil {
			return nil, err
		}
		selected, back, err := chooseRuleset(ui, saved)
		if err != nil {
			return nil, err
		}
		if back {
			con.info("Experiência inalterada; nenhuma preferência foi gravada.")
			return nil, nil
		}
		if err := saveRuleset(target, selected); err != nil {
			return nil, err
		}
		con.success("Experiência padrão alterada para " + play.RulesetLabels[selected] + ".")
		return nil, nil
	}
	con.section("Jogo local")
	installGames, err := install.LoadGames()
	if err != nil {
		return nil, err
	}
	flow := &playFlow{ui: ui, con: con, target: target, games: games, available: install.AvailableGames(target, installGames)}
	ktxOrUnset := request.game == "" || request.game == "ktx"
	configure := request.menu || (request.mode == "" && ktxOrUnset)
	chooseRuleset := request.ruleset == "" && (request.menu || ktxOrUnset)
	return flow.run(request, configure, chooseRuleset)
}

func launchPlay(target, executable string, launch *play.Launch, stdout, stderr io.Writer) int {
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
