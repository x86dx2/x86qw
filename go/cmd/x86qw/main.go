// Command x86qw is the native x86QW CLI (Go port, in progress).
package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"time"

	"github.com/x86dx2/x86qw/go/internal/buildinfo"
	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/plan"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

const usage = `x86QW %s
QuakeWorld moderno

Uso: x86qw <comando> [opções]

Comandos:
  version              mostra a versão
  status               mostra instalação, estado e serviços registrados
  verify               verifica a integridade da instalação
  install              instala ezQuake e um perfil de conteúdo em um diretório novo
  update --dry-run     planeja a atualização dos itens instalados
  upgrade --dry-run    planeja a atualização e as novidades do perfil
  catalog              autentica o catálogo público via TUF
`

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout, stderr io.Writer) int {
	if len(args) == 0 || args[0] == "-h" || args[0] == "--help" || args[0] == "help" {
		fmt.Fprintf(stdout, usage, buildinfo.Version)
		return output.ExitSuccess
	}
	switch args[0] {
	case "version", "--version":
		return runVersion(args[1:], stdout, stderr)
	case "catalog":
		return runCatalog(args[1:], stdout, stderr)
	case "status":
		return runStatus(args[1:], stdout, stderr)
	case "verify":
		return runVerify(args[1:], stdout, stderr)
	case "update", "upgrade":
		return runPlan(args[0], args[1:], stdout, stderr)
	case "install":
		return runInstall(args[1:], stdout, stderr)
	default:
		fmt.Fprintf(stderr, "x86qw: comando desconhecido: %s\n", args[0])
		fmt.Fprintf(stderr, usage, buildinfo.Version)
		return output.ExitUsage
	}
}

func runVersion(args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("version", flag.ContinueOnError)
	flags.SetOutput(stderr)
	asJSON := flags.Bool("json", false, "saída estruturada")
	if err := flags.Parse(args); err != nil {
		return output.ExitUsage
	}
	if *asJSON {
		data := map[string]any{"project": "x86qw", "version": buildinfo.Version}
		if err := output.Success("version", data).Write(stdout); err != nil {
			return output.ExitFailure
		}
		return output.ExitSuccess
	}
	fmt.Fprintf(stdout, "x86QW %s\n", buildinfo.Version)
	return output.ExitSuccess
}

func runCatalog(args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("catalog", flag.ContinueOnError)
	flags.SetOutput(stderr)
	asJSON := flags.Bool("json", false, "saída estruturada")
	baseURL := flags.String("base-url", trust.DefaultBaseURL, "repositório TUF (metadata/ e targets/)")
	cacheDir := flags.String("cache-dir", defaultCacheDir(), "cache de metadata confiável; vazio desativa")
	outPath := flags.String("output", "", "grava o catálogo autenticado neste arquivo")
	if err := flags.Parse(args); err != nil {
		return output.ExitUsage
	}

	catalog, err := trust.LoadCatalog(trust.Options{BaseURL: *baseURL, CacheDir: *cacheDir})
	if err == nil && *outPath != "" {
		err = os.WriteFile(*outPath, catalog.Raw, 0o644)
	}
	if err != nil {
		code := "operation"
		if trust.IsTrustError(err) {
			code = "trust"
		}
		if *asJSON {
			output.Failure("catalog", output.ExitFailure, code, err.Error()).Write(stdout)
		} else {
			fmt.Fprintf(stderr, "✗ Falha de trust do catálogo x86QW: %v\n", err)
		}
		return output.ExitFailure
	}

	if *asJSON {
		roles := map[string]any{}
		for name, state := range catalog.Roles {
			roles[name] = map[string]any{
				"expires": state.Expires.UTC().Format(time.RFC3339),
				"version": state.Version,
			}
		}
		data := map[string]any{
			"packages": catalog.Packages,
			"project":  "x86qw",
			"roles":    roles,
			"sha256":   catalog.SHA256,
		}
		if err := output.Success("catalog", data).Write(stdout); err != nil {
			return output.ExitFailure
		}
		return output.ExitSuccess
	}
	fmt.Fprintf(stdout, "✓ Catálogo autenticado: %d pacotes, sha256 %s\n", catalog.Packages, catalog.SHA256)
	names := make([]string, 0, len(catalog.Roles))
	for name := range catalog.Roles {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		state := catalog.Roles[name]
		fmt.Fprintf(stdout, "  %-9s v%-3d expira %s\n", name, state.Version, state.Expires.UTC().Format(time.RFC3339))
	}
	return output.ExitSuccess
}

func defaultCacheDir() string {
	dir, err := os.UserCacheDir()
	if err != nil {
		return ""
	}
	return filepath.Join(dir, "x86qw", "tuf")
}

