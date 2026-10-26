//go:build !windows

package services

import (
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// TestHangupController is the foreground controller that
// TestAHangupStopsTheServices sends SIGHUP to.
func TestHangupController(t *testing.T) {
	pidFile := os.Getenv("X86QW_TEST_HANGUP")
	if pidFile == "" {
		t.Skip("run by TestAHangupStopsTheServices")
	}
	spec := &Spec{Label: "MVDSV", Arguments: []string{"/bin/sh", "-c", "echo $$ > '" + pidFile + "'; exec sleep 30"}, Cwd: filepath.Dir(pidFile)}
	RunProcesses([]*Spec{spec}, nil, quiet(), nil)
}

// A closed terminal or a dropped SSH session sends SIGHUP: the foreground
// controller stops its services instead of leaving them running unowned.
func TestAHangupStopsTheServices(t *testing.T) {
	if signal.Ignored(syscall.SIGHUP) {
		t.Skip("SIGHUP is ignored here, as under nohup")
	}
	pidFile := filepath.Join(t.TempDir(), "service.pid")
	self, _ := os.Executable()
	controller := exec.Command(self, "-test.run=^TestHangupController$")
	controller.Env = append(os.Environ(), "X86QW_TEST_HANGUP="+pidFile)
	if err := controller.Start(); err != nil {
		t.Fatal(err)
	}
	exited := make(chan error, 1)
	go func() { exited <- controller.Wait() }()
	defer controller.Process.Kill()
	service := 0
	for deadline := time.Now().Add(10 * time.Second); service == 0 && time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
		data, _ := os.ReadFile(pidFile)
		service, _ = strconv.Atoi(strings.TrimSpace(string(data)))
	}
	if service == 0 {
		t.Fatal("the service did not start")
	}
	defer syscall.Kill(service, syscall.SIGKILL)
	controller.Process.Signal(syscall.SIGHUP)
	select {
	case <-exited:
	case <-time.After(10 * time.Second):
		t.Fatal("the controller ignored SIGHUP")
	}
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
		if syscall.Kill(service, 0) != nil {
			return
		}
	}
	t.Fatalf("service %d still runs after its controller got SIGHUP", service)
}
