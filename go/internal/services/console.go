package services

import (
	"fmt"
	"io"
)

// Console mirrors the x86QW console prefixes ([INFO], [OK], [ATENÇÃO], ✗).
type Console struct {
	Out, Err io.Writer
	Verbose  bool
	Color    bool
}

func (c *Console) paint(text, code string) string {
	if !c.Color {
		return text
	}
	return "\x1b[" + code + "m" + text + "\x1b[0m"
}

// Banner prints the action heading.
func (c *Console) Banner(action, target string) {
	fmt.Fprintf(c.Out, "%s %s\n  %s\n", c.paint("x86QW ·", "1;36"), action, target)
}

// Section prints a section title.
func (c *Console) Section(title string) { fmt.Fprintf(c.Out, "\n%s\n", c.paint(title, "1;36")) }

// Info prints an informational line.
func (c *Console) Info(message string) {
	fmt.Fprintf(c.Out, "%s %s\n", c.paint("[INFO]", "36"), message)
}

// Success prints a success line.
func (c *Console) Success(message string) {
	fmt.Fprintf(c.Out, "%s %s\n", c.paint("[OK]", "32"), message)
}

// Warning prints a warning line.
func (c *Console) Warning(message string) {
	fmt.Fprintf(c.Out, "%s %s\n", c.paint("[ATENÇÃO]", "33"), message)
}

// Detail prints a verbose-only line.
func (c *Console) Detail(message string) {
	if c.Verbose {
		fmt.Fprintln(c.Out, c.paint("       "+message, "2"))
	}
}

// Error prints an error on stderr.
func (c *Console) Error(message string) { fmt.Fprintf(c.Err, "✗ %s\n", message) }
