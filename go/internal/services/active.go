package services

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/x86dx2/x86qw/go/internal/lock"
)

// Stack is the service session running in an installation, as data.
type Stack struct {
	SessionID string
	Status    string // starting, running or stopping
	Processes []Process
}

// Process is one service of the stack.
type Process struct {
	Label, Address string
	Port, PID      int
	Alive          bool
	Parameters     map[string]string
}

// Active reports the stack whose controller holds the installation's
// lock and is alive; nil when nothing runs.
func Active(target string) (*Stack, error) {
	target = lock.Resolve(target)
	lockPath := filepath.Join(target, ".x86qw", "sessions", "active.lock")
	if _, err := os.Lstat(lockPath); err != nil {
		return nil, nil
	}
	owner, err := lock.ReadOwner(lockPath)
	if err != nil {
		return nil, err
	}
	if owner.OperationKind == "maintenance" || lock.Probe(owner.ControllerPID, owner.ControllerStartToken, owner.ControllerExecutable) != "alive" {
		return nil, nil
	}
	stack := &Stack{SessionID: owner.SessionID, Status: "starting"}
	paths, err := SessionJournalPaths(target, owner.SessionID)
	if err != nil || len(paths) == 0 {
		return stack, err
	}
	data, err := LoadJournal(paths[0])
	if err != nil {
		return nil, err
	}
	stack.Status = fmt.Sprint(data["status"])
	processes, _ := data["processes"].([]any)
	for _, entry := range processes {
		record, _ := entry.(map[string]any)
		process := Process{Label: fmt.Sprint(record["label"]), Alive: processProbe(entry) == "alive", Parameters: map[string]string{}}
		process.Address, _ = record["address"].(string)
		process.Port, _ = intField(record["port"])
		process.PID, _ = intField(record["pid"])
		if parameters, ok := record["parameters"].(map[string]any); ok {
			for key, value := range parameters {
				process.Parameters[key] = fmt.Sprint(value)
			}
		}
		stack.Processes = append(stack.Processes, process)
	}
	return stack, nil
}
