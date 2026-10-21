package main

import (
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/output"
)

func runInstall(args []string, stdout, stderr io.Writer) int {
	a, root, code, ok := parseSetup("install", args, stdout, stderr)
	if !ok {
		return code
	}
	if a.profile == "" {
		// What is missing is chosen in the installer.
		if a.nonInteractive || !interactive() {
			return usageFailure("install", cliargs.Usagef("sem o app, informe --profile"), stderr)
		}
		return runApp(realBackend(a.catalogFile, a.platform), root, "", stdout, stderr)
	}
	if err := installProfile(root, a, stdout, stderr); err != nil {
		return failure(root, err, stderr)
	}
	fmt.Fprintln(stdout, "  Jogar agora: x86qw play")
	return output.ExitSuccess
}

func init() {
	register(command{name: "install", group: "Instalar e manter", order: 20, synopsis: "[DESTINO]",
		summary: "instala ezQuake e um perfil de conteúdo",
		about:   "Como setup --profile, sem tornar esta CLI a da instalação. Sem --profile, num terminal, abre o instalador do app.",
		flags:   func() *cliargs.Parser { return (&setupArgs{}).parser(false) },
		run:     noName(runInstall)})
}
