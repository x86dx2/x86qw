package tui

import (
	"context"
	_ "embed"
	"fmt"
	"math"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/charmbracelet/x/ansi"

	"github.com/x86dx2/x86qw/go/internal/setup"
)

//go:embed assets/digits.txt
var digitsArt string

var bigDigits = func() map[rune][]string {
	out := map[rune][]string{}
	lines := strings.Split(strings.TrimRight(digitsArt, "\n"), "\n")
	for index := 0; index+4 < len(lines); index += 5 {
		out[[]rune(lines[index])[0]] = lines[index+1 : index+5]
	}
	return out
}()

// Sizes of the screens' parts.
const (
	logoFullRows    = 31 // the full logo shows from this many rows
	logoCompactRows = 24 // the compact one from this many
	labelWidth      = 13 // the labels of an open section
	digitsWidth     = 26 // the big percentage
	sparkColumns    = 18 // the speed history, one column per ~0.6 s
	sparkSamples    = 600
)

// body builds the page of the current screen.
func (m *Model) body(width int) *page {
	p := newPage(m.theme, m.glyphs, width)
	if m.err != nil {
		p.title(m.t("err.title"))
		p.lead(m.explain(m.err))
		return p
	}
	if m.catalog == nil {
		p.add(p.st(m.theme.Gray).Render(m.spinner() + "  " + m.t("net.detecting")))
		return p
	}
	switch m.screen {
	case scIntent:
		m.intentPage(p)
	case scConfigure:
		m.setupPage(p)
	case scInstall:
		m.installPage(p)
	case scDone:
		m.donePage(p)
	case scPanel:
		m.panelPage(p)
	case scMatch:
		m.matchPage(p)
	case scServers:
		m.serversPage(p)
	case scHost:
		m.hostPage(p)
	}
	return p
}

func (m *Model) spinner() string {
	frames := m.glyphs.Spinner
	return frames[int(time.Since(m.started)/(133*time.Millisecond))%len(frames)]
}

// logo draws the wordmark sized to the terminal: full from 30 rows,
// compact from 24, none below.
func (m *Model) logo(p *page) {
	if !m.glyphs.Braille {
		return
	}
	var art string
	switch {
	case m.h >= logoFullRows:
		art = logoFull
	case m.h >= logoCompactRows:
		art = logoCompact
	default:
		return
	}
	logo := newLogo(art)
	left := (p.width - logo.Width()) / 2
	for _, line := range logo.Render(m.theme, time.Now()) {
		p.add(p.st(m.theme.Base).Render(strings.Repeat(" ", max(0, left))) + line)
	}
	p.gap(2)
}

func (m *Model) intentPage(p *page) {
	t := m.theme
	m.logo(p)
	p.title(m.t("intent.q"))
	p.gap(1)
	options := [][2]string{{"play", "play.d"}, {"host", "host.d"}, {"both", "both.d"}}
	for index, option := range options {
		selected := index == m.intent
		hovered := m.hover == fmt.Sprintf("intent:%d", index)
		bg := p.rowBG(selected, hovered)
		base := lipgloss.NewStyle().Background(bg)
		glyph, mark := m.glyphs.RadioOff, base.Foreground(t.Dim)
		label := base.Foreground(t.Text2)
		if selected {
			glyph, mark, label = m.glyphs.Radio, base.Foreground(t.Brand), base.Foreground(t.Text).Bold(true)
		}
		left := base.Render("  ") + mark.Render(glyph) + base.Render("  ") + label.Render(m.t(option[0]))
		right := base.Foreground(t.Gray).Render(m.t(option[1])) + base.Render("  ")
		p.band(spread(left, right, p.width, bg), bg, fmt.Sprintf("intent:%d", index))
	}
	if !m.opts.Intent.WantsClient() {
		p.gap(5)
		return
	}
	p.gap(2)
	nick := m.nick.String()
	content := p.st(t.Text).Render(nick)
	if nick == "" {
		content = p.st(t.Dim).Render(m.t("nick.ph"))
	}
	caption := ""
	if nick != "" {
		caption = fmt.Sprintf("%s \"%s\" %s", m.t("nick.shows"), setup.FakeName(nick), m.t("nick.team"))
	}
	border := t.BorderOn
	if m.nickFlag {
		border = t.Error
	}
	lines := box(t, []string{content}, p.width, m.t("nick"), caption, border, t.Base)
	start := len(p.lines)
	for _, line := range lines {
		p.add(line)
	}
	p.caret = &[2]int{2 + m.nick.Caret, start + 1}
	if m.nickFlag {
		p.add(p.st(t.Error).Render("  " + m.t("nick.need")))
	} else {
		p.gap(1)
	}
}

// reviewRow is one summarised section of the choices.
type reviewRow struct{ id, label, summary string }

// sectionRows summarise each group of choices; both the setup and the
// review screens list them, and each opens its editor.
func (m *Model) sectionRows() []reviewRow {
	o := m.opts
	var rows []reviewRow
	selected := setup.Selected(m.catalog, o)
	contents := m.t("p." + o.Content)
	if !o.Intent.WantsClient() {
		contents = "MVDSV · KTX"
	}
	rows = append(rows, reviewRow{"comps", m.t("sec.comps"), fmt.Sprintf("%s · %d %s · %s", contents, len(selected), m.t("d.itemsN"), m.size(m.totalBytes()))})
	if o.Intent.WantsClient() {
		keys := strings.ToUpper(o.Keys.Preset)
		if o.Keys.Preset == "custom" {
			keys = m.t("l.custom")
		}
		rows = append(rows, reviewRow{"config", m.t("sec.config"), fmt.Sprintf("%s · %s · %s · FOV %d", o.Nick, keys, m.num(o.Mouse.Sensitivity), o.Video.FOV)})
	}
	if o.Intent.WantsServer() {
		ports := o.Server.Ports()
		where := o.Server.Hostname
		if where == "" {
			where = fmt.Sprintf("UDP %d–%d", ports[0], ports[len(ports)-1])
		}
		rows = append(rows, reviewRow{"server", m.t("sec.server"), fmt.Sprintf("%d %s · %s", len(ports), m.t("d.instancesN"), where)})
	}
	on, offered := 0, 0
	can := setup.SupportedIntegration()
	for _, item := range [][2]bool{{can.Shortcuts, o.Integration.Shortcuts}, {can.Path, o.Integration.Path}, {can.QWLinks, o.Integration.QWLinks}, {can.Demos, o.Integration.Demos}} {
		if item[0] {
			offered++
			if item[1] {
				on++
			}
		}
	}
	rows = append(rows, reviewRow{"integ", m.t("sec.integ"), fmt.Sprintf(m.t("d.integN"), on, offered)})
	return append(rows, reviewRow{"folder", m.t("sec.folder"), shortPath(o.Target)})
}

