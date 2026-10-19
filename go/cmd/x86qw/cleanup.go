package main

import (
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
)

func runCleanup(args []string, stdout, stderr io.Writer) int {
	parsed, ok := parseArgs("cleanup", args, nil, []string{"--downloads", "--personal-data"}, nil, stderr)
	if !ok {
		return output.ExitUsage
	}
	fail := func(err error) int {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	target, err := maint.ValidateTarget(parsed.target, true)
	if err != nil {
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
	downloads, personal := parsed.bools["--downloads"], parsed.bools["--personal-data"]
	result, err := maint.Cleanup(target, downloads, personal)
	if err != nil {
		return fail(err)
	}
	fmt.Fprintln(stdout, "\n· Limpeza segura")
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
	} else if !personal {
		fmt.Fprintln(stdout, "· Histórico, logs e demos válidas foram preservados; use --personal-data para removê-los.")
	}
	if !downloads {
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
	register("cleanup", 150, "cleanup [--downloads] [--personal-data] limpa caches e dados locais", noName(runCleanup))
}
