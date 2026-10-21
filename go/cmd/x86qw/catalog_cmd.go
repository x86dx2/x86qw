package main

import (
	"fmt"
	"io"
	"os"
	"sort"
	"time"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

type catalogArgs struct {
	json                      bool
	baseURL, cacheDir, output string
}

func (a *catalogArgs) parser() *cliargs.Parser {
	p := cliargs.New()
	jsonFlag(p, &a.json)
	p.String(&a.baseURL, "URL", "repositório TUF (metadata/ e targets/)", "--base-url")
	p.String(&a.cacheDir, "PASTA", "cache de metadados confiáveis; vazio desativa", "--cache-dir")
	p.String(&a.output, "ARQUIVO", "grava o catálogo autenticado neste arquivo", "--output")
	return p
}

func runCatalog(args []string, stdout, stderr io.Writer) int {
	a := catalogArgs{baseURL: trust.DefaultBaseURL, cacheDir: defaultCacheDir()}
	p := a.parser()
	if code, ok := parse("catalog", p, args, stdout, stderr); !ok {
		return code
	}
	if len(p.Positional()) > 0 {
		return usageFailure("catalog", cliargs.Usagef("argumentos a mais: %v", p.Positional()), stderr)
	}
	catalog, err := trust.LoadCatalog(trust.Options{BaseURL: a.baseURL, CacheDir: a.cacheDir})
	if err == nil && a.output != "" {
		err = os.WriteFile(a.output, catalog.Raw, 0o644)
	}
	if err != nil {
		code := "operation"
		if trust.IsTrustError(err) {
			code = "trust"
		}
		if a.json {
			output.Failure("catalog", output.ExitFailure, code, err.Error()).Write(stdout)
		} else {
			fmt.Fprintf(stderr, "✗ O catálogo x86QW não autenticou: %v\n", err)
		}
		return output.ExitFailure
	}
	if a.json {
		roles := map[string]any{}
		for name, state := range catalog.Roles {
			roles[name] = map[string]any{
				"expires": state.Expires.UTC().Format(time.RFC3339),
				"version": state.Version,
			}
		}
		data := map[string]any{
			"packages": catalog.Packages,
			"project":  "x86qw",
			"roles":    roles,
			"sha256":   catalog.SHA256,
		}
		if err := output.Success("catalog", data).Write(stdout); err != nil {
			return output.ExitFailure
		}
		return output.ExitSuccess
	}
	fmt.Fprintf(stdout, "✓ Catálogo autenticado: %d pacotes, sha256 %s\n", catalog.Packages, catalog.SHA256)
	names := make([]string, 0, len(catalog.Roles))
	for name := range catalog.Roles {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		state := catalog.Roles[name]
		fmt.Fprintf(stdout, "  %-9s v%-3d expira %s\n", name, state.Version, state.Expires.UTC().Format(time.RFC3339))
	}
	return output.ExitSuccess
}

func init() {
	register(command{name: "catalog", group: "Diagnóstico", order: 20, summary: "autentica o catálogo público via TUF",
		flags: (&catalogArgs{}).parser, run: noName(runCatalog)})
}
