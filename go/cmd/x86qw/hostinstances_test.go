package main

import (
	"bytes"
	"net"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/x86dx2/x86qw/go/internal/setup"
)

// queryServer asks a QuakeWorld server for its status and returns the
// map it runs, or "" when it does not answer.
func queryServer(port int) string {
	conn, err := net.Dial("udp", net.JoinHostPort("127.0.0.1", strconv.Itoa(port)))
	if err != nil {
		return ""
	}
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(time.Second))
	conn.Write([]byte("\xff\xff\xff\xffstatus 23\n"))
	buffer := make([]byte, 8192)
	n, err := conn.Read(buffer)
	if err != nil {
		return ""
	}
	fields := strings.Split(strings.SplitN(string(buffer[:n]), "\n", 2)[0], `\`)
	for index := 1; index+1 < len(fields); index += 2 {
		if fields[index] == "map" {
			return fields[index+1]
		}
	}
	return "?"
}

func treeFiles(t *testing.T, root string) []string {
	var files []string
	filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
		if err == nil && info.Mode().IsRegular() {
			files = append(files, path)
		}
		return nil
	})
	sort.Strings(files)
	return files
}

// TestServerInstancesRunTogether installs a two-instance server, starts
// it with the command the installer hands over, and stops it; set
// X86QW_NETWORK_TESTS=1 and X86QW_REAL_INSTALL=1.
func TestServerInstancesRunTogether(t *testing.T) {
	if os.Getenv("X86QW_NETWORK_TESTS") == "" || os.Getenv("X86QW_REAL_INSTALL") == "" {
		t.Skip("set X86QW_NETWORK_TESTS=1 and X86QW_REAL_INSTALL=1 to install for real")
	}
	// MVDSV cuts paths at 128 bytes: keep the folder short.
	short, err := os.MkdirTemp("", "q")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(short) })
	t.Setenv("HOME", filepath.Join(short, "home"))
	t.Setenv("X86QW_CACHE_DIR", filepath.Join(short, "cache"))
	t.Setenv("X86QW_NO_MACOS_PREFERENCES", "1")
	target := filepath.Join(short, "g")
	var stdout, stderr bytes.Buffer
	if code := run([]string{"setup", "--intent", "host", "--servers", "duel:dm6,ffa:dm3+bots", "--base-port", "27720",
		"--integrate", "none", "--target", target, "--yes"}, &stdout, &stderr); code != 0 {
		t.Fatalf("setup: %d\n%s%s", code, stdout.String(), stderr.String())
	}
	options, err := setup.LoadChoices(target)
	if err != nil || options == nil {
		t.Fatalf("escolhas: %v", err)
	}
	before := treeFiles(t, filepath.Join(target, "qw"))
	// The test binary cannot be a background controller: run the same
	// command in the foreground and stop it like the panel would.
	var args []string
	for _, arg := range setup.HostCommand(target, *options) {
		if arg != "--background" {
			args = append(args, arg)
		}
	}
	finished := make(chan int, 1)
	var hostOut bytes.Buffer
	go func() { finished <- run(args, &hostOut, &hostOut) }()
	deadline := time.Now().Add(30 * time.Second)
	for queryServer(27720) == "" || queryServer(27721) == "" {
		if time.Now().After(deadline) {
			t.Fatalf("the instances did not answer:\n%s", hostOut.String())
		}
		time.Sleep(300 * time.Millisecond)
	}
	if first, second := queryServer(27720), queryServer(27721); first != "dm6" || second != "dm3" {
		t.Fatalf("mapas: %q e %q", first, second)
	}
	var status bytes.Buffer
	run([]string{"status", "--target", target}, &status, &status)
	if !strings.Contains(status.String(), "MVDSV #2 · 27721") {
		t.Fatalf("status without the second instance:\n%s", status.String())
	}
	run([]string{"status", "--target", target, "--stop", "--yes"}, &status, &status)
	select {
	case <-finished:
	case <-time.After(30 * time.Second):
		t.Fatal("the session did not stop")
	}
	if after := treeFiles(t, filepath.Join(target, "qw")); strings.Join(after, "\n") != strings.Join(before, "\n") {
		t.Fatalf("qw/ is not back to what it was: %d files before, %d after", len(before), len(after))
	}
}