// sectionSummary is the one-line summary of a section of the setup.
func (m *Model) sectionSummary(id string) string {
	for _, row := range m.sectionRows() {
		if row.id == id {
			return row.summary
		}
	}
	return ""
}

// openSetupAt opens the setup from a screen with one section in focus, so
// the person lands where the change is.
func (m *Model) openSetupAt(from screen, row, section string) tea.Cmd {
	m.openSetup(from, row)
	for index, r := range m.confirmRows() {
		if r.id == section {
			m.focus = index
		}
	}
	return nil
}

// confirmRows are the setup screen: the sections, the recipe, then the
// main action.
func (m *Model) confirmRows() []reviewRow {
	return append(m.sectionRows(), reviewRow{"recipe", m.t("sec.recipe"), "x86qw setup --intent " + string(m.opts.Intent) + " …"},
		reviewRow{"confirm", m.confirmLabel(), ""})
}

// goLabel names the step that starts the work: Install on the first run,
// Apply to a change made from Console.
func (m *Model) goLabel() string {
	if m.fromPanel {
		return m.t("conf.apply")
	}
	return m.t("conf.go")
}

func (m *Model) confirmLabel() string {
	switch {
	case !m.preOn:
		return m.goLabel()
	case !m.preDone:
		return m.t("rev.checking")
	case m.preflightOutcome() == setup.CheckFailed:
		return m.t("rev.blocked")
	case m.preflightOutcome() == setup.CheckWarning:
		return m.t("rev.anyway")
	}
	if m.fromPanel {
		return m.t("rev.apply")
	}
	return m.t("rev.confirm")
}

// totalBytes is what Install downloads: everything for a new folder; for
// an installation, the components it gains (and the updates, from Update).
func (m *Model) totalBytes() int64 {
	var total int64
	have := map[string]bool{}
	if m.existing != nil {
		for _, id := range m.existing.Installed {
			have[id] = true
		}
		if m.opts.Upgrade {
			for _, update := range m.existing.Updates {
				have[update.ID] = true
				total += m.componentSize(update.ID)
			}
		}
	} else {
		// ezQuake comes with every installation, a server's too.
		total = m.catalog.CoreSize + m.catalog.ClientSize[platform()]
	}
	for _, id := range setup.Selected(m.catalog, m.opts) {
		if !have[id] {
			total += m.componentSize(id)
		}
	}
	return total
}

func (m *Model) componentSize(id string) int64 {
	for _, component := range m.catalog.Components {
		if component.ID == id {
			return component.Size
		}
	}
	return 0
}

// primary draws the screen's main action: brand coloured when it can run,
// dim (with a spinner while checks run) when it cannot.
func (m *Model) primary(p *page, row reviewRow, focused, enabled, busy bool, right string) {
	t := m.theme
	hovered := m.hover == "row:"+row.id
	bg, fg := t.Base, t.Brand
	switch {
	case !enabled:
		fg = t.Dim
		if focused {
			bg = t.Band
		}
	case focused:
		bg = Blend(t.Base, t.Brand, 0.28)
	case hovered:
		bg = t.Hover
	}
	base := lipgloss.NewStyle().Background(bg)
	glyph := m.glyphs.Chevron
	if busy {
		glyph = m.spinner()
	}
	left := base.Render("  ") + base.Foreground(fg).Bold(enabled).Render(glyph+" "+row.label)
	p.band(spread(left, base.Foreground(t.Gray).Render(right)+base.Render("  "), p.width, bg), bg, "row:"+row.id)
}

// details lists everything chosen in one section, as label/value pairs.
func (m *Model) details(id string) [][2]string {
	if strings.HasPrefix(id, "m.") {
		return m.matchDetails(id)
	}
	switch id {
	case "h.solo":
		return m.soloServiceDetails()
	case "comps":
		return m.componentDetails()
	case "config":
		return m.configDetails()
	case "server":
		return m.serverDetails()
	case "integ":
		return m.integrationDetails()
	case "folder":
		return [][2]string{{"", shortPath(m.opts.Target)}}
	}
	return nil
}

// soloServiceDetails are the ports QTV and QWFWD would take on their own.
func (m *Model) soloServiceDetails() [][2]string {
	qtv, qwfwd := m.opts.Server.QTVPort, m.opts.Server.QWFWDPort
	if qtv == 0 {
		qtv = setup.DefaultQTVPort
	}
	if qwfwd == 0 {
		qwfwd = setup.DefaultQWFWDPort
	}
	return [][2]string{{"QTV", fmt.Sprintf("TCP %d · %s", qtv, m.t("qtv.d"))}, {"QWFWD", fmt.Sprintf("UDP %d · %s", qwfwd, m.t("qwfwd.d"))}}
}

// componentDetails are the package, its components and its size.
func (m *Model) componentDetails() [][2]string {
	o := m.opts
	var names []string
	for _, sel := range setup.Selected(m.catalog, o) {
		for _, component := range m.catalog.Components {
			if component.ID == sel {
				names = append(names, m.label("component", component.ID, component.Label))
			}
		}
	}
	pack := m.t("p." + o.Content)
	if !o.Intent.WantsClient() {
		pack = m.t("needed.host")
	}
	total := m.totalBytes()
	return [][2]string{
		{m.t("d.bundle"), pack},
		{m.t("d.items"), strings.Join(names, " · ")},
		{m.t("d.size"), fmt.Sprintf("%s %s · %s %s", m.size(total), m.t("rev.download"), m.size(setup.OnDisk(total)), m.t("rev.disk"))},
	}
}

