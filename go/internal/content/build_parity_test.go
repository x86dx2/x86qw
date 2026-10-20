package content

import (
	"bytes"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// TestBuildParityWithManage builds every package with manage.py and with
// the Go port from identical clones and requires byte-identical trees
// (packages, manifest and ownership). It takes minutes, so it only runs
// with X86QW_CONTENT_PARITY=1 and X86QW_CONTENT_PARITY_BUILD=1.
func TestBuildParityWithManage(t *testing.T) {
	python := parityPython(t)
	if os.Getenv("X86QW_CONTENT_PARITY_BUILD") != "1" {
		t.Skip("defina X86QW_CONTENT_PARITY_BUILD=1 para comparar builds")
	}
	root := repoRoot(t)
	requireHydrated(t, root)
	const ref = "0123456789abcdef0123456789abcdef01234567"
	pythonClone, goClone := cloneCheckout(t, root), cloneCheckout(t, root)
	command := exec.Command(python, "-I", "-B", filepath.Join(pythonClone, "maintenance/manage.py"), "build", "--project-ref", ref)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("manage.py build: %v: %s", err, output)
	}
	if err := NewProject(goClone).Build(io.Discard, false, ref, time.Now()); err != nil {
		t.Fatal(err)
	}
	pythonFiles, _ := rglobFiles(filepath.Join(pythonClone, "maintenance/build"))
	goFiles, _ := rglobFiles(filepath.Join(goClone, "maintenance/build"))
	if strings.Join(pythonFiles, "\n") != strings.Join(goFiles, "\n") || len(goFiles) == 0 {
		t.Fatalf("árvores de build divergem:\n%v\n%v", pythonFiles, goFiles)
	}
	for _, relative := range goFiles {
		a, _ := os.ReadFile(filepath.Join(pythonClone, "maintenance/build", relative))
		b, _ := os.ReadFile(filepath.Join(goClone, "maintenance/build", relative))
		if !bytes.Equal(a, b) {
			t.Errorf("build diverge: %s", relative)
		}
	}
}
