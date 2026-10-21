package tui

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	tea "charm.land/bubbletea/v2"

	"github.com/x86dx2/x86qw/go/internal/setup"
)

// drive feeds messages to the model and runs the commands they return
// until they settle, like a program without a terminal.
func drive(m *Model, msgs ...tea.Msg) {
	queue := append([]tea.Msg(nil), msgs...)
	for len(queue) > 0 {
		msg := queue[0]
		queue = queue[1:]
		_, cmd := m.Update(msg)
		// Every intermediate screen is drawn, as the program would, so a
		// state that cannot render fails the test that reaches it.
		m.View()
		queue = append(queue, run(cmd)...)
	}
}

func run(cmd tea.Cmd) []tea.Msg {
	if cmd == nil {
		return nil
	}
	done := make(chan tea.Msg, 1)
	go func() { done <- cmd() }()
	select {
	case msg := <-done:
		switch msg := msg.(type) {
		case tea.BatchMsg:
			var out []tea.Msg
			for _, c := range msg {
				out = append(out, run(c)...)
			}
			return out
		case tickMsg, stackPollMsg, updateCheckMsg, nil:
			return nil
		}
		return []tea.Msg{msg}
	case <-time.After(3 * time.Second):
		return nil
	}
}

func key(s string) tea.Msg { return tea.KeyPressMsg(keyFor(s)) }

func typeText(s string) []tea.Msg {
	var out []tea.Msg
	for _, r := range s {
		out = append(out, tea.KeyPressMsg(tea.Key{Code: r, Text: string(r)}))
	}
	return out
}

// TestSnapshots renders every screen into X86QW_SNAPSHOTS for review.
func TestSnapshots(t *testing.T) {
	dir := os.Getenv("X86QW_SNAPSHOTS")
	if dir == "" {
		t.Skip("set X86QW_SNAPSHOTS=<folder> to write the captures")
	}
	os.MkdirAll(dir, 0o755)
	save := func(m *Model, name string) {
		content := m.View().Content
		if err := os.WriteFile(filepath.Join(dir, name+".ansi"), []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	for _, theme := range []string{"dark", "light"} {
		demo := &setup.Demo{Speed: 4}
		m := New(Config{Backend: demo, Target: "/Users/x86/Games/x86qw", Lang: PT, Demo: true, Theme: theme})
		network := setup.Network{}
		_ = context.Background()
		drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
		drive(m, run(m.Init())...)
		_ = network
		save(m, theme+"-01-intent-empty")
		drive(m, typeText("x86dx2")...)
		save(m, theme+"-02-intent-nick")
		drive(m, key("down"), key("down"))
		save(m, theme+"-03-intent-both")
		drive(m, key("enter"))
		save(m, theme+"-05-setup")
		m.focus = 1
		drive(m, key("enter"))
		save(m, theme+"-06-config-keys")
		drive(m, key("tab"))
		save(m, theme+"-07-config-mouse")
		drive(m, key("esc"))
		m.focus = 2
		drive(m, key("enter"), key("tab"))
		save(m, theme+"-08-server-network")
		drive(m, key("shift+tab"))
		save(m, theme+"-09-server-general")
		drive(m, key("esc"))
		m.focus = 0
		drive(m, key("enter"))
		save(m, theme+"-10-components")
		drive(m, key("esc"))
		drive(m, key("i"))
		save(m, theme+"-06-preflight-review")
		focus := m.focus
		m.focus = 2
		save(m, theme+"-06b-preflight-server-open")
		m.focus = focus
		demo.Speed = 0.2
		m.do(func() tea.Cmd { return m.startInstall() })
		for i := 0; i < 4000 && (m.progress.Total == 0 || m.progress.Done < m.progress.Total*45/100); i++ {
			m.progress = <-m.instCh
		}
		save(m, theme+"-11-install")
		m.showLog = true
		save(m, theme+"-11b-install-log")
		m.showLog = false
		demo.Speed = 50
		for progress := range m.instCh {
			m.progress = progress
		}
		m.screen, m.focus = scDone, 0
		save(m, theme+"-12-done")
	}
	m := New(Config{Backend: &setup.Demo{ExistingInstall: true}, Target: "/Users/x86/Games/x86qw", Lang: PT, Demo: true, Theme: "dark"})
	drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(m, run(m.Init())...)
	save(m, "dark-13-panel")
	m.focus = homeRow(m, "update")
	drive(m, key("enter"))
	save(m, "dark-14-panel-update")
}

func (m *Model) do(f func() tea.Cmd) { f() }

// The quiet update check fires at once in tests, and drive drops it.
func init() { checkEvery = time.Millisecond }
