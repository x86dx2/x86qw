//go:build windows

package maint

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/cli"
)

// Uninstall while ezQuake runs from the installation: Windows lets its
// running exe be renamed but not deleted, so the uninstall fails and puts
// back everything, the state and the CLI included.
func TestUninstallWhileTheGameRuns(t *testing.T) {
	if os.Getenv("X86QW_TEST_GAME") != "" {
		time.Sleep(time.Minute)
		return
	}
	isolate(t)
	root := managedInstall(t)
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	binary, err := os.ReadFile(self)
	if err != nil {
		t.Fatal(err)
	}
	runtime := filepath.Join(root, catalog.ClientSpecs[1].Runtime("nightly"))
	if err := os.WriteFile(runtime, binary, 0o755); err != nil {
		t.Fatal(err)
	}
	game := exec.Command(runtime, "-test.run=^TestUninstallWhileTheGameRuns$")
	game.Env = append(os.Environ(), "X86QW_TEST_GAME=1")
	if err := game.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		game.Process.Kill()
		game.Wait()
	})
	if _, err := Uninstall(root); err == nil {
		t.Fatal("the uninstall should fail while the game runs")
	}
	for _, kept := range []string{stateRelative, cli.BinaryName(), filepath.Base(runtime), "qw/maps/x86qw-a.bsp"} {
		if !exists(filepath.Join(root, filepath.FromSlash(kept))) {
			t.Errorf("%s is gone", kept)
		}
	}
}
