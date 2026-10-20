// Command x86qw-setup-demo runs the installer interface against the demo
// backend: the real catalog and network, a simulated install.
package main

import (
	"flag"
	"fmt"
	"os"

	"github.com/x86dx2/x86qw/go/internal/setup"
	"github.com/x86dx2/x86qw/go/internal/setup/tui"
)

func main() {
	existing := flag.Bool("existing", false, "simular uma instalação existente (painel de gestão)")
	lang := flag.String("lang", "pt", "pt ou en")
	theme := flag.String("theme", "", "dark, light ou vazio para seguir o terminal")
	speed := flag.Float64("speed", 1, "velocidade da instalação simulada")
	start := flag.String("start", "", "abrir direto em match, servers ou host (com --existing)")
	flag.Parse()
	_, err := tui.Run(tui.Config{
		Backend: &setup.Demo{ExistingInstall: *existing, Speed: *speed},
		Target:  tui.DefaultTarget(), Lang: tui.Lang(*lang), Demo: true, Theme: *theme, Start: *start,
	})
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
