package services

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode"

	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/pyjson"
)

// Human `status` and `status --stop`.

var statusParameterLabels = [][2]string{
	{"game", "Game"}, {"mode", "Mode"}, {"map", "Map"}, {"bots", "Bots"}, {"bot_skill", "Skill"},
	{"bot_names", "Names"}, {"bot_team", "Team"}, {"bot_weapon", "Weapon"}, {"bot_health", "Health"},
	{"ctf_hook", "CTF hook"}, {"ctf_runes", "CTF runes"}, {"race_style", "Race style"},
	{"race_scoring", "Race scoring"}, {"hostname", "Hostname"}, {"bind", "Bind"}, {"port", "Port"},
	{"maxclients", "Clients"}, {"mvd", "MVD recording"}, {"memory", "Memory"}, {"secrets", "Secrets"},
	{"protocol", "Protocol"}, {"http", "HTTP"}, {"upstream", "Upstream"}, {"upstream_secret", "Upstream secret"},
}

var sessionStatusLabels = map[string]string{
	"starting": "starting", "running": "running", "stopping": "stopping",
	"interrupted": "interrupted", "clean": "finished",
}

func statusValue(value any) string {
	var text string
	switch typed := value.(type) {
	case string:
		text = typed
	case nil:
		text = "None"
	case bool:
		text = map[bool]string{true: "True", false: "False"}[typed]
	default:
		text = fmt.Sprint(typed)
	}
	var builder strings.Builder
	count := 0
	for _, character := range text {
		if count == 512 {
			break
		}
		if unicode.IsPrint(character) {
			builder.WriteRune(character)
		} else {
			builder.WriteRune('?')
		}
		count++
	}
	return builder.String()
}

func processStatusLabel(status string) string {
	labels := map[string]string{
		"alive": "running", "dead": "stopped",
		"identity_mismatch": "PID reused; process left alone", "inconclusive": "identity inconclusive",
	}
	if label, ok := labels[status]; ok {
		return label
	}
	return statusValue(status)
}

func statusExecutable(target string, value any) string {
	text := statusValue(value)
	executable, _ := value.(string)
	resolved := executable
	if real, err := filepath.EvalSymlinks(executable); err == nil {
		resolved = real
	}
	if relative, err := filepath.Rel(target, resolved); err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator)) && !filepath.IsAbs(relative) {
		return filepath.ToSlash(relative)
	}
	return text
}

func (c *Console) row(label string, value any) {
	padding := 18 - len([]rune(label))
	if padding < 0 {
		padding = 0
	}
	fmt.Fprintf(c.Out, "  %s%s| %s\n", label, strings.Repeat(" ", padding), statusValue(value))
}

func printSession(c *Console, target string, data map[string]any, ownsLock bool) {
	c.Section(fmt.Sprintf("Session %v", data["session_id"]))
	status := fmt.Sprint(data["status"])
	if label, ok := sessionStatusLabels[status]; ok {
		c.row("State", label)
	} else {
		c.row("State", data["status"])
	}
	c.row("Lock", map[bool]string{true: "held", false: "absent"}[ownsLock])
	c.row("Started", data["created_at"])
	if background, _ := data["background"].(bool); background {
		c.row("Runs in", "background")
	} else {
		c.row("Runs in", "foreground")
	}
	if log, ok := data["background_log"].(string); ok && log != "" {
		c.row("Log", log)
	}
	if controller, ok := data["controller"].(map[string]any); ok {
		c.row("Controller", fmt.Sprintf("PID %v · %s", controller["pid"], processStatusLabel(controllerProbe(data))))
		c.row("Command", controller["command"])
	} else {
		c.row("Controller", "identity not recorded")
	}
	processes, _ := data["processes"].([]any)
	if len(processes) == 0 {
		c.Info("No process was recorded; the stack may still be starting.")
		return
	}
	for _, item := range processes {
		entry, _ := item.(map[string]any)
		labelValue, ok := entry["label"]
		if !ok {
			labelValue = "Service"
		}
		label := statusValue(labelValue)
		fmt.Fprintf(c.Out, "\n  %s\n", c.paint(label, "1;36"))
		c.row("State", processStatusLabel(processProbe(entry)))
		c.row("PID", entry["pid"])
		if runtime, ok := entry["runtime"]; ok {
			c.row("Runtime", runtime)
		} else {
			c.row("Runtime", strings.ToLower(label))
		}
		address, okAddress := entry["address"].(string)
		port, okPort := intField(entry["port"])
		if okAddress && okPort {
			c.row("Local endpoint", Endpoint(address, port))
		}
		if executable, ok := entry["executable"].(string); ok && executable != "" {
			c.row("Executable", statusExecutable(target, executable))
		}
		parameters, _ := entry["parameters"].(map[string]any)
		displayed := false
		for _, item := range statusParameterLabels {
			if value, ok := parameters[item[0]]; ok {
				c.row(item[1], value)
				displayed = true
			}
		}
		if !displayed {
			c.row("Parameters", "not recorded in this session")
		}
	}
}

