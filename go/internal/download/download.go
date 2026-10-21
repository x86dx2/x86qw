// Package download fetches pinned artifacts with the policy of
// x86qw_runtime/io/downloader.py: HTTPS on every hop, exact size and
// SHA-256, a total deadline, bounded retries and mirror fallback.
package download

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

// Artifact is one pinned file and its mirrors.
type Artifact struct {
	URLs   []string
	Size   int64
	SHA256 string
}

// Options tunes a fetch.
type Options struct {
	Deadline time.Duration // total budget across attempts and mirrors
	Attempts int
	Client   *http.Client
	// Progress, when set, hears the bytes of the current attempt so far;
	// a retry starts again from zero.
	Progress func(written int64)
}

const maxArtifactBytes = 512 << 20

// IntegrityError means the bytes differ from the pin.
type IntegrityError struct{ msg string }

func (e *IntegrityError) Error() string { return e.msg }

type transientError struct{ err error }

func (e *transientError) Error() string { return e.err.Error() }
func (e *transientError) Unwrap() error { return e.err }

// HTTPError is a non-200 response.
type HTTPError struct {
	Status     int
	URL        string
	RetryAfter time.Duration
}

func (e *HTTPError) Error() string { return fmt.Sprintf("HTTP %d em %s", e.Status, e.URL) }

func transientStatus(status int) bool {
	switch status {
	case 408, 425, 429, 500, 502, 503, 504:
		return true
	}
	return false
}

// ValidateURL enforces the HTTPS policy.
func ValidateURL(raw string) (*url.URL, error) {
	for _, r := range raw {
		if r <= 0x20 || r == 0x7f {
			return nil, fmt.Errorf("URL com caractere inválido: %q", raw)
		}
	}
	parsed, err := url.Parse(raw)
	if err != nil || !strings.EqualFold(parsed.Scheme, "https") || parsed.Hostname() == "" || parsed.User != nil || parsed.Fragment != "" {
		return nil, fmt.Errorf("URL fora da política HTTPS: %q", raw)
	}
	return parsed, nil
}

type clientKey struct{}

// WithClient makes every Fetch under ctx that sets no Options.Client use
// client. Callers deep in an install pass only ctx along, so this is how
// tests point them at an httptest.NewTLSServer; the HTTPS policy still
// applies to every URL.
func WithClient(ctx context.Context, client *http.Client) context.Context {
	return context.WithValue(ctx, clientKey{}, client)
}

func client(ctx context.Context, options Options) *http.Client {
	if options.Client != nil {
		return options.Client
	}
	if injected, ok := ctx.Value(clientKey{}).(*http.Client); ok && injected != nil {
		return injected
	}
	return &http.Client{
		CheckRedirect: func(request *http.Request, via []*http.Request) error {
			if len(via) >= 10 {
				return errors.New("redirecionamentos demais")
			}
			if _, err := ValidateURL(request.URL.String()); err != nil {
				return err
			}
			return nil
		},
	}
}

// Fetch downloads the artifact to destination atomically, trying each
// mirror in order. Transport and integrity failures move to the next mirror;
// policy failures and an exhausted deadline stop.
func Fetch(ctx context.Context, artifact Artifact, destination string, options Options) error {
	if artifact.Size <= 0 || artifact.Size > maxArtifactBytes || len(artifact.SHA256) != 64 {
		return fmt.Errorf("artefato sem tamanho ou SHA-256 válidos")
	}
	if options.Deadline == 0 {
		options.Deadline = 10 * time.Minute
	}
	if options.Attempts == 0 {
		options.Attempts = 3
	}
	for _, raw := range artifact.URLs {
		if _, err := ValidateURL(raw); err != nil {
			return err
		}
	}
	ctx, cancel := context.WithTimeout(ctx, options.Deadline)
	defer cancel()
	httpClient := client(ctx, options)
	var last error
	for _, raw := range artifact.URLs {
		err := fetchMirror(ctx, httpClient, raw, artifact, destination, options.Attempts, options.Progress)
		if err == nil {
			return nil
		}
		last = err
		var integrity *IntegrityError
		var transient *transientError
		var status *HTTPError
		if ctx.Err() != nil || !(errors.As(err, &integrity) || errors.As(err, &transient) || errors.As(err, &status)) {
			return err
		}
	}
	return fmt.Errorf("todos os mirrors falharam: %w", last)
}

func fetchMirror(ctx context.Context, httpClient *http.Client, raw string, artifact Artifact, destination string, attempts int, progress func(int64)) error {
	var err error
	for attempt := 1; attempt <= attempts; attempt++ {
		var wait time.Duration
		wait, err = fetchOnce(ctx, httpClient, raw, artifact, destination, progress)
		var transient *transientError
		var status *HTTPError
		retryable := errors.As(err, &transient) || (errors.As(err, &status) && transientStatus(status.Status))
		if err == nil || !retryable || attempt == attempts {
			return err
		}
		if wait == 0 {
			backoff := 0.5 * float64(int(1)<<(attempt-1))
			if backoff > 8 {
				backoff = 8
			}
			wait = time.Duration(backoff * (0.8 + 0.4*rand.Float64()) * float64(time.Second))
		}
		deadline, ok := ctx.Deadline()
		if ok && time.Until(deadline) < wait {
			return fmt.Errorf("orçamento de tentativas esgotado: %w", err)
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(wait):
		}
	}
	return err
}

