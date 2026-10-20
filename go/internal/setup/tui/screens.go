package tui

import (
	_ "embed"
	"fmt"
	"github.com/charmbracelet/x/ansi"
	"math"
	"runtime"
	"strings"
	"time"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"

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

// body builds the page of the current screen.
func (m *Model) body(width int) *page {
	p := newPage(m.theme, m.glyphs, width)
	if m.err != nil {
		p.title("Erro")
		p.lead(m.err.Error())
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
	case m.h >= 31:
		art = logoFull
	case m.h >= 24:
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

// route draws the five steps as diamonds with the current step named.
func (m *Model) route(p *page) {
	t := m.theme
	steps := []string{"route.intent", "route.config", "route.install", "route.done"}
	current := map[screen]int{scIntent: 0, scConfigure: 1, scInstall: 2, scDone: 3}[m.screen]
	var b strings.Builder
	for index := range steps {
		if index > 0 {
			color := t.Faint()
			if index <= current {
				color = t.Gray
			}
			b.WriteString(p.st(color).Render("───"))
		}
		switch {
		case index < current:
			b.WriteString(p.st(t.Bright).Render(m.glyphs.Diamond))
		case index == current:
			b.WriteString(p.st(t.Brand).Render(m.glyphs.Diamond))
		default:
			b.WriteString(p.st(t.Dim).Render(m.glyphs.DiamondOff))
		}
	}
	line := b.String()
	p.add(p.st(t.Base).Render(strings.Repeat(" ", (p.width-lipgloss.Width(line))/2)) + line)
	label := m.t(steps[current])
	p.add(p.st(t.Base).Render(strings.Repeat(" ", (p.width-lipgloss.Width(label))/2)) + p.st(t.Text2).Render(label))
	p.gap(2)
}

func (m *Model) intentPage(p *page) {
	t := m.theme
	m.logo(p)
	m.route(p)
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
	rows = append(rows, reviewRow{"comps", m.t("sec.comps"), fmt.Sprintf("%s · %d %s · %s", contents, len(selected), m.t("d.itemsN"), size(m.totalBytes()))})
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
	on := 0
	for _, enabled := range []bool{o.Integration.Shortcuts, o.Integration.Path, o.Integration.QWLinks, o.Integration.Demos} {
		if enabled {
			on++
		}
	}
	rows = append(rows, reviewRow{"integ", m.t("sec.integ"), fmt.Sprintf(m.t("d.integN"), on)})
	return append(rows, reviewRow{"folder", m.t("sec.folder"), shortPath(o.Target)})
}

// confirmRows are the setup screen: the sections, the recipe, then the
// main action.
func (m *Model) confirmRows() []reviewRow {
	return append(m.sectionRows(), reviewRow{"recipe", m.t("sec.recipe"), "x86qw setup --intent " + string(m.opts.Intent) + " …"},
		reviewRow{"confirm", m.confirmLabel(), ""})
}

func (m *Model) confirmLabel() string {
	switch {
	case !m.preOn:
		return m.t("conf.go")
	case !m.preDone:
		return m.t("rev.checking")
	case m.preflightOutcome() == setup.CheckFailed:
		return m.t("rev.blocked")
	case m.preflightOutcome() == setup.CheckWarning:
		return m.t("rev.anyway")
	}
	return m.t("rev.confirm")
}

func (m *Model) totalBytes() int64 {
	total := m.catalog.CoreSize
	if m.opts.Intent.WantsClient() {
		total += m.catalog.ClientSize[platform()]
	}
	for _, id := range setup.Selected(m.catalog, m.opts) {
		for _, component := range m.catalog.Components {
			if component.ID == id {
				total += component.Size
			}
		}
	}
	return total
}

// primary draws the screen's main action: brand coloured when it can run,
// dim (with a spinner while checks run) when it cannot.
func (m *Model) primary(p *page, row reviewRow, focused, enabled bool, right string) {
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
	if !enabled && !m.preDone {
		glyph = m.spinner()
	}
	left := base.Render("  ") + base.Foreground(fg).Bold(enabled).Render(glyph+" "+row.label)
	p.band(spread(left, base.Foreground(t.Gray).Render(right)+base.Render("  "), p.width, bg), bg, "row:"+row.id)
}

// details lists everything chosen in one section, as label/value pairs.
func (m *Model) details(id string) [][2]string {
	o := m.opts
	switch id {
	case "comps":
		var names []string
		for _, sel := range setup.Selected(m.catalog, o) {
			for _, component := range m.catalog.Components {
				if component.ID == sel {
					names = append(names, component.Label)
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
			{m.t("d.size"), fmt.Sprintf("%s %s · %s %s", size(total), m.t("rev.download"), size(total*108/100), m.t("rev.disk"))},
		}
	case "config":
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
		experience := m.t("exp.ruleset")
		if o.Experience == "competitive" {
			experience = m.t("exp.comp")
		}
		return [][2]string{
			{m.t("nick"), o.Nick},
			{m.t("tab.keys"), keys},
			{m.t("tab.mouse"), fmt.Sprintf("%s · %d dpi · %s %s · %s", m.num(o.Mouse.Sensitivity), o.Mouse.DPI, m.num(o.Mouse.CentimetersPer360()), m.t("cm360"), axis)},
			{m.t("tab.exp"), experience},
			{m.t("d.video"), fmt.Sprintf("%s · %s · FOV %d°", resolution, m.t("d."+o.Video.Display), o.Video.FOV)},
			{m.t("d.sound"), fmt.Sprintf("%d%%", o.Volume)},
		}
	case "server":
		sv := o.Server
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
		return append(rows, [2]string{m.t("tab.services"), strings.Join(services, " · ")}, [2]string{"rcon", sv.Rcon})
	case "integ":
		in := o.Integration
		mark := func(on bool, key string) string {
			if on {
				return m.glyphs.Ok + " " + m.t(key)
			}
			return m.glyphs.Fail + " " + m.t(key)
		}
		return [][2]string{
			{"", padRight(mark(in.Shortcuts, "i.short"), 30) + mark(in.Path, "i.path")},
			{"", padRight(mark(in.QWLinks, "i.qw"), 30) + mark(in.Demos, "i.demo")},
		}
	case "folder":
		return [][2]string{{"", shortPath(o.Target)}}
	}
	return nil
}

// setupPage lists the choices as an accordion: one line per section, and
// the section in focus open with everything in it. Install and the
// preflight live in the dock under it.
func (m *Model) setupPage(p *page) {
	t := m.theme
	if m.h >= 52 {
		m.route(p)
	}
	p.title(m.t("conf.title"))
	p.lead(m.t("conf.lead"))
	p.gap(1)
	rows := m.confirmRows()
	for index, row := range rows[:len(rows)-1] {
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
	labelWidth := 0 // sections without labels use the whole width
	for _, pair := range pairs {
		if pair[0] != "" {
			labelWidth = 13
		}
	}
	for _, pair := range pairs {
		for n, line := range wrapSegments(pair[1], p.width-labelWidth-6) {
			key := ""
			if n == 0 {
				key = strings.ToLower(pair[0])
			}
			y := p.add(indent + p.st(t.Gray).Render(padRight(key, labelWidth)) + p.st(t.Text2).Render(line))
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
	if m.screen != scConfigure || m.catalog == nil || m.err != nil {
		return nil
	}
	t := m.theme
	p := newPage(t, m.glyphs, width)
	faint := p.st(t.Faint())
	if !m.preOn {
		p.add(faint.Render(strings.Repeat("─", width)))
	} else {
		// The rule names the bar; the line under it is one chip per check.
		label := faint.Render("──") + p.st(t.Gray).Render(" "+m.t("rule.pre")+" ")
		p.add(label + faint.Render(strings.Repeat("─", max(0, width-lipgloss.Width(label)))))
		chips := ""
		for _, check := range m.checks {
			glyph, color := m.spinner(), t.Brand
			switch check.State {
			case setup.CheckOK:
				glyph, color = m.glyphs.Ok, t.Success
			case setup.CheckWarning:
				glyph, color = m.glyphs.Warn, t.Warning
			case setup.CheckFailed:
				glyph, color = m.glyphs.Fail, t.Error
			}
			if chips != "" {
				chips += p.st(t.Base).Render("  ")
			}
			chips += p.st(color).Bold(true).Render(glyph) + p.st(t.Text2).Render(" "+m.checkName(check))
		}
		verdict := p.st(t.Dim).Render(m.t("pre.running"))
		if m.preDone {
			switch m.preflightOutcome() {
			case setup.CheckFailed:
				verdict = p.st(t.Error).Bold(true).Render(m.t("pre.fail"))
			case setup.CheckWarning:
				verdict = p.st(t.Warning).Bold(true).Render(m.t("pre.warn"))
			default:
				verdict = p.st(t.Success).Bold(true).Render(m.t("pre.ok"))
			}
		}
		pad := p.st(t.Base).Render("  ")
		p.add(spread(pad+chips, verdict+pad, width, t.Base))
		for _, check := range m.checks {
			color := t.Warning
			switch check.State {
			case setup.CheckFailed:
				color = t.Error
			case setup.CheckWarning:
			default:
				continue
			}
			text := strings.TrimSpace(check.Detail + " " + check.Fix)
			for n, line := range strings.Split(lipgloss.Wrap(text, width-6, " "), "\n") {
				lead := "    "
				if n == 0 {
					lead = "  " + m.glyphs.Warn + " "
					if check.State == setup.CheckFailed {
						lead = "  " + m.glyphs.Fail + " "
					}
				}
				p.add(p.st(color).Render(lead + line))
			}
		}
	}
	p.gap(1)
	rows := m.confirmRows()
	enabled := !m.preOn || (m.preDone && m.preflightOutcome() != setup.CheckFailed)
	right := ""
	if enabled {
		right = size(m.totalBytes()) + "  ↵"
	}
	m.primary(p, rows[len(rows)-1], m.focus == len(rows)-1, enabled, right)
	return p
}

// checkName is a check's one-word name for the dock.
func (m *Model) checkName(check setup.Check) string {
	if name := m.t("chk." + check.ID); name != "chk."+check.ID {
		return name
	}
	return strings.ToLower(check.Title)
}

func (m *Model) activateConfirm(id string) tea.Cmd {
	switch id {
	case "confirm":
		switch {
		case !m.preOn:
			if err := m.opts.Validate(); err != nil {
				m.say(err.Error())
				return nil
			}
			return m.startPreflight()
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
	m.route(p)
	pr := m.progress
	fraction := 0.0
	if pr.Total > 0 {
		fraction = float64(pr.Done) / float64(pr.Total)
	}
	// Big percentage on the left, figures on the right.
	digits := bigNumber(int(fraction*100+0.0001), m.glyphs)
	stats := []string{
		p.st(t.Text).Bold(true).Render(size(pr.Done)) + p.st(t.Gray).Render(" "+m.t("inst.of")+" "+size(pr.Total)),
		p.st(t.Text).Render(duration(pr.ETA)) + p.st(t.Gray).Render(" "+m.t("inst.left")),
		p.st(t.Text).Render(fmt.Sprint(filesDone(pr))) + p.st(t.Gray).Render(fmt.Sprintf(" %s %d %s", m.t("inst.of"), filesTotal(pr), m.t("inst.files"))),
		sparkline(m, pr) + p.st(t.Text2).Render(fmt.Sprintf("  %.1f MB/s", pr.Speed/1048576)),
	}
	if pr.Paused {
		stats[1] = p.st(t.Warning).Bold(true).Render(m.t("inst.paused"))
	}
	digitStyle := p.st(Blend(t.Text, t.Brand, 0.15))
	if pr.Finished {
		digitStyle = p.st(t.Success)
	}
	for index := range 4 {
		left := p.st(t.Base).Render("  ") + digitStyle.Render(padRight(digits[index], 26))
		p.add(left + p.st(t.Base).Render("  ") + stats[index])
	}
	p.gap(1)
	color := t.Brand
	if pr.Finished {
		color = t.Success
	} else if pr.Paused {
		color = t.Warning
	}
	p.add(p.st(t.Base).Render("  ") + bar(t, m.glyphs, p.width-4, fraction, color))
	p.gap(1)
	room := max(3, m.h-8-len(p.lines))
	if m.showLog {
		log := pr.Log
		if len(log) > room {
			log = log[len(log)-room:]
		}
		for _, line := range log {
			mark := p.st(t.Success).Render("+")
			detail := p.st(t.Dim).Render(size(line.Size))
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
		done := lane.Size > 0 && lane.Done >= lane.Size
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
		left := p.st(t.Base).Render("  ") + mark + p.st(t.Base).Render("  ") + p.st(labelColor).Render(padRight(lane.Label, 26))
		right := bar(t, m.glyphs, 16, frac, laneColor) + p.st(t.Gray).Render(fmt.Sprintf("  %3d%%", int(frac*100))) + p.st(t.Base).Render("  ")
		p.add(spread(left, right, p.width, t.Base))
	}
	if hidden := len(lanes) - shown; hidden > 0 {
		p.add(p.st(t.Dim).Render(fmt.Sprintf("     + %d", hidden)))
	}
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
		n += max(1, int(math.Ceil(float64(lane.Size)/8e6)))
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
		if len(m.speeds) > 600 {
			m.speeds = m.speeds[len(m.speeds)-600:]
		}
	}
	speeds := m.speeds
	// One column per ~0.6 s, 18 columns.
	var out []float64
	step := 18
	for index := len(speeds) - 18*step; index < len(speeds); index += step {
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
	for len(out) < 18 {
		out = append([]float64{0}, out...)
	}
	return out
}

func bigNumber(value int, glyphs Glyphs) []string {
	text := fmt.Sprintf("%d%%", value)
	rows := make([]string, 4)
	if !glyphs.Braille {
		rows[1] = "  " + text
		return rows
	}
	for _, r := range text {
		art := bigDigits[r]
		width := 0
		for _, line := range art {
			width = max(width, len([]rune(line)))
		}
		for index := range rows {
			line := ""
			if index < len(art) {
				line = art[index]
			}
			rows[index] += line + strings.Repeat(" ", width-len([]rune(line))+1)
		}
	}
	return rows
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
	m.route(p)
	title := m.t("done.title")
	if !m.opts.Intent.WantsClient() {
		title = m.t("done.host")
	}
	p.add(p.st(t.Success).Bold(true).Render(m.glyphs.Ok+"  ") + p.st(t.Text).Bold(true).Render(title))
	p.add(p.st(t.Gray).Render("   " + fmt.Sprintf("%s · %s · %s", size(m.progress.Total), duration(time.Since(m.instAt).Round(time.Second)), shortPath(m.opts.Target))))
	p.gap(1)
	for index, row := range m.doneRows() {
		focused, hovered := index == m.focus, m.hover == "row:"+row.id
		bg := p.rowBG(focused, hovered)
		if index == 0 && focused {
			bg = Blend(t.Base, t.Brand, 0.28)
		}
		base := lipgloss.NewStyle().Background(bg)
		label := base.Foreground(t.Text2)
		if focused {
			label = base.Foreground(t.Text).Bold(true)
		}
		chevron := base.Foreground(t.Dim).Render(m.glyphs.Chevron)
		if index == 0 {
			chevron = base.Foreground(t.Brand).Render(m.glyphs.Chevron)
		}
		left := base.Render("  ") + chevron + base.Render(" ") + label.Render(row.label)
		p.band(spread(left, base.Foreground(t.Gray).Render(row.desc)+base.Render("  "), p.width, bg), bg, "row:"+row.id)
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
		p.add(spread(p.st(t.Text2).Render("  rcon"), p.st(t.Text).Render(m.opts.Server.Rcon)+p.st(t.Base).Render("       "), p.width, t.Base))
		p.add(p.st(t.Gray).Render("  " + m.t("ports.open")))
	}
}

func (m *Model) connectHost() string {
	if m.opts.Server.Hostname != "" {
		return m.opts.Server.Hostname
	}
	if m.network != nil && m.network.PublicV4.IsValid() {
		return m.network.PublicV4.String()
	}
	return "localhost"
}

func (m *Model) activateDone(id string) tea.Cmd {
	switch id {
	case "play":
		m.say(m.t("would.play"))
	case "server":
		m.say(m.t("would.srv"))
	case "find":
		m.openModal("servers")
	case "quit":
		return tea.Quit
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
		return []reviewRow{{"keep", keep + "  " + m.t("keep.personal"), "~/Games/x86qw-personal"}, {"remove", m.t("m.remove"), ""}}
	}
	return []reviewRow{
		{"update", m.t("m.update"), fmt.Sprintf("%d %s", len(m.existing.Updates), m.t("m.update.d"))},
		{"change", m.t("m.change"), m.t("m.change.d")},
		{"repair", m.t("m.repair"), m.t("m.repair.d")},
		{"remove", m.t("m.remove"), m.t("m.remove.d")},
	}
}

func (m *Model) panelPage(p *page) {
	t := m.theme
	m.logo(p)
	e := m.existing
	p.title(m.t("panel.title"))
	p.add(p.st(t.Gray).Render(fmt.Sprintf("%s · %d %s · CLI %s", shortPath(e.Target), e.Components, m.t("rev.items"), e.CLIVersion)))
	p.gap(1)
	switch m.panelView {
	case "update":
		for _, u := range e.Updates {
			note := ""
			if u.Note != "" {
				note = p.st(t.Gray).Render("  " + u.Note)
			}
			left := p.st(t.Text2).Render("  "+u.Label) + note
			right := p.st(t.Dim).Render(u.From+"  → ") + p.st(t.Success).Render(u.To) + p.st(t.Base).Render("  ")
			if lipgloss.Width(left)+lipgloss.Width(right)+2 > p.width {
				right = p.st(t.Dim).Render("→ ") + p.st(t.Success).Render(u.To) + p.st(t.Base).Render("  ")
			}
			p.add(spread(left, right, p.width, t.Base))
		}
		p.gap(1)
	case "repair":
		color := t.Brand
		label := m.t("m.repair.d")
		if m.verify >= 100 {
			color, label = t.Success, "544 "+m.t("verify.ok")
		}
		p.add(p.st(t.Base).Render("  ") + bar(t, m.glyphs, p.width-4, m.verify/100, color))
		p.add(p.st(t.Gray).Render("  " + label))
		p.gap(1)
	}
	for index, row := range m.panelRows() {
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
		right := base.Foreground(t.Gray).Render(row.summary) + base.Render("  ")
		if row.id == "update" {
			right = base.Foreground(t.Gray).Render(m.t("m.update.d")) + base.Render("  ")
		}
		p.band(spread(left, right, p.width, bg), bg, "row:"+row.id)
	}
}

func (m *Model) panelKey(key string) tea.Cmd {
	rows := m.panelRows()
	switch key {
	case "up", "k":
		m.focus = (m.focus + len(rows) - 1) % len(rows)
	case "down", "j", "tab":
		m.focus = (m.focus + 1) % len(rows)
	case "enter", "space", " ", "right":
		return m.activatePanel(rows[m.focus].id)
	case "esc", "left", "backspace":
		if m.panelView != "" {
			m.panelView, m.focus, m.verify = "", 0, 0
		} else if m.armedFor("quit") {
			return tea.Quit
		}
	}
	return nil
}

func (m *Model) activatePanel(id string) tea.Cmd {
	if m.panelView == "" && (id == "update" || id == "repair" || id == "remove") {
		m.panelView, m.focus, m.verify = id, 0, 0
		return nil
	}
	switch id {
	case "change":
		m.screen, m.focus, m.fromPanel, m.preOn = scConfigure, 0, true, false
	case "apply":
		return m.startPreflight()
	case "verify":
		if m.verify == 0 || m.verify >= 100 {
			m.verify = 0.5
		}
	case "keep":
		m.keepPersonal = !m.keepPersonal
	case "remove":
		if m.armedFor("remove") {
			m.say(m.t("removed"))
		}
	}
	return nil
}

func size(b int64) string {
	switch {
	case b >= 1e9:
		return fmt.Sprintf("%.2f GB", float64(b)/1e9)
	case b >= 1e8:
		return fmt.Sprintf("%.0f MB", float64(b)/1e6)
	case b >= 1e6:
		return fmt.Sprintf("%.1f MB", float64(b)/1e6)
	case b >= 1e3:
		return fmt.Sprintf("%.0f KB", float64(b)/1e3)
	}
	return fmt.Sprintf("%d B", b)
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

// num formats a decimal the way the interface language writes it.
func (m *Model) num(value float64) string {
	text := fmt.Sprintf("%.1f", value)
	if m.lang == PT {
		text = strings.Replace(text, ".", ",", 1)
	}
	return text
}