// ShowStatus prints the active services; it reports whether a service
// controller is confirmed alive.
func ShowStatus(c *Console, target string) (bool, error) {
	c.Banner("view running services", target)
	lockPath := filepath.Join(target, ".x86qw", "sessions", "active.lock")
	var owner *lock.Owner
	active := false
	if _, err := os.Lstat(lockPath); err == nil {
		if owner, err = lock.ReadOwner(lockPath); err != nil {
			return false, errorf("%v", err)
		}
		probe := lock.Probe(owner.ControllerPID, owner.ControllerStartToken, owner.ControllerExecutable)
		c.Section("Active operation")
		kind := "services"
		if owner.OperationKind == "maintenance" {
			kind = "maintenance"
		}
		c.row("Kind", kind)
		c.row("Command", owner.Command)
		c.row("Session", owner.SessionID)
		c.row("Controller", fmt.Sprintf("PID %d · %s", owner.ControllerPID, processStatusLabel(probe)))
		active = owner.OperationKind != "maintenance" && probe == "alive"
	}
	paths, err := SessionJournalPaths(target, "")
	if err != nil {
		return false, err
	}
	type unfinished struct{ data map[string]any }
	var pending []unfinished
	for _, path := range paths {
		data, err := LoadJournal(path)
		if err != nil {
			return false, err
		}
		if data["status"] != "clean" {
			pending = append(pending, unfinished{data})
		}
	}
	hint := func() {
		if active {
			c.Info("To stop it: x86qw status --stop.")
		}
	}
	if len(pending) == 0 {
		switch {
		case owner != nil && owner.OperationKind == "service":
			c.Info("The stack is starting; no process was recorded yet.")
		case owner != nil:
			c.Info("No services are recorded during this maintenance operation.")
		default:
			c.Success("No x86QW service is running in this installation.")
		}
		hint()
		return active, nil
	}
	lockSession := ""
	if owner != nil {
		lockSession = owner.SessionID
	}
	for _, item := range pending {
		ownsLock := owner != nil && fmt.Sprint(item.data["session_id"]) == lockSession
		if !ownsLock {
			c.Warning(fmt.Sprintf("Session without an active lock, kept for inspection: %v", item.data["session_id"]))
		}
		printSession(c, target, item.data, ownsLock)
	}
	hint()
	return active, nil
}

