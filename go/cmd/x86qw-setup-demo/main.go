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
	existing := flag.Bool("existing", false, "simulate an existing installation (management panel)")
	lang := flag.String("lang", "en", "en or pt")
	theme := flag.String("theme", "", "dark, light or empty to follow the terminal")
	speed := flag.Float64("speed", 1, "speed of the simulated install")
	start := flag.String("start", "", "open straight on match, servers or host (with --existing)")
	flag.Parse()
	err := tui.Run(tui.Config{
		Backend: &setup.Demo{ExistingInstall: *existing, Speed: *speed},
		Target:  tui.DefaultTarget(), Lang: tui.Lang(*lang), Demo: true, Theme: *theme, Start: *start,
	})
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
