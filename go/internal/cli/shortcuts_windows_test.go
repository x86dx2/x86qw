//go:build windows

package cli

import (
	"golang.org/x/sys/windows/registry"
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
			t.Fatalf("shortcut missing: %s %v", path, err)
		}
	}
	if _, err := InstallShortcuts(root); err != nil {
		t.Fatalf("reinstalling its own shortcut: %v", err)
	}
	other := t.TempDir()
	os.MkdirAll(filepath.Join(other, filepath.FromSlash(cliDir)), 0o755)
	os.WriteFile(filepath.Join(other, "x86qw.cmd"), []byte("@echo off\r\n"), 0o644)
	if _, err := InstallShortcuts(other); err == nil {
		t.Fatal("another installation's shortcut was replaced")
	}
	if err := RemoveShortcuts(other); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(shortcuts[0]); err != nil {
		t.Fatal("removing through the other installation deleted this one's shortcut")
	}
	if err := RemoveShortcuts(root); err != nil {
		t.Fatal(err)
	}
	for _, path := range shortcuts {
		if _, err := os.Stat(path); !os.IsNotExist(err) {
			t.Fatalf("shortcut not removed: %s", path)
		}
	}
}

func TestUserPathKeepsOtherEntriesUnexpanded(t *testing.T) {
	key, err := registry.OpenKey(registry.CURRENT_USER, "Environment", registry.QUERY_VALUE|registry.SET_VALUE)
	if err != nil {
		t.Fatal(err)
	}
	defer key.Close()
	original, kind, err := key.GetStringValue("Path")
	if err == nil {
		t.Cleanup(func() {
			if kind == registry.EXPAND_SZ {
				key.SetExpandStringValue("Path", original)
			} else {
				key.SetStringValue("Path", original)
			}
		})
	} else {
		t.Cleanup(func() { key.DeleteValue("Path") })
	}
	if err := key.SetExpandStringValue("Path", `%USERPROFILE%\bin;C:\Tools`); err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	for _, step := range []func(string) error{AddUserPath, AddUserPath} {
		if err := step(root); err != nil {
			t.Fatal(err)
		}
	}
	value, kind, err := key.GetStringValue("Path")
	if err != nil || kind != registry.EXPAND_SZ || value != `%USERPROFILE%\bin;C:\Tools;`+root {
		t.Fatalf("PATH after adding: %q (%d) %v", value, kind, err)
	}
	if err := RemoveUserPath(root); err != nil {
		t.Fatal(err)
	}
	if value, _, _ := key.GetStringValue("Path"); value != `%USERPROFILE%\bin;C:\Tools` {
		t.Fatalf("PATH after removing: %q", value)
	}
}

func TestWindowsHandlersComeAndGo(t *testing.T) {
	const classes = `Software\Classes\`
	for _, key := range []string{"qw", demoProgID, ".mvd", ".qwd"} {
		if k, err := registry.OpenKey(registry.CURRENT_USER, classes+key, registry.QUERY_VALUE); err == nil {
			k.Close()
			t.Skipf("HKCU\\%s%s already exists on this machine", classes, key)
		}
	}
	t.Cleanup(func() {
		for _, key := range []string{"qw", demoProgID, ".mvd", ".qwd"} {
			deleteTree(classes + key)
		}
	})
	root := t.TempDir()
	if err := SetHandlers(root, true, true); err != nil {
		t.Fatal(err)
	}
	if owned, _ := ownedCommand(classes+"qw", root); !owned {
		t.Fatal("qw:// should open with this installation")
	}
	k, _ := registry.OpenKey(registry.CURRENT_USER, classes+".mvd", registry.QUERY_VALUE)
	if value, _, _ := k.GetStringValue(""); value != demoProgID {
		t.Fatalf(".mvd: %q", value)
	}
	k.Close()
	if err := SetHandlers(t.TempDir(), true, false); err == nil {
		t.Fatal("another installation took qw://")
	}
	if err := SetHandlers(root, false, false); err != nil {
		t.Fatal(err)
	}
	if _, exists := ownedCommand(classes+"qw", root); exists {
		t.Fatal("qw:// should be gone")
	}
}
