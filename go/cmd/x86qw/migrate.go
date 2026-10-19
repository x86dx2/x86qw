package main

import (
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
)

func runMigrate(args []string, stdout, stderr io.Writer) int {
	parsed, ok := parseArgs("migrate", args, nil, []string{"--dry-run"}, nil, stderr)
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
	ops, _, err := maint.PlanMigration(target)
	if err != nil {
		return fail(err)
	}
	fmt.Fprintln(stdout, "\nMigração da instalação")
	if len(ops) == 0 {
		fmt.Fprintln(stdout, "✓ Nenhuma migração é necessária; os metadados já estão convergentes.")
		return output.ExitSuccess
	}
	for _, op := range ops {
		fmt.Fprintf(stdout, "· %s: %s → %s\n", op.Phase, op.Source, op.Destination)
	}
	if parsed.bools["--dry-run"] {
		fmt.Fprintln(stdout, "\n· Simulação concluída; nenhum arquivo foi alterado")
		return output.ExitSuccess
	}
	if _, err := maint.Migrate(target); err != nil {
		return fail(err)
	}
	fmt.Fprintln(stdout, "✓ Migração concluída e validada.")
	return output.ExitSuccess
}

func init() {
	register("migrate", 170, "migrate [--dry-run]  migra metadados para o contrato 1.0", noName(runMigrate))
}
