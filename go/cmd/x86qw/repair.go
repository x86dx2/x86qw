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
		fmt.Fprintln(stdout, "✓ No repair needed; the installation is intact.")
		return output.ExitSuccess
	}
	printPlan(planned, stdout)
	if a.dryRun {
		fmt.Fprintln(stdout, "\n· Dry run done; no file was changed.")
		return output.ExitSuccess
	}
	question := fmt.Sprintf("Repair %d item(s) in %s?", len(planned.Operations), target)
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
	fmt.Fprintln(stdout, "✓ Repair done and validated.")
	return output.ExitSuccess
}

func init() {
	register(command{name: "repair", group: "Install and maintain", order: 60, synopsis: "[TARGET]",
		summary: "restores what verify reports",
		about:   "Downloads again what is missing or does not match the catalog, without touching what you edited. Shows the plan and asks first; without a terminal, needs --yes.",
		flags:   (&planArgs{}).parser, run: noName(runRepair)})
}
