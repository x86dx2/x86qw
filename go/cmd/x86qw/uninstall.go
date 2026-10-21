package main

import (
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
)

type uninstallArgs struct {
	target     string
	purge, yes bool
}

func (a *uninstallArgs) parser() *cliargs.Parser {
	p := cliargs.New()
	targetFlag(p, &a.target)
	p.Bool(&a.purge, "deletes the whole folder and the cache, personal data included", "--purge")
	p.Bool(&a.yes, "does not ask", "--yes")
	return p
}

func runUninstall(args []string, stdout, stderr io.Writer) int {
	var a uninstallArgs
	p := a.parser()
	if code, ok := parse("uninstall", p, args, stdout, stderr); !ok {
		return code
	}
	target, err := targetOf(p, a.target)
	if err != nil {
		return usageFailure("uninstall", err, stderr)
	}
	if target, err = maint.ValidateTarget(target, !a.purge); err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	question, retry := "Remove x86QW from "+target+"? Settings, PAKs and demos stay.", "x86qw uninstall "+target
	if a.purge {
		question, retry = "Delete all of "+target+", settings and demos included, and the x86QW cache?", "x86qw uninstall --purge "+target
	}
	if proceed, code := confirm(question, retry, a.yes, stdout, stderr); !proceed {
		return code
	}
	var report *maint.Report
	if a.purge {
		fmt.Fprintln(stdout, "\nFull uninstall")
		report, err = maint.Purge(target)
	} else {
		fmt.Fprintln(stdout, "\nUninstall")
		report, err = maint.Uninstall(target)
	}
	if report != nil {
		for _, warning := range report.Warnings {
			fmt.Fprintf(stderr, "! %s\n", warning)
		}
		for _, line := range report.Lines {
			fmt.Fprintln(stdout, line)
		}
	}
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	return output.ExitSuccess
}

func init() {
	register(command{name: "uninstall", group: "Install and maintain", order: 70, synopsis: "[TARGET]",
		summary: "removes the installation (with --purge, everything)",
		about:   "Removes what x86QW installed and keeps settings, PAKs, demos and files you edited. With --purge, deletes the whole folder and the cache. Asks first; without a terminal, needs --yes.",
		flags:   (&uninstallArgs{}).parser, run: noName(runUninstall)})
}