// configDetails are the player's nick, keys, mouse, ruleset, video and sound.
func (m *Model) configDetails() [][2]string {
	o := m.opts
	k := o.Keys
	layout := strings.ToUpper(k.Preset)
	if k.Preset == "custom" {
		layout = m.t("l.custom")
	}
	keys := fmt.Sprintf("%s · %s %s  %s %s  %s %s  %s %s  %s %s", layout,
		keyLabel(k.Forward), m.t("a.fwd"), keyLabel(k.Back), m.t("a.back"), keyLabel(k.Left), m.t("a.left"),
		keyLabel(k.Right), m.t("a.right"), keyLabel(k.Jump), m.t("a.jump"))
	axis := m.t("d.axis.normal")
	if o.Mouse.Invert {
		axis = m.t("d.axis.inverted")
	}
	resolution := m.t("res.auto")
	if o.Video.Width > 0 {
		resolution = fmt.Sprintf("%d×%d", o.Video.Width, o.Video.Height)
	}
	return [][2]string{
		{m.t("nick"), o.Nick},
		{m.t("tab.keys"), keys},
		{m.t("tab.mouse"), fmt.Sprintf("%s · %d dpi · %s %s · %s", m.num(o.Mouse.Sensitivity), o.Mouse.DPI, m.num(o.Mouse.CentimetersPer360()), m.t("cm360"), axis)},
		{m.t("tab.exp"), m.ruleset(o.Experience)},
		{m.t("d.video"), fmt.Sprintf("%s · %s · FOV %d°", resolution, m.t("d."+o.Video.Display), o.Video.FOV)},
		{m.t("d.sound"), fmt.Sprintf("%d%%", o.Volume)},
	}
}

// serverDetails are the server's name, address, instances, access,
// passwords, services and rcon.
func (m *Model) serverDetails() [][2]string {
	sv := m.opts.Server
	rows := [][2]string{{m.t("srv.name"), sv.Name}, {m.t("d.address"), m.connectHost()}}
	for index, instance := range sv.Instances {
		bots := m.t("bots.off")
		if instance.Bots {
			bots = m.t("bots.on")
		}
		label := ""
		if index == 0 {
			label = m.t("srv.inst")
		}
		rows = append(rows, [2]string{label, fmt.Sprintf("#%d  UDP %d · %s · %s · %s", index+1, sv.BasePort+index, m.mode(instance.Mode).Label, instance.Map, bots)})
	}
	services := []string{}
	if sv.QTVPort != 0 {
		services = append(services, fmt.Sprintf("QTV TCP %d", sv.QTVPort))
	}
	if sv.QWFWDPort != 0 {
		services = append(services, fmt.Sprintf("QWFWD UDP %d", sv.QWFWDPort))
	}
	if len(services) == 0 {
		services = []string{m.t("off")}
	}
	access := m.t("access.public")
	if sv.Local {
		access = m.t("access.local")
	}
	slots := sv.Slots
	if slots == 0 {
		slots = setup.DefaultSlots
	}
	access += fmt.Sprintf(" · %d %s", slots, m.t("access.slots.n"))
	if !sv.NoRecord {
		access += " · " + m.t("access.recording")
	}
	var locked []string
	if sv.Password != "" {
		locked = append(locked, m.t("access.password"))
	}
	if sv.Spectators != "" {
		locked = append(locked, m.t("access.spectator"))
	}
	passwords := m.t("access.none")
	if len(locked) > 0 {
		passwords = strings.Join(locked, " · ")
	}
	return append(rows, [2]string{m.t("tab.access"), access}, [2]string{m.t("access.passwords"), passwords},
		[2]string{m.t("tab.services"), strings.Join(services, " · ")}, [2]string{m.t("rcon"), sv.Rcon})
}

// integrationDetails mark what the system integration will do, two per
// row, offering only what this system supports.
func (m *Model) integrationDetails() [][2]string {
	in := m.opts.Integration
	mark := func(on bool, key string) string {
		if on {
			return m.glyphs.Ok + " " + m.t(key)
		}
		return m.glyphs.Fail + " " + m.t(key)
	}
	can := setup.SupportedIntegration()
	var marks []string
	for _, item := range []struct {
		offered, on bool
		key         string
	}{{can.Shortcuts, in.Shortcuts, "i.short"}, {can.Path, in.Path, "i.path"}, {can.QWLinks, in.QWLinks, "i.qw"}, {can.Demos, in.Demos, "i.demo"}} {
		if item.offered {
			marks = append(marks, mark(item.on, item.key))
		}
	}
	var rows [][2]string
	for index := 0; index < len(marks); index += 2 {
		line := marks[index]
		if index+1 < len(marks) {
			line = padRight(line, 30) + marks[index+1]
		}
		rows = append(rows, [2]string{"", line})
	}
	return rows
}

// setupPage lists the choices as an accordion: one line per section, and
// the section in focus open with everything in it. Install and the
// preflight live in the dock under it.
func (m *Model) setupPage(p *page) {
	p.title(m.t("conf.title"))
	if m.fromPanel {
		p.lead(m.t("conf.lead.apply"))
	} else {
		p.lead(m.t("conf.lead"))
	}
	p.gap(1)
	rows := m.confirmRows()
	m.accordion(p, rows[:len(rows)-1])
}

// accordion lists sections one line each, the focused one open with
// everything in it; the setup, the match and the server screens share it.
func (m *Model) accordion(p *page, rows []reviewRow) {
	t := m.theme
	for index, row := range rows {
		if index == m.focus {
			m.openSection(p, row, index == 0)
			continue
		}
		bg := p.rowBG(false, m.hover == "row:"+row.id)
		base := lipgloss.NewStyle().Background(bg)
		left := base.Render("  ") + base.Foreground(t.Text2).Render(row.label)
		right := base.Foreground(t.Dim).Render(m.t("d.recipe"))
		if row.id != "recipe" {
			room := p.width - lipgloss.Width(row.label) - 10
			right = base.Foreground(t.Gray).Render(ansi.Truncate(row.summary, room, "…")) + base.Render("  ") + base.Foreground(t.Dim).Render(m.glyphs.Chevron)
		}
		p.band(spread(left, right+base.Render("  "), p.width, bg), bg, "row:"+row.id)
	}
}

