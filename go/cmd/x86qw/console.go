package main

import (
	"fmt"
	"io"
)

// console prints a command's progress: · info, ✓ done, ! warning and
// ✗ failure, colored only on a terminal.
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
	return console{out: stdout, err: stderr, color: colorFor(stdout, noColor), verbose: verbose}
}

func (c console) paint(text, code string) string {
	if !c.color {
		return text
	}
	return "\033[" + code + "m" + text + "\033[0m"
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

func (c console) failure(message string) {
	fmt.Fprintf(c.err, "%s %s\n", c.paint("✗", paletteError), message)
}

// failed reports an operation error and returns 1.
func (c console) failed(err error) int {
	c.failure(err.Error())
	return 1
}