// targetFlags parses [--json] [--target DIR] [DIR] for installation commands.
func targetFlags(name string, args []string, stderr io.Writer) (target string, asJSON bool, ok bool) {
	flags := flag.NewFlagSet(name, flag.ContinueOnError)
	flags.SetOutput(stderr)
	jsonFlag := flags.Bool("json", false, "saída estruturada")
	targetFlag := flags.String("target", "", "diretório da instalação")
	if err := flags.Parse(args); err != nil {
		return "", false, false
	}
	positional := flags.Args()
	if len(positional) > 1 {
		fmt.Fprintf(stderr, "x86qw %s: argumentos demais\n", name)
		return "", false, false
	}
	target = *targetFlag
	if len(positional) == 1 {
		if target != "" && filepath.Clean(target) != filepath.Clean(positional[0]) {
			fmt.Fprintf(stderr, "x86qw %s: --target e o destino posicional divergem\n", name)
			return "", false, false
		}
		target = positional[0]
	}
	if target == "" {
		target = defaultTarget()
	}
	if absolute, err := filepath.Abs(target); err == nil {
		target = absolute
	}
	return target, *jsonFlag, true
}

// defaultTarget is the installation that contains this executable
// (<root>/x86qw or <root>/.x86qw/cli/x86qw), else the current directory.
func defaultTarget() string {
	if executable, err := os.Executable(); err == nil {
		if resolved, err := filepath.EvalSymlinks(executable); err == nil {
			dir := filepath.Dir(resolved)
			for _, candidate := range []string{dir, filepath.Join(dir, "..", "..")} {
				if _, err := os.Stat(filepath.Join(candidate, ".x86qw")); err == nil {
					return filepath.Clean(candidate)
				}
			}
		}
	}
	return "."
}

func runStatus(args []string, stdout, stderr io.Writer) int {
	target, asJSON, ok := targetFlags("status", args, stderr)
	if !ok {
		return output.ExitUsage
	}
	status := install.ReadStatus(target)
	if asJSON {
		if err := output.Success("status", status).Write(stdout); err != nil {
			return output.ExitFailure
		}
		return output.ExitSuccess
	}
	fmt.Fprintf(stdout, "Instalação: %s (%s)\nEstado: %s\n", target, status.Installation, status.State)
	if len(status.Sessions) == 0 {
		fmt.Fprintln(stdout, "Nenhum serviço registrado.")
	}
	for _, session := range status.Sessions {
		fmt.Fprintf(stdout, "  %s  %-10s %s\n", session.SessionID, session.Status, session.Command)
	}
	return output.ExitSuccess
}

func runVerify(args []string, stdout, stderr io.Writer) int {
	target, asJSON, ok := targetFlags("verify", args, stderr)
	if !ok {
		return output.ExitUsage
	}
	report, err := install.Verify(target)
	if err != nil {
		if asJSON {
			output.Failure("verify", output.ExitFailure, "operation", err.Error()).Write(stdout)
		} else {
			fmt.Fprintf(stderr, "✗ %v\n", err)
		}
		return output.ExitFailure
	}
	if asJSON {
		data := map[string]any{"target": target, "verified": true}
		if err := output.Success("verify", data).Write(stdout); err != nil {
			return output.ExitFailure
		}
		return output.ExitSuccess
	}
	fmt.Fprintf(stdout, "✓ ezQuake (%d) + %d componentes íntegros · %d arquivos\n", report.Clients, len(report.Components), report.Files)
	return output.ExitSuccess
}

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

func loadCatalog(catalogFile string) (*catalog.Catalog, error) {
	var raw []byte
	var err error
	if catalogFile != "" {
		raw, err = os.ReadFile(catalogFile)
	} else {
		var authenticated *trust.Catalog
		authenticated, err = trust.LoadCatalog(trust.Options{CacheDir: defaultCacheDir()})
		if authenticated != nil {
			raw = authenticated.Raw
		}
	}
	if err != nil {
		return nil, err
	}
	return catalog.Parse(raw)
}

func runInstall(args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("install", flag.ContinueOnError)
	flags.SetOutput(stderr)
	platform := flags.String("platform", install.HostPlatform(), "macos, linux ou windows")
	channel := flags.String("channel", "stable", "canal do ezQuake")
	profile := flags.String("profile", "", "essential, recommended ou complete")
	target := flags.String("target", "", "diretório da instalação")
	catalogFile := flags.String("catalog-file", "", "catálogo local já autenticado (testes)")
	if err := flags.Parse(args); err != nil {
		return output.ExitUsage
	}
	if len(flags.Args()) == 1 && *target == "" {
		*target = flags.Args()[0]
	}
	if *target == "" || *profile == "" || len(flags.Args()) > 1 {
		fmt.Fprintln(stderr, "uso: x86qw install --profile essential|recommended|complete [--platform SO] [--channel stable] DESTINO")
		return output.ExitUsage
	}
	parsed, err := loadCatalog(*catalogFile)
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	inventory, err := catalog.LoadInventory()
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	absolute, _ := filepath.Abs(*target)
	result, err := install.Install(context.Background(), absolute, parsed, inventory,
		install.InstallOptions{Platform: *platform, Channel: *channel, Profile: *profile})
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	for _, warning := range result.Warnings {
		fmt.Fprintf(stderr, "! %s\n", warning)
	}
	fmt.Fprintf(stdout, "✓ Instalação completa: ezQuake + %d componentes · %d arquivos em %s\n", len(result.Installed), result.Files, absolute)
	return output.ExitSuccess
}
