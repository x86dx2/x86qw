//go:build windows

package maint

import (
	"fmt"
	"os"
	"os/exec"
	"testing"
	"time"

	"github.com/x86dx2/x86qw/go/internal/cli"
)

// Purge run by the installation's own x86qw.exe, as a user runs it: Windows
// will not delete that running executable, so it goes once the command
// ends, and the folder with it; the command still succeeds.
func TestPurgeByTheInstalledCLI(t *testing.T) {
	if target := os.Getenv("X86QW_TEST_PURGE"); target != "" {
		if _, err := Purge(target); err != nil {
			fmt.Println("purge:", err)
			os.Exit(1)
		}
		return
	}
	cache := isolate(t)
	ownedCache(t, cache)
	root := managedInstall(t)
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	binary, err := os.ReadFile(self)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cli.Command(root), binary, 0o755); err != nil {
		t.Fatal(err)
	}
	command := exec.Command(cli.Command(root), "-test.run=^TestPurgeByTheInstalledCLI$")
	command.Env = append(os.Environ(), "X86QW_TEST_PURGE="+root)
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("%v\n%s", err, output)
	}
	for deadline := time.Now().Add(30 * time.Second); exists(root); time.Sleep(100 * time.Millisecond) {
		if time.Now().After(deadline) {
			entries, _ := os.ReadDir(root)
			t.Fatalf("the installation folder stayed: %v", entries)
		}
	}
}
