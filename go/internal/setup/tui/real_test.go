package tui

import (
	"os"
	"path/filepath"
	"testing"

	tea "charm.land/bubbletea/v2"

	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/setup"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

// TestInterfaceInstallsForReal drives the interface from the nick to the
// end against the published catalog; set X86QW_NETWORK_TESTS=1 and
// X86QW_REAL_INSTALL=1.
func TestInterfaceInstallsForReal(t *testing.T) {
	if os.Getenv("X86QW_NETWORK_TESTS") == "" || os.Getenv("X86QW_REAL_INSTALL") == "" {
		t.Skip("set X86QW_NETWORK_TESTS=1 and X86QW_REAL_INSTALL=1 to install for real")
	}
	t.Setenv("X86QW_CACHE_DIR", t.TempDir())
	t.Setenv("X86QW_NO_MACOS_PREFERENCES", "1")
	target := filepath.Join(t.TempDir(), "x86qw")
	m := New(Config{Backend: &setup.Real{Trust: trust.Options{CacheDir: t.TempDir()}}, Target: target, Lang: PT})
	drive(m, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(m, run(m.Init())...)
	if m.err != nil || m.catalog == nil {
		t.Fatalf("catalog: %v", m.err)
	}
	drive(m, typeText("tester")...)
	drive(m, key("enter"))
	m.opts.Content = "essential"
	drive(m, key("i"))
	if !m.preDone || m.preflightOutcome() == setup.CheckFailed {
		t.Fatalf("preflight: %+v", m.checks)
	}
	snap(m, "real-01-preflight")
	m.cfg.Backend.(*setup.Real).Publish = nil
	m.do(func() tea.Cmd { return m.startInstall() })
	for progress := range m.instCh {
		if !progress.Finished && progress.Err == nil && progress.Done > progress.Total/2 && m.progress.Done <= m.progress.Total/2 {
			m.progress = progress
			snap(m, "real-02-install")
		}
		m.progress = progress
	}
	m.screen = scDone
	if m.screen != scDone || !m.progress.Finished {
		t.Fatalf("should finish: screen %d, error %v", m.screen, m.progress.Err)
	}
	if filesDone(m.progress) != filesTotal(m.progress) || filesDone(m.progress) == 0 {
		t.Fatalf("files: %d of %d", filesDone(m.progress), filesTotal(m.progress))
	}
	snap(m, "real-03-done")
	if _, err := install.Verify(target); err != nil {
		t.Fatal(err)
	}
	panel := New(Config{Backend: &setup.Real{Trust: trust.Options{CacheDir: t.TempDir()}}, Target: target, Lang: PT})
	drive(panel, tea.WindowSizeMsg{Width: 100, Height: 34})
	drive(panel, run(panel.Init())...)
	snap(panel, "real-04-panel")
	drive(panel, key("down"), key("enter"))
	panel.focus = 0
	drive(panel, key("enter"))
	snap(panel, "real-05-components")
}

func snap(m *Model, name string) {
	if dir := os.Getenv("X86QW_SNAPSHOTS"); dir != "" {
		os.WriteFile(filepath.Join(dir, name+".ansi"), []byte(m.View().Content), 0o644)
	}
}
