package main

import (
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
)

type cleanupArgs struct {
	target              string
	downloads, personal bool
}

func (a *cleanupArgs) parser() *cliargs.Parser {
	p := cliargs.New()
	targetFlag(p, &a.target)
	p.Bool(&a.downloads, "also the maps downloaded from servers", "--downloads")
	p.Bool(&a.personal, "also history, logs and demos", "--personal-data")
	return p
}

func runCleanup(args []string, stdout, stderr io.Writer) int {
	var a cleanupArgs
	p := a.parser()
	if code, ok := parse("cleanup", p, args, stdout, stderr); !ok {
		return code
	}
	target, err := targetOf(p, a.target)
	if err != nil {
		return usageFailure("cleanup", err, stderr)
	}
	fail := func(err error) int {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	if target, err = maint.ValidateTarget(target, true); err != nil {
		return fail(err)
	}
	if err := maint.RequireManagedIdentity(target, "cleanup"); err != nil {
		return fail(err)
	}
	held, err := lock.Acquire(target, "cleanup")
	if err != nil {
		return fail(err)
	}
	defer held.Release()
	fmt.Fprintln(stdout, "\nSafe cleanup")
	result, err := maint.Cleanup(target, a.downloads, a.personal)
	if err != nil {
		return fail(err)
	}
	for _, root := range result.Caches {
		fmt.Fprintf(stdout, "✓ Cache removed: %s\n", root)
	}
	if result.Cache > 0 {
		fmt.Fprintf(stdout, "✓ Regenerable data removed (%s).\n", fileCount(result.Cache))
	} else {
		fmt.Fprintln(stdout, "· No regenerable data in the installation needed removing.")
	}
	if result.Personal > 0 {
		fmt.Fprintf(stdout, "✓ Local personal data removed (%s).\n", fileCount(result.Personal))
	} else if !a.personal {
		fmt.Fprintln(stdout, "· History, logs and demos were kept; use --personal-data to remove them.")
	}
	if !a.downloads {
		fmt.Fprintln(stdout, "· Server downloads were kept; use --downloads to remove them.")
	}
	return output.ExitSuccess
}

func fileCount(count int) string {
	if count == 1 {
		return "1 file"
	}
	return fmt.Sprintf("%d files", count)
}

func init() {
	register(command{name: "cleanup", group: "Your data", order: 50, synopsis: "[TARGET]",
		summary: "deletes caches and, when asked, personal data",
		about:   "Deletes what x86QW can regenerate: caches and temporary files. Maps downloaded from servers and your personal data go only when you ask.",
		flags:   (&cleanupArgs{}).parser, run: noName(runCleanup)})
}
