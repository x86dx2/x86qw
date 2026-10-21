package doctor

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestChecksAndHealth(t *testing.T) {
	missing := Diagnose(filepath.Join(t.TempDir(), "nope"), nil, time.Now())
	if missing.Healthy || missing.Checks[0].Status != "fail" || len(missing.Checks) != 6 {
		t.Fatalf("%+v", missing)
	}
	root := t.TempDir()
	os.MkdirAll(filepath.Join(root, ".x86qw"), 0o700)
	os.WriteFile(filepath.Join(root, ".x86qw", "state.json"), []byte("{}"), 0o644)
	stamp := filepath.Join(root, "timestamp.json")
	os.WriteFile(stamp, []byte(`{"signed":{"version":45,"expires":"2026-01-01T00:00:00Z"}}`), 0o644)
	report := Diagnose(root, []string{stamp}, time.Date(2026, 2, 1, 0, 0, 0, 0, time.UTC))
	if !report.Healthy || report.Checks[1].Summary != "cache TUF local v45 expirado" {
		t.Fatalf("%+v", report)
	}
}

func TestBundleNeverGoesInsideTheInstallation(t *testing.T) {
	root := t.TempDir()
	outside := filepath.Join(t.TempDir(), "bundle.zip")
	if got, err := BundleDestination(outside, root); err != nil || filepath.Base(got) != "bundle.zip" {
		t.Fatalf("fora: %s %v", got, err)
	}
	if _, err := BundleDestination(filepath.Join(root, "sub", "bundle.zip"), root); err == nil {
		t.Fatal("um caminho dentro da instalação deveria ser recusado")
	}
	// A name that starts with two dots is not the parent folder.
	if got, err := BundleDestination(filepath.Join(root, "..bundle.zip"), root); err == nil {
		t.Fatalf("..bundle.zip está dentro da instalação: %s", got)
	}
}
