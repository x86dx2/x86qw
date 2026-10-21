package main

import (
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/theupdateframework/go-tuf/v2/metadata"
	"github.com/theupdateframework/go-tuf/v2/metadata/fetcher"
	"github.com/x86dx2/x86qw/go/internal/trust"
	"github.com/x86dx2/x86qw/go/internal/tufsign"
)

// runSign: x86qw-release sign [--version N] [--keys DIR]
//
// Signs the checked-out catalog as a new generation in place: targets,
// snapshot and timestamp go into the trust projection and the previous
// generation's files are removed. This is also how snapshot and targets
// are renewed before they expire.
func runSign(args []string) int {
	flags := flag.NewFlagSet("sign", flag.ExitOnError)
	keys := flags.String("keys", "", "diretório das chaves offline (padrão: "+keysDir+")")
	version := flags.Int64("version", 0, "versão da geração (padrão: a maior entre o checkout e a produção, mais um)")
	baseURL := flags.String("base-url", trust.DefaultBaseURL, "repositório TUF público")
	flags.Parse(args)
	repo, err := repoRoot()
	if err == nil {
		*version, err = signInPlace(repo, inRepo(repo, *keys, keysDir), *baseURL, *version)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "x86qw-release sign:", err)
		return 1
	}
	fmt.Printf("geração TUF %d assinada em %s\n", *version, trustDir)
	return 0
}

// nextVersion is one past the newest timestamp in the checkout and in
// production; a renewed timestamp is ahead of its snapshot and targets.
func nextVersion(repo, baseURL string) (int64, error) {
	local, err := tufsign.ReadGeneration(filepath.Join(repo, filepath.FromSlash(trustDir)))
	if err != nil {
		return 0, err
	}
	live, err := liveTimestamp(baseURL)
	if err != nil {
		return 0, err
	}
	return max(local.Timestamp, live) + 1, nil
}

// liveTimestamp is the version production serves. A chain that no longer
// authenticates, because it expired, is what sign exists to recover, so
// then it takes the timestamp's version once the root's timestamp key
// vouches for it, whatever its expiry.
func liveTimestamp(baseURL string) (int64, error) {
	catalog, err := trust.LoadCatalog(trust.Options{BaseURL: baseURL})
	if err == nil {
		return catalog.Roles["timestamp"].Version, nil
	}
	fmt.Fprintf(os.Stderr, "aviso: produção não autenticou (%v); lendo só a versão do timestamp\n", err)
	payload, err := fetchLive(baseURL, "metadata/timestamp.json")
	if err != nil {
		return 0, err
	}
	timestamp, err := metadata.Timestamp().FromBytes(payload)
	if err != nil {
		return 0, err
	}
	root, err := metadata.Root().FromBytes(trust.RootJSON)
	if err != nil {
		return 0, err
	}
	if err := root.VerifyDelegate(metadata.TIMESTAMP, timestamp); err != nil {
		return 0, fmt.Errorf("timestamp de produção sem assinatura válida: %w", err)
	}
	return timestamp.Signed.Version, nil
}

// fetchLive downloads a file of the public TUF repository the way the
// client does: Cloudflare challenges other clients from CI runners.
func fetchLive(baseURL, name string) ([]byte, error) {
	f := fetcher.NewDefaultFetcher()
	f.SetHTTPUserAgent(trust.UserAgent)
	return f.DownloadFile(strings.TrimSuffix(baseURL, "/")+"/"+name, 64*1024, time.Minute)
}

// runLiveTimestamp: x86qw-release live-timestamp --out FILE
//
// Saves the timestamp.json production serves, for the workflows to compare.
func runLiveTimestamp(args []string) int {
	flags := flag.NewFlagSet("live-timestamp", flag.ExitOnError)
	baseURL := flags.String("base-url", trust.DefaultBaseURL, "repositório TUF público")
	out := flags.String("out", "", "onde gravar")
	flags.Parse(args)
	if *out == "" {
		flags.Usage()
		return 2
	}
	payload, err := fetchLive(*baseURL, "metadata/timestamp.json")
	if err == nil {
		err = os.WriteFile(*out, payload, 0o644)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "x86qw-release live-timestamp:", err)
		return 1
	}
	return 0
}

// signInPlace signs the checkout's catalog as generation version (or the
// next one when 0), copies it into the projection and prunes the old one.
func signInPlace(repo, keys, baseURL string, version int64) (int64, error) {
	next, err := nextVersion(repo, baseURL)
	if err != nil {
		return 0, err
	}
	if version == 0 {
		version = next
	}
	if version < next {
		return 0, fmt.Errorf("a geração %d já foi usada; use %d ou mais", version, next)
	}
	work, err := os.MkdirTemp("", "x86qw-sign-")
	if err != nil {
		return 0, err
	}
	defer os.RemoveAll(work)
	out := filepath.Join(work, "repo")
	if err := tufsign.Generate(keys, filepath.Join(repo, filepath.FromSlash(rootFile)), filepath.Join(repo, filepath.FromSlash(catalogFile)),
		out, version, time.Now().UTC()); err != nil {
		return 0, err
	}
	generation, err := tufsign.ReadGeneration(out)
	if err != nil {
		return 0, err
	}
	projection := filepath.Join(repo, filepath.FromSlash(trustDir))
	for _, relative := range generation.Files() {
		data, err := os.ReadFile(filepath.Join(out, filepath.FromSlash(relative)))
		if err != nil {
			return 0, err
		}
		if err := os.WriteFile(filepath.Join(projection, filepath.FromSlash(relative)), data, 0o644); err != nil {
			return 0, err
		}
	}
	_, err = tufsign.Prune(projection)
	return version, err
}

// runPrune: x86qw-release prune
func runPrune(args []string) int {
	flags := flag.NewFlagSet("prune", flag.ExitOnError)
	flags.Parse(args)
	repo, err := repoRoot()
	var removed []string
	if err == nil {
		removed, err = tufsign.Prune(filepath.Join(repo, filepath.FromSlash(trustDir)))
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "x86qw-release prune:", err)
		return 1
	}
	fmt.Printf("%d arquivos de gerações antigas removidos\n", len(removed))
	return 0
}

// runRenew: x86qw-release renew-timestamp --current FILE --key PEM --out FILE [--lease-hours 720]
func runRenew(args []string) int {
	flags := flag.NewFlagSet("renew-timestamp", flag.ExitOnError)
	current := flags.String("current", "", "timestamp atual (padrão: o do checkout)")
	key := flags.String("key", "", "chave PEM da role timestamp")
	root := flags.String("root", "", "root TUF (padrão: "+rootFile+")")
	out := flags.String("out", "", "timestamp renovado")
	lease := flags.Int("lease-hours", 720, "duração da lease")
	flags.Parse(args)
	if *key == "" || *out == "" {
		flags.Usage()
		return 2
	}
	repo, err := repoRoot()
	var payload []byte
	if err == nil {
		payload, err = os.ReadFile(inRepo(repo, *current, trustDir+"/metadata/timestamp.json"))
	}
	if err == nil {
		payload, err = tufsign.RenewTimestamp(payload, *key, inRepo(repo, *root, rootFile), time.Duration(*lease)*time.Hour, time.Now().UTC())
	}
	if err == nil {
		err = os.WriteFile(*out, payload, 0o644)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "x86qw-release renew-timestamp:", err)
		return 1
	}
	return 0
}
