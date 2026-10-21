package main

import (
	"io"
	"path/filepath"
	"time"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/services"
)

func runStatus(args []string, stdout, stderr io.Writer) int {
	asJSON := false
	var rest []string
	for _, argument := range args {
		if argument == "--json" {
			asJSON = true
			continue
		}
		rest = append(rest, argument)
	}
	options, err := services.ParseStatus(rest)
	if err != nil {
		return serviceUsage("status", err, stdout, stderr)
	}
	if options.Target == "" {
		options.Target = install.DefaultTarget()
	}
	target, _ := filepath.Abs(expandHome(options.Target))
	target = lock.Resolve(target)
	if asJSON {
		if options.Stop {
			return usageFailure("status", cliargs.Usagef("--json só lê; não combina com --stop"), stderr)
		}
		if err := output.Success("status", install.ReadStatus(target)).Write(stdout); err != nil {
			return output.ExitFailure
		}
		return output.ExitSuccess
	}
	console := serviceConsole(&options.Common, stdout, stderr)
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
	if proceed, code := confirm("Encerrar a stack ativa?", "x86qw status --stop "+target, options.Yes, stdout, stderr); !proceed {
		return code
	}
	if err := services.RequestStop(target, 15*time.Second); err != nil {
		console.Error(err.Error())
		return output.ExitFailure
	}
	console.Success("Stack de serviços encerrada.")
	return output.ExitSuccess
}

func init() {
	register(command{name: "status", group: "Hospedar", order: 40, synopsis: "[DESTINO]",
		summary: "mostra os serviços ativos; --stop encerra",
		about:   "Mostra os servidores, o QTV e o QWFWD que estão rodando. Com --json, o estado da instalação e das sessões. Com --stop, encerra a stack; pergunta antes e, sem terminal, exige --yes.",
		flags: func() *cliargs.Parser {
			p := services.StatusParser()
			jsonFlag(p, new(bool))
			return p
		}, run: noName(runStatus)})
}