func fetchOnce(ctx context.Context, httpClient *http.Client, raw string, artifact Artifact, destination string, progress func(int64)) (time.Duration, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, raw, nil)
	if err != nil {
		return 0, err
	}
	request.Header.Set("User-Agent", "x86qw-downloader/1")
	request.Header.Set("Accept-Encoding", "identity")
	response, err := httpClient.Do(request)
	if err != nil {
		if ctx.Err() != nil {
			return 0, ctx.Err()
		}
		return 0, &transientError{err}
	}
	defer response.Body.Close()
	if _, err := ValidateURL(response.Request.URL.String()); err != nil {
		return 0, err
	}
	if response.StatusCode != http.StatusOK {
		return retryAfter(response.Header.Get("Retry-After")), &HTTPError{Status: response.StatusCode, URL: raw}
	}
	if response.ContentLength >= 0 && response.ContentLength != artifact.Size {
		return 0, &IntegrityError{fmt.Sprintf("tamanho anunciado %d difere de %d: %s", response.ContentLength, artifact.Size, raw)}
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o700); err != nil {
		return 0, err
	}
	temporary, err := os.CreateTemp(filepath.Dir(destination), "."+filepath.Base(destination)+".*.download")
	if err != nil {
		return 0, err
	}
	defer os.Remove(temporary.Name())
	hash := sha256.New()
	sink := io.MultiWriter(temporary, hash)
	if progress != nil {
		progress(0)
		sink = io.MultiWriter(sink, &counter{report: progress})
	}
	written, copyErr := io.Copy(sink, io.LimitReader(response.Body, artifact.Size+1))
	syncErr := temporary.Sync()
	closeErr := temporary.Close()
	switch {
	case copyErr != nil:
		return 0, &transientError{copyErr}
	case written < artifact.Size:
		return 0, &transientError{fmt.Errorf("corpo truncado (%d de %d bytes): %s", written, artifact.Size, raw)}
	case written > artifact.Size:
		return 0, &IntegrityError{fmt.Sprintf("corpo excede %d bytes: %s", artifact.Size, raw)}
	case syncErr != nil:
		return 0, syncErr
	case closeErr != nil:
		return 0, closeErr
	}
	if hex.EncodeToString(hash.Sum(nil)) != artifact.SHA256 {
		return 0, &IntegrityError{fmt.Sprintf("SHA-256 divergente: %s", raw)}
	}
	if err := os.Chmod(temporary.Name(), 0o644); err != nil {
		return 0, err
	}
	return 0, os.Rename(temporary.Name(), destination)
}

func retryAfter(value string) time.Duration {
	if value == "" {
		return 0
	}
	if seconds, err := strconv.Atoi(value); err == nil && seconds >= 0 {
		return time.Duration(seconds) * time.Second
	}
	if when, err := http.ParseTime(value); err == nil {
		if wait := time.Until(when); wait > 0 {
			return wait
		}
	}
	return 0
}

// CacheEnv overrides the cache root (tests and isolated runs).
const CacheEnv = "X86QW_CACHE_DIR"

// TrustCacheName is the directory the CLI keeps its TUF metadata in. On
// Windows and Linux it sits inside the installer cache root
// (os.UserCacheDir()/x86qw/tuf), and the CLI writes it before any install.
const TrustCacheName = "tuf"

// AdoptableCache reports whether root holds nothing but the CLI's own TUF
// metadata, so the installer may claim it as its cache.
func AdoptableCache(root string) bool {
	entries, err := os.ReadDir(root)
	if err != nil {
		return false
	}
	for _, entry := range entries {
		if entry.Name() != TrustCacheName || !entry.IsDir() {
			return false
		}
	}
	return true
}

// CacheDir is the Python installer's download cache, shared with it;
// X86QW_CACHE_DIR overrides it.
func CacheDir() (string, error) {
	if dir := os.Getenv(CacheEnv); dir != "" {
		return filepath.Abs(dir)
	}
	switch runtime.GOOS {
	case "darwin":
		if dir := darwinUserCacheDir(); dir != "" {
			return filepath.Join(dir, "x86qw"), nil
		}
	case "windows":
		if dir := os.Getenv("LOCALAPPDATA"); dir != "" {
			return filepath.Join(dir, "x86qw"), nil
		}
	default:
		if dir := os.Getenv("XDG_CACHE_HOME"); dir != "" {
			return filepath.Join(dir, "x86qw"), nil
		}
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".cache", "x86qw"), nil
}

// Cached returns path if a cached copy already matches the pin.
func Cached(path string, artifact Artifact) bool {
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() != artifact.Size {
		return false
	}
	file, err := os.Open(path)
	if err != nil {
		return false
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return false
	}
	return hex.EncodeToString(hash.Sum(nil)) == artifact.SHA256
}

// counter reports the running total of what passes through it.
type counter struct {
	total  int64
	report func(int64)
}

func (c *counter) Write(p []byte) (int, error) {
	c.total += int64(len(p))
	c.report(c.total)
	return len(p), nil
}
