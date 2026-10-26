package main

import (
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/plan"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

// planArgs are the arguments of update, upgrade and repair.
type planArgs struct {
	target, catalogFile string
	json, dryRun, yes   bool
}

func (a *planArgs) parser() *cliargs.Parser {
	p := cliargs.New()
	targetFlag(p, &a.target)
	p.Bool(&a.dryRun, "shows the plan without changing files", "--dry-run")
	p.Bool(&a.json, "the plan as JSON (needs --dry-run)", "--json")
	p.Bool(&a.yes, "applies without asking", "--yes")
	catalogFileFlag(p, &a.catalogFile)
	return p
}

// parsePlan parses planArgs and resolves the installation.
func parsePlan(name string, args []string, stdout, stderr io.Writer) (*planArgs, string, int, bool) {
	var a planArgs
	p := a.parser()
	if code, ok := parse(name, p, args, stdout, stderr); !ok {
		return nil, "", code, false
	}
	if a.json && !a.dryRun {
		return nil, "", usageFailure(name, cliargs.Usagef("--json needs --dry-run, so no change goes unseen"), stderr), false
	}
	target, err := targetOf(p, a.target)
	if err != nil {
		return nil, "", usageFailure(name, err, stderr), false
	}
	return &a, target, output.ExitSuccess, true
}

// planFailure reports err, as JSON when asked, and returns 1.
func planFailure(name string, asJSON bool, code string, err error, stdout, stderr io.Writer) int {
	if asJSON {
		envelope := output.Failure(name, output.ExitFailure, code, err.Error())
		envelope.DryRun = true
		envelope.Write(stdout)
	} else {
		fmt.Fprintf(stderr, "✗ %v\n", err)
	}
	return output.ExitFailure
}

func printPlan(planned *plan.Plan, stdout io.Writer) {
	for _, row := range planned.Operations {
		fmt.Fprintf(stdout, "  %-10s %-40s %s → %s  (%s)\n", row.Kind, row.Item, row.Installed, row.Available, row.Action)
	}
}

func runPlan(command string, args []string, stdout, stderr io.Writer) int {
	a, target, code, ok := parsePlan(command, args, stdout, stderr)
	if !ok {
		return code
	}
	parsed, err := loadCatalog(a.catalogFile)
	if err != nil {
		reason := "operation"
		if trust.IsTrustError(err) {
			reason = "trust"
		}
		return planFailure(command, a.json, reason, err, stdout, stderr)
	}
	inventory, err := catalog.LoadInventory()
	if err != nil {
		return planFailure(command, a.json, "operation", err, stdout, stderr)
	}
	input := install.PlanInput(target, parsed, inventory)
	compute := plan.Update
	if command == "upgrade" {
		compute = plan.Upgrade
	}
	result, err := compute(input)
	if err != nil {
		return planFailure(command, a.json, "operation", err, stdout, stderr)
	}
	if a.json {
		envelope := output.Success(command, result)
		envelope.DryRun = true
		if err := envelope.Write(stdout); err != nil {
			return output.ExitFailure
		}
		return output.ExitSuccess
	}
	if len(result.Operations) == 0 {
		fmt.Fprintln(stdout, "✓ No update available; the installed content is up to date.")
		return output.ExitSuccess
	}
	printPlan(result, stdout)
	if a.dryRun {
		fmt.Fprintln(stdout, "\n· Dry run done; no file was changed.")
		return output.ExitSuccess
	}
	question := fmt.Sprintf("Apply %d change(s) to %s?", len(result.Operations), target)
	if proceed, code := confirm(question, []string{"x86qw", command, target}, a.yes, stdout, stderr); !proceed {
		return code
	}
	ctx, stop := interruptible()
	defer stop()
	applied, err := install.Apply(ctx, command, input, result)
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	for _, warning := range applied.Warnings {
		fmt.Fprintf(stderr, "! %s\n", warning)
	}
	for _, client := range applied.Clients {
		fmt.Fprintf(stdout, "✓ %s updated\n", client)
	}
	if len(applied.Installed) > 0 {
		fmt.Fprintf(stdout, "✓ %d components updated · %d files\n", len(applied.Installed), applied.Files)
	}
	if applied.CLIVersion != "" {
		fmt.Fprintf(stdout, "✓ x86QW CLI updated to %s\n", applied.CLIVersion)
	}
	fmt.Fprintln(stdout, "✓ Installed content updated and validated.")
	return output.ExitSuccess
}

func init() {
	register(command{name: "update", group: "Install and maintain", order: 30, synopsis: "[TARGET]",
		summary: "updates what is installed",
		about:   "Updates the ezQuake, components and CLI you already have. Shows the plan and asks first; without a terminal, needs --yes.",
		flags:   (&planArgs{}).parser, run: runPlan})
	register(command{name: "upgrade", group: "Install and maintain", order: 40, synopsis: "[TARGET]",
		summary: "updates and adds what your profile gained",
		about:   "Like update, and also installs the components your content profile gained since the install.",
		flags:   (&planArgs{}).parser, run: runPlan})
}
