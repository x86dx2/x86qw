package services

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/atomicfile"
	"github.com/x86dx2/x86qw/go/internal/fspath"
	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/privfs"
	"github.com/x86dx2/x86qw/go/internal/pyjson"
)

// The session journal records the controller and every supervised process,
// so status, stop and recovery work from any later run.

const (
	maxJournalBytes     = 2 << 20
	maxStopRequestBytes = 64 << 10
)

var (
	sessionDirectoryName = regexp.MustCompile(`^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$`)
	initialStagingName   = regexp.MustCompile(`^\.session\.json\.[0-9a-f]{24}\.tmp$`)
)

// Journal is one service session's private journal.
type Journal struct {
	Target    string
	SessionID string
	Directory string
	Path      string
	Data      map[string]any
}

func isoNow() string {
	return time.Now().UTC().Format("2006-01-02T15:04:05.000000-07:00")
}

func tokenHex(size int) string {
	raw := make([]byte, size)
	rand.Read(raw)
	return hex.EncodeToString(raw)
}

func ensurePrivateDirectory(dir string) error {
	if err := privfs.Mkdir(dir); err != nil && !errors.Is(err, os.ErrExist) {
		return err
	}
	info, err := os.Lstat(dir)
	if err != nil || !info.IsDir() {
		return errorf("Diretório de serviço ausente ou inseguro: %s", dir)
	}
	return privfs.Ensure(dir, info)
}

// NewJournal creates .x86qw/sessions/<id>/session.json in state "starting".
func NewJournal(target string, owner *lock.Owner, background bool, backgroundLog string) (*Journal, error) {
	sessions := filepath.Join(target, ".x86qw", "sessions")
	if err := ensurePrivateDirectory(filepath.Dir(sessions)); err != nil {
		return nil, err
	}
	if err := ensurePrivateDirectory(sessions); err != nil {
		return nil, err
	}
	journal := &Journal{Target: target, SessionID: owner.SessionID}
	journal.Directory = filepath.Join(sessions, owner.SessionID)
	if err := privfs.Mkdir(journal.Directory); err != nil {
		return nil, errorf("Diretório exclusivo da sessão não pôde ser criado: %s", journal.Directory)
	}
	journal.Path = filepath.Join(journal.Directory, "session.json")
	var log any
	if backgroundLog != "" {
		log = backgroundLog
	}
	journal.Data = map[string]any{
		"format": 1, "project": "x86qw", "private_filesystem": 1, "session_id": owner.SessionID,
		"created_at": isoNow(), "status": "starting",
		"controller": map[string]any{
			"pid": owner.ControllerPID, "creation_token": owner.ControllerStartToken,
			"executable": owner.ControllerExecutable, "command": owner.Command,
		},
		"background": background, "background_log": log,
		"processes": []any{}, "temporary_files": []any{}, "materialized_files": []any{}, "created_directories": []any{},
	}
	if err := journal.write(); err != nil {
		os.Remove(journal.Directory)
		return nil, err
	}
	return journal, nil
}

func (j *Journal) write() error {
	text, err := pyjson.Dumps(j.Data, 2)
	if err == nil {
		err = atomicfile.Write(j.Path, []byte(text+"\n"), 0o600)
	}
	if err != nil {
		return errorf("O journal privado da sessão não pôde ser gravado.")
	}
	return nil
}

// Relative renders a path relative to the installation (POSIX).
func (j *Journal) Relative(absolute string) (string, error) {
	// The name itself stays as given; only the folders it is in resolve.
	name := filepath.Join(fspath.Resolve(filepath.Dir(absolute)), filepath.Base(absolute))
	root := fspath.Resolve(j.Target)
	relative, err := filepath.Rel(root, name)
	if err != nil || !fspath.Inside(name, root) {
		return "", errorf("O journal recusou um caminho fora da instalação.")
	}
	return filepath.ToSlash(relative), nil
}

// SetStatus updates the session status.
func (j *Journal) SetStatus(status string) error {
	j.Data["status"] = status
	return j.write()
}

func (j *Journal) list(key string) []any {
	items, _ := j.Data[key].([]any)
	return items
}

