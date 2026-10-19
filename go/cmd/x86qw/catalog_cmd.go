package main

import (
	"flag"
	"fmt"
	"io"
	"os"
	"sort"
	"time"

	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

func runCatalog(args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("catalog", flag.ContinueOnError)
	flags.SetOutput(stderr)
	asJSON := flags.Bool("json", false, "saída estruturada")
	baseURL := flags.String("base-url", trust.DefaultBaseURL, "repositório TUF (metadata/ e targets/)")
	cacheDir := flags.String("cache-dir", defaultCacheDir(), "cache de metadata confiável; vazio desativa")
	outPath := flags.String("output", "", "grava o catálogo autenticado neste arquivo")
	if err := flags.Parse(args); err != nil {
		return output.ExitUsage
	}

	catalog, err := trust.LoadCatalog(trust.Options{BaseURL: *baseURL, CacheDir: *cacheDir})
	if err == nil && *outPath != "" {
		err = os.WriteFile(*outPath, catalog.Raw, 0o644)
	}
	if err != nil {
		code := "operation"
		if trust.IsTrustError(err) {
			code = "trust"
		}
		if *asJSON {
			output.Failure("catalog", output.ExitFailure, code, err.Error()).Write(stdout)
		} else {
			fmt.Fprintf(stderr, "✗ Falha de trust do catálogo x86QW: %v\n", err)
		}
		return output.ExitFailure
	}

	if *asJSON {
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
	register("catalog", 100, "catalog              autentica o catálogo público via TUF", noName(runCatalog))
}
