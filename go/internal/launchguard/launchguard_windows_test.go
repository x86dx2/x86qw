//go:build windows

package launchguard

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

func TestHoldBlocksReplacementAndLaunches(t *testing.T) {
	source := filepath.Join(os.Getenv("SystemRoot"), "System32", "whoami.exe")
	payload, err := os.ReadFile(source)
	if err != nil {
		t.Skip("whoami.exe indisponível")
	}
	path := filepath.Join(t.TempDir(), "tool.exe")
	os.WriteFile(path, payload, 0o755)
	sum := sha256.Sum256(payload)
	if _, err := Hold(path, hex.EncodeToString(sum[:4])); err == nil {
		t.Fatal("hash divergente aceito")
	}
	release, err := Hold(path, hex.EncodeToString(sum[:]))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(path, path+".old"); err == nil {
		t.Fatal("o executável foi renomeado enquanto segurado")
	}
	if err := os.WriteFile(path, []byte("x"), 0o755); err == nil {
		t.Fatal("o executável foi sobrescrito enquanto segurado")
	}
	if output, err := exec.Command(path).CombinedOutput(); err != nil {
		t.Fatalf("o executável segurado não iniciou: %v %s", err, output)
	}
	release()
	if err := os.Rename(path, path+".old"); err != nil {
		t.Fatalf("liberação não soltou o arquivo: %v", err)
	}
}