// RecordProcess adds a started process (no guardian: runtime pid == pid).
func (j *Journal) RecordProcess(spec *Spec, pid int) error {
	identity, status := lock.Identity(pid)
	if status != "alive" {
		return errorf("Não foi possível registrar a identidade do processo %s.", spec.Label)
	}
	var address, port any
	if spec.StartupRcon != nil {
		address, port = spec.StartupRcon.Address, spec.StartupRcon.Port
	} else if spec.Readiness != nil {
		address, port = spec.Readiness.Address, spec.Readiness.Port
	}
	parameters := map[string]any{}
	for _, item := range spec.Parameters {
		parameters[item[0]] = item[1]
	}
	j.Data["processes"] = append(j.list("processes"), map[string]any{
		"label": spec.Label, "runtime": strings.ToLower(strings.Fields(spec.Label)[0]), "pid": pid, "process_group": pid,
		"executable": identity.Executable, "creation_token": identity.Token,
		"runtime_executable": spec.Arguments[0], "runtime_pid": pid, "state": "ready",
		"started_at": isoNow(), "address": address, "port": port, "parameters": parameters,
	})
	return j.write()
}

func (j *Journal) replaceOrAppend(key, relative string, entry map[string]any) {
	items := j.list(key)
	for index, item := range items {
		existing, _ := item.(map[string]any)
		if existing != nil && existing["path"] == relative && existing["state"] == "pending" {
			items[index] = entry
			j.Data[key] = items
			return
		}
	}
	j.Data[key] = append(items, entry)
}

// RecordTemporaryIntent records a reserved ephemeral cfg before writing it.
func (j *Journal) RecordTemporaryIntent(absolute, origin string, sensitive bool, identity [2]int64, expectedHash string, expectedSize int) error {
	relative, err := j.Relative(absolute)
	if err != nil {
		return err
	}
	entry := map[string]any{
		"path": relative, "origin": origin, "created_by_session": true, "type": "temporary-config",
		"sensitive": sensitive, "state": "pending", "device": identity[0], "inode": identity[1],
	}
	if !sensitive {
		entry["expected_hash"], entry["expected_size"] = expectedHash, expectedSize
	}
	j.Data["temporary_files"] = append(j.list("temporary_files"), entry)
	return j.write()
}

// RecordTemporary marks an ephemeral cfg ready.
func (j *Journal) RecordTemporary(absolute, origin string, sensitive bool, identity [2]int64, expectedHash string, expectedSize int) error {
	relative, err := j.Relative(absolute)
	if err != nil {
		return err
	}
	entry := map[string]any{
		"path": relative, "origin": origin, "created_by_session": true, "type": "temporary-config",
		"sensitive": sensitive, "state": "ready", "device": identity[0], "inode": identity[1],
	}
	if !sensitive {
		entry["expected_hash"], entry["expected_size"] = expectedHash, expectedSize
	}
	j.replaceOrAppend("temporary_files", relative, entry)
	return j.write()
}

func (j *Journal) materializedRecord(file *MaterializedFile, state string) (map[string]any, error) {
	relative, err := j.Relative(file.Path)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"path": relative, "expected_hash": file.ExpectedHash, "expected_size": file.Size,
		"origin": file.Origin, "created_by_session": file.CreatedBySession, "existed": file.Existed,
		"modified_during_session": false, "type": "materialized-content", "sensitive": false,
		"device": file.Identity[0], "inode": file.Identity[1], "state": state,
	}, nil
}

// RecordMaterializedIntent records a PK3 member before publishing it.
func (j *Journal) RecordMaterializedIntent(file *MaterializedFile) error {
	entry, err := j.materializedRecord(file, "pending")
	if err != nil {
		return err
	}
	j.Data["materialized_files"] = append(j.list("materialized_files"), entry)
	return j.write()
}

// RecordMaterialized marks a PK3 member ready.
func (j *Journal) RecordMaterialized(file *MaterializedFile) error {
	entry, err := j.materializedRecord(file, "ready")
	if err != nil {
		return err
	}
	j.replaceOrAppend("materialized_files", entry["path"].(string), entry)
	return j.write()
}

// RecordDirectory records a directory created for the session.
func (j *Journal) RecordDirectory(dir *MaterializedDirectory) error {
	relative, err := j.Relative(dir.Path)
	if err != nil {
		return err
	}
	for _, item := range j.list("created_directories") {
		if existing, ok := item.(map[string]any); ok && existing["path"] == relative {
			return nil
		}
		if item == relative {
			return nil
		}
	}
	j.Data["created_directories"] = append(j.list("created_directories"), map[string]any{
		"path": relative, "device": dir.Identity[0], "inode": dir.Identity[1],
	})
	return j.write()
}

