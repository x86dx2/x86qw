//go:build windows

package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

// TestAppOpensInARealConsole runs the app in a Windows pseudo console, the
// way a terminal hosts it: it must draw home for the installation in
// X86QW_CONPTY_TARGET, take arrow keys and enter, and quit with ctrl+c
// pressed twice, exiting 0.
func TestAppOpensInARealConsole(t *testing.T) {
	target := os.Getenv("X86QW_CONPTY_TARGET")
	if target == "" {
		t.Skip("defina X86QW_CONPTY_TARGET com uma instalação para abrir o app num console")
	}
	exe := filepath.Join(t.TempDir(), "x86qw.exe")
	if output, err := exec.Command("go", "build", "-o", exe, ".").CombinedOutput(); err != nil {
		t.Fatalf("build: %v %s", err, output)
	}
	var inRead, inWrite, outRead, outWrite windows.Handle
	if err := windows.CreatePipe(&inRead, &inWrite, nil, 0); err != nil {
		t.Fatal(err)
	}
	if err := windows.CreatePipe(&outRead, &outWrite, nil, 0); err != nil {
		t.Fatal(err)
	}
	var console windows.Handle
	if err := windows.CreatePseudoConsole(windows.Coord{X: 120, Y: 40}, inRead, outWrite, 0, &console); err != nil {
		t.Fatal(err)
	}
	attributes, err := windows.NewProcThreadAttributeList(1)
	if err != nil {
		t.Fatal(err)
	}
	defer attributes.Delete()
	// The attribute value is the HPCON itself, not a pointer to it.
	if err := attributes.Update(windows.PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, *(*unsafe.Pointer)(unsafe.Pointer(&console)), unsafe.Sizeof(console)); err != nil {
		t.Fatal(err)
	}
	startup := &windows.StartupInfoEx{ProcThreadAttributeList: attributes.List()}
	startup.Cb = uint32(unsafe.Sizeof(*startup))
	line, err := windows.UTF16PtrFromString(windows.EscapeArg(exe) + " --target " + windows.EscapeArg(target))
	if err != nil {
		t.Fatal(err)
	}
	var process windows.ProcessInformation
	if err := windows.CreateProcess(nil, line, nil, nil, false, windows.EXTENDED_STARTUPINFO_PRESENT, nil, nil, &startup.StartupInfo, &process); err != nil {
		t.Fatal(err)
	}
	defer windows.CloseHandle(process.Process)
	defer windows.CloseHandle(process.Thread)
	// The pseudo console holds its ends of the pipes now.
	windows.CloseHandle(inRead)
	windows.CloseHandle(outWrite)

	var mu sync.Mutex
	var screen strings.Builder
	go func() {
		buffer := make([]byte, 64*1024)
		for {
			var n uint32
			if err := windows.ReadFile(outRead, buffer, &n, nil); err != nil || n == 0 {
				return
			}
			mu.Lock()
			screen.Write(buffer[:n])
			mu.Unlock()
		}
	}()
	ansi := regexp.MustCompile(`\x1b\[[0-9;?<>=]*[a-zA-Z~]|\x1b\][^\x07]*\x07|\x1b[=>]`)
	text := func() string {
		mu.Lock()
		defer mu.Unlock()
		return ansi.ReplaceAllString(screen.String(), "")
	}
	waitFor := func(what string, words ...string) {
		t.Helper()
		deadline := time.Now().Add(30 * time.Second)
		for time.Now().Before(deadline) {
			current := text()
			for _, word := range words {
				if strings.Contains(current, word) {
					return
				}
			}
			time.Sleep(200 * time.Millisecond)
		}
		t.Fatalf("o app não mostrou %s em 30 s:\n%s", what, text())
	}
	send := func(keys string) {
		var n uint32
		if err := windows.WriteFile(inWrite, []byte(keys), &n, nil); err != nil {
			t.Fatal(err)
		}
		time.Sleep(700 * time.Millisecond)
	}
	waitFor("a home", "Hospedar", "Host")
	send("\x1b[B") // down to the next row
	send("\r")     // open it
	send("\x1b")   // and back
	send("\x03")   // ctrl+c arms quitting
	send("\x03")   // and quits
	event, err := windows.WaitForSingleObject(process.Process, 20000)
	if err != nil || event != windows.WAIT_OBJECT_0 {
		windows.TerminateProcess(process.Process, 1)
		t.Fatalf("o app não saiu com ctrl+c duas vezes:\n%s", text())
	}
	var code uint32
	windows.GetExitCodeProcess(process.Process, &code)
	windows.ClosePseudoConsole(console)
	windows.CloseHandle(inWrite)
	if code != 0 {
		t.Fatalf("o app saiu com código %d:\n%s", code, text())
	}
	t.Logf("tela final:\n%s", text())
}
