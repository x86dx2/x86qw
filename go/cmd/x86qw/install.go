package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"path/filepath"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/output"
)

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

func init() {
	register("install", 40, "install              instala ezQuake e um perfil de conteúdo em um diretório novo", noName(runInstall))
}
