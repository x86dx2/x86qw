package tui

import (
	"fmt"
	"strings"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
)

// ctx is a context of an installation. The tabs under the top bar list
// them; the first-run installer has no tabs at all.
type ctx int

const (
	ctxPlay    ctx = iota // start a match
	ctxFind               // find and watch matches
	ctxHost               // host games
	ctxConsole            // the installation: its setup, updates and maintenance
	ctxCount
)

// The tabs, or the installer's steps, sit on tabRow with a rule under
// them; the body starts on bodyRow.
const (
	tabRow  = 2
	ruleRow = 3
	bodyRow = 4
	tabX    = 2
)

var ctxLabels = [ctxCount]string{"ctx.play", "ctx.find", "ctx.host", "ctx.console"}

// contextOf is the context the current screen belongs to. ok is false
// on the installer's screens: they run full screen, with no tabs, until
// the installation exists and its last step hands over to a context.
func (m *Model) contextOf() (c ctx, ok bool) {
	if m.existing == nil {
		return 0, false
	}
	switch m.screen {
	case scMatch:
		return ctxPlay, true
	case scServers:
		return ctxFind, true
	case scHost:
		return ctxHost, true
	case scPanel:
		return ctxConsole, true
	case scConfigure, scInstall, scDone:
		// A change made from a context belongs to that context.
		if m.fromPanel {
			return contextOfScreen(m.returnTo), true
		}
	}
	return 0, false
}

// contextOfScreen is the context a screen the setup returns to belongs to.
func contextOfScreen(s screen) ctx {
	switch s {
	case scMatch:
		return ctxPlay
	case scServers:
		return ctxFind
	case scHost:
		return ctxHost
	}
	return ctxConsole
}

// canRail: the tabs take the keys only from a context's first level, and
// never while the catalog is missing or something is running.
func (m *Model) canRail() bool {
	if _, ok := m.contextOf(); !ok {
		return false
	}
	if m.catalog == nil || m.err != nil || m.busy || m.removed || m.cancelling || m.reloading {
		return false
	}
	switch m.screen {
	case scConfigure, scInstall:
		return false
	}
	return true
}

// landing opens the first screen of a context.
func (m *Model) landing(c ctx) tea.Cmd {
	m.panelView, m.focus, m.fromPanel = "", 0, false
	switch c {
	case ctxPlay:
		return m.openScreen(scMatch)
	case ctxFind:
		return m.openScreen(scServers)
	case ctxHost:
		return m.openScreen(scHost)
	}
	m.screen = scPanel
	return m.loadStack()
}

// gotoCtx opens a context from the tabs and leaves them.
func (m *Model) gotoCtx(c ctx) tea.Cmd {
	m.railOn, m.railFocus = false, c
	return m.landing(c)
}

// toRail moves the focus from a context's first level to the tabs, on the
// context the screen belongs to. Elsewhere it does nothing.
func (m *Model) toRail() tea.Cmd {
	c, ok := m.contextOf()
	if !ok {
		return nil
	}
	m.railOn, m.railFocus = true, c
	return nil
}

// railKey moves along the tabs with ← and →, wrapping; the content follows
// each one. enter or ↓ goes into the content, esc quits.
func (m *Model) railKey(key string) tea.Cmd {
	switch key {
	case "left", "h", "shift+tab":
		m.railFocus = (m.railFocus + ctxCount - 1) % ctxCount
		return m.follow()
	case "right", "l", "tab":
		m.railFocus = (m.railFocus + 1) % ctxCount
		return m.follow()
	case "1", "2", "3", "4":
		m.railFocus = ctx(key[0] - '1')
		return m.follow()
	case "enter", "space", " ", "down":
		m.railOn = false
	case "esc":
		return m.quit(key)
	}
	return nil
}

// follow opens the context under the tab cursor when it is not open yet;
// the tabs keep the focus.
func (m *Model) follow() tea.Cmd {
	if c, _ := m.contextOf(); c == m.railFocus {
		return nil
	}
	return m.landing(m.railFocus)
}

// stripItem is one label of a row of tabs or steps.
type stripItem struct {
	label string
	color lipgloss.Style
	bold  bool
	rule  lipgloss.Style // the rule under the label
}

// strip lays the items out in one row with a rule under it, and returns
// the x range each one takes, for clicks.
func strip(items []stripItem) (row, rule string, spans [][2]int) {
	var b, r strings.Builder
	x := tabX
	for i, item := range items {
		w := lipgloss.Width(item.label)
		b.WriteString(item.color.Bold(item.bold).Render(item.label))
		r.WriteString(item.rule.Render(strings.Repeat("━", w)))
		spans = append(spans, [2]int{x, x + w - 1})
		x += w
		if i < len(items)-1 {
			b.WriteString("  ")
			r.WriteString("  ")
			x += 2
		}
	}
	return b.String(), r.String(), spans
}

// tabs are the installation's contexts, with the boxes a click takes.
func (m *Model) tabs() (row, rule string, hits []hit) {
	t := m.theme
	current, _ := m.contextOf()
	var items []stripItem
	for c := ctx(0); c < ctxCount; c++ {
		item := stripItem{label: fmt.Sprintf("%d %s", c+1, m.t(ctxLabels[c])), color: t.Style(t.Gray), rule: t.Style(t.Faint())}
		switch {
		case c == current:
			item.color, item.bold = t.Style(t.Text), true
		}
		if m.railOn && c == m.railFocus {
			item.color, item.bold, item.rule = t.Style(t.Brand), true, t.Style(t.Brand)
		}
		items = append(items, item)
	}
	row, rule, spans := strip(items)
	for c, span := range spans {
		hits = append(hits, hit{y: tabRow, x0: span[0], x1: span[1], id: fmt.Sprintf("ctx:%d", c+1)})
	}
	return row, rule, hits
}

// stepper is the first run's route on the tab row: the steps the installer
// goes through, the current one brand, the ones done bright.
func (m *Model) stepper() (row, rule string) {
	t := m.theme
	steps := []string{"route.intent", "route.config", "route.install", "route.done"}
	current := map[screen]int{scIntent: 0, scConfigure: 1, scInstall: 2, scDone: 3}[m.screen]
	var items []stripItem
	for i, key := range steps {
		item := stripItem{label: m.t(key), color: t.Style(t.Dim), rule: t.Style(t.Faint())}
		switch {
		case i < current:
			item.color, item.rule = t.Style(t.Text), t.Style(t.Gray)
		case i == current:
			item.color, item.bold, item.rule = t.Style(t.Brand), true, t.Style(t.Brand)
		}
		items = append(items, item)
	}
	row, rule, _ = strip(items)
	return row, rule
}

// bodyBox is the body's left edge, width and first row: the body is
// centred under the tabs, as the screens always were.
func (m *Model) bodyBox() (left, width, first int) {
	width = min(m.w-6, maxWidth)
	return (m.w - width) / 2, width, bodyRow
}