// openSection draws the section in focus: a marked header and every
// choice in it, label and value.
func (m *Model) openSection(p *page, row reviewRow, first bool) {
	t := m.theme
	start := len(p.lines)
	if !first {
		p.gap(1)
	}
	base := lipgloss.NewStyle().Background(t.Band)
	hint := m.t("k.edit.hint")
	if row.id == "recipe" {
		hint = m.t("d.recipe")
	}
	left := base.Foreground(t.Brand).Render(m.glyphs.Focus+" ") + base.Foreground(t.Text).Bold(true).Render(row.label)
	p.band(spread(left, base.Foreground(t.Gray).Render(hint)+base.Render("  "), p.width, t.Band), t.Band, "row:"+row.id)
	indent := p.st(t.Base).Render("    ")
	if row.id == "recipe" {
		lines := strings.Split(lipgloss.Wrap(shellJoin(m.opts.Recipe()), p.width-6, " "), "\n")
		if len(lines) > 4 {
			lines = append(lines[:3], m.glyphs.Ellipsis)
		}
		for _, line := range lines {
			p.add(indent + p.st(t.Gray).Render(line))
		}
	}
	pairs := m.details(row.id)
	width := 0 // sections without labels use the whole width
	for _, pair := range pairs {
		if pair[0] != "" {
			width = labelWidth
		}
	}
	for _, pair := range pairs {
		for n, line := range wrapSegments(pair[1], p.width-width-6) {
			key := ""
			if n == 0 {
				key = strings.ToLower(pair[0])
			}
			y := p.add(indent + p.st(t.Gray).Render(padRight(key, width)) + p.st(t.Text2).Render(line))
			p.hits = append(p.hits, hit{y: y, x0: 0, x1: p.width, id: "row:" + row.id})
		}
	}
	p.gap(1)
	if first {
		start = 0 // the first section brings the title along
	}
	p.keep(start, len(p.lines)-1)
}

// dock is the fixed bar under the setup: the preflight, once Install is
// pressed, and the button it decides. It never scrolls away.
func (m *Model) dock(width int) *page {
	if m.catalog == nil || m.err != nil {
		return nil
	}
	switch m.screen {
	case scMatch, scHost, scServers:
		return m.gameDock(width)
	case scConfigure:
	default:
		return nil
	}
	t := m.theme
	p := newPage(t, m.glyphs, width)
	faint := p.st(t.Faint())
	if !m.preOn {
		p.add(faint.Render(strings.Repeat("─", width)))
	} else {
		// The rule names the bar and carries the verdict; a line per check
		// follows, with what was found.
		verdict := p.st(t.Dim).Render(" " + m.t("pre.running") + " ")
		if m.preDone {
			switch m.preflightOutcome() {
			case setup.CheckFailed:
				verdict = p.st(t.Error).Bold(true).Render(" " + m.t("pre.fail") + " ")
			case setup.CheckWarning:
				verdict = p.st(t.Warning).Bold(true).Render(" " + m.t("pre.warn") + " ")
			default:
				verdict = p.st(t.Success).Bold(true).Render(" " + m.t("pre.ok") + " ")
			}
		}
		label := faint.Render("──") + p.st(t.Gray).Render(" "+m.t("rule.pre")+" ")
		p.add(label + faint.Render(strings.Repeat("─", max(0, width-lipgloss.Width(label)-lipgloss.Width(verdict)-2))) + verdict + faint.Render("──"))
		for _, check := range m.checks {
			glyph, style, value := m.spinner(), p.st(t.Brand), p.st(t.Dim).Render(m.t("net.detecting"))
			switch check.State {
			case setup.CheckOK:
				glyph, style, value = m.glyphs.Ok, p.st(t.Success), p.st(t.Gray).Render(shortPath(m.in(check.Says.Value)))
			case setup.CheckWarning:
				glyph, style, value = m.glyphs.Warn, p.st(t.Warning), p.st(t.Warning).Render(m.in(check.Says.Value))
			case setup.CheckFailed:
				glyph, style, value = m.glyphs.Fail, p.st(t.Error), p.st(t.Error).Render(m.in(check.Says.Value))
			}
			left := p.st(t.Base).Render("  ") + style.Bold(true).Render(glyph) + p.st(t.Text2).Render("  "+m.in(check.Says.Title))
			if room := width - lipgloss.Width(left) - 6; lipgloss.Width(value) > room {
				value = ansi.Truncate(value, room, m.glyphs.Ellipsis)
			}
			p.add(spread(left, value+p.st(t.Base).Render("  "), width, t.Base))
		}
		for _, check := range m.checks {
			color := t.Warning
			switch check.State {
			case setup.CheckFailed:
				color = t.Error
			case setup.CheckWarning:
			default:
				continue
			}
			text := strings.TrimSpace(m.in(check.Says.Detail) + " " + m.in(check.Says.Fix))
			for n, line := range strings.Split(lipgloss.Wrap(text, width-9, " "), "\n") {
				// Notes hang under the check's title.
				lead := "       "
				if n == 0 {
					lead = "     " + m.glyphs.Chevron + " "
				}
				p.add(p.st(color).Render(lead + line))
			}
		}
	}
	if m.confirmErr != nil {
		// Why Install refused stays until the choices change.
		for n, line := range strings.Split(lipgloss.Wrap(m.explain(m.confirmErr), width-9, " "), "\n") {
			lead := "       "
			if n == 0 {
				lead = "  " + m.glyphs.Fail + "  "
			}
			p.add(p.st(t.Error).Render(lead + line))
		}
	}
	p.gap(1)
	rows := m.confirmRows()
	enabled := !m.preOn || (m.preDone && m.preflightOutcome() != setup.CheckFailed)
	right := ""
	if enabled {
		right = m.size(m.totalBytes()) + "  ↵"
	}
	m.primary(p, rows[len(rows)-1], m.focus == len(rows)-1, enabled, m.preOn && !m.preDone, right)
	return p
}

func (m *Model) activateConfirm(id string) tea.Cmd {
	switch id {
	case "confirm":
		switch {
		case !m.preOn:
			if err := m.opts.Validate(); err != nil {
				m.confirmErr = err
				return nil
			}
			return m.startPreflight(true)
		case m.preDone && m.preflightOutcome() != setup.CheckFailed:
			return m.startInstall()
		}
		return nil
	default:
		m.openModal(id)
	}
	return nil
}

