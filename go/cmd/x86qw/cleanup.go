package main

import (
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
)

type cleanupArgs struct {
	target              string
	downloads, personal bool
}

func (a *cleanupArgs) parser() *cliargs.Parser {
	p := cliargs.New()
	targetFlag(p, &a.target)
	p.Bool(&a.downloads, "também os mapas baixados de servidores", "--downloads")
	p.Bool(&a.personal, "também histórico, logs e demos", "--personal-data")
	return p
}

func runCleanup(args []string, stdout, stderr io.Writer) int {
	var a cleanupArgs
	p := a.parser()
	if code, ok := parse("cleanup", p, args, stdout, stderr); !ok {
		return code
	}
	target, err := targetOf(p, a.target)
	if err != nil {
		return usageFailure("cleanup", err, stderr)
	}
	fail := func(err error) int {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	if target, err = maint.ValidateTarget(target, true); err != nil {
		return fail(err)
	}
	if err := maint.RequireManagedIdentity(target, "cleanup"); err != nil {
		return fail(err)
	}
	held, err := lock.Acquire(target, "cleanup")
	if err != nil {
		return fail(err)
	}
	defer held.Release()
	fmt.Fprintln(stdout, "\nLimpeza segura")
	result, err := maint.Cleanup(target, a.downloads, a.personal)
	if err != nil {
		return fail(err)
	}
	for _, root := range result.Caches {
		fmt.Fprintf(stdout, "✓ Cache removido: %s\n", root)
	}
	if result.Cache > 0 {
		fmt.Fprintf(stdout, "✓ Dados regeneráveis removidos (%s).\n", fileCount(result.Cache))
	} else {
		fmt.Fprintln(stdout, "· Nenhum dado regenerável da instalação precisava ser removido.")
	}
	if result.Personal > 0 {
		fmt.Fprintf(stdout, "✓ Dados pessoais locais removidos (%s).\n", fileCount(result.Personal))
	} else if !a.personal {
		fmt.Fprintln(stdout, "· Histórico, logs e demos foram preservados; use --personal-data para removê-los.")
	}
	if !a.downloads {
		fmt.Fprintln(stdout, "· Downloads de servidores foram preservados; use --downloads para removê-los.")
	}
	return output.ExitSuccess
}

func fileCount(count int) string {
	if count == 1 {
		return "1 arquivo"
	}
	return fmt.Sprintf("%d arquivos", count)
}

func init() {
	register(command{name: "cleanup", group: "Seus dados", order: 50, synopsis: "[DESTINO]",
		summary: "apaga caches e, se pedir, dados pessoais",
		about:   "Apaga o que o x86QW sabe regenerar: caches e arquivos temporários. Mapas baixados de servidores e seus dados pessoais só saem quando você pede.",
		flags:   (&cleanupArgs{}).parser, run: noName(runCleanup)})
}
