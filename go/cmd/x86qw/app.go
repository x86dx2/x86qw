package main

import (
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
		// The tools print into the app's floating panel, never on the
		// terminal it owns.
		Capture: func(args []string) (string, error) {
			var buffer bytes.Buffer
			if code := run(args, &buffer, &buffer); code != output.ExitSuccess {
				return buffer.String(), errors.New(lastLines(buffer.String(), 3))
			}
			return buffer.String(), nil
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
		fmt.Fprintf(out, "x86QW shortcuts created in the Start Menu and on the Desktop (%s); the x86qw command works in a new terminal.\n", command)
	case command != "":
		fmt.Fprintf(out, "x86qw command available at %s\n", command)
	}
	retireLegacy(target, out)
	return nil
}

// lastLines keeps the end of a command's output, where its error is.
func lastLines(text string, count int) string {
	var lines []string
	for _, line := range strings.Split(strings.TrimSpace(text), "\n") {
		if line = strings.TrimSpace(strings.TrimLeft(line, "✗!· ")); line != "" && !strings.HasPrefix(line, "See: x86qw") {
			lines = append(lines, line)
		}
	}
	if len(lines) > count {
		lines = lines[len(lines)-count:]
	}
	if len(lines) == 0 {
		return "the command failed"
	}
	return strings.Join(lines, " · ")
}

// runApp opens the interface on root, at start ("" is home).
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
				fmt.Fprintf(stderr, "! The native CLI could not be published: %v\n", err)
			}
		}
	}
	// Everything, the tools included, runs inside the app.
	appOpen = true
	err := tui.Run(tui.Config{Backend: backend, Target: root, Lang: interfaceLanguage(), Start: start})
	appOpen = false
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	return output.ExitSuccess
}

// interfaceLanguage follows the locale: English unless it is Portuguese.
// Windows keeps the language in its settings, not in LANG.
func interfaceLanguage() tui.Lang {
	for _, name := range []string{"LC_ALL", "LC_MESSAGES", "LANG"} {
		if value := os.Getenv(name); value != "" && value != "C" && value != "POSIX" {
			return languageOf(value)
		}
	}
	return languageOf(systemLanguage())
}

// languageOf is the interface language for a locale such as pt_BR.UTF-8.
func languageOf(locale string) tui.Lang {
	if strings.HasPrefix(strings.ToLower(locale), "pt") {
		return tui.PT
	}
	return tui.EN
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
		fmt.Fprintf(stderr, "x86qw: error: %v\nSee: x86qw help\n", err)
		return output.ExitUsage
	}
	if len(p.Positional()) > 0 {
		fmt.Fprintf(stderr, "x86qw: error: unexpected arguments: %s\nSee: x86qw help\n", strings.Join(p.Positional(), " "))
		return output.ExitUsage
	}
	if !interactive() {
		fmt.Fprint(stdout, usageText())
		return output.ExitSuccess
	}
	root, err := targetOf(p, a.target)
	if err != nil {
		fmt.Fprintf(stderr, "x86qw: error: %v\n", err)
		return output.ExitUsage
	}
	return runApp(realBackend("", install.HostPlatform()), root, "", stdout, stderr)
}

func init() {
	register(command{name: "menu", run: noName(runMenuCommand)})
}
