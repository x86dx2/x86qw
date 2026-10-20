package main

import (
	"fmt"
	"io"
	"os"

	"golang.org/x/term"

	"github.com/x86dx2/x86qw/go/internal/menu"
)

// console mirrors the Console reporter of x86qw_runtime/ui/console.py for
// the interactive flows: ✓/·/!/✗ prefixes, colored only on a terminal.
type console struct {
	out, err io.Writer
	color    bool
	verbose  bool
}

const (
	paletteAccent  = "38;2;255;77;77"
	paletteMuted   = "38;2;90;100;128"
	paletteSuccess = "38;2;0;229;204"
	paletteWarning = "38;2;255;176;32"
	paletteError   = "38;2;230;57;70"
)

func newConsole(stdout, stderr io.Writer, noColor, verbose bool) console {
	_, noColorEnv := os.LookupEnv("NO_COLOR")
	color := false
	if file, ok := stdout.(*os.File); ok {
		color = term.IsTerminal(int(file.Fd())) && !noColor && !noColorEnv
	}
	return console{out: stdout, err: stderr, color: color, verbose: verbose}
}

func (c console) paint(text, code string) string {
	if !c.color {
		return text
	}
	return "\033[" + code + "m" + text + "\033[0m"
}

func (c console) section(title string) {
	fmt.Fprintf(c.out, "\n%s\n", c.paint(title, paletteAccent+"m\033[1"))
}

func (c console) info(message string) {
	fmt.Fprintf(c.out, "%s %s\n", c.paint("·", paletteMuted), message)
}

func (c console) success(message string) {
	fmt.Fprintf(c.out, "%s %s\n", c.paint("✓", paletteSuccess), message)
}

func (c console) warning(message string) {
	fmt.Fprintf(c.out, "%s %s\n", c.paint("!", paletteWarning), message)
}

// failure mirrors console.error: "[ERRO]" only when stderr is a terminal too.
func (c console) failure(message string) {
	label := "✗"
	if file, ok := c.err.(*os.File); ok && c.color && term.IsTerminal(int(file.Fd())) {
		label = c.paint("[ERRO]", paletteError)
	}
	fmt.Fprintf(c.err, "%s %s\n", label, message)
}

// failed reports an operation error with the --verbose hint and returns 1.
func (c console) failed(err error) int {
	c.failure(err.Error())
	if !c.verbose {
		fmt.Fprintln(c.err, "       Execute novamente com --verbose para obter detalhes técnicos.")
	}
	return 1
}

func isTerminalFile(file *os.File) bool { return term.IsTerminal(int(file.Fd())) }

// sessionUI is the one menu.UI of this process: every picker, including the
// ones reached from the main menu, must share its buffered stdin.
var sessionUI *menu.UI

func interactiveUI(noColor bool) *menu.UI {
	if sessionUI == nil {
		sessionUI = menu.Stdio(noColor)
	}
	return sessionUI
}
