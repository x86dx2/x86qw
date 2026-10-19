package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/plan"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

func runPlan(command string, args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	flags.SetOutput(stderr)
	asJSON := flags.Bool("json", false, "saída estruturada (exige --dry-run)")
	dryRun := flags.Bool("dry-run", false, "mostra o plano sem alterar arquivos")
	yes := flags.Bool("yes", false, "aplica o plano sem confirmação interativa")
	targetFlag := flags.String("target", "", "diretório da instalação")
	catalogFile := flags.String("catalog-file", "", "catálogo local já autenticado (testes)")
	if err := flags.Parse(args); err != nil {
		return output.ExitUsage
	}
	if *asJSON && !*dryRun {
		fmt.Fprintf(stderr, "x86qw %s: --json exige --dry-run\n", command)
		return output.ExitUsage
	}
	rest := append([]string{}, flags.Args()...)
	if *targetFlag != "" {
		rest = append([]string{"--target", *targetFlag}, rest...)
	}
	target, _, ok := targetFlags(command, rest, stderr)
	if !ok {
		return output.ExitUsage
	}
	fail := func(code string, err error) int {
		if *asJSON {
			envelope := output.Failure(command, output.ExitFailure, code, err.Error())
			envelope.DryRun = true
			envelope.Write(stdout)
		} else {
			fmt.Fprintf(stderr, "✗ %v\n", err)
		}
		return output.ExitFailure
	}
	var raw []byte
	var err error
	if *catalogFile != "" {
		raw, err = os.ReadFile(*catalogFile)
	} else {
		var authenticated *trust.Catalog
		authenticated, err = trust.LoadCatalog(trust.Options{CacheDir: defaultCacheDir()})
		if authenticated != nil {
			raw = authenticated.Raw
		}
	}
	if err != nil {
		return fail("trust", err)
	}
	parsed, err := catalog.Parse(raw)
	if err != nil {
		return fail("operation", err)
	}
	inventory, err := catalog.LoadInventory()
	if err != nil {
		return fail("operation", err)
	}
	input := plan.Input{Root: target, Catalog: parsed, Inventory: inventory}
	compute := plan.Update
	if command == "upgrade" {
		compute = plan.Upgrade
	}
	result, err := compute(input)
	if err != nil {
		return fail("operation", err)
	}
	if !*dryRun {
		return applyPlan(command, input, result, *yes, stdout, stderr)
	}
	if *asJSON {
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
	for _, row := range result.Operations {
		fmt.Fprintf(stdout, "  %-10s %-40s %s → %s  (%s)\n", row.Kind, row.Item, row.Installed, row.Available, row.Action)
	}
	return output.ExitSuccess
}

func applyPlan(command string, input plan.Input, planned *plan.Plan, yes bool, stdout, stderr io.Writer) int {
	if len(planned.Operations) == 0 {
		fmt.Fprintln(stdout, "✓ Nenhuma atualização disponível; o conteúdo instalado já está atualizado.")
		return output.ExitSuccess
	}
	for _, row := range planned.Operations {
		fmt.Fprintf(stdout, "  %-10s %-40s %s → %s  (%s)\n", row.Kind, row.Item, row.Installed, row.Available, row.Action)
	}
	if !yes {
		fmt.Fprintf(stderr, "Confirme com: x86qw %s --yes\n", command)
		return output.ExitUsage
	}
	result, err := install.Apply(context.Background(), command, input, planned)
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	for _, warning := range result.Warnings {
		fmt.Fprintf(stderr, "! %s\n", warning)
	}
	fmt.Fprintf(stdout, "✓ %d componentes atualizados e validados · %d arquivos\n", len(result.Installed), result.Files)
	return output.ExitSuccess
}

func init() {
	register("update", 50, "update [--dry-run]   atualiza os itens instalados", runPlan)
	register("upgrade", 60, "upgrade [--dry-run]  atualiza e incorpora as novidades do perfil", runPlan)
}