func (m *Model) installPage(p *page) {
	t := m.theme
	pr := m.progress
	fraction := 0.0
	if pr.Total > 0 {
		fraction = float64(pr.Done) / float64(pr.Total)
	}
	switch {
	case pr.Finished:
		fraction = 1 // also when there was nothing to download
	default:
		// The bytes arrive before the files are placed and verified.
		fraction = min(fraction, 0.99)
	}
	// Big percentage on the left, figures on the right.
	digits := bigNumber(int(fraction*100+0.0001), m.glyphs)
	stats := []string{
		p.st(t.Text).Bold(true).Render(m.size(pr.Done)) + p.st(t.Gray).Render(" "+m.t("inst.of")+" "+m.size(pr.Total)),
		p.st(t.Text).Render(duration(pr.ETA)) + p.st(t.Gray).Render(" "+m.t("inst.left")),
		m.filesLine(p, pr),
		sparkline(m, pr) + p.st(t.Text2).Render("  "+m.size(int64(pr.Speed))+"/s"),
	}
	switch {
	case pr.Paused && pr.Holding:
		stats[1] = p.st(t.Warning).Bold(true).Render(m.t("inst.paused"))
	case pr.Paused:
		stats[1] = p.st(t.Warning).Render(m.spinner() + " " + m.t("inst.pausing"))
	}
	digitStyle := p.st(Blend(t.Text, t.Brand, 0.15))
	if pr.Finished {
		digitStyle = p.st(t.Success)
	}
	for index := range 4 {
		left := p.st(t.Base).Render("  ") + digitStyle.Render(padRight(digits[index], digitsWidth))
		p.add(left + p.st(t.Base).Render("  ") + stats[index])
	}
	p.gap(1)
	color := t.Brand
	if pr.Finished {
		color = t.Success
	} else if pr.Paused {
		color = t.Warning
	}
	if pr.Err != nil {
		color = t.Error
	}
	p.add(p.st(t.Base).Render("  ") + bar(t, m.glyphs, p.width-4, fraction, color))
	p.gap(1)
	if pr.Err != nil {
		m.installFailure(p, pr.Err)
		return
	}
	room := max(3, m.h-8-len(p.lines))
	if m.showLog {
		log := pr.Log
		if len(log) > room {
			log = log[len(log)-room:]
		}
		for _, line := range log {
			mark := p.st(t.Success).Render("+")
			detail := p.st(t.Dim).Render(m.size(line.Size))
			if line.Kind == '=' {
				mark, detail = p.st(t.Gray).Render("="), p.st(t.Dim).Render(m.t("unchanged"))
			}
			p.add(spread(p.st(t.Dim).Render(fmt.Sprintf("  %6.1fs  ", line.At.Seconds()))+mark+p.st(t.Text2).Render(" "+line.Path), detail+p.st(t.Base).Render("  "), p.width, t.Base))
		}
		return
	}
	lanes := append([]setup.Lane(nil), pr.Lanes...)
	shown, finished := 0, 0
	for _, lane := range lanes {
		done := laneDone(pr, lane)
		if shown >= room {
			if done {
				finished++
			}
			continue
		}
		shown++
		var mark string
		labelColor := t.Text2
		switch {
		case done:
			mark = p.st(t.Success).Render(m.glyphs.Ok)
			labelColor = t.Gray
		case lane.Done > 0:
			mark = p.st(t.Brand).Render(m.spinner())
			labelColor = t.Text
		default:
			mark = p.st(t.Dim).Render("·")
		}
		frac := 0.0
		if lane.Size > 0 {
			frac = float64(lane.Done) / float64(lane.Size)
		}
		laneColor := t.Bright
		if done {
			laneColor = Blend(t.Success, t.Base, 0.35)
		}
		left := p.st(t.Base).Render("  ") + mark + p.st(t.Base).Render("  ") + p.st(labelColor).Render(padRight(m.label("component", lane.ID, lane.Label), 26))
		right := bar(t, m.glyphs, 16, frac, laneColor) + p.st(t.Gray).Render(fmt.Sprintf("  %3d%%", int(frac*100))) + p.st(t.Base).Render("  ")
		p.add(spread(left, right, p.width, t.Base))
	}
	if hidden := len(lanes) - shown; hidden > 0 {
		p.add(p.st(t.Dim).Render(fmt.Sprintf("     + %d", hidden)))
	}
}

// laneDone is a component whose files are all in place.
func laneDone(pr setup.Progress, lane setup.Lane) bool {
	return pr.Finished || (lane.FileCount > 0 && lane.Files >= lane.FileCount)
}

// filesLine counts the files placed, against the total once every
// component has said how many it has.
func (m *Model) filesLine(p *page, pr setup.Progress) string {
	t := m.theme
	known := true
	for _, lane := range pr.Lanes {
		known = known && lane.FileCount > 0
	}
	done := p.st(t.Text).Render(fmt.Sprint(filesDone(pr)))
	if !known && !pr.Finished {
		return done + p.st(t.Gray).Render(" "+m.t("inst.files"))
	}
	return done + p.st(t.Gray).Render(fmt.Sprintf(" %s %d %s", m.t("inst.of"), filesTotal(pr), m.t("inst.files")))
}

// installFailure explains a failed installation: it was rolled back, or,
// when only the new CLI failed, the content stays updated.
func (m *Model) installFailure(p *page, err error) {
	t := m.theme
	title, lead := m.t("inst.failed"), m.t("inst.rolled")
	if setup.ContentUpdated(err) {
		title, lead = m.t("inst.partial"), m.t("inst.partial.d")
	}
	p.add(p.st(t.Error).Bold(true).Render("  " + m.glyphs.Fail + "  " + title))
	for _, line := range strings.Split(lipgloss.Wrap(m.explain(err), p.width-7, " "), "\n") {
		p.add(p.st(t.Text2).Render("     " + line))
	}
	p.gap(1)
	p.lead(lead)
}

func filesDone(pr setup.Progress) int {
	n := 0
	for _, lane := range pr.Lanes {
		n += lane.Files
	}
	return n
}

