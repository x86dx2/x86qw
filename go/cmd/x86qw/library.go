package main

import (
	"fmt"
	"io"
	"time"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
)

type libraryArgs struct{ target, add, remove string }

func (a *libraryArgs) parser() *cliargs.Parser {
	p := cliargs.New()
	targetFlag(p, &a.target)
	p.String(&a.add, "ADDRESS", "adds a server to the favorites", "--add")
	p.String(&a.remove, "ADDRESS", "removes a server from the favorites", "--remove")
	p.Exclusive("--add", "--remove")
	return p
}

func runLibrary(args []string, stdout, stderr io.Writer) int {
	var a libraryArgs
	p := a.parser()
	if code, ok := parse("library", p, args, stdout, stderr); !ok {
		return code
	}
	target, err := targetOf(p, a.target)
	if err != nil {
		return usageFailure("library", err, stderr)
	}
	switch {
	case p.Given("--add"):
		err = maint.AddFavorite(target, a.add, time.Now())
	case p.Given("--remove"):
		err = maint.RemoveFavorite(target, a.remove)
	}
	if err == nil {
		var library *maint.Library
		if library, err = maint.LoadLibrary(target); err == nil {
			fmt.Fprint(stdout, maint.RenderLibrary(library))
			return output.ExitSuccess
		}
	}
	fmt.Fprintf(stderr, "✗ %v\n", err)
	return output.ExitFailure
}

func init() {
	register(command{name: "library", group: "Your data", order: 10, synopsis: "[TARGET]",
		summary: "shows and edits the favorite and recent servers",
		flags:   (&libraryArgs{}).parser, run: noName(runLibrary)})
}
