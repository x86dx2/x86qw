package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/tufsign"
)

// noStoreHeaders keeps mutable API documents out of the CDN cache.
const noStoreHeaders = "\n/api/v1/product.json\n  Cache-Control: no-store, no-cache, must-revalidate\n  CDN-Cache-Control: no-store\n  Access-Control-Allow-Origin: *\n"

// runSite assembles the deployable site from site/public: every file, the
// current TUF generation alone (timestamp, its snapshot and targets, the
// root and the catalog target), the provenance documents version and
// api/v1/health, which exist only in the deploy, and the no-store headers.
//
//	x86qw-release site --source site/public --out DIR --commit SHA [--validate-run N --deploy-run N]
func runSite(args []string) int {
	flags := flag.NewFlagSet("site", flag.ExitOnError)
	source := flags.String("source", "site/public", "projeção do site no checkout")
	out := flags.String("out", "", "diretório de saída (novo)")
	commit := flags.String("commit", "", "commit implantado (40 hex)")
	validateRun := flags.Int64("validate-run", 0, "run do Validate")
	deployRun := flags.Int64("deploy-run", 0, "run do deploy")
	flags.Parse(args)
	if *out == "" || len(*commit) != 40 {
		flags.Usage()
		return 2
	}
	if err := assembleSite(*source, *out, *commit, *validateRun, *deployRun); err != nil {
		fmt.Fprintln(os.Stderr, "x86qw-release site:", err)
		return 1
	}
	fmt.Println("site montado em", *out)
	return 0
}

func copyTree(source, destination string, skip func(string) bool) error {
	return filepath.WalkDir(source, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		relative, _ := filepath.Rel(source, path)
		relative = filepath.ToSlash(relative)
		if relative != "." && skip(relative) {
			if entry.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		target := filepath.Join(destination, filepath.FromSlash(relative))
		if entry.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		if !entry.Type().IsRegular() {
			return fmt.Errorf("arquivo não regular no site: %s", relative)
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		data, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(target, data, info.Mode().Perm())
	})
}

func encodeIndented(value any) []byte {
	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	encoder.Encode(value)
	return buffer.Bytes()
}

func assembleSite(source, out, commit string, validateRun, deployRun int64) error {
	if _, err := os.Stat(out); err == nil {
		return fmt.Errorf("destino já existe: %s", out)
	}
	trust := filepath.Join(source, "api", "v1", "trust")
	generation, err := tufsign.ReadGeneration(trust)
	if err != nil {
		return err
	}
	if err := copyTree(source, out, func(relative string) bool {
		return relative == "api/v1/trust" || strings.HasSuffix(relative, ".DS_Store")
	}); err != nil {
		return err
	}
	for _, relative := range generation.Files() {
		data, err := os.ReadFile(filepath.Join(trust, filepath.FromSlash(relative)))
		if err != nil {
			return err
		}
		target := filepath.Join(out, "api", "v1", "trust", filepath.FromSlash(relative))
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(target, data, 0o644); err != nil {
			return err
		}
	}
	catalog, err := os.ReadFile(filepath.Join(source, "api", "v1", "catalog.json"))
	if err != nil {
		return err
	}
	sum := sha256.Sum256(catalog)
	version := map[string]any{
		"catalog_sha256": hex.EncodeToString(sum[:]), "commit": commit, "deploy_run_id": deployRun,
		"external_public": false, "format": 1, "project": "x86qw", "release_audience": "owner-only",
		"validate_run_id": validateRun,
	}
	health := map[string]any{
		"commit": commit, "deploy_run_id": deployRun, "external_public": false, "format": 1,
		"project": "x86qw", "release_audience": "owner-only", "status": "ok", "validate_run_id": validateRun,
	}
	for _, relative := range []string{"version", "api/v1/version"} {
		if err := os.WriteFile(filepath.Join(out, filepath.FromSlash(relative)), encodeIndented(version), 0o644); err != nil {
			return err
		}
	}
	if err := os.WriteFile(filepath.Join(out, "api", "v1", "health"), encodeIndented(health), 0o644); err != nil {
		return err
	}
	headers := filepath.Join(out, "_headers")
	current, err := os.ReadFile(headers)
	if err != nil {
		return err
	}
	return os.WriteFile(headers, append(current, []byte(noStoreHeaders)...), 0o644)
}
