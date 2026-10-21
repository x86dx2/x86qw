package tui

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"

	"github.com/x86dx2/x86qw/go/internal/setup"
)

// The tools run inside the app: what one prints shows in a floating panel,
// and what it can do sits under it as buttons. Nothing leaves the screen.

// toolAction is a button under a tool's report.
type toolAction struct {
	id string // its texts are tool.<tool>.<id> and tool.<tool>.<id>.d
	// args is the command it runs; nil args hide the button.
	args func(m *Model) []string
	// open is what it does instead of running a command.
	open    func(m *Model) error
	armed   bool // it deletes or overwrites: a second enter confirms
	changes bool // the installation changed: read it again
}

// toolSpec is one entry of the tools dialog.
type toolSpec struct {
	name string
	// report is the command whose output the panel shows when it opens;
	// without one the panel explains the tool (tool.<name>.about).
	report  func(m *Model) []string
	actions []toolAction
}

func onTarget(command string, extra ...string) func(m *Model) []string {
	return func(m *Model) []string { return append([]string{command, "--target", m.target()}, extra...) }
}

var tools = []toolSpec{
	{name: "doctor", report: onTarget("doctor")},
	{name: "verify", report: onTarget("verify"), actions: []toolAction{{id: "repair", args: onTarget("repair", "--yes"), changes: true}}},
	{name: "upgrade", report: onTarget("upgrade", "--dry-run"), actions: []toolAction{{id: "apply", args: onTarget("upgrade", "--yes"), changes: true}}},
	{name: "changes", report: onTarget("changes"), actions: []toolAction{{id: "gitignore", args: onTarget("changes", "--sync-gitignore")}}},
	{name: "library", report: onTarget("library")},
	{name: "profile", report: onTarget("profile"), actions: []toolAction{
		{id: "backup", args: func(m *Model) []string {
			return []string{"profile", "--target", m.target(), "--backup", newBackup()}
		}},
		{id: "restore", armed: true, changes: true, args: func(m *Model) []string {
			if latest := latestBackup(); latest != "" {
				return []string{"profile", "--target", m.target(), "--restore", latest}
			}
			return nil
		}},
	}},
	{name: "cleanup", actions: []toolAction{
		{id: "caches", args: onTarget("cleanup")},
		{id: "downloads", args: onTarget("cleanup", "--downloads")},
		{id: "personal", armed: true, args: onTarget("cleanup", "--downloads", "--personal-data")},
	}},
	{name: "ui", report: onTarget("ui"), actions: []toolAction{{id: "open", open: func(m *Model) error {
		return setup.OpenInBrowser(strings.TrimSpace(strings.Join(m.toolPanel.lines, "\n")))
	}}}},
	{name: "help", report: func(*Model) []string { return []string{"help"} }},
}

// Profile backups go to the home folder, newest last by name.
const backupPrefix = "x86qw-profile-"

func newBackup() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, backupPrefix+time.Now().Format("20060102-150405")+".zip")
}

func latestBackup() string {
	home, _ := os.UserHomeDir()
	found, _ := filepath.Glob(filepath.Join(home, backupPrefix+"*.zip"))
	if len(found) == 0 {
		return ""
	}
	sort.Strings(found)
	return found[len(found)-1]
}

// report is a tool open in its floating panel.
type report struct {
	spec   *toolSpec
	lines  []string // what the last command printed
	failed bool     // the last command failed
	busy   string   // "report" or the action running, "" when idle
	offset int      // the first line on view
	height int      // the text's rows, fixed while the panel is open
}

// toolMsg is what a tool printed.
type toolMsg struct {
	tool, action string
	output       string
	err          error
}

// openTool opens a tool's panel and runs its report.
func (m *Model) openTool(spec *toolSpec) tea.Cmd {
	m.modal = &modal{kind: "tool", field: map[string]*Field{}, before: cloneOptions(m.opts), match: m.game.match}
	m.toolPanel = &report{spec: spec, height: max(6, min(12, m.h-16))}
	if spec.report == nil {
		m.toolPanel.lines = []string{m.t("tool." + spec.name + ".about")}
		return nil
	}
	return m.runTool("report", spec.report(m))
}

func (m *Model) runTool(action string, args []string) tea.Cmd {
	m.toolPanel.busy = action
	backend, tool := m.cfg.Backend, m.toolPanel.spec.name
	return func() tea.Msg {
		output, err := backend.Tool(context.Background(), args)
		return toolMsg{tool, action, output, err}
	}
}

// toolDone shows what a tool printed; a panel closed meanwhile only gets
// a toast.
func (m *Model) toolDone(msg toolMsg) tea.Cmd {
	changed := false
	for _, spec := range tools {
		for _, action := range spec.actions {
			changed = changed || (spec.name == msg.tool && action.id == msg.action && action.changes)
		}
	}
	output := strings.TrimRight(msg.output, "\n")
	if msg.err != nil && strings.TrimSpace(output) == "" {
		output = m.explain(msg.err)
	}
	if m.toolPanel == nil || m.toolPanel.spec.name != msg.tool {
		if msg.action != "report" {
			lines := strings.Split(output, "\n")
			m.say(lines[len(lines)-1])
		}
	} else {
		m.toolPanel.lines, m.toolPanel.failed, m.toolPanel.busy, m.toolPanel.offset = strings.Split(output, "\n"), msg.err != nil, "", 0
	}
	if changed {
		return m.reload()
	}
	return nil
}

