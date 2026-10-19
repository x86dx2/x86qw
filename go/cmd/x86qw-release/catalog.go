package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
)

// cliCoordinates maps release targets to catalog platform/architecture.
var cliCoordinates = map[string][2]string{
	"darwin-arm64":  {"macos", "arm64"},
	"darwin-amd64":  {"macos", "x86_64"},
	"linux-amd64":   {"linux", "x86_64"},
	"linux-arm64":   {"linux", "arm64"},
	"windows-amd64": {"windows", "x64"},
}

// runCatalogCLI replaces the x86qw-cli entries of catalog.json with the
// pinned assets of one release (x86qw-release catalog-cli --version V --assets DIR).
func runCatalogCLI(args []string) int {
	flags := flag.NewFlagSet("catalog-cli", flag.ExitOnError)
	version := flags.String("version", "", "versão da CLI")
	assets := flags.String("assets", "", "diretório com os binários do release")
	path := flags.String("catalog", "site/public/api/v1/catalog.json", "catálogo")
	flags.Parse(args)
	if *version == "" || *assets == "" {
		flags.Usage()
		return 2
	}
	pins, err := collect(*version, *assets)
	if err != nil {
		fmt.Fprintln(os.Stderr, "x86qw-release catalog-cli:", err)
		return 1
	}
	raw, err := os.ReadFile(*path)
	if err != nil {
		fmt.Fprintln(os.Stderr, "x86qw-release catalog-cli:", err)
		return 1
	}
	updated, err := withCLIPackages(raw, *version, pins)
	if err == nil {
		err = os.WriteFile(*path, updated, 0o644)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "x86qw-release catalog-cli:", err)
		return 1
	}
	fmt.Printf("%d pacotes x86qw-cli %s gravados em %s\n", len(Targets), *version, strings.TrimSpace(*path))
	return 0
}

// catalogDocument keeps the catalog's key order (format, project,
// generated_at, packages) and every existing entry's own order.
type catalogDocument struct {
	Format      json.RawMessage   `json:"format"`
	Project     json.RawMessage   `json:"project"`
	GeneratedAt json.RawMessage   `json:"generated_at"`
	Packages    []json.RawMessage `json:"packages"`
}

type cliEntry struct {
	Component              string   `json:"component"`
	Package                string   `json:"package"`
	Version                string   `json:"version"`
	Channel                string   `json:"channel"`
	Platform               string   `json:"platform"`
	Architecture           string   `json:"architecture"`
	Filename               string   `json:"filename"`
	Size                   int64    `json:"size"`
	SHA256                 string   `json:"sha256"`
	URLs                   []string `json:"urls"`
	OriginURL              string   `json:"origin_url"`
	License                string   `json:"license"`
	LicenseURL             string   `json:"license_url"`
	SourceURLs             []string `json:"source_urls"`
	RedistributionReviewed bool     `json:"redistribution_reviewed"`
	ReleaseURL             string   `json:"release_url"`
}

// withCLIPackages replaces x86qw-cli entries, rendering like Python's
// json.dumps(indent=2, ensure_ascii=False).
func withCLIPackages(raw []byte, version string, p *pins) ([]byte, error) {
	var document catalogDocument
	if err := json.Unmarshal(raw, &document); err != nil {
		return nil, err
	}
	var kept []json.RawMessage
	for _, item := range document.Packages {
		var probe struct {
			Package string `json:"package"`
		}
		if json.Unmarshal(item, &probe) == nil && probe.Package == "x86qw-cli" {
			continue
		}
		kept = append(kept, item)
	}
	if p != nil {
		base := "https://github.com/x86dx2/x86qw/releases/download/x86qw-cli-" + version + "/"
		for _, target := range Targets {
			name := assetName(version, target)
			coordinates := cliCoordinates[target]
			entry, err := json.Marshal(cliEntry{
				Component: "x86qw", Package: "x86qw-cli", Version: version, Channel: "stable",
				Platform: coordinates[0], Architecture: coordinates[1], Filename: name,
				Size: p.Size[target], SHA256: p.SHA256[target], URLs: []string{base + name}, OriginURL: base + name,
				License: "MIT", LicenseURL: "https://github.com/x86dx2/x86qw/blob/main/LICENSE",
				SourceURLs: []string{"https://github.com/x86dx2/x86qw"}, RedistributionReviewed: true,
				ReleaseURL: "https://github.com/x86dx2/x86qw/releases/tag/x86qw-cli-" + version,
			})
			if err != nil {
				return nil, err
			}
			kept = append(kept, entry)
		}
	}
	document.Packages = kept
	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(document); err != nil {
		return nil, err
	}
	return buffer.Bytes(), nil
}
