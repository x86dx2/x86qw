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

	"github.com/x86dx2/x86qw/go/internal/buildinfo"
	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/cli"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/menu"
	"github.com/x86dx2/x86qw/go/internal/output"
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

// publishCLI installs this executable as the installation's CLI and links
// ~/.local/bin/x86qw to its launcher.
func publishCLI(target string, stdout, stderr io.Writer) error {
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
	if err := flags.Parse(args); err != nil {
		return output.ExitUsage
	}
	given := map[string]bool{}
	flags.Visit(func(f *flag.Flag) { given[f.Name] = true })
	if *target == "" && len(flags.Args()) == 1 {
		*target = flags.Args()[0]
	}
	targetGiven := *target != ""
	if !targetGiven {
		home, _ := os.UserHomeDir()
		*target = filepath.Join(home, "Games", "x86qw")
	}
	root, _ := filepath.Abs(*target)
	if hasInstallation(root) {
		held, err := lock.Acquire(root, "install")
		if err != nil {
			fmt.Fprintf(stderr, "✗ %v\n", err)
			return output.ExitFailure
		}
		err = publishCLI(root, stdout, stderr)
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
	request := &installRequest{target: root, platform: *platform, channel: *channel, profile: *profile}
	if *profile == "" {
		// A new installation without --profile asks like the install wizard:
		// destination (when not given), channel, release, content and the
		// plan confirmation. Without a terminal there is nobody to ask.
		if !stdinIsTerminal() {
			fmt.Fprintln(stderr, "uso: x86qw setup --profile essential|recommended|complete [--target DESTINO]")
			return output.ExitUsage
		}
		if !targetGiven {
			request.target = ""
		}
		con := newConsole(stdout, stderr, false, false)
		stop := trapInterrupt(stderr, "Operação cancelada. Nenhuma seleção pendente foi aplicada.")
		proceed, err := runInstallWizard(con, request, *catalogFile, false, given["platform"], given["channel"])
		stop()
		switch {
		case err == nil && !proceed:
			return output.ExitSuccess
		case err == nil:
		case errors.Is(err, menu.ErrInterrupt):
			con.failure("Operação cancelada. Nenhuma seleção pendente foi aplicada.")
			return exitInterrupted
		case errors.Is(err, menu.ErrExit):
			con.info("Menu encerrado; nenhuma seleção pendente foi aplicada.")
			return output.ExitSuccess
		case errors.Is(err, menu.ErrCancelled):
			con.info("Operação cancelada; nenhuma seleção pendente foi aplicada.")
			return exitInterrupted
		default:
			return con.failed(err)
		}
		root, _ = filepath.Abs(request.target)
		if hasInstallation(root) {
			fmt.Fprintf(stderr, "✗ Já existe uma instalação em %s; execute: x86qw setup --target %s\n", root, root)
			return output.ExitFailure
		}
	}
	parsed, err := loadCatalog(*catalogFile)
	if err == nil {
		var inventory *catalog.Inventory
		if inventory, err = catalog.LoadInventory(); err == nil {
			var result *install.Result
			result, err = install.Install(context.Background(), root, parsed, inventory, install.InstallOptions{
				Platform: request.platform, Channel: request.channel, Release: request.release,
				Profile: request.profile, Requested: request.requested,
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
		err = publishCLI(root, stdout, stderr)
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

func init() {
	register("setup", 35, "setup                instala ou adota uma instalação e publica a CLI nativa", noName(runSetup))
}
