package install

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/buildinfo"
	"github.com/x86dx2/x86qw/go/internal/cli"
	"github.com/x86dx2/x86qw/go/internal/receipt"
)

func realRoot(t *testing.T) string {
	t.Helper()
	home, _ := os.UserHomeDir()
	root := filepath.Join(home, "Games", "x86qw")
	if _, err := os.Stat(filepath.Join(root, ".x86qw", "state.json")); err != nil {
		t.Skip("no local installation")
	}
	return root
}

// TestVerifyRealInstallation verifies the developer's own installation.
func TestVerifyRealInstallation(t *testing.T) {
	report, err := Verify(realRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	if report.Clients == 0 || len(report.Components) == 0 || report.Files == 0 {
		t.Fatalf("empty report: %+v", report)
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
		t.Fatalf("order: %s", got)
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
	if _, err := verifyRecord(root, records); err == nil || !strings.Contains(err.Error(), "changed") {
		t.Fatalf("tampered file not detected: %v", err)
	}
}

func loadForTest(root string) (*receipt.Record, error) {
	return receipt.Load(filepath.Join(root, ".x86qw/components/docs"))
}

func TestEmbeddedGamesMatchMaintenance(t *testing.T) {
	want, err := os.ReadFile(filepath.Join("..", "..", "..", "maintenance", "inventory", "games.json"))
	if err != nil {
		t.Fatal(err)
	}
	if string(want) != string(GamesJSON) {
		t.Fatal("go/internal/install/games.json differs from maintenance/inventory/games.json")
	}
}

// TestPlaySupportOfRealInstallation derives the gamecode the installation
// recorded.
func TestPlaySupportOfRealInstallation(t *testing.T) {
	root := realRoot(t)
	games, err := LoadGames()
	if err != nil {
		t.Fatal(err)
	}
	available := AvailableGames(root, games)
	issues, expected, err := PlaySupportIssues(root, available)
	if err != nil {
		t.Fatal(err)
	}
	if len(issues) != 0 || len(expected) != 4 {
		t.Fatalf("play-support: %v (%d files)", issues, len(expected))
	}
}

// The CLI an update plans from is the one installed: an update may have
// replaced it under the CLI still running.
func TestPlanComparesTheInstalledCLI(t *testing.T) {
	root := t.TempDir()
	if got := planCLI(root); got != buildinfo.Version {
		t.Fatalf("no receipt: %q", got)
	}
	os.MkdirAll(filepath.Join(root, ".x86qw", "cli"), 0o755)
	os.WriteFile(filepath.Join(root, ".x86qw", "cli", "receipt"), cli.Receipt("2.5.1"), 0o644)
	if got := PlanInput(root, nil, nil).CLIVersion; got != "2.5.1" {
		t.Fatalf("the plan compares %q, not the installed 2.5.1", got)
	}
}
