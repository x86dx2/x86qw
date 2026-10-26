//go:build !windows

package maint

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/lock"
)

// The CLI that removes itself does so in place, last: set aside under
// another name, it would no longer match its own lock, which the next
// command would then take for abandoned while the removal finishes.
func TestTheRunningCLIRemovesItselfInPlace(t *testing.T) {
	if os.Getenv("X86QW_TEST_SELF_REMOVE") != "" {
		self, _ := os.Executable()
		before, _ := lock.Identity(os.Getpid())
		err := quarantineRemove([]string{self})
		after, _ := lock.Identity(os.Getpid())
		fmt.Printf("%v|%s|%s\n", err, before.Executable, after.Executable)
		return
	}
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(self)
	if err != nil {
		t.Fatal(err)
	}
	binary := filepath.Join(t.TempDir(), "x86qw")
	if err := os.WriteFile(binary, data, 0o755); err != nil {
		t.Fatal(err)
	}
	command := exec.Command(binary, "-test.run=^TestTheRunningCLIRemovesItselfInPlace$")
	command.Env = append(os.Environ(), "X86QW_TEST_SELF_REMOVE=1")
	output, err := command.Output()
	line := strings.SplitN(string(output), "\n", 2)[0]
	parts := strings.Split(line, "|")
	if err != nil || len(parts) != 3 || parts[0] != "<nil>" || parts[1] != parts[2] {
		t.Fatalf("%v: %s", err, output)
	}
	if _, err := os.Lstat(binary); err == nil {
		t.Fatal("the CLI is still there")
	}
}
