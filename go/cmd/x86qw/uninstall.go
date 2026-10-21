package main

import (
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
)

type uninstallArgs struct {
	target     string
	purge, yes bool
}

func (a *uninstallArgs) parser() *cliargs.Parser {
	p := cliargs.New()
	targetFlag(p, &a.target)
	p.Bool(&a.purge, "apaga a pasta inteira e o cache, dados pessoais inclusive", "--purge")
	p.Bool(&a.yes, "não pergunta", "--yes")
	return p
}

func runUninstall(args []string, stdout, stderr io.Writer) int {
	var a uninstallArgs
	p := a.parser()
	if code, ok := parse("uninstall", p, args, stdout, stderr); !ok {
		return code
	}
	target, err := targetOf(p, a.target)
	if err != nil {
		return usageFailure("uninstall", err, stderr)
	}
	if target, err = maint.ValidateTarget(target, !a.purge); err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	question, retry := "Remover o x86QW de "+target+"? Configurações, PAKs e demos ficam.", "x86qw uninstall "+target
	if a.purge {
		question, retry = "Apagar "+target+" inteira, com configurações e demos, e o cache do x86QW?", "x86qw uninstall --purge "+target
	}
	if proceed, code := confirm(question, retry, a.yes, stdout, stderr); !proceed {
		return code
	}
	var report *maint.Report
	if a.purge {
		fmt.Fprintln(stdout, "\nDesinstalação completa")
		report, err = maint.Purge(target)
	} else {
		fmt.Fprintln(stdout, "\nDesinstalação")
		report, err = maint.Uninstall(target)
	}
	if report != nil {
		for _, warning := range report.Warnings {
			fmt.Fprintf(stderr, "! %s\n", warning)
		}
		for _, line := range report.Lines {
			fmt.Fprintln(stdout, line)
		}
	}
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	return output.ExitSuccess
}

func init() {
	register(command{name: "uninstall", group: "Instalar e manter", order: 70, synopsis: "[DESTINO]",
		summary: "remove a instalação (com --purge, tudo)",
		about:   "Remove o que o x86QW instalou e mantém configurações, PAKs, demos e arquivos que você editou. Com --purge, apaga a pasta inteira e o cache. Pergunta antes; sem terminal, exige --yes.",
		flags:   (&uninstallArgs{}).parser, run: noName(runUninstall)})
}
