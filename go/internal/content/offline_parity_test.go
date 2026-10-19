package content

import (
	"bytes"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Offline commands compared byte for byte with manage.py.

func runManage(t *testing.T, python, root string, args ...string) (int, string, string) {
	t.Helper()
	command := exec.Command(python, append([]string{"-I", "-B", filepath.Join(root, "maintenance/manage.py")}, args...)...)
	command.Dir = root
	var stdout, stderr bytes.Buffer
	command.Stdout, command.Stderr = &stdout, &stderr
	err := command.Run()
	if exit, ok := err.(*exec.ExitError); ok {
		return exit.ExitCode(), stdout.String(), stderr.String()
	}
	if err != nil {
		t.Fatal(err)
	}
	return 0, stdout.String(), stderr.String()
}

func TestOfflineCheckParity(t *testing.T) {
	python := parityPython(t)
	root := repoRoot(t)
	for _, asJSON := range []bool{false, true} {
		args := []string{"check", "--offline"}
		if asJSON {
			args = append(args, "--json")
		}
		pythonCode, pythonOut, _ := runManage(t, python, root, args...)
		var out bytes.Buffer
		code, err := NewProject(root).Check(&out, &bytes.Buffer{}, true, asJSON)
		if err != nil {
			t.Fatal(err)
		}
		if code != pythonCode || out.String() != pythonOut {
			t.Fatalf("check %v diverge (exit %d/%d):\npython:\n%s\ngo:\n%s", args, pythonCode, code, pythonOut, out.String())
		}
	}
}

// TestAddDryRunParity validates distribution-change definitions offline
// with both implementations; both must accept or reject with the same
// message.
func TestAddDryRunParity(t *testing.T) {
	python := parityPython(t)
	root := repoRoot(t)
	requireHydrated(t, root)
	manifest, err := LoadManifest(filepath.Join(root, ManifestPath))
	if err != nil {
		t.Fatal(err)
	}
	const relative = "mods/ktx/1.47/source/ktx-1.47.tar.gz"
	metadata, ok := manifest.Value("files").(*Object).Value(relative).(*Object)
	if !ok {
		t.Skip("artefato KTX ausente do manifesto")
	}
	entry := func(mutate func(*Object)) *Object {
		file := object("destination", "dist/"+relative, "url", metadata.Value("url"), "size", metadata.Value("size"),
			"sha256", metadata.Value("sha256"), "managed", true, "distribution_component", metadata.Value("component"),
			"consumer", metadata.Value("consumer"), "package", metadata.Value("package"))
		if mutate != nil {
			mutate(file)
		}
		return object("format", pyjson.Number("1"), "project", "x86qw", "kind", "distribution-change", "files", []any{file})
	}
	cases := map[string]*Object{
		"identical-managed-file": entry(nil),
		"digest-mismatch":        entry(func(file *Object) { file.Set("sha256", strings.Repeat("0", 64)) }),
		"unknown-consumer":       entry(func(file *Object) { file.Set("consumer", "install:nobody") }),
		"wrong-package":          entry(func(file *Object) { file.Set("package", "pro-x") }),
		"unmanaged-remote":       entry(func(file *Object) { file.Set("managed", false) }),
		"outside-dist":           entry(func(file *Object) { file.Set("destination", "maintenance/x.bin") }),
		"wrong-kind":             object("format", pyjson.Number("1"), "project", "x86qw", "kind", "other", "files", []any{}),
	}
	for name, definition := range cases {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "definition.json")
			if err := WriteJSON(path, definition); err != nil {
				t.Fatal(err)
			}
			pythonCode, pythonOut, pythonErr := runManage(t, python, root, "add", path, "--dry-run")
			var out bytes.Buffer
			goErr := NewProject(root).Add(&out, path, AddOptions{DryRun: true})
			if (pythonCode == 0) != (goErr == nil) {
				t.Fatalf("aceitação diverge: python exit %d (%s), go %v", pythonCode, strings.TrimSpace(pythonErr), goErr)
			}
			if goErr != nil {
				if strings.TrimSpace(pythonErr) != "[ERRO] "+goErr.Error() {
					t.Errorf("mensagem diverge:\npython: %s\ngo:     [ERRO] %v", strings.TrimSpace(pythonErr), goErr)
				}
				return
			}
			if out.String() != pythonOut {
				t.Errorf("plano diverge:\npython:\n%s\ngo:\n%s", pythonOut, out.String())
			}
		})
	}
}
