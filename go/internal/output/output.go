// Package output renders the stable JSON envelope shared with the Python CLI
// (x86qw_runtime/contracts/output.py).
package output

import (
	"encoding/json"
	"io"
)

// Exit codes are part of the public contract.
const (
	ExitSuccess     = 0
	ExitFailure     = 1
	ExitUsage       = 2
	ExitInterrupted = 130
)

// SchemaVersion of the JSON envelope.
const SchemaVersion = 1

// Error is one machine-readable failure.
type Error struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// Envelope is the single JSON document a command emits.
type Envelope struct {
	// Fields are declared in key order: the Python CLI emits sorted keys.
	Command       string  `json:"command"`
	Data          any     `json:"data"`
	DryRun        bool    `json:"dry_run"`
	Errors        []Error `json:"errors"`
	ExitCode      int     `json:"exit_code"`
	OK            bool    `json:"ok"`
	SchemaVersion int     `json:"schema_version"`
}

// Success builds a successful envelope.
func Success(command string, data any) Envelope {
	return Envelope{SchemaVersion: SchemaVersion, Command: command, OK: true, Data: data, Errors: []Error{}}
}

// Failure builds a failed envelope; exitCode must be non-zero.
func Failure(command string, exitCode int, code, message string) Envelope {
	return Envelope{
		SchemaVersion: SchemaVersion,
		Command:       command,
		Data:          nil,
		Errors:        []Error{{Code: code, Message: message}},
		ExitCode:      exitCode,
	}
}

// Write emits the envelope as one compact JSON line with sorted keys.
// Data must be a map or a struct whose fields are declared in key order.
func (e Envelope) Write(w io.Writer) error {
	encoder := json.NewEncoder(w)
	encoder.SetEscapeHTML(false)
	return encoder.Encode(e)
}
