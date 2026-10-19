package main

import (
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
)

func runUninstall(args []string, stdout, stderr io.Writer) int {
	parsed, ok := parseArgs("uninstall", args, nil, []string{"--purge"}, nil, stderr)
	if !ok {
		return output.ExitUsage
	}
	purge := parsed.bools["--purge"]
	target, err := maint.ValidateTarget(parsed.target, !purge)
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	var report *maint.Report
	if purge {
		fmt.Fprintln(stdout, "\n· Desinstalação completa")
		report, err = maint.Purge(target)
	} else {
		fmt.Fprintln(stdout, "\n· Desinstalação")
		report, err = maint.Uninstall(target)
	}
	if report != nil {
		for _, warning := range report.Warnings {
			fmt.Fprintf(stderr, "! %s\n", warning)
		}
		for _, line := range report.Info {
			fmt.Fprintf(stdout, "· %s\n", line)
		}
		for _, line := range report.Success {
			fmt.Fprintf(stdout, "✓ %s\n", line)
		}
	}
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	return output.ExitSuccess
}

func init() {
	register("uninstall", 160, "uninstall [--purge]  remove o conteúdo gerenciado (ou tudo, com --purge)", noName(runUninstall))
}
