package main

import (
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/play"
	"github.com/x86dx2/x86qw/go/internal/services"
)

// serviceMain parses host, qtv and proxy and runs them; --menu opens the
// server screen instead.
func serviceMain(action string, args []string, stdout, stderr io.Writer) int {
	var op *services.Operation
	var common *services.Common
	switch action {
	case "host":
		games, err := play.LoadGames()
		if err != nil {
			fmt.Fprintf(stderr, "✗ %v\n", err)
			return output.ExitFailure
		}
		options, err := services.ParseHost(args, games)
		if err != nil {
			return serviceUsage("host", err, stderr)
		}
		op, common = &services.Operation{Action: "host", Host: options, Args: args}, &options.Common
	case "qtv":
		options, err := services.ParseQtv(args)
		if err != nil {
			return serviceUsage("qtv", err, stderr)
		}
		op, common = &services.Operation{Action: "qtv", Qtv: options, Args: args}, &options.Common
	default:
		options, err := services.ParseProxy(args)
		if err != nil {
			return serviceUsage("proxy", err, stderr)
		}
		op, common = &services.Operation{Action: "proxy", Proxy: options, Args: args}, &options.Common
	}
	if common.Menu && !common.BackgroundChild {
		// The server screen does what the host, QTV and proxy menus did.
		if !interactive() {
			fmt.Fprintf(stderr, "x86qw %s: sem terminal, informe as opções em vez de --menu\n", action)
			return output.ExitUsage
		}
		if common.Target == "" {
			common.Target = defaultTarget()
		}
		return runApp(realBackend("", install.HostPlatform()), appTarget(common.Target), "host", stdout, stderr)
	}
	return runService(op, common, stdout, stderr)
}
