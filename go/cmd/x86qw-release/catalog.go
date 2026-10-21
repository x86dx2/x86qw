package main

import (
	"bytes"
	"encoding/json"
)

// cliCoordinates maps release targets to catalog platform/architecture.
var cliCoordinates = map[string][2]string{
	"darwin-arm64":  {"macos", "arm64"},
	"darwin-amd64":  {"macos", "x86_64"},
	"linux-amd64":   {"linux", "x86_64"},
	"linux-arm64":   {"linux", "arm64"},
	"windows-amd64": {"windows", "x64"},
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

// withCLIPackages replaces the x86qw-cli entries with p's binaries. Two-space
// indent and no HTML escaping keep a round trip byte for byte; a non-empty
// generatedAt replaces the catalog's generated_at.
func withCLIPackages(raw []byte, version string, p *pins, generatedAt string) ([]byte, error) {
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
	if generatedAt != "" {
		document.GeneratedAt, _ = json.Marshal(generatedAt)
	}
	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(document); err != nil {
		return nil, err
	}
	return buffer.Bytes(), nil
}
