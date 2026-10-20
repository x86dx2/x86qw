package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
)

// noStoreHeaders keeps mutable API documents out of the CDN cache.
const noStoreHeaders = "\n/api/v1/product.json\n  Cache-Control: no-store, no-cache, must-revalidate\n  CDN-Cache-Control: no-store\n  Access-Control-Allow-Origin: *\n"

// runSite assembles the deployable site from site/public: every file except
// the TUF history, only the current TUF generation (the timestamp's
// snapshot and targets, the root and the authorized catalog target), deploy
// provenance and the no-store headers. It replaces render_release_site.py,
// assemble_site_release.py and build_deploy_provenance.py.
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

func readSigned(path string) (map[string]any, error) {
	payload, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var document struct {
		Signed map[string]any `json:"signed"`
	}
	if err := json.Unmarshal(payload, &document); err != nil {
		return nil, err
	}
	return document.Signed, nil
}

func metaVersion(signed map[string]any, name string) (int64, error) {
	meta, _ := signed["meta"].(map[string]any)
	entry, _ := meta[name].(map[string]any)
	version, ok := entry["version"].(float64)
	if !ok {
		return 0, fmt.Errorf("metadata sem versão de %s", name)
	}
	return int64(version), nil
}

// currentGeneration lists the trust files the live repository needs.
func currentGeneration(trust string) ([]string, error) {
	timestamp, err := readSigned(filepath.Join(trust, "metadata", "timestamp.json"))
	if err != nil {
		return nil, err
	}
	snapshotVersion, err := metaVersion(timestamp, "snapshot.json")
	if err != nil {
		return nil, err
	}
	snapshotName := fmt.Sprintf("metadata/%d.snapshot.json", snapshotVersion)
	snapshot, err := readSigned(filepath.Join(trust, filepath.FromSlash(snapshotName)))
	if err != nil {
		return nil, err
	}
	targetsVersion, err := metaVersion(snapshot, "targets.json")
	if err != nil {
		return nil, err
	}
	targetsName := fmt.Sprintf("metadata/%d.targets.json", targetsVersion)
	targets, err := readSigned(filepath.Join(trust, filepath.FromSlash(targetsName)))
	if err != nil {
		return nil, err
	}
	entries, _ := targets["targets"].(map[string]any)
	catalog, _ := entries["catalog/catalog.json"].(map[string]any)
	hashes, _ := catalog["hashes"].(map[string]any)
	digest, _ := hashes["sha256"].(string)
	if len(digest) != 64 {
		return nil, errors.New("targets sem o hash do catálogo")
	}
	files := []string{"metadata/timestamp.json", snapshotName, targetsName, "targets/catalog/" + digest + ".catalog.json"}
	roots, _ := filepath.Glob(filepath.Join(trust, "metadata", "*.root.json"))
	for _, root := range roots {
		files = append(files, "metadata/"+filepath.Base(root))
	}
	return files, nil
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
	generation, err := currentGeneration(trust)
	if err != nil {
		return err
	}
	if err := copyTree(source, out, func(relative string) bool {
		return relative == "api/v1/trust" || strings.HasSuffix(relative, ".DS_Store")
	}); err != nil {
		return err
	}
	for _, relative := range generation {
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
