//go:build windows

package maint

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"

	"github.com/x86dx2/x86qw/go/internal/cli"
)

// Purge run by the installation's own x86qw.exe, as a user runs it: Windows
// will not delete that running executable, so it goes once the command
// ends, and the folder and an empty ~/Games with it; the command still
// succeeds.
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
	root, games := inGames(t, managedInstall(t))
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
	for deadline := time.Now().Add(30 * time.Second); exists(games); time.Sleep(100 * time.Millisecond) {
		if time.Now().After(deadline) {
			entries, _ := os.ReadDir(root)
			t.Fatalf("the installation folder stayed: %v", entries)
		}
	}
}

// The running x86qw.exe goes however long the app that ran the uninstall
// stays open; x86qw.cmd waits for its cmd.exe at most a minute.
func TestRemovalWaitsForTheCLIWithoutALimit(t *testing.T) {
	if script := removalScript([]int{1}, `C:\x86qw\x86qw.exe`, 0); !strings.HasPrefix(script, "Wait-Process -Id 1 -ErrorAction") {
		t.Fatalf("script: %s", script)
	}
	if script := removalScript([]int{1, 2}, `C:\x86qw\x86qw.cmd`, 60); !strings.HasPrefix(script, "Wait-Process -Id 1,2 -Timeout 60 ") {
		t.Fatalf("script: %s", script)
	}
}
