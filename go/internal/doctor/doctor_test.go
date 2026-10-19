package doctor

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestChecksAndHealth(t *testing.T) {
	missing := Diagnose(filepath.Join(t.TempDir(), "nope"), []string{"verify"}, nil, time.Now())
	if missing.Healthy || missing.Checks[0].Status != "fail" || len(missing.Checks) != 7 {
		t.Fatalf("%+v", missing)
	}
	root := t.TempDir()
	os.MkdirAll(filepath.Join(root, ".x86qw"), 0o700)
	os.WriteFile(filepath.Join(root, ".x86qw", "state.json"), []byte("{}"), 0o644)
	stamp := filepath.Join(root, "timestamp.json")
	os.WriteFile(stamp, []byte(`{"signed":{"version":45,"expires":"2026-01-01T00:00:00Z"}}`), 0o644)
	report := Diagnose(root, []string{"verify"}, []string{stamp}, time.Date(2026, 2, 1, 0, 0, 0, 0, time.UTC))
	if !report.Healthy || report.Checks[2].Summary != "cache TUF local v45 expirado" {
		t.Fatalf("%+v", report)
	}
}
