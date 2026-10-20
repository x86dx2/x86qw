// Command x86qw-content is the native port of maintenance/manage.py: it
// checks, updates, extends, verifies, builds and publishes the x86QW
// content distribution, and regenerates the public product facts.
//
// Exit codes follow manage.py: 0 success, 1 operational error ("[ERRO] ..."
// on stderr), 2 usage error or, for check, pending updates.
package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/content"
)

const (
	exitOK    = 0
	exitError = 1
	exitUsage = 2
)

type command struct {
	help string
	run  func(env *environment, args []string) (int, error)
}

var commands = map[string]command{}

func register(name, help string, run func(env *environment, args []string) (int, error)) {
	commands[name] = command{help: help, run: run}
}

// environment is what every subcommand receives.
type environment struct {
	project *content.Project
	stdout  io.Writer
	stderr  io.Writer
	stdin   *os.File
}

func usage(stderr io.Writer) {
	var names []string
	for name := range commands {
		names = append(names, name)
	}
	sort.Strings(names)
	fmt.Fprintf(stderr, "usage: x86qw-content [--root DIR] [--verbose] {%s} ...\n\n", strings.Join(names, ","))
	fmt.Fprintln(stderr, "Fonte unica para verificar, atualizar, ampliar, montar e publicar o dist x86QW.")
	for _, name := range names {
		fmt.Fprintf(stderr, "  %-8s %s\n", name, commands[name].help)
	}
}

// findRoot walks up from dir to the checkout holding the inventories.
func findRoot(dir string) (string, error) {
	for {
		if _, err := os.Stat(filepath.Join(dir, content.ComponentsPath)); err == nil {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", errors.New("checkout x86QW não encontrado; use --root")
		}
		dir = parent
	}
}

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout, stderr io.Writer) int {
	root := ""
	verbose := false
	for len(args) > 0 && strings.HasPrefix(args[0], "-") {
		switch {
		case args[0] == "--verbose":
			verbose = true
			args = args[1:]
		case args[0] == "--root" && len(args) > 1:
			root, args = args[1], args[2:]
		case strings.HasPrefix(args[0], "--root="):
			root, args = strings.TrimPrefix(args[0], "--root="), args[1:]
		case args[0] == "-h" || args[0] == "--help":
			usage(stdout)
			return exitOK
		default:
			usage(stderr)
			fmt.Fprintf(stderr, "x86qw-content: error: unrecognized arguments: %s\n", args[0])
			return exitUsage
		}
	}
	if len(args) == 0 {
		usage(stderr)
		fmt.Fprintln(stderr, "x86qw-content: error: the following arguments are required: command")
		return exitUsage
	}
	cmd, ok := commands[args[0]]
	if !ok {
		usage(stderr)
		fmt.Fprintf(stderr, "x86qw-content: error: argument command: invalid choice: '%s'\n", args[0])
		return exitUsage
	}
	if root == "" {
		cwd, _ := os.Getwd()
		found, err := findRoot(cwd)
		if err != nil {
			fmt.Fprintf(stderr, "[ERRO] %v\n", err)
			return exitError
		}
		root = found
	}
	absolute, err := filepath.Abs(root)
	if err != nil {
		fmt.Fprintf(stderr, "[ERRO] %v\n", err)
		return exitError
	}
	env := &environment{project: content.NewProject(absolute), stdout: stdout, stderr: stderr, stdin: os.Stdin}
	code, err := cmd.run(env, args[1:])
	if err != nil {
		var usageErr *usageError
		if errors.As(err, &usageErr) {
			if usageErr.help {
				return exitOK
			}
			fmt.Fprintf(stderr, "x86qw-content %s: error: %s\n", args[0], usageErr.msg)
			return exitUsage
		}
		fmt.Fprintf(stderr, "[ERRO] %v\n", err)
		if verbose {
			fmt.Fprintf(stderr, "%+v\n", err)
		}
		return exitError
	}
	return code
}

type usageError struct {
	msg  string
	help bool
}

func (e *usageError) Error() string { return e.msg }

// parseFlags parses subcommand flags, mapping errors to usage errors.
func parseFlags(name string, flags *flag.FlagSet, args []string, stderr io.Writer) error {
	flags.SetOutput(stderr)
	flags.Usage = func() {
		fmt.Fprintf(stderr, "usage: x86qw-content %s [options]\n", name)
		flags.PrintDefaults()
	}
	if err := flags.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return &usageError{help: true}
		}
		return &usageError{msg: err.Error()}
	}
	return nil
}