// MarkModified flags a materialized file changed during the session.
func (j *Journal) MarkModified(relative string) error {
	for _, item := range j.list("materialized_files") {
		if entry, ok := item.(map[string]any); ok && entry["path"] == relative {
			entry["modified_during_session"] = true
		}
	}
	return j.write()
}

// ConsumeStopRequest removes a valid stop.request and reports it.
func (j *Journal) ConsumeStopRequest() (bool, error) {
	request := filepath.Join(j.Directory, "stop.request")
	info, err := os.Lstat(request)
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	if err != nil || !info.Mode().IsRegular() || info.Size() > maxStopRequestBytes {
		return false, errorf("Pedido de encerramento inválido preservado para inspeção: %s", request)
	}
	payload, err := os.ReadFile(request)
	var document map[string]any
	if err != nil || json.Unmarshal(payload, &document) != nil || len(document) != 4 ||
		document["format"] != float64(1) || document["project"] != "x86qw" || document["session_id"] != j.SessionID {
		return false, errorf("Pedido de encerramento inválido preservado para inspeção: %s", request)
	}
	if _, ok := document["requested_at"].(string); !ok {
		return false, errorf("Pedido de encerramento inválido preservado para inspeção: %s", request)
	}
	return true, os.Remove(request)
}

// JournalPath resolves a recorded relative path inside the installation.
func JournalPath(target string, relative any) (string, bool) {
	text, ok := relative.(string)
	if !ok || text == "" || path.IsAbs(text) {
		return "", false
	}
	for _, part := range strings.Split(text, "/") {
		if part == "" || part == "." || part == ".." {
			return "", false
		}
	}
	return filepath.Join(target, filepath.FromSlash(text)), true
}

// LoadJournal validates a session.json like load_session_journal.
func LoadJournal(file string) (map[string]any, error) {
	invalid := errorf("Journal de sessão inválido preservado para inspeção: %s", filepath.Dir(file))
	info, err := os.Lstat(file)
	if err != nil || !info.Mode().IsRegular() || info.Size() > maxJournalBytes {
		return nil, invalid
	}
	payload, err := os.ReadFile(file)
	if err != nil {
		return nil, invalid
	}
	value, err := pyjson.Decode(payload)
	data, ok := value.(map[string]any)
	if err != nil || !ok {
		return nil, invalid
	}
	required := []string{"format", "project", "session_id", "created_at", "status", "processes", "temporary_files", "materialized_files", "created_directories"}
	allowed := map[string]bool{"controller": true, "recovery_actions": true, "recovered_at": true, "background": true, "background_log": true, "private_filesystem": true}
	for _, key := range required {
		if _, ok := data[key]; !ok {
			return nil, invalid
		}
		allowed[key] = true
	}
	for key := range data {
		if !allowed[key] {
			return nil, invalid
		}
	}
	status, _ := data["status"].(string)
	switch status {
	case "starting", "running", "stopping", "interrupted", "clean":
	default:
		return nil, invalid
	}
	if !isNumber(data["format"], 1) || data["project"] != "x86qw" {
		return nil, invalid
	}
	if id, ok := data["session_id"].(string); !ok || id == "" {
		return nil, invalid
	}
	for _, key := range []string{"processes", "temporary_files", "materialized_files", "created_directories"} {
		if _, ok := data[key].([]any); !ok {
			return nil, invalid
		}
	}
	if _, ok := data["controller"]; !ok {
		data["controller"] = nil
	}
	if controller, ok := data["controller"].(map[string]any); ok {
		if _, ok := intField(controller["pid"]); !ok || len(controller) != 4 {
			return nil, invalid
		}
	} else if data["controller"] != nil {
		return nil, invalid
	}
	if _, ok := data["background"]; !ok {
		data["background"] = false
	}
	return data, nil
}

func controllerProbe(data map[string]any) string {
	controller, ok := data["controller"].(map[string]any)
	if !ok {
		return ""
	}
	pid, _ := intField(controller["pid"])
	token, _ := controller["creation_token"].(string)
	executable, _ := controller["executable"].(string)
	return lock.Probe(pid, token, executable)
}

