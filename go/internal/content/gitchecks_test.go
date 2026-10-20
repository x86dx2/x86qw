package content

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// Synthetic repositories exercise check_lfs.py and check_committed_diff.py
// against their Go ports; the Python side runs only under
// X86QW_CONTENT_PARITY=1.

func gitRun(t *testing.T, dir string, args ...string) string {
	t.Helper()
	command := exec.Command("git", append([]string{"-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"}, args...)...)
	command.Dir = dir
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v: %s", args, err, output)
	}
	return strings.TrimSpace(string(output))
}

func isolateGit(t *testing.T) {
	t.Helper()
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git indisponível")
	}
	t.Setenv("GIT_CONFIG_GLOBAL", os.DevNull)
	t.Setenv("GIT_CONFIG_NOSYSTEM", "1")
}

// lfsRepository builds a repo whose working tree holds the given files;
// paths matching *.bin are LFS-tracked by attribute.
func lfsRepository(t *testing.T, files map[string][]byte) string {
	t.Helper()
	dir := t.TempDir()
	gitRun(t, dir, "init", "-q")
	for name, payload := range files {
		path := filepath.Join(dir, filepath.FromSlash(name))
		os.MkdirAll(filepath.Dir(path), 0o755)
		if err := os.WriteFile(path, payload, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	gitRun(t, dir, "add", "-A")
	// Attributes arrive after staging so no clean filter rewrites the files.
	os.WriteFile(filepath.Join(dir, ".gitattributes"), []byte("*.bin filter=lfs diff=lfs merge=lfs -text\n"), 0o644)
	gitRun(t, dir, "add", ".gitattributes")
	return dir
}

func pythonCheckLFS(t *testing.T, python, repo string) int {
	t.Helper()
	source := filepath.Join(repoRoot(t), "maintenance/tools/check_lfs.py")
	payload, err := os.ReadFile(source)
	if err != nil {
		t.Fatal(err)
	}
	script := filepath.Join(repo, "maintenance/tools/check_lfs.py")
	os.MkdirAll(filepath.Dir(script), 0o755)
	os.WriteFile(script, payload, 0o644)
	command := exec.Command(python, "-I", "-B", script)
	command.Dir = repo
	output, err := command.CombinedOutput()
	os.RemoveAll(filepath.Join(repo, "maintenance"))
	if exit, ok := err.(*exec.ExitError); ok {
		t.Logf("check_lfs.py: %s", output)
		return exit.ExitCode()
	}
	return 0
}

func TestCheckLFS(t *testing.T) {
	isolateGit(t)
	binary := append(bytes.Repeat([]byte{1}, 10), 0)
	large := append([]byte{0}, bytes.Repeat([]byte{7}, 1536*1024)...)
	pointer := append(append([]byte{}, GitLFSPointerPrefix...), "oid sha256:00\nsize 1\n"...)
	cases := []struct {
		name     string
		files    map[string][]byte
		problems int
	}{
		{"clean", map[string][]byte{"a.bin": binary, "small.dat": binary, "README": []byte("text\n")}, 0},
		{"unhydrated-pointer", map[string][]byte{"a.bin": pointer}, 1},
		{"large-direct-binary", map[string][]byte{"big.dat": large}, 1},
	}
	python := ""
	if os.Getenv("X86QW_CONTENT_PARITY") == "1" {
		python, _ = exec.LookPath("python3.13")
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			repo := lfsRepository(t, item.files)
			_, problems, err := CheckLFS(repo)
			if err != nil {
				t.Fatal(err)
			}
			if len(problems) != item.problems {
				t.Fatalf("problemas: %v", problems)
			}
			if python != "" {
				if code := pythonCheckLFS(t, python, repo); (code != 0) != (item.problems > 0) {
					t.Fatalf("check_lfs.py exit %d diverge de %v", code, problems)
				}
			}
		})
	}
}

func TestCommittedDiffCheck(t *testing.T) {
	isolateGit(t)
	dir := t.TempDir()
	gitRun(t, dir, "init", "-q")
	os.WriteFile(filepath.Join(dir, "a.txt"), []byte("clean\n"), 0o644)
	gitRun(t, dir, "add", "a.txt")
	gitRun(t, dir, "commit", "-q", "-m", "base")
	base := gitRun(t, dir, "rev-parse", "HEAD")
	os.WriteFile(filepath.Join(dir, "a.txt"), []byte("clean\nstill clean\n"), 0o644)
	gitRun(t, dir, "commit", "-q", "-am", "clean change")
	clean := gitRun(t, dir, "rev-parse", "HEAD")
	os.WriteFile(filepath.Join(dir, "a.txt"), []byte("clean\nstill clean\ntrailing   \n"), 0o644)
	gitRun(t, dir, "commit", "-q", "-am", "dirty change")
	dirty := gitRun(t, dir, "rev-parse", "HEAD")

	python := ""
	if os.Getenv("X86QW_CONTENT_PARITY") == "1" {
		python, _ = exec.LookPath("python3.13")
	}
	for _, item := range []struct {
		name, before, after string
		fails               bool
	}{
		{"clean-push", base, clean, false},
		{"whitespace-push", clean, dirty, true},
		{"new-branch-head", zeroSHA, dirty, true},
	} {
		t.Run(item.name, func(t *testing.T) {
			event := object("before", item.before, "after", item.after)
			command, err := CommittedDiffCommand("push", event)
			if err != nil {
				t.Fatal(err)
			}
			_, goErr := CheckCommittedDiff(dir, command)
			if (goErr != nil) != item.fails {
				t.Fatalf("Go: %v", goErr)
			}
			if python == "" {
				return
			}
			eventFile := filepath.Join(t.TempDir(), "event.json")
			os.WriteFile(eventFile, []byte(`{"before": "`+item.before+`", "after": "`+item.after+`"}`), 0o644)
			script := filepath.Join(repoRoot(t), "maintenance/tools/check_committed_diff.py")
			process := exec.Command(python, "-I", "-B", script, "--event-name", "push", "--event-file", eventFile)
			process.Dir = dir
			if err := process.Run(); (err != nil) != item.fails {
				t.Fatalf("check_committed_diff.py: %v", err)
			}
		})
	}
	if _, err := CommittedDiffCommand("push", object("before", "xyz", "after", dirty)); err == nil {
		t.Fatal("SHA inválido aceito")
	}
}
