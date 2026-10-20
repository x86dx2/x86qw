//go:build windows

package cli

import (
	"os"
	"path/filepath"
	"testing"
)

func TestShortcutsLifecycle(t *testing.T) {
	t.Setenv("APPDATA", t.TempDir())
	t.Setenv("USERPROFILE", t.TempDir())
	root := t.TempDir()
	os.MkdirAll(filepath.Join(root, filepath.FromSlash(cliDir)), 0o755)
	os.WriteFile(filepath.Join(root, "x86qw.cmd"), []byte("@echo off\r\n"), 0o644)
	shortcuts, err := ShortcutPaths()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := InstallShortcuts(root); err != nil {
		t.Fatal(err)
	}
	for _, path := range shortcuts {
		if info, err := os.Stat(path); err != nil || info.Size() == 0 {
			t.Fatalf("atalho ausente: %s %v", path, err)
		}
	}
	if _, err := InstallShortcuts(root); err != nil {
		t.Fatalf("reinstalar o próprio atalho: %v", err)
	}
	other := t.TempDir()
	os.MkdirAll(filepath.Join(other, filepath.FromSlash(cliDir)), 0o755)
	os.WriteFile(filepath.Join(other, "x86qw.cmd"), []byte("@echo off\r\n"), 0o644)
	if _, err := InstallShortcuts(other); err == nil {
		t.Fatal("atalho de outra instalação foi substituído")
	}
	if err := RemoveShortcuts(other); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(shortcuts[0]); err != nil {
		t.Fatal("remover pela outra instalação apagou o atalho desta")
	}
	if err := RemoveShortcuts(root); err != nil {
		t.Fatal(err)
	}
	for _, path := range shortcuts {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Fatalf("atalho não removido: %s", path)
		}
	}
}

func TestUserPathLifecycle(t *testing.T) {
	root := t.TempDir()
	if err := AddUserPath(root); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { RemoveUserPath(root) })
	if err := AddUserPath(root); err != nil {
		t.Fatalf("adicionar de novo: %v", err)
	}
	if err := RemoveUserPath(root); err != nil {
		t.Fatal(err)
	}
}
