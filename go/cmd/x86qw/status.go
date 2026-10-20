package main

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/services"
)

func runStatus(args []string, stdout, stderr io.Writer) int {
	for _, argument := range args {
		if argument == "--json" {
			return runStatusJSON(args, stdout, stderr)
		}
	}
	options, err := services.ParseStatus(args)
	if err != nil {
		return serviceUsage("status", err, stderr)
	}
	console := serviceConsole(&options.Common, stdout, stderr)
	target, _ := filepath.Abs(options.Target)
	target = lock.Resolve(target)
	active, err := services.ShowStatus(console, target)
	if err != nil {
		console.Error(err.Error())
		return output.ExitFailure
	}
	if !options.Stop {
		return output.ExitSuccess
	}
	if !active {
		console.Error("Nenhuma stack de serviços x86QW está ativa.")
		return output.ExitFailure
	}
	if !options.Yes && !confirmStop(options.Menu, options.NoColor, stdout) {
		console.Info("Encerramento cancelado; a stack continua ativa.")
		return output.ExitSuccess
	}
	if err := services.RequestStop(target, 15*time.Second); err != nil {
		console.Error(err.Error())
		return output.ExitFailure
	}
	console.Success("Stack de serviços encerrada coordenadamente.")
	return output.ExitSuccess
}

// confirmStop asks on a terminal; without one it declines (use --yes).
func confirmStop(fromMenu, noColor bool, stdout io.Writer) bool {
	if !isTerminal(os.Stdin) {
		return false
	}
	fmt.Fprint(stdout, "Encerrar a stack ativa? [s/N] ")
	answer, _ := bufio.NewReader(os.Stdin).ReadString('\n')
	switch strings.ToLower(strings.TrimSpace(answer)) {
	case "s", "sim", "y", "yes":
		return true
	}
	return false
}

func runStatusJSON(args []string, stdout, stderr io.Writer) int {
	target, _, ok := targetFlags("status", args, stderr)
	if !ok {
		return output.ExitUsage
	}
	if err := output.Success("status", install.ReadStatus(target)).Write(stdout); err != nil {
		return output.ExitFailure
	}
	return output.ExitSuccess
}

func init() {
	register("status", 20, "status               mostra os serviços ativos (--json: instalação e sessões; --stop encerra a stack)", noName(runStatus))
}
