//go:build !windows

// Package lock takes the per-installation operation lock
// (.x86qw/sessions/active.lock) in the exact format 3 that
// x86qw_runtime/session_control.py writes, reads and reclaims, so the Go and
// Python CLIs exclude each other and recover each other's stale locks.
package lock

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"
)

// Owner is the lock document; fields are declared in sorted key order.
type Owner struct {
	Command              string `json:"command"`
	ControllerExecutable string `json:"controller_executable"`
	ControllerPID        int    `json:"controller_pid"`
	ControllerStartToken string `json:"controller_start_token"`
	CreatedAt            string `json:"created_at"`
	Format               int    `json:"format"`
	Installation         string `json:"installation"`
	OperationKind        string `json:"operation_kind"`
	PrivateFilesystem    int    `json:"private_filesystem"`
	Project              string `json:"project"`
	SessionID            string `json:"session_id"`
}

// Lock is a held installation lock.
type Lock struct {
	Path      string
	Owner     Owner
	reclaimed string
}

var serviceCommands = map[string]bool{"host": true, "proxy": true, "qtv": true}

// Acquire takes the lock for command on target, reclaiming a lock whose
// controller is provably gone and refusing when it is alive or unknown.
func Acquire(target, command string) (*Lock, error) {
	// Path.resolve(strict=False) in Python: follow symlinks when present.
	target = Resolve(target)
	sessions := filepath.Join(target, ".x86qw", "sessions")
	for _, dir := range []string{filepath.Join(target, ".x86qw"), sessions} {
		if err := os.Mkdir(dir, 0o700); err != nil && !errors.Is(err, os.ErrExist) {
			return nil, err
		}
		info, err := os.Lstat(dir)
		if err != nil || !info.IsDir() {
			return nil, fmt.Errorf("diretório de controle inseguro: %s", dir)
		}
	}
	self, status := Identity(os.Getpid())
	if status != "alive" {
		return nil, errors.New("não foi possível confirmar a identidade desta CLI; nenhuma sessão foi recuperada")
	}
	kind := "maintenance"
	if serviceCommands[command] {
		kind = "service"
	}
	owner := Owner{
		Format: 3, Project: "x86qw", SessionID: newSessionID(), OperationKind: kind, Command: command,
		ControllerPID: self.PID, ControllerStartToken: self.Token, ControllerExecutable: self.Executable,
		CreatedAt:    time.Now().UTC().Format("2006-01-02T15:04:05.000000-07:00"),
		Installation: target, PrivateFilesystem: 1,
	}
	directory, err := os.Open(sessions)
	if err != nil {
		return nil, err
	}
	defer directory.Close()
	if err := syscall.Flock(int(directory.Fd()), syscall.LOCK_EX); err != nil {
		return nil, err
	}
	defer syscall.Flock(int(directory.Fd()), syscall.LOCK_UN)
	return acquireSerialized(sessions, owner)
}

func acquireSerialized(sessions string, owner Owner) (*Lock, error) {
	path := filepath.Join(sessions, "active.lock")
	reclaimed := ""
	for {
		file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL|syscall.O_NOFOLLOW, 0o600)
		if errors.Is(err, os.ErrExist) {
			existing, err := readOwner(path)
			if err != nil {
				return nil, err
			}
			if existing.Installation != owner.Installation {
				return nil, fmt.Errorf("lock de operação pertence a outra instalação: %s", path)
			}
			switch Probe(existing.ControllerPID, existing.ControllerStartToken, existing.ControllerExecutable) {
			case "alive":
				return nil, fmt.Errorf("existe uma operação x86QW ativa nesta instalação.\nOperação: %s\nSessão: %s\nControlador: PID %d\n\nEncerre a operação antes de modificar o conteúdo instalado",
					existing.Command, existing.SessionID, existing.ControllerPID)
			case "inconclusive":
				return nil, fmt.Errorf("não foi possível confirmar se a operação x86QW existente terminou; lock preservado em %s", sessions)
			}
			candidate := filepath.Join(sessions, ".active.lock.reclaimed."+owner.SessionID)
			if err := os.Link(path, candidate); err != nil {
				return nil, err
			}
			if err := os.Remove(path); err != nil {
				return nil, err
			}
			reclaimed = candidate
			continue
		}
		if err != nil {
			return nil, fmt.Errorf("não foi possível criar o lock privado da instalação: %w", err)
		}
		var payload bytes.Buffer
		encoder := json.NewEncoder(&payload)
		encoder.SetEscapeHTML(false)
		encoder.SetIndent("", "  ")
		encoder.Encode(owner)
		_, writeErr := file.Write(payload.Bytes())
		syncErr := file.Sync()
		closeErr := file.Close()
		if err := errors.Join(writeErr, syncErr, closeErr); err != nil {
			os.Remove(path)
			return nil, fmt.Errorf("lock de operação não pôde ser gravado: %w", err)
		}
		return &Lock{Path: path, Owner: owner, reclaimed: reclaimed}, nil
	}
}

// Release removes the lock if this session still owns it.
func (l *Lock) Release() error {
	current, err := readOwner(l.Path)
	if err == nil && current.SessionID == l.Owner.SessionID {
		if err := os.Remove(l.Path); err != nil {
			return err
		}
	}
	if l.reclaimed != "" {
		os.Remove(l.reclaimed)
	}
	return nil
}

