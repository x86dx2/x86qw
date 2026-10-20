package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/buildinfo"
	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/cli"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/setup"
	"github.com/x86dx2/x86qw/go/internal/setup/tui"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

// hasInstallation reports whether target already holds an x86QW install.
func hasInstallation(target string) bool {
	if _, err := os.Stat(filepath.Join(target, ".x86qw", "state.json")); err == nil {
		return true
	}
	for _, spec := range catalog.ClientSpecs {
		for _, channel := range []string{"stable", "nightly"} {
			if _, err := os.Stat(filepath.Join(target, filepath.FromSlash(spec.Receipt(channel)))); err == nil {
				return true
			}
		}
	}
	return false
}

// publishCLI installs this executable as the installation's CLI and, with
// link, links ~/.local/bin/x86qw to its launcher (shortcuts on Windows).
func publishCLI(target string, link bool, stdout, stderr io.Writer) error {
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	if resolved, err := filepath.EvalSymlinks(executable); err == nil {
		executable = resolved
	}
	installed := filepath.Join(target, ".x86qw", "cli", cli.BinaryName())
	if same, _ := filepath.EvalSymlinks(installed); same == executable {
		// Already running the installed copy; refresh launcher and receipt only.
		copied := filepath.Join(os.TempDir(), fmt.Sprintf("x86qw-self-%d", os.Getpid()))
		if err := copyExecutable(executable, copied); err != nil {
			return err
		}
		defer os.Remove(copied)
		executable = copied
	}
	layer, err := cli.Install(target, executable, buildinfo.Version)
	if err != nil {
		return err
	}
	if !link {
		return nil
	}
	home, _ := os.UserHomeDir()
	command, err := cli.LinkUserCommand(target, home)
	if err != nil {
		layer.Rollback()
		return err
	}
	switch {
	case command != "" && runtime.GOOS == "windows":
		fmt.Fprintf(stdout, "Atalhos do x86QW criados no Menu Iniciar e na Área de Trabalho (%s).\n", command)
	case command != "":
		fmt.Fprintf(stdout, "Comando x86qw disponível em %s\n", command)
	}
	return nil
}

func copyExecutable(source, destination string) error {
	data, err := os.ReadFile(source)
	if err != nil {
		return err
	}
	return os.WriteFile(destination, data, 0o755)
}

func runSetup(args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("setup", flag.ContinueOnError)
	flags.SetOutput(stderr)
	target := flags.String("target", "", "diretório da instalação")
	profile := flags.String("profile", "", "essential, recommended ou complete (instalação nova)")
	platform := flags.String("platform", install.HostPlatform(), "macos, linux ou windows")
	channel := flags.String("channel", "stable", "canal do ezQuake")
	catalogFile := flags.String("catalog-file", "", "catálogo local já autenticado (testes)")
	recipe := setup.AddRecipeFlags(flags)
	yes := flags.Bool("yes", false, "confirma a receita sem perguntar")
	if err := flags.Parse(args); err != nil {
		return output.ExitUsage
	}
	given := map[string]bool{}
	flags.Visit(func(f *flag.Flag) { given[f.Name] = true })
	if *target == "" && len(flags.Args()) == 1 {
		*target = flags.Args()[0]
	}
	if *target == "" {
		*target = tui.DefaultTarget()
	}
	root, _ := filepath.Abs(*target)
	backend := &setup.Real{Trust: trust.Options{CacheDir: defaultCacheDir()}, CatalogFile: *catalogFile, Platform: *platform}
	switch {
	case given["intent"]:
		// A recipe installs without asking.
		options, err := recipe(root)
		if err != nil {
			fmt.Fprintf(stderr, "x86qw setup: %v\n", err)
			return output.ExitUsage
		}
		backend.Publish = func(target string) error { return publishCLI(target, false, stdout, stderr) }
		backend.Integrate = setup.Integrate
		return runRecipe(backend, options, *yes, stdout, stderr)
	case *profile == "" && stdinIsTerminal() && isTerminal(os.Stdout):
		return runSetupInterface(backend, root, stdout, stderr)
	}
	if hasInstallation(root) {
		held, err := lock.Acquire(root, "install")
		if err != nil {
			fmt.Fprintf(stderr, "✗ %v\n", err)
			return output.ExitFailure
		}
		err = publishCLI(root, true, stdout, stderr)
		held.Release()
		if err != nil {
			fmt.Fprintf(stderr, "✗ %v\n", err)
			return output.ExitFailure
		}
		if _, err := install.Verify(root); err != nil {
			fmt.Fprintf(stderr, "! CLI nativa instalada, mas a verificação encontrou problemas: %v\n  Execute: x86qw repair\n", err)
			return output.ExitFailure
		}
		fmt.Fprintf(stdout, "✓ Instalação existente em %s agora usa a CLI nativa x86QW %s.\n", root, buildinfo.Version)
		return output.ExitSuccess
	}
	if *profile == "" {
		// Without a terminal there is nobody to ask: a profile or a recipe.
		fmt.Fprintln(stderr, "uso: x86qw setup --profile essential|recommended|complete [--target DESTINO]")
		fmt.Fprintln(stderr, "     x86qw setup --intent play|host|both … --yes (a receita que o instalador copia)")
		return output.ExitUsage
	}
	parsed, err := loadCatalog(*catalogFile)
	if err == nil {
		var inventory *catalog.Inventory
		if inventory, err = catalog.LoadInventory(); err == nil {
			var result *install.Result
			result, err = install.Install(context.Background(), root, parsed, inventory, install.InstallOptions{
				Platform: *platform, Channel: *channel, Profile: *profile,
			})
			if err == nil {
				for _, warning := range result.Warnings {
					fmt.Fprintf(stderr, "! %s\n", warning)
				}
				fmt.Fprintf(stdout, "✓ ezQuake + %d componentes instalados em %s\n", len(result.Installed), root)
			}
		}
	}
	if err == nil {
		err = publishCLI(root, true, stdout, stderr)
	}
	if err != nil {
		if errors.Is(err, os.ErrPermission) {
			fmt.Fprintf(stderr, "✗ sem permissão para escrever em %s\n", root)
		} else {
			fmt.Fprintf(stderr, "✗ %v\n", err)
		}
		return output.ExitFailure
	}
	fmt.Fprintln(stdout, "  Jogar agora: x86qw play")
	return output.ExitSuccess
}

