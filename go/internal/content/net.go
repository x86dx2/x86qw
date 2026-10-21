package content

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"time"
)

// Bounded HTTPS transfers: HTTPS only (redirects included), size ceilings,
// deadlines, and pinned
// size/SHA-256 for every persistent artifact.

// HTTPError is an HTTP status other than 200.
type HTTPError struct {
	Status int
	URL    string
}

func (e *HTTPError) Error() string {
	return fmt.Sprintf("HTTP %d at %s", e.Status, safeURLForLog(e.URL))
}

// IntegrityError is a download whose size or digest is not the pinned one.
type IntegrityError struct{ msg string }

func (e *IntegrityError) Error() string { return e.msg }

// IsHTTPStatus reports whether err is an HTTP error with the given status.
func IsHTTPStatus(err error, status int) bool {
	var target *HTTPError
	return errors.As(err, &target) && target.Status == status
}

func (p *Project) client() *http.Client {
	base := p.HTTP
	if base == nil {
		base = http.DefaultClient
	}
	client := *base
	client.CheckRedirect = func(request *http.Request, via []*http.Request) error {
		if len(via) >= 10 {
			return errors.New("too many redirects")
		}
		if _, err := validateHTTPSURL(request.URL.String(), "redirect"); err != nil {
			return err
		}
		return nil
	}
	return &client
}

// Response is a bounded metadata response.
type Response struct {
	URL    string
	Header http.Header
	Data   []byte
}

func (p *Project) request(method, url string, headers map[string]string, body io.Reader, deadline time.Duration) (*http.Response, context.CancelFunc, error) {
	if _, err := validateHTTPSURL(url, "URL"); err != nil {
		return nil, nil, err
	}
	ctx, cancel := context.WithTimeout(context.Background(), deadline)
	request, err := http.NewRequestWithContext(ctx, method, url, body)
	if err != nil {
		cancel()
		return nil, nil, err
	}
	for key, value := range headers {
		request.Header.Set(key, value)
	}
	if request.Header.Get("User-Agent") == "" {
		request.Header.Set("User-Agent", userAgent)
	}
	response, err := p.client().Do(request)
	if err != nil {
		cancel()
		return nil, nil, fmt.Errorf("network failure at %s: %v", safeURLForLog(url), err)
	}
	return response, cancel, nil
}

// Metadata fetches a small document, bounded by maximum and deadline.
func (p *Project) Metadata(method, url string, maximum int64, deadline time.Duration, headers map[string]string) (*Response, error) {
	response, cancel, err := p.request(method, url, headers, nil, deadline)
	if err != nil {
		return nil, err
	}
	defer cancel()
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode > 299 {
		return nil, &HTTPError{Status: response.StatusCode, URL: url}
	}
	result := &Response{URL: response.Request.URL.String(), Header: response.Header}
	if method == http.MethodHead {
		return result, nil
	}
	data, err := io.ReadAll(io.LimitReader(response.Body, maximum+1))
	if err != nil {
		return nil, err
	}
	if int64(len(data)) > maximum {
		return nil, fmt.Errorf("response exceeds %d bytes: %s", maximum, safeURLForLog(url))
	}
	result.Data = data
	return result, nil
}

// Pinned downloads url into destination, requiring the exact size and
// SHA-256 (download(PinnedArtifact(...))).
func (p *Project) Pinned(url, destination string, size int64, digest string, attempts int) error {
	if size <= 0 || size > MaxArtifactBytes || !sha256Pattern.MatchString(digest) {
		return fmt.Errorf("download needs a reviewed size and SHA-256: %s", safeURLForLog(url))
	}
	var last error
	for attempt := 0; attempt < max(1, attempts); attempt++ {
		last = p.pinnedOnce(url, destination, size, digest)
		var integrity *IntegrityError
		var status *HTTPError
		if last == nil || errors.As(last, &integrity) || (errors.As(last, &status) && status.Status < 500) {
			return last
		}
	}
	return last
}

func (p *Project) pinnedOnce(url, destination string, size int64, digest string) error {
	response, cancel, err := p.request(http.MethodGet, url, nil, nil, 120*time.Second)
	if err != nil {
		return err
	}
	defer cancel()
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode > 299 {
		return &HTTPError{Status: response.StatusCode, URL: url}
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(destination), "."+filepath.Base(destination)+".")
	if err != nil {
		return err
	}
	temporary := file.Name()
	defer os.Remove(temporary)
	hash := sha256.New()
	written, err := io.Copy(io.MultiWriter(file, hash), io.LimitReader(response.Body, size+1))
	closeErr := file.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if written != size {
		return &IntegrityError{fmt.Sprintf("size differs at %s: expected %d, got %d", safeURLForLog(url), size, written)}
	}
	if hex.EncodeToString(hash.Sum(nil)) != digest {
		return &IntegrityError{"SHA-256 differs at " + safeURLForLog(url)}
	}
	if err := os.Chmod(temporary, 0o644); err != nil {
		return err
	}
	return os.Rename(temporary, destination)
}

// RemoteFingerprint downloads a pinned artifact to a temporary file and
// returns its (size, sha256); a 404 returns ok=false.
func (p *Project) RemoteFingerprint(url string, size int64, digest string) (int64, string, bool, error) {
	dir, err := os.MkdirTemp("", "x86qw-mirror-verify-")
	if err != nil {
		return 0, "", false, err
	}
	defer os.RemoveAll(dir)
	err = p.Pinned(url, filepath.Join(dir, "artifact"), size, digest, 1)
	if IsHTTPStatus(err, 404) {
		return 0, "", false, nil
	}
	if err != nil {
		return 0, "", false, err
	}
	return size, digest, true, nil
}

// Put uploads payload (GitLab generic packages). Like the curl upload it
// replaces (--max-redirs 0), a redirect is never followed: the private
// token header must not reach another origin.
func (p *Project) Put(url string, headers map[string]string, payload []byte) error {
	if _, err := validateHTTPSURL(url, "URL"); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 900*time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodPut, url, bytes.NewReader(payload))
	if err != nil {
		return err
	}
	for key, value := range headers {
		request.Header.Set(key, value)
	}
	request.Header.Set("User-Agent", "x86qw-gitlab-mirror/1")
	client := *p.client()
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	response, err := client.Do(request)
	if err != nil {
		return fmt.Errorf("GitLab upload failed: %v", err)
	}
	defer response.Body.Close()
	io.Copy(io.Discard, response.Body)
	if response.StatusCode < 200 || response.StatusCode > 299 {
		return fmt.Errorf("GitLab upload failed with HTTP status %d", response.StatusCode)
	}
	return nil
}

const userAgent = "x86qw-maintenance/1"
