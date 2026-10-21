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
		t.Skip("whoami.exe unavailable")
	}
	path := filepath.Join(t.TempDir(), "tool.exe")
	os.WriteFile(path, payload, 0o755)
	sum := sha256.Sum256(payload)
	if _, err := Hold(path, hex.EncodeToString(sum[:4])); err == nil {
		t.Fatal("mismatched hash accepted")
	}
	release, err := Hold(path, hex.EncodeToString(sum[:]))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(path, path+".old"); err == nil {
		t.Fatal("the executable was renamed while held")
	}
	if err := os.WriteFile(path, []byte("x"), 0o755); err == nil {
		t.Fatal("the executable was overwritten while held")
	}
	if output, err := exec.Command(path).CombinedOutput(); err != nil {
		t.Fatalf("the held executable did not start: %v %s", err, output)
	}
	release()
	if err := os.Rename(path, path+".old"); err != nil {
		t.Fatalf("release did not let the file go: %v", err)
	}
}
