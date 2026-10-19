package main

import (
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
)

func runChanges(args []string, stdout, stderr io.Writer) int {
	parsed, ok := parseArgs("changes", args, nil, []string{"--sync-gitignore"}, nil, stderr)
	if !ok {
		return output.ExitUsage
	}
	target, err := maint.ValidateTarget(parsed.target, true)
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	if parsed.bools["--sync-gitignore"] {
		if err := maint.SyncGitignore(target); err != nil {
			fmt.Fprintf(stderr, "✗ Não foi possível atualizar o filtro Git da instalação: %s\n", target+"/.gitignore")
			return output.ExitFailure
		}
		fmt.Fprintf(stdout, "· Filtro Git seletivo atualizado: %s/.gitignore\n", target)
	}
	changes, err := maint.InspectChanges(target)
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	fmt.Fprintln(stdout, "\n· Mudanças locais da instalação")
	for _, change := range changes {
		component := ""
		if change.Component != "" {
			component = "  [" + change.Component + "]"
		}
		fmt.Fprintf(stdout, "%s  %s%s\n", change.Status, change.Path, component)
	}
	if len(changes) > 0 {
		fmt.Fprintf(stdout, "· %d diferença(s): A novo, M alterado, D removido.\n", len(changes))
	} else {
		fmt.Fprintln(stdout, "✓ Nenhuma diferença local em relação à instalação-base.")
	}
	return output.ExitSuccess
}

func init() {
	register("changes", 140, "changes [--sync-gitignore] compara mudanças locais com a instalação", noName(runChanges))
}
