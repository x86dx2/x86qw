package main

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestVerifyHandoff(t *testing.T) {
	commit := strings.Repeat("a", 40)
	digest := "sha256:" + strings.Repeat("b", 64)
	artifacts := `[{"id": 77, "name": "tuf-metadata-x", "expired": false, "digest": "` + digest + `", "created_at": "c", "updated_at": "u"}]`
	run := `{"head_sha": "` + commit + `", "status": "completed", "conclusion": "success", "path": ".github/workflows/w.yml",
		"event": "workflow_dispatch", "head_branch": "main", "repository": {"full_name": "o/r"}, "head_repository": {"full_name": "o/r"}}`
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer token" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		switch r.URL.Path {
		case "/repos/o/r/actions/runs/5":
			fmt.Fprint(w, run)
		case "/repos/o/r/actions/runs/5/artifacts":
			fmt.Fprintf(w, `{"artifacts": %s}`, artifacts)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()
	handoffAPIBase = server.URL
	branch := "main"
	request := func() handoffRequest {
		return handoffRequest{Repository: "o/r", RunID: "5", Commit: commit, Artifacts: []string{"tuf-metadata-x"},
			ArtifactIDs: map[string]int64{"tuf-metadata-x": 77}, Token: "token", Workflow: ".github/workflows/w.yml",
			Event: "workflow_dispatch", HeadBranch: &branch}
	}
	verified, err := verifyHandoff(request())
	if err != nil || verified["tuf-metadata-x"].ID != 77 || verified["tuf-metadata-x"].Digest != digest {
		t.Fatalf("%v %+v", err, verified)
	}
	output := filepath.Join(t.TempDir(), "out.json")
	if err := writeHandoffOutput(output, verified); err != nil {
		t.Fatal(err)
	}
	payload, _ := os.ReadFile(output)
	want := "{\n  \"tuf-metadata-x\": {\n    \"created_at\": \"c\",\n    \"digest\": \"" + digest + "\",\n    \"id\": 77,\n    \"updated_at\": \"u\"\n  }\n}\n"
	if info, _ := os.Stat(output); string(payload) != want || info.Mode().Perm() != 0o600 {
		t.Fatalf("output %o %q", info.Mode().Perm(), payload)
	}
	if err := writeHandoffOutput(output, verified); err == nil {
		t.Fatal("existing output overwritten")
	}

	for name, mutate := range map[string]func(*handoffRequest){
		"commit":   func(r *handoffRequest) { r.Commit = strings.Repeat("c", 40) },
		"id":       func(r *handoffRequest) { r.ArtifactIDs["tuf-metadata-x"] = 78 },
		"branch":   func(r *handoffRequest) { other := "dev"; r.HeadBranch = &other },
		"workflow": func(r *handoffRequest) { r.Workflow = ".github/workflows/other.yml" },
		"missing":  func(r *handoffRequest) { r.Artifacts = []string{"other"}; r.ArtifactIDs = nil },
		"token":    func(r *handoffRequest) { r.Token = "wrong" },
		"repo":     func(r *handoffRequest) { r.Repository = "o/r/x" },
	} {
		r := request()
		mutate(&r)
		if _, err := verifyHandoff(r); err == nil {
			t.Fatalf("%s: accepted", name)
		}
	}
	artifacts = strings.Replace(artifacts, `"expired": false`, `"expired": null`, 1)
	if _, err := verifyHandoff(request()); err == nil || !strings.Contains(err.Error(), "expirado") {
		t.Fatalf("expired: %v", err)
	}
}
