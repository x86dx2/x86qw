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
			return usageFailure("install", cliargs.Usagef("without the app, give --profile"), stderr)
		}
		return runApp(realBackend(a.catalogFile, a.platform), root, "", stdout, stderr)
	}
	if err := installProfile(root, a, stdout, stderr); err != nil {
		return failure(root, err, stderr)
	}
	fmt.Fprintln(stdout, "  Play now: x86qw play")
	return output.ExitSuccess
}

func init() {
	register(command{name: "install", group: "Install and maintain", order: 20, synopsis: "[TARGET]",
		summary: "installs ezQuake and a content profile",
		about:   "Like setup --profile, without making this CLI the installation's. Without --profile, on a terminal, opens the app's installer.",
		flags:   func() *cliargs.Parser { return (&setupArgs{}).parser(false) },
		run:     noName(runInstall)})
}
