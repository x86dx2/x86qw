package services

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"sync"
	"time"

	"github.com/x86dx2/x86qw/go/internal/launchguard"
)

// The supervisor has no guardian process: each runtime is exec'd directly
// in its own session after its
// bytes are re-checked against the verified inventory hash (held open
// through process creation on Windows, see launchguard).

type started struct {
	spec    *Spec
	command *exec.Cmd
	mu      sync.Mutex
	done    bool
	code    int
}

func (s *started) exited() (bool, int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.done, s.code
}

// notifySignals is signal.Notify; tests deliver the signals an OS would.
var notifySignals = signal.Notify

// RunProcesses starts specs in order, waits for readiness, marks the journal
// running and supervises until a stop request, a child exit or a signal.
func RunProcesses(specs []*Spec, journal *Journal, console *Console, output *os.File) (code int, err error) {
	signals := make(chan os.Signal, 1)
	notifySignals(signals, serviceSignals...)
	defer signal.Stop(signals)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var received os.Signal
	var receivedMu sync.Mutex
	go func() {
		select {
		case sig := <-signals:
			receivedMu.Lock()
			received = sig
			receivedMu.Unlock()
			cancel()
		case <-ctx.Done():
		}
	}()
	interrupted := func() (os.Signal, bool) {
		receivedMu.Lock()
		defer receivedMu.Unlock()
		return received, received != nil
	}

	var processes []*started
	defer func() {
		var commands []*exec.Cmd
		exited := map[int]bool{}
		for _, process := range processes {
			commands = append(commands, process.command)
			if done, _ := process.exited(); done {
				exited[process.command.Process.Pid] = true
			}
		}
		if len(commands) == 0 {
			return
		}
		if stopErr := stopGroups(commands, exited); stopErr != nil {
			console.Warning("Failed to stop the process tree: " + stopErr.Error())
			if err == nil {
				err = stopErr
			}
		}
	}()

	onSignal := func() (int, error) {
		sig, _ := interrupted()
		console.Info("Stopping x86QW services…")
		if journal != nil {
			if err := journal.SetStatus("interrupted"); err != nil {
				return 0, err
			}
		}
		return 128 + signalNumber(sig), nil
	}
	failed := func(err error) (int, error) {
		if _, ok := interrupted(); ok && errors.Is(err, context.Canceled) {
			return onSignal()
		}
		return 0, err
	}

	for _, spec := range specs {
		if _, ok := interrupted(); ok {
			return onSignal()
		}
		console.Detail(fmt.Sprintf("Starting %s: %s", spec.Label, spec.Arguments[0]))
		release, holdErr := launchguard.Hold(spec.Arguments[0], spec.ExpectedSHA256)
		if holdErr != nil {
			return 0, errorf("The %s command does not match the validated target.", spec.Label)
		}
		command, startErr := startDetached(spec.Arguments, spec.Cwd, output)
		release()
		if startErr != nil {
			var installerErr *Error
			if errors.As(startErr, &installerErr) {
				return 0, startErr
			}
			return 0, errorf("Could not start a service: %v", startErr)
		}
		process := &started{spec: spec, command: command}
		processes = append(processes, process)
		go func() {
			command.Wait()
			process.mu.Lock()
			process.done, process.code = true, exitCode(command.ProcessState)
			process.mu.Unlock()
		}()
		if journal != nil {
			if err := journal.RecordProcess(spec, command.Process.Pid); err != nil {
				return 0, err
			}
		}
		exited := func() bool { done, _ := process.exited(); return done }
		if spec.StartupRcon != nil {
			if err := applyStartupRcon(ctx, spec.StartupRcon); err != nil {
				return failed(err)
			}
			console.Detail("MVDSV ready; post-map configuration applied and RCON password restored.")
		}
		if spec.Readiness != nil {
			switch spec.Readiness.Kind {
			case "http":
				if err := waitHTTPReadiness(ctx, exited, spec.Readiness); err != nil {
					return failed(err)
				}
				console.Detail("QTV ready and answering over HTTP.")
			case "udp":
				if err := waitUDPReadiness(ctx, exited, spec.Readiness); err != nil {
					return failed(err)
				}
				console.Detail("QWFWD ready and holding the UDP port.")
			}
		}
	}
	if journal != nil {
		if err := journal.SetStatus("running"); err != nil {
			return 0, err
		}
	}
	for {
		if _, ok := interrupted(); ok {
			return onSignal()
		}
		if journal != nil {
			stop, err := journal.ConsumeStopRequest()
			if err != nil {
				return 0, err
			}
			if stop {
				console.Info("Stop requested by the x86QW manager…")
				return 0, nil
			}
		}
		for _, process := range processes {
			if done, code := process.exited(); done {
				if code != 0 {
					console.Warning(fmt.Sprintf("%s exited with code %d.", process.spec.Label, code))
				}
				return code, nil
			}
		}
		select {
		case <-ctx.Done():
		case <-time.After(100 * time.Millisecond):
		}
	}
}
