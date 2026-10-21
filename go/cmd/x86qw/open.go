package main

import (
	"errors"
	"fmt"
	"io"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/play"
)

type openArgs struct {
	target string
	dryRun bool
}

func (a *openArgs) parser() *cliargs.Parser {
	p := cliargs.New()
	targetFlag(p, &a.target)
	p.Bool(&a.dryRun, "mostra o comando do ezQuake sem abrir", "--dry-run")
	return p
}

func runOpen(args []string, stdout, stderr io.Writer) int {
	var a openArgs
	p := a.parser()
	if code, ok := parse("open", p, args, stdout, stderr); !ok {
		return code
	}
	if len(p.Positional()) != 1 {
		return usageFailure("open", cliargs.Usagef("informe um endereço qw:// ou uma demo"), stderr)
	}
	item := p.Positional()[0]
	root, err := targetOf(cliargs.New(), a.target)
	if err != nil {
		return usageFailure("open", err, stderr)
	}
	extra, err := play.OpenArguments(root, item)
	if cliargs.IsUsage(err) {
		return usageFailure("open", err, stderr)
	}
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	ruleset, err := play.ClientRuleset(root)
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	arguments := append(play.RulesetArguments(ruleset), extra...)
	if a.dryRun {
		fmt.Fprintln(stdout, strings.Join(append([]string{"-nohome", "-basedir", root}, arguments...), " "))
		return output.ExitSuccess
	}
	runtimes, err := install.HostRuntimes(root)
	if err == nil && len(runtimes) == 0 {
		err = errors.New("nenhum ezQuake instalado; execute x86qw setup")
	}
	if err == nil {
		err = play.Start(root, runtimes[0].Path, runtimes[0].BinarySHA, &play.Launch{Arguments: arguments})
	}
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	return output.ExitSuccess
}

func init() {
	register(command{name: "open", group: "Jogar", order: 20, synopsis: "ENDEREÇO|DEMO",
		summary: "abre um servidor qw:// ou uma demo .mvd/.qwd no ezQuake",
		flags:   (&openArgs{}).parser, run: noName(runOpen)})
}
