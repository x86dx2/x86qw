package install

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
)

const maxSessions = 1024

// Session is one supervised service as exposed by `status --json`.
type Session struct {
	Command   string `json:"command"`
	SessionID string `json:"session_id"`
	Status    string `json:"status"`
}

// Status mirrors manager.py _json_status_data: a read-only snapshot that
// never loads catalogs.
type Status struct {
	Installation string    `json:"installation"`
	Project      string    `json:"project"`
	Sessions     []Session `json:"sessions"`
	State        string    `json:"state"`
	Target       string    `json:"target"`
}

// ReadStatus inspects target without modifying it.
func ReadStatus(target string) Status {
	status := Status{Project: "x86qw", Target: target, Installation: "missing", State: "missing", Sessions: []Session{}}
	if _, err := os.Stat(target); err == nil {
		status.Installation = "present"
	}
	if info, err := os.Stat(filepath.Join(target, ".x86qw", "state.json")); err == nil && info.Mode().IsRegular() {
		status.State = "present"
	}
	sessionsDir := filepath.Join(target, ".x86qw", "sessions")
	if info, err := os.Lstat(sessionsDir); err != nil || !info.IsDir() {
		return status
	}
	entries, err := os.ReadDir(sessionsDir)
	if err != nil {
		return status
	}
	sort.Slice(entries, func(a, b int) bool { return entries[a].Name() < entries[b].Name() })
	for _, entry := range entries {
		if len(status.Sessions) >= maxSessions || !entry.IsDir() {
			continue
		}
		journal := filepath.Join(sessionsDir, entry.Name(), "session.json")
		info, err := os.Lstat(journal)
		if err != nil || !info.Mode().IsRegular() {
			continue
		}
		payload, err := os.ReadFile(journal)
		if err != nil {
			continue
		}
		var document map[string]any
		if json.Unmarshal(payload, &document) != nil {
			continue
		}
		status.Sessions = append(status.Sessions, Session{
			SessionID: stringField(document, "session_id", entry.Name()),
			Status:    stringField(document, "status", "unknown"),
			Command:   stringField(document, "command", "unknown"),
		})
	}
	return status
}

func stringField(document map[string]any, key, fallback string) string {
	value, ok := document[key]
	if !ok {
		return fallback
	}
	if text, ok := value.(string); ok {
		return text
	}
	return fmt.Sprint(value)
}
