package main

import (
	"fmt"
	"io"
	"time"

	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
)

func runLibrary(args []string, stdout, stderr io.Writer) int {
	parsed, ok := parseArgs("library", args, []string{"--add", "--remove"}, nil, nil, stderr)
	if !ok {
		return output.ExitUsage
	}
	add, hasAdd := parsed.values["--add"]
	remove, hasRemove := parsed.values["--remove"]
	if hasAdd && hasRemove {
		fmt.Fprintln(stderr, "x86qw library: erro: --add e --remove não podem ser combinados")
		return output.ExitUsage
	}
	var err error
	switch {
	case hasAdd:
		err = maint.AddFavorite(parsed.target, add, time.Now())
	case hasRemove:
		err = maint.RemoveFavorite(parsed.target, remove)
	}
	if err == nil {
		var library *maint.Library
		if library, err = maint.LoadLibrary(parsed.target); err == nil {
			fmt.Fprint(stdout, maint.RenderLibrary(library))
			return output.ExitSuccess
		}
	}
	fmt.Fprintf(stderr, "✗ %v\n", err)
	return output.ExitFailure
}

func init() {
	register("library", 110, "library [--add|--remove] favoritos e recentes locais", noName(runLibrary))
}
