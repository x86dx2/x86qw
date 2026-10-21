// Command x86qw-release is the maintainer tooling for CLI releases and the
// public TUF repository. Paths default to the checkout it runs in.
//
//	x86qw-release prepare|sign|prune|renew-timestamp|live-timestamp|site|monitor ... --version V   puts a published CLI release into the checkout
//	x86qw-release sign                  signs the checked-out catalog as the next generation
//	x86qw-release prune                 drops TUF files the current generation does not use
//	x86qw-release renew-timestamp --key PEM --out FILE
//	x86qw-release live-timestamp --out FILE   saves the timestamp production serves
//	x86qw-release site --out DIR --commit SHA
//	x86qw-release monitor [--checkout]
package main

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

var commands = map[string]func(args []string) int{
	"prepare":         runPrepare,
	"sign":            runSign,
	"prune":           runPrune,
	"renew-timestamp": runRenew,
	"live-timestamp":  runLiveTimestamp,
	"site":            runSite,
	"monitor":         runMonitor,
}

func main() {
	if len(os.Args) < 2 || commands[os.Args[1]] == nil {
		fmt.Fprintln(os.Stderr, "uso: x86qw-release prepare|sign|prune|renew-timestamp|site|monitor ...")
		os.Exit(2)
	}
	os.Exit(commands[os.Args[1]](os.Args[2:]))
}

// Checkout paths, relative to the repository root.
const (
	catalogFile = "site/public/api/v1/catalog.json"
	trustDir    = "site/public/api/v1/trust"
	rootFile    = "maintenance/trust/root.json"
	keysDir     = ".tuf-production/keys"
	siteDir     = "site/public"
)

// repoRoot is the nearest folder up from the working directory that holds
// the site's catalog, so `go -C go run` works as well as the repo root.
func repoRoot() (string, error) {
	dir, err := os.Getwd()
	if err != nil {
		return "", err
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, filepath.FromSlash(catalogFile))); err == nil {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", errors.New("rode dentro do checkout do x86qw")
		}
		dir = parent
	}
}

// inRepo resolves a checkout path, keeping an explicit flag value as given.
func inRepo(repo, value, fallback string) string {
	if value != "" {
		return value
	}
	return filepath.Join(repo, filepath.FromSlash(fallback))
}
