package main

import (
	"io"
	"path/filepath"
	"time"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/services"
	"github.com/x86dx2/x86qw/go/internal/setup"
)

func runStatus(args []string, stdout, stderr io.Writer) int {
	asJSON := false
	var rest []string
	for _, argument := range args {
		if argument == "--json" {
			asJSON = true
			continue
		}
		rest = append(rest, argument)
	}
	options, err := services.ParseStatus(rest)
	if err != nil {
		return serviceUsage("status", err, stdout, stderr)
	}
	if options.Target == "" {
		options.Target = install.DefaultTarget()
	}
	target, _ := filepath.Abs(setup.ExpandHome(options.Target))
	target = lock.Resolve(target)
	if asJSON {
		if options.Stop {
			return usageFailure("status", cliargs.Usagef("--json only reads; it does not go with --stop"), stderr)
		}
		if err := output.Success("status", install.ReadStatus(target)).Write(stdout); err != nil {
			return output.ExitFailure
		}
		return output.ExitSuccess
	}
	console := serviceConsole(&options.Common, stdout, stderr)
	active, err := services.ShowStatus(console, target)
	if err != nil {
		console.Error(err.Error())
		return output.ExitFailure
	}
	if !options.Stop {
		return output.ExitSuccess
	}
	if !active {
		console.Error("No x86QW service stack is running.")
		return output.ExitFailure
	}
	if proceed, code := confirm("Stop the running stack?", []string{"x86qw", "status", "--stop", target}, options.Yes, stdout, stderr); !proceed {
		return code
	}
	if err := services.RequestStop(target, 15*time.Second); err != nil {
		console.Error(err.Error())
		return output.ExitFailure
	}
	console.Success("Service stack stopped.")
	return output.ExitSuccess
}

func init() {
	register(command{name: "status", group: "Host", order: 40, synopsis: "[TARGET]",
		summary: "shows the running services; --stop stops them",
		about:   "Shows the servers, QTV and QWFWD that are running. With --json, the state of the installation and the sessions. With --stop, stops the stack; asks first and, without a terminal, needs --yes.",
		flags: func() *cliargs.Parser {
			p := services.StatusParser()
			jsonFlag(p, new(bool))
			return p
		}, run: noName(runStatus)})
}
