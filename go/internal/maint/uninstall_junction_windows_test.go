package maint

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// A managed folder the user turned into a junction leads out of the
// installation, though links are not followed through it: what lies there
// is not uninstalled.
func TestUninstallStaysOutOfAJunction(t *testing.T) {
	isolate(t)
	root := managedInstall(t)
	shared := filepath.Join(t.TempDir(), "shared-maps")
	if err := os.Rename(filepath.Join(root, "qw", "maps"), shared); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("cmd", "/c", "mklink", "/J", filepath.Join(root, "qw", "maps"), shared).CombinedOutput(); err != nil {
		t.Fatalf("mklink /J: %v %s", err, output)
	}
	report, err := Uninstall(root)
	if err != nil {
		t.Fatal(err)
	}
	if !exists(filepath.Join(shared, "x86qw-a.bsp")) || !strings.Contains(strings.Join(report.Warnings, "\n"), "x86qw-a.bsp") {
		t.Fatalf("behind the junction: a %v; warnings %v", exists(filepath.Join(shared, "x86qw-a.bsp")), report.Warnings)
	}
}
