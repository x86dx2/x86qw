package main

import (
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/output"
)

// targetArgs are the arguments of commands that take only the
// installation and --json.
type targetArgs struct {
	target string
	json   bool
}

func (a *targetArgs) parser() *cliargs.Parser {
	p := cliargs.New()
	targetFlag(p, &a.target)
	jsonFlag(p, &a.json)
	return p
}

// parseTarget parses targetArgs and resolves the installation.
func parseTarget(name string, args []string, stdout, stderr io.Writer) (*targetArgs, string, int, bool) {
	var a targetArgs
	p := a.parser()
	if code, ok := parse(name, p, args, stdout, stderr); !ok {
		return nil, "", code, false
	}
	target, err := targetOf(p, a.target)
	if err != nil {
		return nil, "", usageFailure(name, err, stderr), false
	}
	return &a, target, output.ExitSuccess, true
}

func runVerify(args []string, stdout, stderr io.Writer) int {
	a, target, code, ok := parseTarget("verify", args, stdout, stderr)
	if !ok {
		return code
	}
	report, err := install.Verify(target)
	if err != nil {
		if a.json {
			output.Failure("verify", output.ExitFailure, "operation", err.Error()).Write(stdout)
		} else {
			fmt.Fprintf(stderr, "✗ %v\n", err)
		}
		return output.ExitFailure
	}
	if a.json {
		data := map[string]any{"target": target, "verified": true}
		if err := output.Success("verify", data).Write(stdout); err != nil {
			return output.ExitFailure
		}
		return output.ExitSuccess
	}
	fmt.Fprintf(stdout, "✓ ezQuake (%d) + %d components intact · %d files\n", report.Clients, len(report.Components), report.Files)
	return output.ExitSuccess
}

func init() {
	register(command{name: "verify", group: "Install and maintain", order: 50, synopsis: "[TARGET]",
		summary: "checks every managed file against the catalog",
		flags:   (&targetArgs{}).parser, run: noName(runVerify)})
}
