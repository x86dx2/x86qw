package main

import (
	"context"
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

func runRepair(args []string, stdout, stderr io.Writer) int {
	a, target, code, ok := parsePlan("repair", args, stdout, stderr)
	if !ok {
		return code
	}
	target, err := maint.ValidateTarget(target, true)
	if err != nil {
		return planFailure("repair", a.json, "operation", err, stdout, stderr)
	}
	parsed, err := loadCatalog(a.catalogFile)
	if err != nil {
		reason := "operation"
		if trust.IsTrustError(err) {
			reason = "trust"
		}
		return planFailure("repair", a.json, reason, err, stdout, stderr)
	}
	inventory, err := catalog.LoadInventory()
	if err != nil {
		return planFailure("repair", a.json, "operation", err, stdout, stderr)
	}
	input := install.PlanInput(target, parsed, inventory)
	planned, _, err := install.RepairPlan(input)
	if err != nil {
		return planFailure("repair", a.json, "operation", err, stdout, stderr)
	}
	if a.json {
		envelope := output.Success("repair", planned)
		envelope.DryRun = true
		envelope.Write(stdout)
		return output.ExitSuccess
	}
	if len(planned.Operations) == 0 {
		fmt.Fprintln(stdout, "✓ Nenhum reparo é necessário; a instalação está íntegra.")
		return output.ExitSuccess
	}
	printPlan(planned, stdout)
	if a.dryRun {
		fmt.Fprintln(stdout, "\n· Simulação concluída; nenhum arquivo foi alterado.")
		return output.ExitSuccess
	}
	question := fmt.Sprintf("Reparar %d item(ns) em %s?", len(planned.Operations), target)
	if proceed, code := confirm(question, "x86qw repair "+target, a.yes, stdout, stderr); !proceed {
		return code
	}
	result, err := install.Repair(context.Background(), input)
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	for _, warning := range result.Warnings {
		fmt.Fprintf(stderr, "! %s\n", warning)
	}
	fmt.Fprintln(stdout, "✓ Reparo concluído e validado.")
	return output.ExitSuccess
}

func init() {
	register(command{name: "repair", group: "Instalar e manter", order: 60, synopsis: "[DESTINO]",
		summary: "restaura o que verify acusar",
		about:   "Baixa de novo o que falta ou não confere com o catálogo, sem tocar no que você editou. Mostra o plano e pergunta antes; sem terminal, exige --yes.",
		flags:   (&planArgs{}).parser, run: noName(runRepair)})
}
