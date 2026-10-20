package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"path/filepath"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/menu"
	"github.com/x86dx2/x86qw/go/internal/output"
)

func runInstall(args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("install", flag.ContinueOnError)
	flags.SetOutput(stderr)
	platform := flags.String("platform", install.HostPlatform(), "macos, linux ou windows")
	channel := flags.String("channel", "stable", "canal do ezQuake: stable ou nightly")
	release := flags.String("release", "", "versão do ezQuake ou latest")
	profile := flags.String("profile", "", "essential, recommended ou complete")
	target := flags.String("target", "", "diretório da instalação")
	catalogFile := flags.String("catalog-file", "", "catálogo local já autenticado (testes)")
	nonInteractive := flags.Bool("non-interactive", false, "nunca perguntar; exige --profile")
	noColor := flags.Bool("no-color", false, "desativa cores")
	verbose := flags.Bool("verbose", false, "detalhes técnicos")
	if err := flags.Parse(args); err != nil {
		return output.ExitUsage
	}
	given := map[string]bool{}
	flags.Visit(func(f *flag.Flag) { given[f.Name] = true })
	if len(flags.Args()) == 1 && *target == "" {
		*target = flags.Args()[0]
	}
	// Without --profile (and without --non-interactive) the missing choices
	// are asked like the Python installer's wizard.
	wizard := *profile == "" && !*nonInteractive
	if (!wizard && (*target == "" || *profile == "")) || len(flags.Args()) > 1 {
		fmt.Fprintln(stderr, "uso: x86qw install [--profile essential|recommended|complete] [--platform SO] [--channel stable|nightly] [--release VERSÃO] [DESTINO]")
		return output.ExitUsage
	}
	con := newConsole(stdout, stderr, *noColor, *verbose)
	request := &installRequest{target: *target, platform: *platform, channel: *channel, release: *release, profile: *profile}
	if wizard {
		stop := trapInterrupt(stderr, "Operação cancelada. Nenhuma seleção pendente foi aplicada.")
		proceed, err := runInstallWizard(con, request, *catalogFile, *noColor, given["platform"], given["channel"])
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
	absolute, _ := filepath.Abs(request.target)
	result, err := install.Install(context.Background(), absolute, parsed, inventory, install.InstallOptions{
		Platform: request.platform, Channel: request.channel, Release: request.release,
		Profile: request.profile, Requested: request.requested,
	})
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

func runInstallWizard(con console, request *installRequest, catalogFile string, noColor, platformGiven, channelGiven bool) (bool, error) {
	ui := interactiveUI(noColor)
	if request.target == "" {
		chosen, err := chooseTarget(ui)
		if err != nil {
			return false, err
		}
		request.target = chosen
	}
	parsed, err := loadCatalog(catalogFile)
	if err != nil {
		return false, err
	}
	inventory, err := catalog.LoadInventory()
	if err != nil {
		return false, err
	}
	return installWizard(ui, con, parsed, inventory, request, platformGiven, channelGiven)
}

func init() {
	register("install", 40, "install              instala ezQuake e um perfil de conteúdo (pergunta o que faltar)", noName(runInstall))
}
