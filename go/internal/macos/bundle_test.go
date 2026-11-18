package macos

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestEnsureProcessAbsentNamesASilentFailure runs a pgrep that fails
// without a word: the message must still say what went wrong.
func TestEnsureProcessAbsentNamesASilentFailure(t *testing.T) {
	if !IsDarwin() {
		t.Skip("pgrep is only checked on macOS")
	}
	bin := t.TempDir()
	if err := os.WriteFile(filepath.Join(bin, "pgrep"), []byte("#!/bin/sh\nexit 3\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", bin)
	err := EnsureProcessAbsent("ezQuake")
	if err == nil || !strings.Contains(err.Error(), "exit status 3") {
		t.Fatalf("error %v", err)
	}
}
