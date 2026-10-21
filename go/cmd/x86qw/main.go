// Command x86qw is the x86QW command line and app: without a command it
// opens the app on a terminal; each command does one task.
package main

import (
	"errors"
	"fmt"
	"io"
	"os"
	"runtime"
	"sort"
	"strings"
	"unicode/utf8"

	"github.com/x86dx2/x86qw/go/internal/buildinfo"
	"github.com/x86dx2/x86qw/go/internal/cli"
	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/output"
)

// command is one CLI verb. Each command file registers itself in init so
// independent changes never touch this dispatcher.
type command struct {
	name     string
	group    string // section of `x86qw help`; empty keeps it out
	order    int    // position inside the section
	synopsis string // what follows "x86qw NAME" in the usage
	summary  string // its line in `x86qw help`
	about    string // the paragraph of its --help, when more than summary
	// flags builds the command's parser, for its --help.
	flags func() *cliargs.Parser
	run   func(name string, args []string, stdout, stderr io.Writer) int
}

var commands = map[string]*command{}

// groups are the sections of `x86qw help`, by task.
var groups = []string{"Jogar", "Hospedar", "Instalar e manter", "Seus dados", "Diagnóstico"}

// register adds a command; aliases run it but are not listed.
func register(c command, aliases ...string) {
	commands[c.name] = &c
	for _, alias := range aliases {
		copied := c
		copied.group = ""
		commands[alias] = &copied
	}
}

// noName adapts a handler that does not need the invoked name.
func noName(run func(args []string, stdout, stderr io.Writer) int) func(string, []string, io.Writer, io.Writer) int {
	return func(_ string, args []string, stdout, stderr io.Writer) int { return run(args, stdout, stderr) }
}

// listed are the commands of a section, in order.
func listed(group string) []*command {
	var entries []*command
	for name, cmd := range commands {
		if cmd.group == group && cmd.name == name {
			entries = append(entries, cmd)
		}
	}
	sort.Slice(entries, func(a, b int) bool { return entries[a].order < entries[b].order })
	return entries
}

func usageText() string {
	var builder strings.Builder
	fmt.Fprintf(&builder, "x86QW %s · QuakeWorld moderno\n\nUso: x86qw [COMANDO] [opções]\nSem comando, abre o app.\n", buildinfo.Version)
	width := 0
	for _, group := range groups {
		for _, cmd := range listed(group) {
			width = max(width, utf8.RuneCountInString(strings.TrimSpace(cmd.name+" "+cmd.synopsis)))
		}
	}
	for _, group := range groups {
		fmt.Fprintf(&builder, "\n%s\n", group)
		for _, cmd := range listed(group) {
			left := strings.TrimSpace(cmd.name + " " + cmd.synopsis)
			fmt.Fprintf(&builder, "  %s%s  %s\n", left, strings.Repeat(" ", width-utf8.RuneCountInString(left)), cmd.summary)
		}
	}
	builder.WriteString("\nDetalhes de um comando: x86qw COMANDO --help\n")
	return builder.String()
}

// commandHelp is the --help of one command.
func commandHelp(name string) string {
	cmd := commands[name]
	var builder strings.Builder
	fmt.Fprintf(&builder, "Uso: %s\n\n", strings.TrimSpace("x86qw "+cmd.name+" "+cmd.synopsis))
	about := cmd.about
	if about == "" {
		about = strings.ToUpper(cmd.summary[:1]) + cmd.summary[1:] + "."
	}
	fmt.Fprintf(&builder, "%s\n", about)
	if cmd.flags != nil {
		if flags := cmd.flags().Flags(); flags != "" {
			fmt.Fprintf(&builder, "\nOpções:\n%s", flags)
		}
	}
	return builder.String()
}

// parse reads args into p for the command name. On --help it prints the
// help and returns 0, on a mistake the message and 2; ok is false then.
func parse(name string, p *cliargs.Parser, args []string, stdout, stderr io.Writer) (code int, ok bool) {
	err := p.Parse(args)
	switch {
	case err == nil:
		return output.ExitSuccess, true
	case errors.Is(err, cliargs.ErrHelp):
		fmt.Fprint(stdout, commandHelp(name))
		return output.ExitSuccess, false
	}
	return usageFailure(name, err, stderr), false
}

// usageFailure reports a mistake in the command line and returns 2.
func usageFailure(name string, err error, stderr io.Writer) int {
	fmt.Fprintf(stderr, "x86qw %s: erro: %v\n", name, err)
	fmt.Fprintf(stderr, "Veja: x86qw %s --help\n", name)
	return output.ExitUsage
}

func main() {
	if runtime.GOOS == "windows" {
		cli.TidyAside()
	}
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout, stderr io.Writer) int {
	if len(args) == 0 || strings.HasPrefix(args[0], "-") && args[0] != "-h" && args[0] != "--help" && args[0] != "--version" {
		// No command: the app, on the installation --target names.
		return runMenuCommand(args, stdout, stderr)
	}
	if args[0] == "-h" || args[0] == "--help" || args[0] == "help" {
		if len(args) > 1 && args[0] == "help" {
			if cmd, ok := commands[args[1]]; ok && cmd.group != "" {
				fmt.Fprint(stdout, commandHelp(cmd.name))
				return output.ExitSuccess
			}
			fmt.Fprintf(stderr, "x86qw help: comando desconhecido: %s\n", args[1])
			return output.ExitUsage
		}
		fmt.Fprint(stdout, usageText())
		return output.ExitSuccess
	}
	cmd, ok := commands[args[0]]
	if !ok {
		fmt.Fprintf(stderr, "x86qw: comando desconhecido: %s\n", args[0])
		fmt.Fprintln(stderr, "Veja: x86qw help")
		return output.ExitUsage
	}
	return cmd.run(args[0], args[1:], stdout, stderr)
}