func filesTotal(pr setup.Progress) int {
	n := 0
	for _, lane := range pr.Lanes {
		n += lane.FileCount
	}
	return max(n, filesDone(pr))
}

// sparkline draws the recent speed history, newest on the right.
func sparkline(m *Model, pr setup.Progress) string {
	t := m.theme
	history := m.speedHistory(pr.Speed)
	var b strings.Builder
	peak := 1.0
	for _, v := range history {
		peak = math.Max(peak, v)
	}
	for _, v := range history {
		b.WriteString(m.glyphs.Spark[min(7, int(v/peak*7.99))])
	}
	return t.Style(Blend(t.Brand, t.Base, 0.2)).Render(b.String())
}

func (m *Model) speedHistory(speed float64) []float64 {
	if speed > 0 && !m.progress.Paused {
		m.speeds = append(m.speeds, speed)
		if len(m.speeds) > sparkSamples {
			m.speeds = m.speeds[len(m.speeds)-sparkSamples:]
		}
	}
	speeds := m.speeds
	var out []float64
	step := sparkColumns
	for index := len(speeds) - sparkColumns*step; index < len(speeds); index += step {
		if index < 0 {
			continue
		}
		sum, n := 0.0, 0
		for j := index; j < min(index+step, len(speeds)); j++ {
			sum += speeds[j]
			n++
		}
		out = append(out, sum/float64(max(1, n)))
	}
	for len(out) < sparkColumns {
		out = append([]float64{0}, out...)
	}
	return out
}

func bigNumber(value int, glyphs Glyphs) []string {
	text := fmt.Sprintf("%d%%", value)
	rows := make([]string, 4)
	if !glyphs.Braille {
		rows[1] = fmt.Sprintf("  %4s", text)
		return rows
	}
	// Every digit sits centred in a cell as wide as the widest digit, and
	// the number is right-aligned in three cells, so the sign never moves.
	cell := 0
	for r := '0'; r <= '9'; r++ {
		cell = max(cell, artWidth(bigDigits[r]))
	}
	text = fmt.Sprintf("%3d%%", value)
	for _, r := range text {
		art, width := bigDigits[r], cell
		if r == '%' {
			width = artWidth(art)
		}
		left := (width - artWidth(art)) / 2
		for index := range rows {
			line := ""
			if index < len(art) {
				line = art[index]
			}
			rows[index] += strings.Repeat(" ", left) + line + strings.Repeat(" ", width-left-len([]rune(line))+1)
		}
	}
	return rows
}

func artWidth(art []string) int {
	width := 0
	for _, line := range art {
		width = max(width, len([]rune(line)))
	}
	return width
}

// doneRow is an action on the final screen.
type doneRow struct{ id, label, desc string }

func (m *Model) doneRows() []doneRow {
	var rows []doneRow
	if m.opts.Intent.WantsClient() {
		rows = append(rows, doneRow{"play", m.t("go.play"), m.t("go.play.d")})
	}
	if m.opts.Intent.WantsServer() {
		rows = append(rows, doneRow{"server", m.t("go.srv"), m.t("go.srv.d")})
	}
	return append(rows, doneRow{"find", m.t("go.find"), m.t("go.find.d")}, doneRow{"quit", m.t("go.quit"), m.t("go.quit.d")})
}

func (m *Model) donePage(p *page) {
	t := m.theme
	title := m.t("done.title")
	if !m.opts.Intent.WantsClient() {
		title = m.t("done.host")
	}
	p.add(p.st(t.Success).Bold(true).Render(m.glyphs.Ok+"  ") + p.st(t.Text).Bold(true).Render(title))
	downloaded := m.size(m.progress.Total)
	if m.progress.Total == 0 {
		downloaded = m.t("done.nothing")
	}
	p.add(p.st(t.Gray).Render("   " + fmt.Sprintf("%s · %s · %s", downloaded, duration(time.Since(m.instAt).Round(time.Second)), shortPath(m.opts.Target))))
	// Windows hands the new PATH to new terminals only.
	if runtime.GOOS == "windows" && m.opts.Integration.Path {
		p.add(p.st(t.Gray).Render("   " + fmt.Sprintf(m.t("done.path"), filepath.Join(m.opts.Target, "x86qw.exe"))))
	}
	p.gap(1)
	for index, row := range m.doneRows() {
		m.actionRow(p, row.id, row.label, row.desc, index == m.focus, index == 0)
	}
	if m.opts.Intent.WantsServer() {
		p.gap(1)
		p.rule(m.t("srv.yours"))
		host := m.connectHost()
		for index, port := range m.opts.Server.Ports() {
			instance := m.opts.Server.Instances[index]
			p.add(spread(p.st(t.Text2).Render(fmt.Sprintf("  #%d  %s · %s", index+1, instance.Mode, instance.Map)), p.st(t.Text).Render(fmt.Sprintf("%s:%d", host, port))+p.st(t.Gray).Render("  UDP  "), p.width, t.Base))
		}
		if m.opts.Server.QTVPort != 0 {
			p.add(spread(p.st(t.Text2).Render("  QTV"), p.st(t.Text).Render(fmt.Sprintf("%s:%d", host, m.opts.Server.QTVPort))+p.st(t.Gray).Render("  TCP  "), p.width, t.Base))
		}
		p.add(spread(p.st(t.Text2).Render("  "+m.t("rcon")), p.st(t.Text).Render(m.opts.Server.Rcon)+p.st(t.Base).Render("       "), p.width, t.Base))
		if m.opts.Server.Local {
			p.add(p.st(t.Gray).Render("  " + m.t("local.only")))
		} else {
			p.add(p.st(t.Gray).Render("  " + m.t("ports.open")))
		}
		p.lead("  " + m.t("srv.together"))
	}
	if warnings := m.progress.Notes; len(warnings) > 0 {
		p.gap(1)
		p.rule(m.t("done.notes"))
		for _, warning := range warnings {
			for n, line := range strings.Split(lipgloss.Wrap(m.in(warning), p.width-6, " "), "\n") {
				lead := "    "
				if n == 0 {
					lead = "  " + m.glyphs.Warn + " "
				}
				p.add(p.st(t.Warning).Render(lead + line))
			}
		}
	}
}

