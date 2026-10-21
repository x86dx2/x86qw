package main

import (
	"bufio"
	"bytes"
	"errors"
	"fmt"
	"io"
	"os"
	"runtime"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/buildinfo"
	"github.com/x86dx2/x86qw/go/internal/cli"
	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/setup"
	"github.com/x86dx2/x86qw/go/internal/setup/tui"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

// appOpen is set while the interface owns the terminal: a command it runs
// in-process must never open a second one.
var appOpen bool

// interactive is whether the interface can take the terminal.
func interactive() bool { return !appOpen && stdinIsTerminal() && isTerminal(os.Stdout) }

// realBackend is the interface's backend for this command line: actions
// that are x86qw commands run through the dispatcher, their output kept
// for the error they may end with.
func realBackend(catalogFile, platform string) *setup.Real {
	return &setup.Real{
		Trust: trust.Options{CacheDir: defaultCacheDir()}, CatalogFile: catalogFile, Platform: platform,
		// The interface owns the screen: what publishing prints is dropped.
		Publish:   func(target string) error { return publishCLI(target, false, io.Discard) },
		Integrate: setup.Integrate,
		// Services the app starts always run with --background, so none
		// writes to the terminal the interface owns; ezQuake starts with
		// its output discarded.
		Run: func(args []string) error {
			var buffer bytes.Buffer
			if code := run(args, &buffer, &buffer); code != output.ExitSuccess {
				return errors.New(lastLines(buffer.String(), 3))
			}
			return nil
		},
	}
}

// publishCLI installs this executable as the installation's CLI (the same
// version too) and, with link, links the user command or creates the
// Windows shortcuts, saying where on out.
func publishCLI(target string, link bool, out io.Writer) error {
	command, err := cli.Publish(target, buildinfo.Version, true, link)
	switch {
	case err != nil:
		return err
	case command != "" && runtime.GOOS == "windows":
		fmt.Fprintf(out, "Atalhos do x86QW criados no Menu Iniciar e na Área de Trabalho (%s).\n", command)
	case command != "":
		fmt.Fprintf(out, "Comando x86qw disponível em %s\n", command)
	}
	return nil
}

// lastLines keeps the end of a command's output, where its error is.
func lastLines(text string, count int) string {
	var lines []string
	for _, line := range strings.Split(strings.TrimSpace(text), "\n") {
		if line = strings.TrimSpace(strings.TrimLeft(line, "✗!· ")); line != "" && !strings.HasPrefix(line, "Veja: x86qw") {
			lines = append(lines, line)
		}
	}
	if len(lines) > count {
		lines = lines[len(lines)-count:]
	}
	if len(lines) == 0 {
		return "o comando falhou"
	}
	return strings.Join(lines, " · ")
}

// runApp opens the interface on root, at start ("" is home), and opens it
// again after each report it hands to the command line.
func runApp(backend *setup.Real, root, start string, stdout, stderr io.Writer) int {
	if install.Installed(root) && cli.Replaces(buildinfo.Version, root, false) {
		// A newer CLI opening the app publishes itself, as re-running the
		// bootstrap always did, linked unless the setup said otherwise;
		// an older or development build leaves the installed one alone.
		// A running server holds the lock: then the CLI stays as it is.
		if held, err := lock.Acquire(root, "install"); err == nil {
			link := true
			if choices, _ := setup.LoadChoices(root); choices != nil {
				link = choices.Integration.Path
			}
			err = publishCLI(root, link, io.Discard)
			held.Release()
			if err != nil {
				fmt.Fprintf(stderr, "! A CLI nativa não pôde ser publicada: %v\n", err)
			}
		}
	}
	notice := ""
	for {
		appOpen = true
		result, err := tui.Run(tui.Config{Backend: backend, Target: root, Lang: interfaceLanguage(), Start: start, Notice: notice})
		appOpen = false
		if err != nil {
			fmt.Fprintf(stderr, "✗ %v\n", err)
			return output.ExitFailure
		}
		if result.Next == nil {
			return output.ExitSuccess
		}
		code := run(result.Next, stdout, stderr)
		if !result.Back {
			return code
		}
		back := "Enter volta ao x86QW…"
		if interfaceLanguage() == tui.EN {
			back = "Enter returns to x86QW…"
		}
		fmt.Fprint(stdout, "\n"+back)
		if _, err := bufio.NewReader(stdin).ReadString('\n'); err != nil {
			return code
		}
		start, notice = "", ""
	}
}

// interfaceLanguage follows the locale: Portuguese unless it says otherwise.
func interfaceLanguage() tui.Lang {
	for _, name := range []string{"LC_ALL", "LC_MESSAGES", "LANG"} {
		if value := os.Getenv(name); value != "" && value != "C" && value != "POSIX" {
			if strings.HasPrefix(strings.ToLower(value), "pt") {
				return tui.PT
			}
			return tui.EN
		}
	}
	return tui.PT
}

type appArgs struct {
	target           string
	verbose, noColor bool
}

func (a *appArgs) parser() *cliargs.Parser {
	p := cliargs.New()
	targetFlag(p, &a.target)
	p.Bool(&a.verbose, "", "-v", "--verbose")
	p.Bool(&a.noColor, "", "--no-color")
	p.Hidden("--verbose")
	p.Hidden("--no-color")
	return p
}

// runMenuCommand is `x86qw` without a command: the app on a terminal,
// the usage otherwise.
func runMenuCommand(args []string, stdout, stderr io.Writer) int {
	var a appArgs
	p := a.parser()
	if err := p.Parse(args); err != nil {
		if errors.Is(err, cliargs.ErrHelp) {
			fmt.Fprint(stdout, usageText())
			return output.ExitSuccess
		}
		fmt.Fprintf(stderr, "x86qw: erro: %v\nVeja: x86qw help\n", err)
		return output.ExitUsage
	}
	if len(p.Positional()) > 0 {
		fmt.Fprintf(stderr, "x86qw: erro: argumentos a mais: %s\nVeja: x86qw help\n", strings.Join(p.Positional(), " "))
		return output.ExitUsage
	}
	if !interactive() {
		fmt.Fprint(stdout, usageText())
		return output.ExitSuccess
	}
	root, err := targetOf(p, a.target)
	if err != nil {
		fmt.Fprintf(stderr, "x86qw: erro: %v\n", err)
		return output.ExitUsage
	}
	return runApp(realBackend("", install.HostPlatform()), root, "", stdout, stderr)
}

func init() {
	register(command{name: "menu", run: noName(runMenuCommand)})
}
