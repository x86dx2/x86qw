package download

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func pin(body string) (int64, string) {
	digest := sha256.Sum256([]byte(body))
	return int64(len(body)), hex.EncodeToString(digest[:])
}

func server(t *testing.T, handler http.HandlerFunc) (*httptest.Server, Options) {
	t.Helper()
	srv := httptest.NewTLSServer(handler)
	t.Cleanup(srv.Close)
	client := srv.Client()
	client.CheckRedirect = func(request *http.Request, via []*http.Request) error {
		_, err := ValidateURL(request.URL.String())
		return err
	}
	return srv, Options{Client: client, Deadline: 10 * time.Second}
}

func TestFetchesPinnedArtifact(t *testing.T) {
	body := "payload bytes"
	srv, options := server(t, func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(body)) })
	size, digest := pin(body)
	destination := filepath.Join(t.TempDir(), "a.zip")
	if err := Fetch(context.Background(), Artifact{URLs: []string{srv.URL + "/a.zip"}, Size: size, SHA256: digest}, destination, options); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(destination)
	if string(data) != body || !Cached(destination, Artifact{Size: size, SHA256: digest}) {
		t.Fatal("artefato baixado diverge")
	}
}

func TestFallsBackToNextMirrorOnIntegrityAndNotFound(t *testing.T) {
	body := "good"
	srv, options := server(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/missing":
			http.NotFound(w, r)
		case "/bad":
			w.Write([]byte("evil"))
		default:
			w.Write([]byte(body))
		}
	})
	size, digest := pin(body)
	destination := filepath.Join(t.TempDir(), "a")
	artifact := Artifact{URLs: []string{srv.URL + "/missing", srv.URL + "/bad", srv.URL + "/good"}, Size: size, SHA256: digest}
	if err := Fetch(context.Background(), artifact, destination, options); err != nil {
		t.Fatal(err)
	}
}

func TestRetriesTransientStatus(t *testing.T) {
	var calls atomic.Int32
	body := "eventually"
	srv, options := server(t, func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) < 2 {
			w.Header().Set("Retry-After", "0")
			w.WriteHeader(503)
			return
		}
		w.Write([]byte(body))
	})
	size, digest := pin(body)
	if err := Fetch(context.Background(), Artifact{URLs: []string{srv.URL}, Size: size, SHA256: digest}, filepath.Join(t.TempDir(), "a"), options); err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 2 {
		t.Fatalf("chamadas: %d", calls.Load())
	}
}

func TestRejectsWrongHashOversizeAndPlainHTTP(t *testing.T) {
	srv, options := server(t, func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("0123456789")) })
	destination := filepath.Join(t.TempDir(), "a")
	size, _ := pin("0123456789")
	if err := Fetch(context.Background(), Artifact{URLs: []string{srv.URL}, Size: size, SHA256: strings.Repeat("0", 64)}, destination, options); err == nil {
		t.Fatal("hash divergente aceito")
	}
	if err := Fetch(context.Background(), Artifact{URLs: []string{srv.URL}, Size: 4, SHA256: strings.Repeat("0", 64)}, destination, options); err == nil {
		t.Fatal("corpo maior que o pin aceito")
	}
	if err := Fetch(context.Background(), Artifact{URLs: []string{"http://example.invalid/a"}, Size: 1, SHA256: strings.Repeat("0", 64)}, destination, options); err == nil {
		t.Fatal("HTTP simples aceito")
	}
	if _, err := os.Stat(destination); err == nil {
		t.Fatal("destino criado após falha")
	}
}

func TestRejectsRedirectToPlainHTTP(t *testing.T) {
	srv, options := server(t, func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, "http://example.invalid/a", http.StatusFound)
	})
	size, digest := pin("x")
	if err := Fetch(context.Background(), Artifact{URLs: []string{srv.URL}, Size: size, SHA256: digest}, filepath.Join(t.TempDir(), "a"), options); err == nil {
		t.Fatal("redirect para HTTP aceito")
	}
}

func TestReportsProgressUpToTheSize(t *testing.T) {
	body := strings.Repeat("q", 300_000)
	srv, options := server(t, func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(body)) })
	size, digest := pin(body)
	var seen []int64
	options.Progress = func(written int64) { seen = append(seen, written) }
	if err := Fetch(context.Background(), Artifact{URLs: []string{srv.URL + "/a.zip"}, Size: size, SHA256: digest}, filepath.Join(t.TempDir(), "a.zip"), options); err != nil {
		t.Fatal(err)
	}
	if len(seen) < 2 || seen[0] != 0 || seen[len(seen)-1] != size {
		t.Fatalf("progresso: %v", seen)
	}
	for index := 1; index < len(seen); index++ {
		if seen[index] < seen[index-1] {
			t.Fatalf("progresso voltou: %v", seen)
		}
	}
}

// TestContextCarriesTheClient: an install passes only ctx down to Fetch,
// so a client injected there must be used when Options sets none.
func TestContextCarriesTheClient(t *testing.T) {
	body := "from the context"
	srv, options := server(t, func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(body)) })
	size, digest := pin(body)
	artifact := Artifact{URLs: []string{srv.URL + "/c.zip"}, Size: size, SHA256: digest}
	ctx := WithClient(context.Background(), options.Client)
	if err := Fetch(ctx, artifact, filepath.Join(t.TempDir(), "c.zip"), Options{Deadline: 10 * time.Second, Attempts: 1}); err != nil {
		t.Fatalf("o cliente do contexto deveria confiar no servidor de teste: %v", err)
	}
	// Without it, the default client refuses the test certificate.
	if err := Fetch(context.Background(), artifact, filepath.Join(t.TempDir(), "d.zip"), Options{Deadline: 10 * time.Second, Attempts: 1}); err == nil {
		t.Fatal("o cliente padrão aceitou um certificado de teste")
	}
}
