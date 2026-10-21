package main

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/content"
	"github.com/x86dx2/x86qw/go/internal/semver"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

const releaseBase = "https://github.com/x86dx2/x86qw/releases/download/"

// Where the checkout names the current CLI release, besides the catalog,
// product.json and the bootstraps: README.md by tag and the page by span.
var (
	releaseTag  = regexp.MustCompile(`x86qw-cli-[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?`)
	releaseSpan = regexp.MustCompile(`<span class="release-product-version">[^<]*</span>`)
	// versionDocuments are checked by TestCheckoutNamesOneRelease.
	versionDocuments = []string{"README.md", "site/public/index.html"}
)

// runPrepare: x86qw-release prepare --version V [--assets DIR] [--keys DIR]
//
// Puts a published CLI release into the checkout: downloads its binaries
// and checks them against SHA256SUMS, rewrites the catalog, product.json,
// the bootstraps and the version in README.md and index.html, signs the
// next TUF generation, prunes the old one and runs x86qw-content verify.
// What is left is a diff to review and merge; the merge deploys the site.
func runPrepare(args []string) int {
	flags := flag.NewFlagSet("prepare", flag.ExitOnError)
	version := flags.String("version", "", "versão da CLI já publicada em x86qw-cli-VERSÃO")
	assets := flags.String("assets", "", "binários já baixados (padrão: baixa da release)")
	keys := flags.String("keys", "", "diretório das chaves offline (padrão: "+keysDir+")")
	flags.Parse(args)
	if _, err := semver.Parse(*version); err != nil {
		fmt.Fprintln(os.Stderr, "x86qw-release prepare: --version: uma versão SemVer, como 2.4.0")
		return 2
	}
	repo, err := repoRoot()
	if err == nil {
		err = prepare(repo, *version, *assets, inRepo(repo, *keys, keysDir))
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "x86qw-release prepare:", err)
		return 1
	}
	return 0
}

func prepare(repo, version, assets, keys string) error {
	if assets == "" {
		dir, err := os.MkdirTemp("", "x86qw-cli-"+version+"-")
		if err != nil {
			return err
		}
		defer os.RemoveAll(dir)
		if err := download(version, dir); err != nil {
			return err
		}
		assets = dir
	}
	p, err := checkSums(version, assets)
	if err != nil {
		return err
	}
	path := filepath.Join(repo, filepath.FromSlash(catalogFile))
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	updated, err := withCLIPackages(raw, version, p, time.Now().UTC().Format("2006-01-02T15:04:05Z"))
	if err == nil {
		err = os.WriteFile(path, updated, 0o644)
	}
	if err != nil {
		return err
	}
	project := content.NewProject(repo)
	if err := project.WriteProduct(); err != nil {
		return err
	}
	if err := writeBootstraps(p, filepath.Join(repo, filepath.FromSlash(siteDir))); err != nil {
		return err
	}
	for _, document := range versionDocuments {
		if err := nameRelease(filepath.Join(repo, filepath.FromSlash(document)), version); err != nil {
			return err
		}
	}
	generation, err := signInPlace(repo, keys, trust.DefaultBaseURL, 0)
	if err != nil {
		return err
	}
	if err := project.Verify(os.Stdout, content.VerifyOptions{SkipGit: true}); err != nil {
		return err
	}
	fmt.Printf("CLI %s pronta no checkout com a geração TUF %d: revise o diff e abra o PR.\n", version, generation)
	return nil
}

// download fetches the release's binaries and SHA256SUMS into dir.
func download(version, dir string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	names := []string{"SHA256SUMS"}
	for _, target := range Targets {
		names = append(names, assetName(version, target))
	}
	for _, name := range names {
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, releaseBase+"x86qw-cli-"+version+"/"+name, nil)
		if err != nil {
			return err
		}
		response, err := http.DefaultClient.Do(request)
		if err != nil {
			return err
		}
		if response.StatusCode != http.StatusOK {
			response.Body.Close()
			return fmt.Errorf("%s: HTTP %d; a release x86qw-cli-%s foi publicada?", name, response.StatusCode, version)
		}
		file, err := os.Create(filepath.Join(dir, name))
		if err == nil {
			_, err = io.Copy(file, response.Body)
			err = errors.Join(err, file.Close())
		}
		response.Body.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

// checkSums hashes the binaries and requires SHA256SUMS to list each one
// with the same digest.
func checkSums(version, dir string) (*pins, error) {
	p, err := collect(version, dir)
	if err != nil {
		return nil, err
	}
	sums, err := os.ReadFile(filepath.Join(dir, "SHA256SUMS"))
	if err != nil {
		return nil, err
	}
	listed := map[string]string{}
	scanner := bufio.NewScanner(bytes.NewReader(sums))
	for scanner.Scan() {
		if fields := strings.Fields(scanner.Text()); len(fields) == 2 {
			listed[strings.TrimPrefix(fields[1], "*")] = fields[0]
		}
	}
	for _, target := range Targets {
		name := assetName(version, target)
		if listed[name] != p.SHA256[target] {
			return nil, fmt.Errorf("%s não confere com o SHA256SUMS da release", name)
		}
	}
	return p, nil
}

// nameRelease points path's release tags and version spans at version.
func nameRelease(path, version string) error {
	payload, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	payload = releaseTag.ReplaceAll(payload, []byte("x86qw-cli-"+version))
	payload = releaseSpan.ReplaceAll(payload, []byte(`<span class="release-product-version">`+version+`</span>`))
	return os.WriteFile(path, payload, 0o644)
}
