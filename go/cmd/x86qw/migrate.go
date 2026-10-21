package main

import (
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
)

type migrateArgs struct {
	target string
	dryRun bool
}

func (a *migrateArgs) parser() *cliargs.Parser {
	p := cliargs.New()
	targetFlag(p, &a.target)
	p.Bool(&a.dryRun, "mostra o que mudaria sem alterar arquivos", "--dry-run")
	return p
}

func runMigrate(args []string, stdout, stderr io.Writer) int {
	var a migrateArgs
	p := a.parser()
	if code, ok := parse("migrate", p, args, stdout, stderr); !ok {
		return code
	}
	target, err := targetOf(p, a.target)
	if err != nil {
		return usageFailure("migrate", err, stderr)
	}
	fail := func(err error) int {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	if target, err = maint.ValidateTarget(target, true); err != nil {
		return fail(err)
	}
	ops, _, err := maint.PlanMigration(target)
	if err != nil {
		return fail(err)
	}
	fmt.Fprintln(stdout, "\nMigração da instalação")
	if len(ops) == 0 {
		fmt.Fprintln(stdout, "✓ Nenhuma migração é necessária; os metadados já estão no formato atual.")
		return output.ExitSuccess
	}
	for _, op := range ops {
		fmt.Fprintf(stdout, "· %s: %s → %s\n", op.Phase, op.Source, op.Destination)
	}
	if a.dryRun {
		fmt.Fprintln(stdout, "\n· Simulação concluída; nenhum arquivo foi alterado.")
		return output.ExitSuccess
	}
	if _, err := maint.Migrate(target); err != nil {
		return fail(err)
	}
	fmt.Fprintln(stdout, "✓ Migração concluída e validada.")
	return output.ExitSuccess
}

func init() {
	register(command{name: "migrate", group: "Diagnóstico", order: 30, synopsis: "[DESTINO]",
		summary: "converte metadados de instalações antigas para o formato atual",
		flags:   (&migrateArgs{}).parser, run: noName(runMigrate)})
}
