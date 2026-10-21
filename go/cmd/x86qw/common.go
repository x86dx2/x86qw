package main

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"golang.org/x/term"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/download"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

// defaultCacheDir keeps the TUF metadata inside the download cache, so
// X86QW_CACHE_DIR moves both and uninstall --purge removes both.
func defaultCacheDir() string {
	dir, err := download.CacheDir()
	if err != nil {
		return ""
	}
	return filepath.Join(dir, download.TrustCacheName)
}

// targetFlag declares --target; the installation may also come as the
// one positional argument.
func targetFlag(p *cliargs.Parser, target *string) {
	p.String(target, "TARGET", "installation folder (default: this CLI's, else ~/Games/x86qw)", "--target")
}

func jsonFlag(p *cliargs.Parser, asJSON *bool) {
	p.Bool(asJSON, "JSON output on one line", "--json")
}

// targetOf is the installation the command acts on: the positional
// TARGET or --target (they must agree), else install.DefaultTarget.
func targetOf(p *cliargs.Parser, flagTarget string) (string, error) {
	positional := p.Positional()
	if len(positional) > 1 {
		return "", cliargs.Usagef("unexpected arguments: %s", strings.Join(positional[1:], " "))
	}
	target := flagTarget
	if len(positional) == 1 {
		if target != "" && filepath.Clean(target) != filepath.Clean(positional[0]) {
			return "", cliargs.Usagef("--target and the positional target disagree")
		}
		target = positional[0]
	}
	if target == "" {
		target = install.DefaultTarget()
	}
	return filepath.Abs(expandHome(target))
}

// expandHome turns a leading ~ into the home folder.
func expandHome(path string) string {
	if path == "~" || strings.HasPrefix(path, "~/") || strings.HasPrefix(path, `~\`) {
		if home, err := os.UserHomeDir(); err == nil {
			return filepath.Join(home, path[1:])
		}
	}
	return path
}

// stdinIsTerminal is whether someone can answer (replaced by the tests).
var stdinIsTerminal = func() bool { return isTerminal(os.Stdin) }

// stdin is where answers come from (replaced by the tests).
var stdin io.Reader = os.Stdin

// isTerminal reports whether file is a terminal (not /dev/null).
func isTerminal(file *os.File) bool { return term.IsTerminal(int(file.Fd())) }

// writerIsTerminal reports whether w is a terminal.
func writerIsTerminal(w io.Writer) bool {
	file, ok := w.(*os.File)
	return ok && isTerminal(file)
}

// colorFor is whether to color what goes to w: on a terminal, unless
// --no-color or NO_COLOR (when set and not empty) say otherwise.
func colorFor(w io.Writer, noColor bool) bool {
	return !noColor && os.Getenv("NO_COLOR") == "" && writerIsTerminal(w)
}

// confirm is the one rule of every command that changes or stops
// something: --yes goes ahead; on a terminal it asks; without one there
// is nobody to ask, so it says to repeat with --yes and returns 2. A "no"
// returns 0: nothing was asked to happen.
func confirm(question, retry string, yes bool, stdout, stderr io.Writer) (proceed bool, code int) {
	if yes {
		return true, output.ExitSuccess
	}
	// While the app owns the terminal nobody can answer here either.
	if !stdinIsTerminal() || appOpen {
		fmt.Fprintf(stderr, "No terminal to confirm on. Run again with --yes: %s --yes\n", retry)
		return false, output.ExitUsage
	}
	fmt.Fprintf(stdout, "%s [y/N] ", question)
	answer, _ := bufio.NewReader(stdin).ReadString('\n')
	switch strings.ToLower(strings.TrimSpace(answer)) {
	case "s", "sim", "y", "yes":
		return true, output.ExitSuccess
	}
	fmt.Fprintln(stdout, "Canceled; nothing was changed.")
	return false, output.ExitSuccess
}

func loadCatalog(catalogFile string) (*catalog.Catalog, error) {
	var raw []byte
	var err error
	if catalogFile != "" {
		raw, err = os.ReadFile(catalogFile)
	} else {
		var authenticated *trust.Catalog
		authenticated, err = trust.LoadCatalog(trust.Options{CacheDir: defaultCacheDir()})
		if authenticated != nil {
			raw = authenticated.Raw
		}
	}
	if err != nil {
		return nil, err
	}
	return catalog.Parse(raw)
}

func catalogFileFlag(p *cliargs.Parser, file *string) {
	p.String(file, "FILE", "local catalog, already authenticated (tests)", "--catalog-file")
	p.Hidden("--catalog-file")
}
