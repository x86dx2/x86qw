package services

import (
	"io"
	"os"
	"slices"
	"syscall"
	"testing"
	"time"
)

// The supervisor stops its servers on SIGTERM, which Windows sends when the
// console closes, the user logs off or the machine shuts down, and exits
// with 128+15.
func TestTheSupervisorStopsOnSIGTERM(t *testing.T) {
	saved := notifySignals
	t.Cleanup(func() { notifySignals = saved })
	var asked []os.Signal
	notifySignals = func(c chan<- os.Signal, signals ...os.Signal) {
		asked = signals
		go func() {
			time.Sleep(time.Second)
			c <- syscall.SIGTERM
		}()
	}
	t.Setenv("X86QW_TEST_CHILD", "sleep")
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	code, err := RunProcesses([]*Spec{{Label: "MVDSV", Arguments: []string{self}, Cwd: t.TempDir()}}, nil, &Console{Out: io.Discard, Err: io.Discard}, nil)
	if !slices.Contains(asked, os.Signal(syscall.SIGTERM)) || err != nil || code != 128+15 {
		t.Fatalf("asked for %v; code %d, err %v", asked, code, err)
	}
}
