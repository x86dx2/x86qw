package main

import (
	"bufio"
	"bytes"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/buildinfo"
	"github.com/x86dx2/x86qw/go/internal/cli"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/play"
	"github.com/x86dx2/x86qw/go/internal/setup"
	"github.com/x86dx2/x86qw/go/internal/setup/tui"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

const (
	commandList = "Comandos: play, open, host, proxy, qtv, status, hub, setup, update, upgrade, verify, doctor, ui, profile, library, changes, migrate, repair, cleanup, uninstall e version."
)

// stdinIsTerminal is whether someone can answer (replaced by the tests).
var stdinIsTerminal = func() bool { return isTerminalFile(os.Stdin) }

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
		Publish:   func(target string) error { return publishCLI(target, false, io.Discard, io.Discard) },
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

// lastLines keeps the end of a command's output, where its error is.
func lastLines(text string, count int) string {
	var lines []string
	for _, line := range strings.Split(strings.TrimSpace(text), "\n") {
		if line = strings.TrimSpace(strings.TrimLeft(line, "✗!· ")); line != "" {
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

// appTarget is the installation the app opens: the one holding this
// executable, else the default folder, where the installer starts.
func appTarget(target string) string {
	root, _ := filepath.Abs(target)
	if hasInstallation(root) {
		return root
	}
	if target == "." || target == "" {
		return tui.DefaultTarget()
	}
	return root
}

// runApp opens the interface on root, at start ("" is home), and opens it
// again after each report it hands to the command line.
func runApp(backend *setup.Real, root, start string, stdout, stderr io.Writer) int {
	if hasInstallation(root) && cli.Replaces(buildinfo.Version, root, false) {
		// A newer CLI opening the app publishes itself, as re-running the
		// bootstrap always did, linked unless the setup said otherwise;
		// an older or development build leaves the installed one alone.
		// A running server holds the lock: then the CLI stays as it is.
		if held, err := lock.Acquire(root, "install"); err == nil {
			link := true
			if choices, _ := setup.LoadChoices(root); choices != nil {
				link = choices.Integration.Path
			}
			err = publishCLI(root, link, io.Discard, io.Discard)
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
		fmt.Fprint(stdout, "\nEnter volta ao x86QW…")
		if _, err := bufio.NewReader(os.Stdin).ReadString('\n'); err != nil {
			return code
		}
		start, notice = "", ""
	}
}

// runMenuCommand is `x86qw` without a command: the app on a terminal,
// the usage otherwise.
func runMenuCommand(args []string, stdout, stderr io.Writer) int {
	target := defaultTarget()
	for _, arg := range args {
		if !strings.HasPrefix(arg, "-") {
			target = arg
		}
	}
	if !interactive() {
		launcher := play.Launcher()
		fmt.Fprintf(stdout, "\nUso: %s <comando> [opções]\n", launcher)
		fmt.Fprintf(stdout, "Exemplo: %s play\n", launcher)
		fmt.Fprintln(stdout, commandList)
		return output.ExitSuccess
	}
	return runApp(realBackend("", install.HostPlatform()), appTarget(target), "", stdout, stderr)
}

func init() {
	register("menu", -1, "", noName(runMenuCommand))
}
