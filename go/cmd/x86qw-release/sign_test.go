package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/tufsign"
)

// TestLiveVersionSurvivesAnExpiredChain: sign must work when production no
// longer authenticates, so the version then comes from the signed
// timestamp alone. A test server stands in for production; a tampered
// timestamp is refused.
func TestLiveVersionSurvivesAnExpiredChain(t *testing.T) {
	projection := filepath.Join("..", "..", "..", filepath.FromSlash(trustDir))
	generation, err := tufsign.ReadGeneration(projection)
	if err != nil {
		t.Fatal(err)
	}
	timestamp, err := os.ReadFile(filepath.Join(projection, "metadata", "timestamp.json"))
	if err != nil {
		t.Fatal(err)
	}
	serve := func(payload string) *httptest.Server {
		return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if !strings.HasSuffix(r.URL.Path, "/metadata/timestamp.json") {
				http.NotFound(w, r)
				return
			}
			w.Write([]byte(payload))
		}))
	}
	server := serve(string(timestamp))
	defer server.Close()
	if version, err := liveTimestamp(server.URL + "/trust/"); err != nil || version != generation.Timestamp {
		t.Fatalf("versão %d, esperada %d: %v", version, generation.Timestamp, err)
	}
	tampered := serve(strings.Replace(string(timestamp), `"version":`, `"version": 1000, "x":`, 1))
	defer tampered.Close()
	if _, err := liveTimestamp(tampered.URL + "/trust/"); err == nil {
		t.Fatal("timestamp adulterado aceito")
	}
}
