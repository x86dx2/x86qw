package main

import (
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/play"
	"github.com/x86dx2/x86qw/go/internal/services"
)

// serviceConsole builds the console for a service command.
func serviceConsole(common *services.Common, stdout, stderr io.Writer) *services.Console {
	if common.Target == "" {
		common.Target = install.DefaultTarget()
	}
	return &services.Console{Out: stdout, Err: stderr, Verbose: common.Verbose, Color: colorFor(stdout, common.NoColor)}
}

// runService runs a parsed service operation.
func runService(op *services.Operation, common *services.Common, stdout, stderr io.Writer) int {
	op.Console = serviceConsole(common, stdout, stderr)
	op.Stdin = os.Stdin
	op.Output = os.Stdout
	if file, ok := stdout.(*os.File); ok {
		op.Output = file
	}
	return op.Run()
}

// serviceUsage handles what a service parser returned instead of options:
// the help, a usage mistake (2) or a failure (1).
func serviceUsage(name string, err error, stdout, stderr io.Writer) int {
	switch {
	case errors.Is(err, cliargs.ErrHelp):
		fmt.Fprint(stdout, commandHelp(name))
		return output.ExitSuccess
	case services.IsUsage(err):
		return usageFailure(name, err, stderr)
	}
	fmt.Fprintf(stderr, "✗ %v\n", err)
	return output.ExitFailure
}

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
			return serviceUsage("host", err, stdout, stderr)
		}
		op, common = &services.Operation{Action: "host", Host: options, Args: args}, &options.Common
	case "qtv":
		options, err := services.ParseQtv(args)
		if err != nil {
			return serviceUsage("qtv", err, stdout, stderr)
		}
		op, common = &services.Operation{Action: "qtv", Qtv: options, Args: args}, &options.Common
	default:
		options, err := services.ParseProxy(args)
		if err != nil {
			return serviceUsage("proxy", err, stdout, stderr)
		}
		op, common = &services.Operation{Action: "proxy", Proxy: options, Args: args}, &options.Common
	}
	if common.Menu && !common.BackgroundChild {
		// The server screen does what the host, QTV and proxy menus did.
		if !interactive() {
			return usageFailure(action, cliargs.Usagef("--menu precisa de um terminal; informe as opções"), stderr)
		}
		if common.Target == "" {
			common.Target = install.DefaultTarget()
		}
		root, _ := filepath.Abs(expandHome(common.Target))
		return runApp(realBackend("", install.HostPlatform()), root, "host", stdout, stderr)
	}
	return runService(op, common, stdout, stderr)
}

func init() {
	register(command{name: "host", group: "Hospedar", order: 10, synopsis: "[JOGO] [opções]",
		summary: "hospeda um servidor MVDSV (KTX pede --mode)",
		about: "Hospeda um servidor MVDSV. KTX, o padrão, pede --mode; --instance acrescenta mais servidores à mesma sessão, " +
			"--with-qtv e --with-proxy sobem o QTV e o QWFWD junto. Sem opções num terminal, use o app: x86qw host --menu.",
		flags: services.HostParser,
		run: func(_ string, args []string, stdout, stderr io.Writer) int {
			return serviceMain("host", args, stdout, stderr)
		}})
	register(command{name: "qtv", group: "Hospedar", order: 20, summary: "inicia o relay QTV de um servidor",
		flags: services.QtvParser,
		run: func(_ string, args []string, stdout, stderr io.Writer) int {
			return serviceMain("qtv", args, stdout, stderr)
		}})
	register(command{name: "proxy", group: "Hospedar", order: 30, summary: "inicia o proxy QWFWD",
		flags: services.ProxyParser,
		run: func(_ string, args []string, stdout, stderr io.Writer) int {
			return serviceMain("proxy", args, stdout, stderr)
		}})
}
