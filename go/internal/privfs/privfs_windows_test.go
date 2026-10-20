//go:build windows

package privfs

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"golang.org/x/sys/windows"
)

// protected reports whether path has a protected DACL that grants access
// only to the current user and LOCAL SYSTEM.
func protected(t *testing.T, path string) bool {
	t.Helper()
	sd, err := windows.GetNamedSecurityInfo(path, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION)
	if err != nil {
		t.Fatal(err)
	}
	control, _, err := sd.Control()
	if err != nil {
		t.Fatal(err)
	}
	return control&windows.SE_DACL_PROTECTED != 0 && IsPrivateFile(path, nil)
}

func acl(t *testing.T, path string) string {
	t.Helper()
	sd, err := windows.GetNamedSecurityInfo(path, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION)
	if err != nil {
		t.Fatal(err)
	}
	return sd.String()
}

func TestPrivateDirectoriesAndInheritance(t *testing.T) {
	root := filepath.Join(t.TempDir(), "a", "b")
	if err := MkdirAll(root); err != nil {
		t.Fatal(err)
	}
	if err := Mkdir(root); !os.IsExist(err) {
		t.Fatalf("segundo Mkdir: %v", err)
	}
	for _, path := range []string{filepath.Dir(root), root} {
		if !protected(t, path) || strings.Count(acl(t, path), "(A;") != 2 {
			t.Fatalf("DACL de %s não é privada: %s", path, acl(t, path))
		}
	}
	file := filepath.Join(root, "state.json")
	if err := os.WriteFile(file, []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	if !IsPrivateFile(file, nil) || strings.Count(acl(t, file), "(A;") != 2 {
		t.Fatalf("arquivo não herdou a DACL privada: %s", acl(t, file))
	}
	plain := filepath.Join(t.TempDir(), "plain")
	os.Mkdir(plain, 0o755)
	if err := Protect(plain); err != nil {
		t.Fatal(err)
	}
	if !protected(t, plain) {
		t.Fatalf("Protect não aplicou a DACL privada: %s", acl(t, plain))
	}
}
