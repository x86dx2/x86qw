//go:build windows

package services

import (
	"bytes"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"golang.org/x/sys/windows"
)

// shownWindows counts the visible windows of the process its parameter
// names; one callback serves every call, as callbacks are never freed.
var (
	shownWindows atomic.Int32
	countShown   = sync.OnceValue(func() uintptr {
		return windows.NewCallback(func(window windows.HWND, pid uintptr) uintptr {
			var owner uint32
			windows.GetWindowThreadProcessId(window, &owner)
			if uintptr(owner) == pid && windows.IsWindowVisible(window) {
				shownWindows.Add(1)
			}
			return 1
		})
	})
)

// TestMVDSVWindowHelper is the background controller TestTheWindowsServerOpensNoWindow
// starts. It starts the MVDSV it names as the supervisor does, hiding nothing,
// and reports whether it showed a window in its first three seconds, held its
// port and was still running.
func TestMVDSVWindowHelper(t *testing.T) {
	report := os.Getenv("X86QW_TEST_MVDSV_REPORT")
	if report == "" {
		t.Skip("run by TestTheWindowsServerOpensNoWindow")
	}
	dir := filepath.Dir(report)
	port, _ := strconv.Atoi(os.Getenv("X86QW_TEST_MVDSV_PORT"))
	log, err := os.Create(filepath.Join(dir, "mvdsv.log"))
	if err != nil {
		os.WriteFile(report, []byte("error: "+err.Error()), 0o644)
		return
	}
	defer log.Close()
	command, err := startDetached([]string{os.Getenv("X86QW_TEST_MVDSV"), "-basedir", dir, "-ip", "127.0.0.1", "-port", strconv.Itoa(port), "-mem", "64", "-noerrormsgbox",
		"+sv_progtype", "2", "+map", "start"}, dir, log, false)
	if err != nil {
		os.WriteFile(report, []byte("error: "+err.Error()), 0o644)
		return
	}
	pid := command.Process.Pid
	visible, held := false, false
	for deadline := time.Now().Add(3 * time.Second); time.Now().Before(deadline); time.Sleep(10 * time.Millisecond) {
		shownWindows.Store(0)
		enumWindows.Call(countShown(), uintptr(pid))
		visible = visible || shownWindows.Load() > 0
		held = held || udpHeldBy("127.0.0.1", port, pid)
	}
	alive := processGroupStatus(pid) == "alive"
	command.Process.Kill()
	command.Wait()
	ended := ""
	if !alive {
		ended = fmt.Sprintf(" exit=%#x", uint32(command.ProcessState.ExitCode()))
	}
	os.WriteFile(report, []byte(fmt.Sprintf("visible=%v held=%v alive=%v%s", visible, held, alive, ended)), 0o644)
}

// mvdsvReport runs binary under a background controller, on the start map
// with KTX, and returns what the helper reported and MVDSV's output.
func mvdsvReport(t *testing.T, binary string, game map[string]string) (string, string) {
	t.Helper()
	free, err := net.ListenPacket("udp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := free.LocalAddr().(*net.UDPAddr).Port
	free.Close()
	report := filepath.Join(t.TempDir(), "report")
	for name, source := range game {
		data, err := os.ReadFile(source)
		if err == nil {
			os.MkdirAll(filepath.Join(filepath.Dir(report), filepath.Dir(name)), 0o755)
			err = os.WriteFile(filepath.Join(filepath.Dir(report), name), data, 0o644)
		}
		if err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("X86QW_TEST_MVDSV_REPORT", report)
	t.Setenv("X86QW_TEST_MVDSV", binary)
	t.Setenv("X86QW_TEST_MVDSV_PORT", strconv.Itoa(port))
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	command, err := spawnBackgroundController(self, []string{"-test.run=^TestMVDSVWindowHelper$"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer command.Process.Kill()
	for deadline := time.Now().Add(60 * time.Second); time.Now().Before(deadline); time.Sleep(100 * time.Millisecond) {
		if data, err := os.ReadFile(report); err == nil && len(data) > 0 {
			command.Wait()
			output, _ := os.ReadFile(filepath.Join(filepath.Dir(report), "mvdsv.log"))
			return strings.TrimSpace(string(data)), string(output)
		}
	}
	t.Fatal("the controller never reported")
	return "", ""
}

// The x86QW build of MVDSV for Windows is a console program: a background
// session starts it in its hidden console, where it opens no window and serves
// its port. The upstream build, the control, opens a window of its own.
func TestTheWindowsServerOpensNoWindow(t *testing.T) {
	dist, err := filepath.Abs(filepath.Join("..", "..", "..", "dist"))
	if err != nil {
		t.Fatal(err)
	}
	console := filepath.Join(dist, "servers", "mvdsv", "1.11", "x86qw", "runtime", "windows-x64", "mvdsv.exe")
	upstream := filepath.Join(dist, "servers", "mvdsv", "1.11", "upstream", "windows-x64", "mvdsv.exe")
	game := map[string]string{
		"id1/pak0.pak":   filepath.Join(dist, "game-data", "id1", "pak0.pak"),
		"qw/qwprogs.qvm": filepath.Join(dist, "mods", "ktx", "1.47", "x86qw", "runtime", "qwprogs.qvm"),
	}
	for _, file := range []string{console, upstream, game["id1/pak0.pak"], game["qw/qwprogs.qvm"]} {
		if data, err := os.ReadFile(file); err != nil || bytes.HasPrefix(data, []byte("version https://git-lfs")) {
			t.Skipf("Git LFS did not fetch %s", file)
		}
	}
	consoleGot, consoleOutput := mvdsvReport(t, console, game)
	t.Logf("the console build: %s\n%s", consoleGot, consoleOutput)
	got, output := mvdsvReport(t, upstream, game)
	t.Logf("the release: %s\n%s", got, output)
	if !strings.HasPrefix(got, "visible=") {
		t.Fatalf("the release: %s\n%s", got, output)
	}
	if !strings.HasPrefix(got, "visible=true") {
		t.Skipf("this session shows no windows to tell apart (%s)", got)
	}
	if !strings.HasPrefix(got, "visible=true held=true alive=true") {
		t.Fatalf("the release did not serve the map: %s", got)
	}
	if consoleGot != "visible=false held=true alive=true" {
		t.Fatalf("the console build: %s", consoleGot)
	}
}
