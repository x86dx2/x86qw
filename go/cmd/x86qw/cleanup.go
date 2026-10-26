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
	p.Bool(&a.downloads, "also what no component installed in fortress/progs and fortress/sound, yours included", "--downloads")
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
		fmt.Fprintf(stdout, "✓ Regenerable data removed (%s).\n", itemCount(result.Cache))
	} else {
		fmt.Fprintln(stdout, "· No regenerable data in the installation needed removing.")
	}
	if result.Personal > 0 {
		fmt.Fprintf(stdout, "✓ Local personal data removed (%s).\n", itemCount(result.Personal))
	} else if !a.personal {
		fmt.Fprintln(stdout, "· History, logs and demos were kept; use --personal-data to remove them.")
	}
	if !a.downloads {
		fmt.Fprintln(stdout, "· fortress/progs and fortress/sound were kept; --downloads removes what no component installed there.")
	}
	return output.ExitSuccess
}

func fileCount(count int) string {
	if count == 1 {
		return "1 file"
	}
	return fmt.Sprintf("%d files", count)
}

// itemCount names what cleanup counts: a folder removed whole is one item.
func itemCount(count int) string {
	if count == 1 {
		return "1 item"
	}
	return fmt.Sprintf("%d items", count)
}

func init() {
	register(command{name: "cleanup", group: "Your data", order: 50, synopsis: "[TARGET]",
		summary: "deletes caches and, when asked, personal data",
		about:   "Deletes what x86QW can regenerate: caches and temporary files. What no component installed in fortress/progs and fortress/sound (server downloads, your own files too) and your personal data go only when you ask.",
		flags:   (&cleanupArgs{}).parser, run: noName(runCleanup)})
}
