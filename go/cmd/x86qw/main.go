package main

import (
	"fmt"
	"io"
	"os"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/buildinfo"
	"github.com/x86dx2/x86qw/go/internal/output"
)

// command is one CLI verb. Each command file registers itself in init so
// independent changes never touch this dispatcher.
type command struct {
	usage string // one-line help shown by `x86qw help`
	order int    // position in the help listing
	run   func(name string, args []string, stdout, stderr io.Writer) int
}

var commands = map[string]command{}

// register adds a command; aliases share its entry but are not listed twice.
func register(name string, order int, usage string, run func(name string, args []string, stdout, stderr io.Writer) int, aliases ...string) {
	commands[name] = command{usage: usage, order: order, run: run}
	for _, alias := range aliases {
		commands[alias] = command{order: -1, run: run}
	}
}

// noName adapts a handler that does not need the invoked name.
func noName(run func(args []string, stdout, stderr io.Writer) int) func(string, []string, io.Writer, io.Writer) int {
	return func(_ string, args []string, stdout, stderr io.Writer) int { return run(args, stdout, stderr) }
}

func usageText() string {
	type entry struct {
		name string
		command
	}
	var entries []entry
	for name, cmd := range commands {
		if cmd.order >= 0 {
			entries = append(entries, entry{name, cmd})
		}
	}
	sort.Slice(entries, func(a, b int) bool { return entries[a].order < entries[b].order })
	var builder strings.Builder
	fmt.Fprintf(&builder, "x86QW %s\nQuakeWorld moderno\n\nUso: x86qw <comando> [opções]\n\nComandos:\n", buildinfo.Version)
	for _, item := range entries {
		fmt.Fprintf(&builder, "  %s\n", item.usage)
	}
	return builder.String()
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout, stderr io.Writer) int {
	if len(args) == 0 {
		// Like `x86qw.sh` without arguments: the task-oriented main menu,
		// which prints the usage lines and exits 0 when stdin is not a TTY.
		return runMenuCommand(nil, stdout, stderr)
	}
	if args[0] == "-h" || args[0] == "--help" || args[0] == "help" {
		fmt.Fprint(stdout, usageText())
		return output.ExitSuccess
	}
	if args[0] == "--target" || args[0] == "-v" || args[0] == "--verbose" || args[0] == "--no-color" {
		// x86qw --target DIR opens the app on that installation.
		return runMenuCommand(args, stdout, stderr)
	}
	cmd, ok := commands[args[0]]
	if !ok {
		fmt.Fprintf(stderr, "x86qw: comando desconhecido: %s\n", args[0])
		fmt.Fprint(stderr, usageText())
		return output.ExitUsage
	}
	return cmd.run(args[0], args[1:], stdout, stderr)
}
