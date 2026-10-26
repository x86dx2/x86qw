//go:build windows

package setup

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// A copy of this test binary run with X86QW_TEST_ECHO prints its arguments,
// one per line, before testing parses any flag.
func init() {
	if os.Getenv("X86QW_TEST_ECHO") != "" {
		for _, arg := range os.Args[1:] {
			fmt.Println(arg)
		}
		os.Exit(0)
	}
}

// The commands x86QW prints, pasted into PowerShell, where the installer
// runs, start the same program with the same arguments: from a folder with
// spaces, a quote and a dollar, or as x86qw on the PATH.
func TestPrintedCommandsRunInPowerShell(t *testing.T) {
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	binary, err := os.ReadFile(self)
	if err != nil {
		t.Fatal(err)
	}
	folder := filepath.Join(t.TempDir(), "My Games", "it's $HOME")
	os.MkdirAll(folder, 0o755)
	program := filepath.Join(folder, "x86qw.exe")
	if err := os.WriteFile(program, binary, 0o755); err != nil {
		t.Fatal(err)
	}
	args := []string{"host", "--target", folder, "--servers", "duel:dm6,ffa:dm3+bots", "--server-name", "x86QW (KTX) #1 & co @home", "--base-port", "27500"}
	// A nick PowerShell would split at the dot, and a selection of no
	// component, which Windows PowerShell 5.1 would drop as an empty argument.
	o := Defaults(Play, folder)
	o.Nick, o.Content, o.Components = "-x.y-", "custom", nil
	recipe := o.Recipe()[1:]
	for _, command := range [][]string{append([]string{program}, args...), append([]string{"x86qw"}, args...), append([]string{program}, recipe...)} {
		args := command[1:]
		line := ShellJoin(command)
		shell := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", line)
		shell.Env = append(os.Environ(), "X86QW_TEST_ECHO=1", "PATH="+folder+";"+os.Getenv("PATH"))
		output, err := shell.CombinedOutput()
		if got := strings.Split(strings.TrimRight(strings.ReplaceAll(string(output), "\r\n", "\n"), "\n"), "\n"); err != nil || strings.Join(got, "\n") != strings.Join(args, "\n") {
			t.Fatalf("%s\n%v\n%s", line, err, output)
		}
	}
}
