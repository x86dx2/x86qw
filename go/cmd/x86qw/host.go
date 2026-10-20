package main

import (
	"fmt"
	"io"
	"os"

	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/services"
)

// serviceConsole builds the console for a service command.
func serviceConsole(common *services.Common, stdout, stderr io.Writer) *services.Console {
	if common.Target == "" {
		common.Target = defaultTarget()
	}
	color := !common.NoColor && os.Getenv("NO_COLOR") == ""
	if file, ok := stdout.(*os.File); !ok || !isTerminal(file) {
		color = false
	}
	return &services.Console{Out: stdout, Err: stderr, Verbose: common.Verbose, Color: color}
}

func isTerminal(file *os.File) bool {
	info, err := file.Stat()
	return err == nil && info.Mode()&os.ModeCharDevice != 0
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

func serviceUsage(name string, err error, stderr io.Writer) int {
	fmt.Fprintf(stderr, "x86qw %s: erro: %v\n", name, err)
	if services.IsUsage(err) {
		return output.ExitUsage
	}
	return output.ExitFailure
}

func runHost(args []string, stdout, stderr io.Writer) int {
	code, _ := serviceMain("host", args, stdout, stderr, false)
	return code
}

func init() {
	register("host", 75, "host JOGO --map MAPA hospeda um servidor MVDSV (KTX exige --mode; --with-qtv, --with-proxy)", noName(runHost))
}
