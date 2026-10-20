package content

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// testdata/candidate was produced by release_candidate.py prepare (nested
// archive members, project MIT and upstream entries, a path with a space).
func copyCandidate(t *testing.T) string {
	t.Helper()
	destination := filepath.Join(t.TempDir(), "candidate")
	err := filepath.WalkDir("testdata/candidate", func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		relative, _ := filepath.Rel("testdata/candidate", path)
		target := filepath.Join(destination, relative)
		if entry.IsDir() {
			return os.MkdirAll(target, 0o755)
		}
		payload, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(target, payload, 0o644)
	})
	if err != nil {
		t.Fatal(err)
	}
	return destination
}

func TestVerifyCandidate(t *testing.T) {
	if _, err := VerifyCandidate(copyCandidate(t), VerifyCandidateOptions{}); err != nil {
		t.Fatalf("fixture rejected: %v", err)
	}
	for name, test := range map[string]struct {
		mutate func(candidate string)
		want   string
	}{
		"payload": {func(c string) { os.WriteFile(filepath.Join(c, "a.txt"), []byte("tampered\n"), 0o644) }, "artefato do candidato diverge: a.txt"},
		"unregistered": {func(c string) { os.WriteFile(filepath.Join(c, "zz.bin"), []byte("x"), 0o644) },
			"arquivos não registrados no candidato: ['zz.bin']"},
		"digest": {func(c string) {
			path := filepath.Join(c, "candidate.json")
			payload, _ := os.ReadFile(path)
			os.WriteFile(path, []byte(strings.Replace(string(payload), `"1.2.3-rc.1"`, `"1.2.4"`, 1)), 0o644)
		}, "candidate_sha256 do manifest diverge"},
		"bound metadata": {func(c string) {
			path := filepath.Join(c, "sbom.spdx.json")
			payload, _ := os.ReadFile(path)
			os.WriteFile(path, append(payload, ' '), 0o644)
		}, "metadata imutável diverge do manifest: sbom.spdx.json"},
		"evidence": {func(c string) { os.WriteFile(filepath.Join(c, "release-evidence.json"), []byte("{}"), 0o644) },
			"evidência nativa M3 não é suportada"},
		"symlink": {func(c string) { os.Symlink("a.txt", filepath.Join(c, "link")) }, "candidato não aceita symlink"},
	} {
		candidate := copyCandidate(t)
		test.mutate(candidate)
		if _, err := VerifyCandidate(candidate, VerifyCandidateOptions{}); err == nil || !strings.Contains(err.Error(), test.want) {
			t.Errorf("%s: %v", name, err)
		}
	}
	if _, err := VerifyCandidate(copyCandidate(t), VerifyCandidateOptions{TrustRoot: "root.json"}); err == nil {
		t.Error("--trust-root accepted without the evidence port")
	}
}