// actionRow draws a row that does something: home, the final screen and
// the panel's sub-views share it. primary rows carry the brand.
func (m *Model) actionRow(p *page, id, label, desc string, focused, primary bool) {
	t := m.theme
	bg := p.rowBG(focused, m.hover == "row:"+id)
	if primary && focused {
		bg = Blend(t.Base, t.Brand, 0.28)
	}
	base := lipgloss.NewStyle().Background(bg)
	text := base.Foreground(t.Text2)
	if focused {
		text = base.Foreground(t.Text).Bold(true)
	}
	chevron := base.Foreground(t.Dim).Render(m.glyphs.Chevron)
	if primary {
		chevron = base.Foreground(t.Brand).Render(m.glyphs.Chevron)
	}
	left := base.Render("  ") + chevron + base.Render(" ") + text.Render(label)
	p.band(spread(left, base.Foreground(t.Gray).Render(desc)+base.Render("  "), p.width, bg), bg, "row:"+id)
}

// connectHost is where players reach the server: this machine for a
// local one, else the hostname or the public address.
func (m *Model) connectHost() string {
	switch {
	case m.opts.Server.Local:
		return "127.0.0.1"
	case m.opts.Server.Hostname != "":
		return m.opts.Server.Hostname
	case m.network != nil && m.network.PublicV4.IsValid():
		return m.network.PublicV4.String()
	}
	return "localhost"
}

// activateDone opens the app screen a row names, or quits.
func (m *Model) activateDone(id string) tea.Cmd {
	switch id {
	case "quit":
		return tea.Quit
	case "play":
		return m.gotoCtx(ctxPlay)
	case "server":
		return m.gotoCtx(ctxHost)
	case "find":
		return m.gotoCtx(ctxFind)
	}
	return nil
}

// panel: an installation already exists.
func (m *Model) panelRows() []reviewRow {
	if m.panelView == "update" {
		return []reviewRow{{"apply", m.t("m.update"), fmt.Sprintf("%d", len(m.existing.Updates))}}
	}
	if m.panelView == "repair" {
		return []reviewRow{{"verify", m.t("m.repair"), ""}}
	}
	if m.panelView == "remove" {
		keep := m.glyphs.CheckOff
		if m.keepPersonal {
			keep = m.glyphs.Check
		}
		return []reviewRow{{"keep", keep + "  " + m.t("keep.personal"), m.t("keep.where")}, {"remove", m.t("m.remove"), ""}}
	}
	return []reviewRow{
		{"update", m.t("m.update"), ""},
		{"change", m.t("m.change"), m.t("m.change.d")},
		{"remove", m.t("m.remove"), m.t("m.remove.d")},
		{"repair", m.t("m.repair"), m.t("m.repair.d")},
		{"tools", m.t("h.tools"), m.t("h.tools.d")},
	}
}

func (m *Model) panelPage(p *page) {
	t := m.theme
	m.logo(p)
	e := m.existing
	p.title(m.t("home.title"))
	facts := fmt.Sprintf(m.t("home.facts"), shortPath(e.Target), e.Components)
	if e.CLIVersion != "" {
		facts += " · CLI " + e.CLIVersion
	}
	p.add(p.st(t.Gray).Render(facts))
	p.gap(1)
	switch m.panelView {
	case "update":
		for _, u := range e.Updates {
			note := ""
			if !u.Note.IsZero() {
				note = p.st(t.Gray).Render("  " + m.in(u.Note))
			}
			// What is not a component, and words standing for a version,
			// are plan words.
			label, to := m.label("component", u.ID, u.Label), m.label("plan", u.To, u.To)
			if u.ID == "" {
				label = m.label("plan", u.Label, u.Label)
			}
			left := p.st(t.Text2).Render("  "+label) + note
			right := p.st(t.Dim).Render(m.in(u.From)+"  → ") + p.st(t.Success).Render(to) + p.st(t.Base).Render("  ")
			if lipgloss.Width(left)+lipgloss.Width(right)+2 > p.width {
				right = p.st(t.Dim).Render("→ ") + p.st(t.Success).Render(to) + p.st(t.Base).Render("  ")
			}
			// The versions always show; the name gives way.
			if room := p.width - lipgloss.Width(right) - 2; lipgloss.Width(left) > room {
				left = ansi.Truncate(left, room, m.glyphs.Ellipsis)
			}
			p.add(spread(left, right, p.width, t.Base))
		}
		p.gap(1)
	case "repair", "remove":
		m.panelResult(p)
	}
	if m.removed {
		return
	}
	for index, row := range m.panelRows() {
		if m.panelView == "" {
			// Home groups the game and the installation.
			switch row.id {
			case "update":
				p.heading(m.t("h.group.install"))
			case "repair":
				p.gap(1)
				p.heading(m.t("h.group.maint"))
			}
		}
		focused, hovered := index == m.focus, m.hover == "row:"+row.id
		bg := p.rowBG(focused, hovered)
		danger := row.id == "remove" && m.panelView == "remove"
		if (row.id == "apply" || row.id == "verify" || danger) && focused {
			bg = Blend(t.Base, t.Brand, 0.28)
		}
		base := lipgloss.NewStyle().Background(bg)
		label := base.Foreground(t.Text2)
		if focused {
			label = base.Foreground(t.Text).Bold(true)
		}
		count := ""
		if row.id == "update" && len(e.Updates) > 0 {
			count = base.Foreground(t.Brand).Bold(true).Render(fmt.Sprintf(" %d", len(e.Updates)))
		}
		left := base.Render("  ") + base.Foreground(t.Dim).Render(m.glyphs.Chevron) + base.Render(" ") + label.Render(row.label) + count
		summary := row.summary
		if row.id == "update" {
			summary = m.updateNote()
		}
		right := base.Foreground(t.Gray).Render(summary) + base.Render("  ")
		p.band(spread(left, right, p.width, bg), bg, "row:"+row.id)
	}
}

// updateNote is what home says about the updates.
func (m *Model) updateNote() string {
	switch {
	case m.existing.UpdatesErr != nil:
		return m.t("m.updates.off")
	case len(m.existing.Updates) == 0:
		return m.t("m.uptodate")
	}
	return m.t("m.update.d")
}

