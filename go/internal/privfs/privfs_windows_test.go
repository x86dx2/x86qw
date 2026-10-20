//go:build windows

package privfs

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"golang.org/x/sys/windows"
)

func acl(t *testing.T, path string) string {
	t.Helper()
	sd, err := windows.GetNamedSecurityInfo(path, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION)
	if err != nil {
		t.Fatal(err)
	}
	return sd.String()
}

func TestPrivateDirectoriesAndInheritance(t *testing.T) {
	user, err := windows.GetCurrentProcessToken().GetTokenUser()
	if err != nil {
		t.Fatal(err)
	}
	sid := user.User.Sid.String()
	root := filepath.Join(t.TempDir(), "a", "b")
	if err := MkdirAll(root); err != nil {
		t.Fatal(err)
	}
	if err := Mkdir(root); !os.IsExist(err) {
		t.Fatalf("segundo Mkdir: %v", err)
	}
	for _, path := range []string{filepath.Dir(root), root} {
		got := acl(t, path)
		if !strings.HasPrefix(got, "D:P") || strings.Count(got, "(A;") != 2 || !strings.Contains(got, sid) || !strings.Contains(got, ";SY)") {
			t.Fatalf("DACL de %s não é privada: %s", path, got)
		}
	}
	file := filepath.Join(root, "state.json")
	if err := os.WriteFile(file, []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	if got := acl(t, file); strings.Count(got, "(A;") != 2 || !strings.Contains(got, sid) || strings.Contains(got, ";WD)") || strings.Contains(got, ";BU)") {
		t.Fatalf("arquivo não herdou a DACL privada: %s", got)
	}
	plain := filepath.Join(t.TempDir(), "plain")
	os.Mkdir(plain, 0o755)
	if err := Protect(plain); err != nil {
		t.Fatal(err)
	}
	if got := acl(t, plain); !strings.HasPrefix(got, "D:P") || strings.Count(got, "(A;") != 2 {
		t.Fatalf("Protect não aplicou a DACL privada: %s", got)
	}
}
