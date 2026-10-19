package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
)

func expandHome(path string) string {
	if strings.HasPrefix(path, "~") {
		if home, err := os.UserHomeDir(); err == nil {
			return home + path[1:]
		}
	}
	return path
}

func runUI(args []string, stdout, stderr io.Writer) int {
	parsed, ok := parseArgs("ui", args, []string{"--output"}, nil, nil, stderr)
	if !ok {
		return output.ExitUsage
	}
	destination := parsed.values["--output"]
	if destination == "" {
		file, err := os.CreateTemp("", "x86qw-ui-*.html")
		if err != nil {
			fmt.Fprintf(stderr, "✗ %v\n", err)
			return output.ExitFailure
		}
		destination = file.Name()
		file.Close()
		os.Remove(destination)
	}
	written, err := maint.WriteLocalUI(parsed.target, destination)
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	fmt.Fprintln(stdout, filepath.ToSlash(written))
	return output.ExitSuccess
}

func init() {
	register("ui", 130, "ui                   painel HTML local somente leitura", noName(runUI))
}
