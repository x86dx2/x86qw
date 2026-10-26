//go:build windows

package services

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"golang.org/x/sys/windows"
)

var (
	getConsoleWindow = windows.NewLazySystemDLL("kernel32.dll").NewProc("GetConsoleWindow")
	isWindowVisible  = windows.NewLazySystemDLL("user32.dll").NewProc("IsWindowVisible")
)

// TestConsoleWindowHelper plays the two parts TestServersOpenNoConsoleWindow
// starts: a background controller, which starts a server stand-in the way
// the supervisor does, and that stand-in, which reports whether a console
// window shows for it.
func TestConsoleWindowHelper(t *testing.T) {
	report := os.Getenv("X86QW_TEST_CONSOLE_REPORT")
	switch os.Getenv("X86QW_TEST_CONSOLE") {
	case "controller":
		self, _ := os.Executable()
		os.Setenv("X86QW_TEST_CONSOLE", "server")
		command, err := startDetached([]string{self, "-test.run=^TestConsoleWindowHelper$"}, filepath.Dir(report), nil)
		if err != nil {
			os.WriteFile(report, []byte("error: "+err.Error()), 0o644)
			return
		}
		command.Wait()
	case "server":
		window, _, _ := getConsoleWindow.Call()
		visible, _, _ := isWindowVisible.Call(window)
		os.WriteFile(report, []byte(fmt.Sprintf("visible=%v", window != 0 && visible != 0)), 0o644)
	default:
		t.Skip("run by TestServersOpenNoConsoleWindow")
	}
}

// consoleReport runs a controller through start and returns what its server
// stand-in reported.
func consoleReport(t *testing.T, start func(self string, arguments []string) (*exec.Cmd, error)) string {
	t.Helper()
	report := filepath.Join(t.TempDir(), "report")
	t.Setenv("X86QW_TEST_CONSOLE", "controller")
	t.Setenv("X86QW_TEST_CONSOLE_REPORT", report)
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	command, err := start(self, []string{"-test.run=^TestConsoleWindowHelper$"})
	if err != nil {
		t.Fatal(err)
	}
	defer command.Process.Kill()
	for deadline := time.Now().Add(30 * time.Second); time.Now().Before(deadline); time.Sleep(100 * time.Millisecond) {
		if data, err := os.ReadFile(report); err == nil && len(data) > 0 {
			command.Wait()
			return strings.TrimSpace(string(data))
		}
	}
	t.Fatal("the server stand-in never reported")
	return ""
}

// The servers a background session starts share its hidden console instead
// of each opening a window that ends the session when closed. A controller
// started the old way, detached from any console, shows the window this test
// looks for.
func TestServersOpenNoConsoleWindow(t *testing.T) {
	detached := consoleReport(t, func(self string, arguments []string) (*exec.Cmd, error) {
		command := exec.Command(self, arguments...)
		command.SysProcAttr = &syscall.SysProcAttr{CreationFlags: windows.CREATE_NEW_PROCESS_GROUP | windows.DETACHED_PROCESS}
		return command, command.Start()
	})
	if detached != "visible=true" {
		t.Skipf("this session shows no console windows to tell apart (%s)", detached)
	}
	if got := consoleReport(t, func(self string, arguments []string) (*exec.Cmd, error) {
		return spawnBackgroundController(self, arguments, nil)
	}); got != "visible=false" {
		t.Fatalf("a server of a background session: %s", got)
	}
}