// runSetupInterface opens the installer interface. An installation
// already at root first gets this CLI, as re-running the bootstrap always
// did, then opens in the management panel.
func runSetupInterface(backend *setup.Real, root string, stdout, stderr io.Writer) int {
	if hasInstallation(root) {
		held, err := lock.Acquire(root, "install")
		if err == nil {
			// Linked as before unless the setup recorded otherwise.
			link := true
			if choices, _ := setup.LoadChoices(root); choices != nil {
				link = choices.Integration.Path
			}
			err = publishCLI(root, link, io.Discard, io.Discard)
			held.Release()
		}
		if err != nil {
			fmt.Fprintf(stderr, "! A CLI nativa não pôde ser publicada: %v\n", err)
		}
	}
	// The interface owns the screen: what publishing prints is dropped.
	backend.Publish = func(target string) error { return publishCLI(target, false, io.Discard, io.Discard) }
	backend.Integrate = setup.Integrate
	next, err := tui.Run(tui.Config{Backend: backend, Target: root, Lang: interfaceLanguage()})
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	if next == nil {
		return output.ExitSuccess
	}
	return run(next, stdout, stderr)
}

// interfaceLanguage follows the locale: Portuguese unless it says otherwise.
func interfaceLanguage() tui.Lang {
	for _, name := range []string{"LC_ALL", "LC_MESSAGES", "LANG"} {
		if value := os.Getenv(name); value != "" && value != "C" && value != "POSIX" {
			if strings.HasPrefix(strings.ToLower(value), "pt") {
				return tui.PT
			}
			return tui.EN
		}
	}
	return tui.PT
}

// runRecipe installs options without the interface, printing the
// preflight, each component as it lands and how to start.
func runRecipe(backend *setup.Real, options setup.Options, yes bool, stdout, stderr io.Writer) int {
	con := newConsole(stdout, stderr, false, false)
	if !yes {
		fmt.Fprintf(stderr, "Confirme com: %s\n", strings.Join(options.Recipe(), " "))
		return output.ExitUsage
	}
	ctx := context.Background()
	blocked := false
	for check := range backend.Preflight(ctx, options) {
		text := check.Title + "  " + check.Value
		switch check.State {
		case setup.CheckFailed:
			blocked = true
			con.failure(text + " — " + strings.TrimSpace(check.Detail+" "+check.Fix))
		case setup.CheckWarning:
			con.warning(text + " — " + strings.TrimSpace(check.Detail+" "+check.Fix))
		default:
			con.success(text)
		}
	}
	if blocked {
		return output.ExitFailure
	}
	stop := trapInterrupt(stderr, "Instalação cancelada; a pasta voltou ao que era.")
	defer stop()
	announced := map[string]bool{}
	var last setup.Progress
	for progress := range backend.Install(ctx, options, nil) {
		for _, lane := range progress.Lanes {
			if !announced[lane.ID] && lane.FileCount > 0 && lane.Files >= lane.FileCount {
				announced[lane.ID] = true
				con.success(fmt.Sprintf("%s · %d arquivos", lane.Label, lane.Files))
			}
		}
		last = progress
	}
	if last.Err != nil {
		con.failure(last.Err.Error() + " — a pasta voltou ao que era.")
		return output.ExitFailure
	}
	for _, warning := range last.Warnings {
		con.warning(warning)
	}
	fmt.Fprintf(stdout, "✓ x86QW instalado em %s\n", options.Target)
	if options.Intent.WantsServer() {
		for index := range options.Server.Instances {
			fmt.Fprintf(stdout, "  Servidor #%d: x86qw %s\n", index+1, strings.Join(setup.HostCommand(options.Target, options, index), " "))
		}
		fmt.Fprintf(stdout, "  rcon: %s (guardado em %s)\n", options.Server.Rcon, filepath.Join(options.Target, filepath.FromSlash(setup.RconFile)))
	}
	if options.Intent.WantsClient() {
		fmt.Fprintln(stdout, "  Jogar agora: x86qw play")
	}
	return output.ExitSuccess
}

func init() {
	register("setup", 35, "setup                instala ou adota uma instalação e publica a CLI nativa", noName(runSetup))
}