func readOwner(path string) (*Owner, error) {
	file, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return nil, fmt.Errorf("lock de operação inválido foi preservado para inspeção: %s", path)
	}
	defer file.Close()
	payload, err := io.ReadAll(io.LimitReader(file, 65537))
	if err != nil || len(payload) > 65536 {
		return nil, fmt.Errorf("lock de operação inválido foi preservado para inspeção: %s", path)
	}
	var document map[string]any
	if err := json.Unmarshal(payload, &document); err != nil {
		return nil, fmt.Errorf("lock de operação inválido foi preservado para inspeção: %s", path)
	}
	var owner Owner
	if err := json.Unmarshal(payload, &owner); err != nil || owner.Project != "x86qw" || owner.ControllerPID <= 0 ||
		owner.Installation == "" || owner.ControllerStartToken == "" {
		return nil, fmt.Errorf("lock de operação inválido foi preservado para inspeção: %s", path)
	}
	return &owner, nil
}

func newSessionID() string {
	suffix := make([]byte, 6)
	rand.Read(suffix)
	return time.Now().UTC().Format("20060102T150405Z") + "-" + hex.EncodeToString(suffix)
}

// ProcessIdentity mirrors platform/processes.py ProcessIdentity.
type ProcessIdentity struct {
	PID        int
	Token      string
	Executable string
}

// Identity returns ("alive"|"dead"|"inconclusive") and the identity of pid,
// computed exactly like processes.process_identity.
func Identity(pid int) (ProcessIdentity, string) {
	if pid <= 0 {
		return ProcessIdentity{}, "dead"
	}
	switch runtime.GOOS {
	case "darwin":
		return darwinIdentity(pid)
	case "linux":
		return linuxIdentity(pid)
	}
	return ProcessIdentity{}, "inconclusive"
}

func darwinIdentity(pid int) (ProcessIdentity, string) {
	run := func(field string) (string, int, error) {
		command := exec.Command("ps", "-p", strconv.Itoa(pid), "-o", field+"=")
		command.Env = append(os.Environ(), "LC_ALL=C")
		output, err := command.Output()
		code := 0
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			code, err = exit.ExitCode(), nil
		}
		return strings.TrimSpace(string(output)), code, err
	}
	started, code, err := run("lstart")
	if err != nil {
		return ProcessIdentity{}, "inconclusive"
	}
	if code == 1 && started == "" {
		return ProcessIdentity{}, "dead"
	}
	if code != 0 || started == "" {
		return ProcessIdentity{}, "inconclusive"
	}
	comm, code, err := run("comm")
	if err != nil || code != 0 || comm == "" {
		return ProcessIdentity{}, "inconclusive"
	}
	return ProcessIdentity{PID: pid, Token: "macos:" + strings.Join(strings.Fields(started), " "), Executable: realpath(comm)}, "alive"
}

func linuxIdentity(pid int) (ProcessIdentity, string) {
	stat, err := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if errors.Is(err, os.ErrNotExist) {
		return ProcessIdentity{}, "dead"
	}
	if err != nil {
		return ProcessIdentity{}, "inconclusive"
	}
	text := string(stat)
	closing := strings.LastIndex(text, ")")
	if closing < 0 || closing+2 > len(text) {
		return ProcessIdentity{}, "inconclusive"
	}
	fields := strings.Fields(text[closing+2:])
	if len(fields) <= 19 {
		return ProcessIdentity{}, "inconclusive"
	}
	if fields[0] == "Z" {
		return ProcessIdentity{}, "dead"
	}
	boot := "boot-unknown"
	if data, err := os.ReadFile("/proc/sys/kernel/random/boot_id"); err == nil {
		boot = strings.TrimSpace(string(data))
	}
	exe, err := os.Readlink(fmt.Sprintf("/proc/%d/exe", pid))
	if err != nil {
		return ProcessIdentity{}, "inconclusive"
	}
	return ProcessIdentity{PID: pid, Token: "linux:" + boot + ":" + fields[19], Executable: realpath(exe)}, "alive"
}

// Resolve returns the canonical installation path Python records.
func Resolve(target string) string {
	absolute, err := filepath.Abs(target)
	if err != nil {
		return target
	}
	if resolved, err := filepath.EvalSymlinks(absolute); err == nil {
		return resolved
	}
	return absolute
}

func realpath(path string) string {
	if resolved, err := filepath.EvalSymlinks(path); err == nil {
		if absolute, err := filepath.Abs(resolved); err == nil {
			return absolute
		}
	}
	return path
}

// comparable trims macOS framework paths at Versions/<n>, like
// processes._comparable_executable.
func comparable(path string) string {
	canonical := realpath(path)
	if runtime.GOOS == "darwin" {
		parts := strings.Split(canonical, "/")
		for index, part := range parts {
			if part == "Versions" && index+1 < len(parts) && parts[index+1] != "" {
				return strings.Join(parts[:index+2], "/")
			}
		}
	}
	return canonical
}

// Probe reports "alive" only when pid still has the recorded start token
// and executable; "dead" and identity mismatches allow reclaiming.
func Probe(pid int, token, executable string) string {
	actual, status := Identity(pid)
	if status != "alive" {
		return status
	}
	if actual.Token != token || comparable(actual.Executable) != comparable(executable) {
		return "identity_mismatch"
	}
	return "alive"
}
