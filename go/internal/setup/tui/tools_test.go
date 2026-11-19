package tui

import (
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/setup"
)

func toolNamed(name string) *toolSpec {
	for index := range tools {
		if tools[index].name == name {
			return &tools[index]
		}
	}
	return nil
}

// A tool opens a floating panel over the app with what it printed; the
// interface never quits for it.
func TestToolsRunInAFloatingPanel(t *testing.T) {
	m := started(t, true)
	m.lang = EN
	toConsole(m)
	m.focus = homeRow(m, "tools")
	drive(m, key("enter"))
	if m.modal == nil || m.modal.kind != "tools" {
		t.Fatal("the tools dialog did not open")
	}
	drive(m, key("enter"))
	if m.modal == nil || m.modal.kind != "tool" || m.toolPanel.spec.name != "doctor" || m.toolPanel.busy != "" {
		t.Fatalf("the doctor's panel is not open: %+v", m.modal)
	}
	view := m.View().Content
	for _, want := range []string{"Diagnose", "[ok] installation", "No problems found."} {
		if !strings.Contains(view, want) {
			t.Fatalf("%q missing:\n%s", want, view)
		}
	}
	drive(m, key("esc"))
	if m.modal != nil || m.toolPanel != nil {
		t.Fatal("esc should close the panel")
	}
}

// A button runs inside the panel; one that deletes asks for a second
// enter first.
func TestToolButtonsAct(t *testing.T) {
	m := started(t, true)
	m.lang = EN
	drive(m, run(m.openTool(toolNamed("upgrade")))...)
	if view := m.View().Content; !strings.Contains(view, "Dry run done") || !strings.Contains(view, "Apply") {
		t.Fatalf("the plan and its button:\n%s", view)
	}
	drive(m, key("enter"))
	if m.toolPanel == nil || m.toolPanel.busy != "" || m.toolPanel.failed {
		t.Fatalf("apply did not finish in the panel: %+v", m.toolPanel)
	}

	drive(m, key("esc"))
	drive(m, run(m.openTool(toolNamed("cleanup")))...)
	if view := m.View().Content; !strings.Contains(view, "Deletes what x86QW can regenerate") {
		t.Fatalf("cleanup should explain itself:\n%s", view)
	}
	m.modal.focus = 2 // also personal data
	drive(m, key("enter"))
	if !strings.Contains(m.View().Content, "Deletes what x86QW can regenerate") || m.armed != "tool.personal" {
		t.Fatalf("the first enter ran it: armed %q", m.armed)
	}
	drive(m, key("enter"))
	if view := m.View().Content; !strings.Contains(view, "Safe cleanup") {
		t.Fatalf("the second enter did not run it:\n%s", view)
	}
}

// The app asks before a tool deletes or overwrites files, so the command
// it runs must not ask again: inside the app nobody could answer it.
func TestToolsThatChangeFilesSayYes(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	if err := os.WriteFile(filepath.Join(home, backupPrefix+"20260101-000000.zip"), nil, 0o644); err != nil {
		t.Fatal(err)
	}
	m := started(t, true)
	for _, name := range []string{"profile", "cleanup"} {
		for _, action := range toolNamed(name).actions {
			args := action.args(m)
			changes := slices.Contains(args, "--restore") || slices.Contains(args, "--downloads") || slices.Contains(args, "--personal-data")
			if changes != slices.Contains(args, "--yes") {
				t.Errorf("%s %s: %v", name, action.id, args)
			}
		}
	}
}

// A long report scrolls inside the panel, which keeps its height.
func TestToolReportScrolls(t *testing.T) {
	m := started(t, true)
	drive(m, run(m.openTool(toolNamed("doctor")))...)
	m.toolPanel.lines = strings.Split(strings.Repeat("line\n", 60), "\n")
	before := len(strings.Split(m.View().Content, "\n"))
	drive(m, key("end"))
	if m.toolPanel.offset == 0 {
		t.Fatal("end did not scroll")
	}
	if after := len(strings.Split(m.View().Content, "\n")); after != before {
		t.Fatalf("the panel moved: %d lines, then %d", before, after)
	}
}

// A quiet check that finds new versions shows them without touching the
// choices on screen.
func TestQuietCheckShowsNewUpdates(t *testing.T) {
	m := started(t, true)
	m.lang = EN
	before := len(m.existing.Updates)
	opts := m.opts
	found := *m.existing
	found.Updates = append(append([]setup.Update(nil), m.existing.Updates...), setup.Update{ID: "cli", Label: "x86QW CLI", To: "9.9.9"})
	drive(m, freshMsg{catalog: m.catalog, existing: &found})
	if len(m.existing.Updates) != before+1 || m.toast != "New versions available" {
		t.Fatalf("updates %d (was %d), toast %q", len(m.existing.Updates), before, m.toast)
	}
	if !reflect.DeepEqual(m.opts, opts) {
		t.Fatal("the quiet check changed the choices")
	}
}
