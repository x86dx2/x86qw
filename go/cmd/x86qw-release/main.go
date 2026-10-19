// Command x86qw-release is maintainer tooling for the native CLI release:
// it renders the pinned bootstraps from the built release assets.
//
//	x86qw-release bootstrap --version V --assets DIR --out DIR
package main

import (
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"text/template"
)

//go:embed templates/install.sh templates/install.ps1
var templates embed.FS

// Targets are the published platform builds.
var Targets = []string{"darwin-arm64", "darwin-amd64", "linux-amd64", "linux-arm64", "windows-amd64"}

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

func render(p *pins, out string) error {
	for _, name := range []string{"install.sh", "install.ps1"} {
		tmpl, err := template.ParseFS(templates, "templates/"+name)
		if err != nil {
			return err
		}
		destination := filepath.Join(out, name)
		file, err := os.Create(destination)
		if err != nil {
			return err
		}
		err = tmpl.Execute(file, p)
		file.Close()
		if err != nil {
			return err
		}
		mode := os.FileMode(0o644)
		if name == "install.sh" {
			mode = 0o755
		}
		if err := os.Chmod(destination, mode); err != nil {
			return err
		}
	}
	return nil
}

func main() {
	if len(os.Args) >= 2 && os.Args[1] == "sign" {
		os.Exit(runSign(os.Args[2:]))
	}
	if len(os.Args) >= 2 && os.Args[1] == "renew-timestamp" {
		os.Exit(runRenew(os.Args[2:]))
	}
	if len(os.Args) < 2 || os.Args[1] != "bootstrap" {
		fmt.Fprintln(os.Stderr, "uso: x86qw-release bootstrap|sign|renew-timestamp ...")
		os.Exit(2)
	}
	flags := flag.NewFlagSet("bootstrap", flag.ExitOnError)
	version := flags.String("version", "", "versão da CLI")
	assets := flags.String("assets", "", "diretório com os binários")
	out := flags.String("out", "", "diretório de saída (site/public)")
	flags.Parse(os.Args[2:])
	if *version == "" || *assets == "" || *out == "" {
		flags.Usage()
		os.Exit(2)
	}
	p, err := collect(*version, *assets)
	if err == nil {
		err = render(p, *out)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "x86qw-release:", err)
		os.Exit(1)
	}
	for _, target := range Targets {
		fmt.Printf("%s  %s  %d\n", p.SHA256[target], assetName(*version, target), p.Size[target])
	}
}
