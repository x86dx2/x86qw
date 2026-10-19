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