func processProbe(entry any) string {
	record, ok := entry.(map[string]any)
	if !ok {
		return "inconclusive"
	}
	pid, ok := intField(record["pid"])
	if !ok {
		return "inconclusive"
	}
	token, _ := record["creation_token"].(string)
	executable, _ := record["executable"].(string)
	if token == "" || executable == "" {
		if _, status := lock.Identity(pid); status == "dead" {
			return "dead"
		}
		return "inconclusive"
	}
	return lock.Probe(pid, token, executable)
}

// SessionJournalPaths lists session.json files (optionally one session).
func SessionJournalPaths(target, sessionID string) ([]string, error) {
	sessions := filepath.Join(target, ".x86qw", "sessions")
	info, err := os.Lstat(sessions)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil || !info.IsDir() {
		return nil, errorf("Diretório de sessões ausente ou inseguro: %s", sessions)
	}
	var directories []string
	if sessionID != "" {
		directories = []string{filepath.Join(sessions, sessionID)}
	} else {
		entries, _ := os.ReadDir(sessions)
		for _, entry := range entries {
			directories = append(directories, filepath.Join(sessions, entry.Name()))
		}
		sort.Strings(directories)
	}
	var paths []string
	for _, dir := range directories {
		info, err := os.Lstat(dir)
		if err != nil || !info.IsDir() {
			continue
		}
		journal := filepath.Join(dir, "session.json")
		if _, err := os.Lstat(journal); err == nil {
			paths = append(paths, journal)
			continue
		}
		if removeIncompleteInitialSession(dir) {
			continue
		}
		return nil, errorf("Journal de sessão inválido preservado para inspeção: %s", dir)
	}
	return paths, nil
}

func removeIncompleteInitialSession(dir string) bool {
	if !sessionDirectoryName.MatchString(filepath.Base(dir)) {
		return false
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return false
	}
	if len(entries) == 1 {
		if !initialStagingName.MatchString(entries[0].Name()) {
			return false
		}
		staging := filepath.Join(dir, entries[0].Name())
		if info, err := os.Lstat(staging); err != nil || !info.Mode().IsRegular() || info.Size() != 0 {
			return false
		}
		if os.Remove(staging) != nil {
			return false
		}
	} else if len(entries) > 1 {
		return false
	}
	return os.Remove(dir) == nil
}

// AssertRecoveryConfirmable refuses to go on while a journal's processes
// cannot be confirmed dead or ours.
func AssertRecoveryConfirmable(target string) error {
	paths, err := SessionJournalPaths(target, "")
	if err != nil {
		return err
	}
	for _, file := range paths {
		data, err := LoadJournal(file)
		if err != nil {
			return err
		}
		if data["status"] == "clean" {
			continue
		}
		probe := controllerProbe(data)
		if probe == "alive" {
			controller := data["controller"].(map[string]any)
			return errorf("O controlador registrado no journal continua ativo; recuperação bloqueada.\nSessão: %v\nControlador: PID %v", data["session_id"], controller["pid"])
		}
		if probe == "inconclusive" {
			return errorf("Não foi possível confirmar o controlador do journal; lock, processos e arquivos foram preservados em %s.", filepath.Dir(file))
		}
		processes, _ := data["processes"].([]any)
		for _, entry := range processes {
			status := processProbe(entry)
			if status == "inconclusive" {
				label := "processo"
				if record, ok := entry.(map[string]any); ok {
					if text, ok := record["label"].(string); ok {
						label = text
					}
				}
				return errorf("Não foi possível confirmar a identidade de %s na sessão abandonada; lock, journal e arquivos foram preservados em %s.", label, filepath.Dir(file))
			}
			if probe == "" && status == "alive" {
				return errorf("Journal legado possui processo vivo e não prova que seu controlador terminou; processos e arquivos foram preservados em %s.", filepath.Dir(file))
			}
		}
	}
	return nil
}

// RecoverSessions reconciles every abandoned session (recover_sessions).
func RecoverSessions(target string, warn func(string)) error {
	if err := AssertRecoveryConfirmable(target); err != nil {
		return err
	}
	paths, err := SessionJournalPaths(target, "")
	if err != nil {
		return err
	}
	for _, file := range paths {
		if err := reconcileJournal(target, file, warn); err != nil {
			return err
		}
	}
	return nil
}

