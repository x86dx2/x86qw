package install

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/receipt"
)

func realRoot(t *testing.T) string {
	t.Helper()
	home, _ := os.UserHomeDir()
	root := filepath.Join(home, "Games", "x86qw")
	if _, err := os.Stat(filepath.Join(root, ".x86qw", "state.json")); err != nil {
		t.Skip("sem instalação local")
	}
	return root
}

// TestVerifyRealInstallation checks the same installation that the Python
// `x86qw verify` accepts.
func TestVerifyRealInstallation(t *testing.T) {
	report, err := Verify(realRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	if report.Clients == 0 || len(report.Components) == 0 || report.Files == 0 {
		t.Fatalf("relatório vazio: %+v", report)
	}
}

func TestPackageOrderPriorityThenCaseInsensitive(t *testing.T) {
	root := t.TempDir()
	os.MkdirAll(filepath.Join(root, "qw"), 0o755)
	for _, name := range []string{"zeta.pk3", "Alpha.pk3", "models.pk3", "ktx.pk3", "notes.txt"} {
		os.WriteFile(filepath.Join(root, "qw", name), nil, 0o644)
	}
	order, err := ExpectedPackageOrder(root)
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(order, ","); got != "ktx.pk3,models.pk3,Alpha.pk3,zeta.pk3" {
		t.Fatalf("ordem: %s", got)
	}
}

func TestStatusOfMissingTarget(t *testing.T) {
	status := ReadStatus(filepath.Join(t.TempDir(), "nope"))
	if status.Installation != "missing" || status.State != "missing" || status.Sessions == nil {
		t.Fatalf("%+v", status)
	}
}

func TestVerifyDetectsTamperedFile(t *testing.T) {
	root := t.TempDir()
	write := func(name, content string) {
		path := filepath.Join(root, filepath.FromSlash(name))
		os.MkdirAll(filepath.Dir(path), 0o755)
		if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write("qw/readme.txt", "original")
	digest, _ := fileSHA256(filepath.Join(root, "qw/readme.txt"))
	inventory := "qw/readme.txt\t" + digest + "\n"
	write(".x86qw/components/docs/inventory", inventory)
	invDigest, _ := fileSHA256(filepath.Join(root, ".x86qw/components/docs/inventory"))
	write(".x86qw/components/docs/receipt", "format\t1\ncomponent\tdocs\nselection\t1\nsource\tx\ninventory_sha256\t"+invDigest+"\n")
	write("qw/readme.txt", "tampered")
	records, err := loadForTest(root)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := verifyRecord(root, records); err == nil || !strings.Contains(err.Error(), "alterado") {
		t.Fatalf("arquivo adulterado não detectado: %v", err)
	}
}

func loadForTest(root string) (*receipt.Record, error) {
	return receipt.Load(filepath.Join(root, ".x86qw/components/docs"))
}
