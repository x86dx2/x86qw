package main

import (
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

func defaultCacheDir() string {
	dir, err := os.UserCacheDir()
	if err != nil {
		return ""
	}
	return filepath.Join(dir, "x86qw", "tuf")
}

// targetFlags parses [--json] [--target DIR] [DIR] for installation commands.
func targetFlags(name string, args []string, stderr io.Writer) (target string, asJSON bool, ok bool) {
	flags := flag.NewFlagSet(name, flag.ContinueOnError)
	flags.SetOutput(stderr)
	jsonFlag := flags.Bool("json", false, "saída estruturada")
	targetFlag := flags.String("target", "", "diretório da instalação")
	if err := flags.Parse(args); err != nil {
		return "", false, false
	}
	positional := flags.Args()
	if len(positional) > 1 {
		fmt.Fprintf(stderr, "x86qw %s: argumentos demais\n", name)
		return "", false, false
	}
	target = *targetFlag
	if len(positional) == 1 {
		if target != "" && filepath.Clean(target) != filepath.Clean(positional[0]) {
			fmt.Fprintf(stderr, "x86qw %s: --target e o destino posicional divergem\n", name)
			return "", false, false
		}
		target = positional[0]
	}
	if target == "" {
		target = defaultTarget()
	}
	if absolute, err := filepath.Abs(target); err == nil {
		target = absolute
	}
	return target, *jsonFlag, true
}

// defaultTarget is the installation that contains this executable
// (<root>/x86qw or <root>/.x86qw/cli/x86qw), else the current directory.
func defaultTarget() string {
	if executable, err := os.Executable(); err == nil {
		if resolved, err := filepath.EvalSymlinks(executable); err == nil {
			dir := filepath.Dir(resolved)
			for _, candidate := range []string{dir, filepath.Join(dir, "..", "..")} {
				if _, err := os.Stat(filepath.Join(candidate, ".x86qw")); err == nil {
					return filepath.Clean(candidate)
				}
			}
		}
	}
	return "."
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