// toolActions are the buttons a tool shows now.
func (m *Model) toolActions() []toolAction {
	var shown []toolAction
	for _, action := range m.toolPanel.spec.actions {
		if action.args == nil || action.args(m) != nil {
			shown = append(shown, action)
		}
	}
	return shown
}

// toolKey: ↑↓ scroll the report, ←→ pick a button, enter presses it.
func (m *Model) toolKey(key string) tea.Cmd {
	r := m.toolPanel
	actions := m.toolActions()
	lines := m.toolLines()
	bottom := max(0, len(lines)-r.height)
	switch key {
	case "esc":
		return m.closeModal(false)
	case "up", "k":
		r.offset = max(0, r.offset-1)
	case "down", "j":
		r.offset = min(bottom, r.offset+1)
	case "pgup":
		r.offset = max(0, r.offset-r.height)
	case "pgdown", "space", " ":
		r.offset = min(bottom, r.offset+r.height)
	case "home":
		r.offset = 0
	case "end":
		r.offset = bottom
	case "left", "h", "shift+tab":
		if len(actions) > 0 {
			m.modal.focus = (m.modal.focus + len(actions) - 1) % len(actions)
		}
	case "right", "l", "tab":
		if len(actions) > 0 {
			m.modal.focus = (m.modal.focus + 1) % len(actions)
		}
	case "enter":
		if len(actions) == 0 {
			return m.closeModal(false)
		}
		return m.pressTool(actions[min(m.modal.focus, len(actions)-1)])
	}
	return nil
}

// pressTool runs a button, once it is armed when it has to be.
func (m *Model) pressTool(action toolAction) tea.Cmd {
	r := m.toolPanel
	if r.busy != "" || (action.armed && !m.armedFor("tool."+action.id)) {
		return nil
	}
	if action.open != nil {
		if err := action.open(m); err != nil {
			m.say(m.explain(err))
		}
		return nil
	}
	return m.runTool(action.id, action.args(m))
}

// toolLines are the report's lines wrapped to the panel, with their tone.
func (m *Model) toolLines() []string {
	t := m.theme
	inner := max(minWidth, min(m.w-8, modalWidth)) - 4
	var out []string
	for _, line := range m.toolPanel.lines {
		style := t.Style(t.Text2)
		switch trimmed := strings.TrimSpace(line); {
		case strings.HasPrefix(trimmed, "✓"), strings.HasPrefix(trimmed, "[ok]"):
			style = t.Style(t.Success)
		case strings.HasPrefix(trimmed, "✗"), strings.HasPrefix(trimmed, "[fail]"):
			style = t.Style(t.Error)
		case strings.HasPrefix(trimmed, "!"), strings.HasPrefix(trimmed, "[warn]"):
			style = t.Style(t.Warning)
		case strings.HasPrefix(trimmed, "·"), strings.HasPrefix(trimmed, "[skip]"):
			style = t.Style(t.Gray)
		}
		for _, wrapped := range strings.Split(lipgloss.Wrap(line, inner-2, " /\\"), "\n") {
			out = append(out, style.Render(wrapped))
		}
	}
	return out
}

// toolRowsView is the panel: the report's window, with where it is when it
// scrolls, then the buttons.
func (m *Model) toolRowsView() []formRow {
	r := m.toolPanel
	t := m.theme
	inner := max(minWidth, min(m.w-8, modalWidth)) - 4
	var window []string
	if r.busy == "report" {
		window = []string{t.Style(t.Gray).Render(m.spinner() + " " + m.t("tool.running"))}
	} else {
		lines := m.toolLines()
		room := r.height
		if len(lines) > room {
			room-- // the last row tells where the window is
		}
		r.offset = min(r.offset, max(0, len(lines)-room))
		window = append(window, lines[r.offset:min(len(lines), r.offset+room)]...)
		if len(lines) > room {
			window = append(window, t.Style(t.Gray).Render(fmt.Sprintf("↑↓ %d–%d / %d", r.offset+1, r.offset+room, len(lines))))
		}
	}
	for len(window) < r.height {
		window = append(window, "")
	}
	rows := []formRow{{kind: rowCustom, lines: window}}
	actions := m.toolActions()
	if len(actions) == 0 {
		return rows
	}
	rows = append(rows, formRow{kind: rowCustom, lines: []string{"", t.Style(t.Faint()).Render(strings.Repeat("─", inner))}})
	for index, action := range actions {
		desc := m.t("tool." + r.spec.name + "." + action.id + ".d")
		if r.busy == action.id {
			desc = m.spinner() + " " + m.t("tool.running")
		}
		rows = append(rows, formRow{id: "tool." + action.id, kind: rowAction, label: m.t("tool." + r.spec.name + "." + action.id), desc: desc,
			activate: func() { m.modal.focus = index; m.pending = m.pressTool(action) }})
	}
	return rows
}
