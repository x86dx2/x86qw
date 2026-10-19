package main

import (
	"context"
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/plan"
)

func runRepair(args []string, stdout, stderr io.Writer) int {
	parsed, ok := parseArgs("repair", args, []string{"--catalog-file"}, []string{"--dry-run", "--yes"}, nil, stderr)
	if !ok {
		return output.ExitUsage
	}
	asJSON, dryRun := parsed.bools["--json"], parsed.bools["--dry-run"]
	if asJSON && !dryRun {
		fmt.Fprintln(stderr, "x86qw repair: erro: repair --json exige --dry-run para não ocultar mutações")
		return output.ExitUsage
	}
	fail := func(code string, err error) int {
		if asJSON {
			envelope := output.Failure("repair", output.ExitFailure, code, err.Error())
			envelope.DryRun = true
			envelope.Write(stdout)
		} else {
			fmt.Fprintf(stderr, "✗ %v\n", err)
		}
		return output.ExitFailure
	}
	target, err := maint.ValidateTarget(parsed.target, true)
	if err != nil {
		return fail("operation", err)
	}
	parsedCatalog, err := loadCatalog(parsed.values["--catalog-file"])
	if err != nil {
		return fail("trust", err)
	}
	inventory, err := catalog.LoadInventory()
	if err != nil {
		return fail("operation", err)
	}
	input := plan.Input{Root: target, Catalog: parsedCatalog, Inventory: inventory}
	planned, _, err := install.RepairPlan(input)
	if err != nil {
		return fail("operation", err)
	}
	if asJSON {
		envelope := output.Success("repair", planned)
		envelope.DryRun = true
		envelope.Write(stdout)
		return output.ExitSuccess
	}
	if len(planned.Operations) == 0 {
		fmt.Fprintln(stdout, "✓ Nenhum reparo é necessário; a instalação está íntegra.")
		return output.ExitSuccess
	}
	for _, row := range planned.Operations {
		fmt.Fprintf(stdout, "  %-10s %-40s %s → %s  (%s)\n", row.Kind, row.Item, row.Installed, row.Available, row.Action)
	}
	if dryRun {
		fmt.Fprintln(stdout, "\n· Simulação concluída; nenhum arquivo foi alterado")
		return output.ExitSuccess
	}
	result, err := install.Repair(context.Background(), input)
	if err != nil {
		return fail("operation", err)
	}
	for _, warning := range result.Warnings {
		fmt.Fprintf(stderr, "! %s\n", warning)
	}
	fmt.Fprintln(stdout, "✓ Reparo concluído e validado.")
	return output.ExitSuccess
}

func init() {
	register("repair", 180, "repair [--dry-run]   diagnostica e repara conteúdo gerenciado", noName(runRepair))
}