func (m *Model) panelKey(key string) tea.Cmd {
	if m.removed {
		// Nothing is left to act on: only the way out.
		if key == "esc" {
			return m.quit(key)
		}
		return nil
	}
	rows := m.panelRows()
	if listNav(key, &m.focus, len(rows)) {
		return nil
	}
	switch key {
	case "enter", "space", " ", "right":
		return m.activatePanel(rows[m.focus].id)
	case "esc", "left", "backspace":
		if m.panelView != "" {
			back := m.panelView
			m.panelView, m.report, m.panelErr = "", nil, nil
			delete(m.tops, scPanel) // home keeps its own place, not the sub-view's
			m.focusRow(back)
		} else if key == "esc" {
			return m.toRail()
		}
	}
	return nil
}

// runPanel verifies and repairs the installation, or uninstalls it.
func (m *Model) runPanel(remove bool) tea.Cmd {
	m.busy, m.report, m.panelErr = true, nil, nil
	backend, target, keep := m.cfg.Backend, m.existing.Target, m.keepPersonal
	return tea.Batch(func() tea.Msg {
		if remove {
			return panelMsg{removed: true, err: backend.Uninstall(context.Background(), target, keep)}
		}
		report, err := backend.Repair(context.Background(), target)
		return panelMsg{report: &report, err: err}
	})
}

// panelResult shows a repair or uninstall running, then how it ended.
func (m *Model) panelResult(p *page) {
	t := m.theme
	switch {
	case m.busy && m.panelView == "repair":
		p.add(p.st(t.Brand).Render("  "+m.spinner()) + p.st(t.Text2).Render("  "+m.t("verify.running")))
	case m.busy:
		p.add(p.st(t.Brand).Render("  "+m.spinner()) + p.st(t.Text2).Render("  "+m.t("remove.running")))
	case m.panelErr != nil:
		p.add(p.st(t.Error).Bold(true).Render("  " + m.glyphs.Fail + "  " + m.explain(m.panelErr)))
	case m.removed:
		p.add(p.st(t.Success).Bold(true).Render("  " + m.glyphs.Ok + "  " + fmt.Sprintf(m.t("removed"), shortPath(m.existing.Target))))
		p.gap(1)
		p.lead(m.t("removed.d"))
	case m.report != nil && m.report.Repaired:
		p.add(p.st(t.Warning).Render("  " + m.glyphs.Warn + "  " + m.report.Problem))
		p.add(p.st(t.Success).Bold(true).Render("  " + m.glyphs.Ok + "  " + fmt.Sprintf(m.t("verify.fixed"), m.report.Files)))
	case m.report != nil:
		p.add(p.st(t.Success).Bold(true).Render("  " + m.glyphs.Ok + "  " + fmt.Sprintf(m.t("verify.ok"), m.report.Files, m.report.Components)))
	default:
		return
	}
	p.gap(1)
}

func (m *Model) activatePanel(id string) tea.Cmd {
	if m.panelView == "" && id == "update" && (len(m.existing.Updates) == 0 || m.existing.UpdatesErr != nil) {
		m.say(m.updateNote())
		return nil
	}
	if id == "change" && m.catalog.Unavailable != nil {
		m.say(m.t("m.change.off"))
		return nil
	}
	if m.panelView == "" && (id == "update" || id == "repair" || id == "remove") {
		m.panelView, m.focus, m.report, m.panelErr = id, 0, nil, nil
		return nil
	}
	switch id {
	case "tools":
		m.openModal("tools")
	case "change":
		return m.openSetupAt(scPanel, "change", "comps")
	case "apply":
		// Update applies every new version, from home and back to it.
		m.openSetup(scPanel, "update")
		m.opts.Upgrade = true
		return m.startPreflight(true)
	case "verify":
		if !m.busy {
			return m.runPanel(false)
		}
	case "keep":
		m.keepPersonal = !m.keepPersonal
	case "remove":
		if !m.busy && m.armedFor("remove") {
			return m.runPanel(true)
		}
	}
	return nil
}

func duration(d time.Duration) string {
	if d <= 0 {
		return "0s"
	}
	if d >= time.Minute {
		return fmt.Sprintf("%dm%02ds", int(d.Minutes()), int(d.Seconds())%60)
	}
	return fmt.Sprintf("%ds", int(d.Seconds()+0.5))
}

func platform() string {
	switch runtime.GOOS {
	case "darwin":
		return "macos"
	case "windows":
		return "windows"
	}
	return "linux"
}

func shortPath(path string) string {
	if home := homeDir(); home != "" && strings.HasPrefix(path, home) {
		return "~" + strings.TrimPrefix(path, home)
	}
	return path
}

// shellJoin quotes arguments for a POSIX shell when they need it.
func shellJoin(args []string) string {
	out := make([]string, len(args))
	for index, arg := range args {
		if arg == "" || strings.ContainsAny(arg, " \t\"'$`\\!*?;&|<>()[]{}~#") {
			out[index] = "'" + strings.ReplaceAll(arg, "'", `'\''`) + "'"
		} else {
			out[index] = arg
		}
	}
	return strings.Join(out, " ")
}

// wrapSegments breaks a " · " separated value between segments, falling
// back to word wrapping for a segment longer than the width.
func wrapSegments(value string, width int) []string {
	var lines []string
	line := ""
	for _, segment := range strings.Split(value, " · ") {
		switch {
		case line == "":
			line = segment
		case lipgloss.Width(line)+3+lipgloss.Width(segment) <= width:
			line += " · " + segment
		default:
			lines = append(lines, line)
			line = segment
		}
	}
	lines = append(lines, line)
	var out []string
	for _, l := range lines {
		out = append(out, strings.Split(lipgloss.Wrap(l, width, " "), "\n")...)
	}
	return out
}

// ruleset names a client ruleset; one this version does not know shows
// by its own name.
func (m *Model) ruleset(key string) string {
	if _, ok := texts["rs."+key]; ok {
		return m.t("rs." + key)
	}
	return key
}

// num formats a decimal the way the interface language writes it.
func (m *Model) num(value float64) string {
	text := fmt.Sprintf("%.1f", value)
	if m.lang == PT {
		text = strings.Replace(text, ".", ",", 1)
	}
	return text
}
