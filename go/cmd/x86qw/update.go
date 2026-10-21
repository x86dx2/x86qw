package main

import (
	"context"
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
	p.Bool(&a.dryRun, "mostra o plano sem alterar arquivos", "--dry-run")
	p.Bool(&a.json, "o plano em JSON (exige --dry-run)", "--json")
	p.Bool(&a.yes, "aplica sem perguntar", "--yes")
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
		return nil, "", usageFailure(name, cliargs.Usagef("--json exige --dry-run, para não esconder mudanças"), stderr), false
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
		fmt.Fprintln(stdout, "✓ Nenhuma atualização disponível; o conteúdo instalado já está atualizado.")
		return output.ExitSuccess
	}
	printPlan(result, stdout)
	if a.dryRun {
		fmt.Fprintln(stdout, "\n· Simulação concluída; nenhum arquivo foi alterado.")
		return output.ExitSuccess
	}
	question := fmt.Sprintf("Aplicar %d mudança(s) em %s?", len(result.Operations), target)
	if proceed, code := confirm(question, "x86qw "+command+" "+target, a.yes, stdout, stderr); !proceed {
		return code
	}
	applied, err := install.Apply(context.Background(), command, input, result)
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	for _, warning := range applied.Warnings {
		fmt.Fprintf(stderr, "! %s\n", warning)
	}
	for _, client := range applied.Clients {
		fmt.Fprintf(stdout, "✓ %s atualizado\n", client)
	}
	if len(applied.Installed) > 0 {
		fmt.Fprintf(stdout, "✓ %d componentes atualizados · %d arquivos\n", len(applied.Installed), applied.Files)
	}
	if applied.CLIVersion != "" {
		fmt.Fprintf(stdout, "✓ CLI x86QW atualizada para %s\n", applied.CLIVersion)
	}
	fmt.Fprintln(stdout, "✓ Conteúdo instalado atualizado e validado.")
	return output.ExitSuccess
}

func init() {
	register(command{name: "update", group: "Instalar e manter", order: 30, synopsis: "[DESTINO]",
		summary: "atualiza o que está instalado",
		about:   "Atualiza o ezQuake, os componentes e a CLI que você já tem. Mostra o plano e pergunta antes; sem terminal, exige --yes.",
		flags:   (&planArgs{}).parser, run: runPlan})
	register(command{name: "upgrade", group: "Instalar e manter", order: 40, synopsis: "[DESTINO]",
		summary: "atualiza e acrescenta as novidades do seu perfil",
		about:   "Como update, e também instala os componentes que o seu perfil de conteúdo ganhou desde a instalação.",
		flags:   (&planArgs{}).parser, run: runPlan})
}
