package main

import (
	"bytes"
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"io"
	"os"
	"path/filepath"
	"text/template"
)

//go:embed templates/install.sh templates/install.ps1
var templates embed.FS

// Targets are the published platform builds.
var Targets = []string{"darwin-arm64", "darwin-amd64", "linux-amd64", "linux-arm64", "windows-amd64"}

// bootstraps are the pinned install scripts served from site/public.
var bootstraps = []string{"install.sh", "install.ps1"}

type pins struct {
	Version string
	SHA256  map[string]string
	Size    map[string]int64
}

func assetName(version, target string) string {
	name := "x86qw-" + version + "-" + target
	if target == "windows-amd64" {
		name += ".exe"
	}
	return name
}

func collect(version, dir string) (*pins, error) {
	result := &pins{Version: version, SHA256: map[string]string{}, Size: map[string]int64{}}
	for _, target := range Targets {
		path := filepath.Join(dir, assetName(version, target))
		file, err := os.Open(path)
		if err != nil {
			return nil, err
		}
		hash := sha256.New()
		size, err := io.Copy(hash, file)
		file.Close()
		if err != nil {
			return nil, err
		}
		result.SHA256[target] = hex.EncodeToString(hash.Sum(nil))
		result.Size[target] = size
	}
	return result, nil
}

// renderBootstrap is one pinned install script.
func renderBootstrap(p *pins, name string) ([]byte, error) {
	tmpl, err := template.ParseFS(templates, "templates/"+name)
	if err != nil {
		return nil, err
	}
	var out bytes.Buffer
	err = tmpl.Execute(&out, p)
	return out.Bytes(), err
}

// writeBootstraps writes both install scripts into the site folder.
func writeBootstraps(p *pins, site string) error {
	for _, name := range bootstraps {
		payload, err := renderBootstrap(p, name)
		if err != nil {
			return err
		}
		mode := os.FileMode(0o644)
		if name == "install.sh" {
			mode = 0o755
		}
		destination := filepath.Join(site, name)
		if err := os.WriteFile(destination, payload, mode); err != nil {
			return err
		}
		if err := os.Chmod(destination, mode); err != nil {
			return err
		}
	}
	return nil
}
