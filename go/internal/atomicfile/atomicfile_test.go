package atomicfile

import (
	"os"
	"path/filepath"
	"runtime"
	"sync"
	"testing"
)

func TestWriteReplacesAndLeavesNothingBehind(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "doc.json")
	if err := os.WriteFile(path, []byte("old"), 0o644); err != nil {
		t.Fatal(err)
	}
	var group sync.WaitGroup
	for range 8 {
		group.Add(1)
		go func() {
			defer group.Done()
			if err := Write(path, []byte("new"), 0o600); err != nil {
				t.Error(err)
			}
		}()
	}
	group.Wait()
	if data, _ := os.ReadFile(path); string(data) != "new" {
		t.Fatalf("content: %q", data)
	}
	if info, _ := os.Stat(path); runtime.GOOS != "windows" && info.Mode().Perm() != 0o600 {
		t.Fatalf("mode: %v", info.Mode())
	}
	if entries, _ := os.ReadDir(dir); len(entries) != 1 {
		t.Fatalf("temporary file left: %v", entries)
	}
	if err := Write(filepath.Join(dir, "missing", "doc.json"), []byte("x"), 0o644); err == nil {
		t.Fatal("a folder that does not exist should fail")
	}
}
