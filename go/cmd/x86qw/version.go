package main

import (
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/buildinfo"
	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/output"
)

type versionArgs struct{ json bool }

func (a *versionArgs) parser() *cliargs.Parser {
	p := cliargs.New()
	jsonFlag(p, &a.json)
	return p
}

func runVersion(args []string, stdout, stderr io.Writer) int {
	var a versionArgs
	p := a.parser()
	if code, ok := parse("version", p, args, stdout, stderr); !ok {
		return code
	}
	if len(p.Positional()) > 0 {
		return usageFailure("version", cliargs.Usagef("unexpected arguments: %v", p.Positional()), stderr)
	}
	if a.json {
		data := map[string]any{"project": "x86qw", "version": buildinfo.Version}
		if err := output.Success("version", data).Write(stdout); err != nil {
			return output.ExitFailure
		}
		return output.ExitSuccess
	}
	fmt.Fprintf(stdout, "x86QW %s\n", buildinfo.Version)
	return output.ExitSuccess
}

func init() {
	register(command{name: "version", group: "Diagnostics", order: 40, summary: "shows the version",
		flags: (&versionArgs{}).parser, run: noName(runVersion)}, "--version")
}
