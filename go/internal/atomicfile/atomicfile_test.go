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
	if err := os.WriteFile(path, []byte("velho"), 0o644); err != nil {
		t.Fatal(err)
	}
	var group sync.WaitGroup
	for range 8 {
		group.Add(1)
		go func() {
			defer group.Done()
			if err := Write(path, []byte("novo"), 0o600); err != nil {
				t.Error(err)
			}
		}()
	}
	group.Wait()
	if data, _ := os.ReadFile(path); string(data) != "novo" {
		t.Fatalf("conteúdo: %q", data)
	}
	if info, _ := os.Stat(path); runtime.GOOS != "windows" && info.Mode().Perm() != 0o600 {
		t.Fatalf("modo: %v", info.Mode())
	}
	if entries, _ := os.ReadDir(dir); len(entries) != 1 {
		t.Fatalf("sobrou temporário: %v", entries)
	}
	if err := Write(filepath.Join(dir, "falta", "doc.json"), []byte("x"), 0o644); err == nil {
		t.Fatal("uma pasta que não existe deveria falhar")
	}
}
