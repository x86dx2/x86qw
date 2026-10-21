package main

import (
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
)

type changesArgs struct {
	target        string
	syncGitignore bool
}

func (a *changesArgs) parser() *cliargs.Parser {
	p := cliargs.New()
	targetFlag(p, &a.target)
	p.Bool(&a.syncGitignore, "atualiza o .gitignore seletivo da instalação", "--sync-gitignore")
	return p
}

func runChanges(args []string, stdout, stderr io.Writer) int {
	var a changesArgs
	p := a.parser()
	if code, ok := parse("changes", p, args, stdout, stderr); !ok {
		return code
	}
	target, err := targetOf(p, a.target)
	if err != nil {
		return usageFailure("changes", err, stderr)
	}
	if target, err = maint.ValidateTarget(target, true); err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	if a.syncGitignore {
		if err := maint.SyncGitignore(target); err != nil {
			fmt.Fprintf(stderr, "✗ Não foi possível atualizar o filtro Git da instalação: %s/.gitignore\n", target)
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
	register(command{name: "changes", group: "Seus dados", order: 30, synopsis: "[DESTINO]",
		summary: "compara seus arquivos com os da instalação",
		flags:   (&changesArgs{}).parser, run: noName(runChanges)})
}