func reconcileJournal(target, file string, warn func(string)) error {
	data, err := LoadJournal(file)
	if err != nil {
		warn(fmt.Sprintf("Journal de sessão inválido preservado para inspeção: %s", filepath.Dir(file)))
		return nil
	}
	if data["status"] == "clean" {
		return nil
	}
	probe := controllerProbe(data)
	if probe == "alive" {
		return errorf("O controlador da sessão %v continua ativo; recuperação bloqueada.", data["session_id"])
	}
	if probe == "inconclusive" {
		return errorf("O controlador da sessão %v está inconclusivo; estado preservado.", data["session_id"])
	}
	data["status"] = "interrupted"
	actions, _ := data["recovery_actions"].([]any)
	processes, _ := data["processes"].([]any)
	if probe == "" {
		for _, entry := range processes {
			if status := processProbe(entry); status == "alive" || status == "inconclusive" {
				return errorf("Journal legado não prova que seu controlador terminou; processos e arquivos foram preservados em %s.", filepath.Dir(file))
			}
		}
	}
	for index := len(processes) - 1; index >= 0; index-- {
		entry, ok := processes[index].(map[string]any)
		if processProbe(processes[index]) == "inconclusive" {
			return errorf("Processo possivelmente ativo não pôde ser confirmado; journal preservado: %s", filepath.Dir(file))
		}
		if !ok {
			continue
		}
		result, err := terminateRecordedProcess(entry)
		if err != nil {
			return err
		}
		label := entry["label"]
		if label == nil {
			label = "processo"
		}
		actions = append(actions, map[string]any{"at": isoNow(), "label": label, "pid": entry["pid"], "result": result})
	}
	data["recovery_actions"] = actions
	if actions == nil {
		data["recovery_actions"] = []any{}
	}
	for _, collection := range []string{"temporary_files", "materialized_files"} {
		entries, _ := data[collection].([]any)
		for _, item := range entries {
			entry, ok := item.(map[string]any)
			if !ok || entry["created_by_session"] != true {
				continue
			}
			candidate, ok := JournalPath(target, entry["path"])
			if !ok {
				continue
			}
			if _, err := os.Lstat(candidate); err != nil {
				continue
			}
			identity, hasIdentity := recordedIdentity(entry)
			if entry["sensitive"] == true {
				if hasIdentity && cleanupSensitive(candidate, identity) {
					continue
				}
				entry["modified_during_session"] = true
				warn("Temporário sensível substituído foi preservado: " + candidate)
				return errorf("Temporário sensível sem identidade original confirmável foi preservado: %s", candidate)
			}
			expected, _ := entry["expected_hash"].(string)
			size, sizeOK := intField(entry["expected_size"])
			if expected != "" && sizeOK && hasIdentity {
				file := &MaterializedFile{Path: candidate, ExpectedHash: expected, Size: int64(size), Identity: identity, CreatedBySession: true}
				if cleanupMaterializedFile(file) {
					continue
				}
			}
			entry["modified_during_session"] = true
			warn("Arquivo de sessão alterado foi preservado: " + candidate)
		}
	}
	directories, _ := data["created_directories"].([]any)
	for index := len(directories) - 1; index >= 0; index-- {
		var relative any = directories[index]
		var identity [2]int64
		hasIdentity := false
		if record, ok := directories[index].(map[string]any); ok {
			relative = record["path"]
			identity, hasIdentity = recordedIdentity(record)
		}
		candidate, ok := JournalPath(target, relative)
		if !ok {
			continue
		}
		if _, err := os.Lstat(candidate); err != nil {
			continue
		}
		if hasIdentity && cleanupDirectory(candidate, identity) {
			continue
		}
		warn("Diretório de sessão sem identidade confirmável foi preservado: " + candidate)
	}
	data["status"] = "clean"
	data["recovered_at"] = isoNow()
	text, err := pyjson.Dumps(data, 2)
	if err == nil {
		err = atomicfile.Write(file, []byte(text+"\n"), 0o600)
	}
	if err != nil {
		return errorf("O journal privado de recuperação não pôde ser gravado.")
	}
	return nil
}

func recordedIdentity(entry map[string]any) ([2]int64, bool) {
	device, okDevice := intField(entry["device"])
	inode, okInode := intField(entry["inode"])
	return [2]int64{int64(device), int64(inode)}, okDevice && okInode
}