// RequestStop publishes stop.request for the
// confirmed controller and wait until it releases the lock.
func RequestStop(target string, timeout time.Duration) error {
	lockPath := filepath.Join(target, ".x86qw", "sessions", "active.lock")
	if _, err := os.Lstat(lockPath); err != nil {
		return errorf("No x86QW service stack is running.")
	}
	owner, err := lock.ReadOwner(lockPath)
	if err != nil {
		return errorf("%v", err)
	}
	if owner.OperationKind != "" && owner.OperationKind != "service" {
		return errorf("The active operation is %s, not a service stack.", owner.Command)
	}
	if lock.Probe(owner.ControllerPID, owner.ControllerStartToken, owner.ControllerExecutable) != "alive" {
		return errorf("The active controller could not be confirmed; no process was stopped.")
	}
	paths, err := SessionJournalPaths(target, owner.SessionID)
	if err != nil {
		return err
	}
	if len(paths) != 1 {
		return errorf("The active stack's journal is not available; no process was stopped.")
	}
	journal, err := LoadJournal(paths[0])
	if err != nil {
		return err
	}
	controller, _ := journal["controller"].(map[string]any)
	pid, _ := intField(controller["pid"])
	executable, _ := controller["executable"].(string)
	if controller == nil || pid != owner.ControllerPID || controller["creation_token"] != owner.ControllerStartToken ||
		realpath(executable) != realpath(owner.ControllerExecutable) {
		return errorf("The lock and the journal do not name the same controller; nothing was stopped.")
	}
	request := filepath.Join(filepath.Dir(paths[0]), "stop.request")
	payload, err := pyjson.Dumps(map[string]any{
		"format": 1, "project": "x86qw", "session_id": owner.SessionID, "requested_at": isoNow(),
	}, -1)
	if err != nil {
		return err
	}
	identity, err := publishStopRequest(request, []byte(payload+"\n"))
	if err != nil {
		return err
	}
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if _, err := os.Lstat(lockPath); errors.Is(err, os.ErrNotExist) {
			unlinkStopRequest(request, identity)
			return nil
		}
		current, err := lock.ReadOwner(lockPath)
		if err != nil {
			if _, statErr := os.Lstat(lockPath); errors.Is(statErr, os.ErrNotExist) {
				unlinkStopRequest(request, identity)
				return nil
			}
			return errorf("%v", err)
		}
		if current.SessionID != owner.SessionID {
			return errorf("The lock changed owner while stopping.")
		}
		time.Sleep(100 * time.Millisecond)
	}
	return errorf("The stack got the request but did not finish stopping in time.")
}

func realpath(path string) string {
	if resolved, err := filepath.EvalSymlinks(path); err == nil {
		return resolved
	}
	if absolute, err := filepath.Abs(path); err == nil {
		return absolute
	}
	return path
}

// publishStopRequest stages a private ".stop-*.request" and links it into
// place (create-only), like sessions.publish_stop_request.
func publishStopRequest(request string, payload []byte) ([2]int64, error) {
	var file *os.File
	var temporary string
	for attempt := 0; attempt < 128 && file == nil; attempt++ {
		temporary = filepath.Join(filepath.Dir(request), ".stop-"+tokenHex(4)+".request")
		created, err := os.OpenFile(temporary, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
		if errors.Is(err, os.ErrExist) {
			continue
		}
		if err != nil {
			return [2]int64{}, errorf("Could not create the private stop request.")
		}
		file = created
	}
	if file == nil {
		return [2]int64{}, errorf("Could not create the private stop request.")
	}
	defer os.Remove(temporary)
	identity, err := identityOfFile(file)
	if err != nil {
		file.Close()
		return [2]int64{}, errorf("Could not create the private stop request.")
	}
	_, writeErr := file.Write(payload)
	if err := errors.Join(writeErr, file.Sync(), file.Close()); err != nil {
		return [2]int64{}, errorf("Could not publish the private stop request.")
	}
	if err := os.Link(temporary, request); err != nil {
		if errors.Is(err, os.ErrExist) {
			return [2]int64{}, errorf("A stop request for this stack already exists.")
		}
		return [2]int64{}, errorf("Could not publish the private stop request.")
	}
	return identity, nil
}

func unlinkStopRequest(request string, identity [2]int64) {
	if info, err := os.Lstat(request); err == nil && info.Mode().IsRegular() && identityAt(request, info) == identity {
		os.Remove(request)
	}
}
