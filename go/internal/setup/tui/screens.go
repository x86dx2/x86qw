package tui

import (
	_ "embed"
	"fmt"
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
	case scPreflight:
		m.preflightPage(p)
	case scReview:
		m.reviewPage(p)
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
	case m.h >= 30:
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
	steps := []string{"route.intent", "route.pre", "route.review", "route.install", "route.done"}
	current := map[screen]int{scIntent: 0, scPreflight: 1, scReview: 2, scInstall: 3, scDone: 4}[m.screen]
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

func (m *Model) preflightPage(p *page) {
	t := m.theme
	m.route(p)
	p.title(m.t("pre.title"))
	p.lead(m.t("pre.lead"))
	p.gap(1)
	warned := false
	for _, check := range m.checks {
		var mark lipgloss.Style
		glyph := m.spinner()
		value := p.st(t.Dim).Render(m.t("net.detecting"))
		switch check.State {
		case setup.CheckOK:
			glyph, mark, value = m.glyphs.Ok, p.st(t.Success), p.st(t.Text2).Render(check.Value)
		case setup.CheckWarning:
			glyph, mark, value, warned = m.glyphs.Warn, p.st(t.Warning), p.st(t.Warning).Render(check.Value), true
		case setup.CheckFailed:
			glyph, mark, value = m.glyphs.Fail, p.st(t.Error), p.st(t.Error).Render(check.Value)
		default:
			mark = p.st(t.Brand)
		}
		left := p.st(t.Base).Render("  ") + mark.Render(glyph) + p.st(t.Base).Render("  ") + p.st(t.Text).Render(check.Title)
		p.add(spread(left, value+p.st(t.Base).Render("  "), p.width, t.Base))
		if check.State != setup.CheckRunning && check.Detail != "" {
			p.add(p.st(t.Gray).Render("     " + check.Detail))
		}
		if check.Fix != "" {
			p.add(p.st(t.Warning).Render("     " + m.glyphs.Chevron + " " + check.Fix))
		}
	}
	if m.preDone {
		p.gap(1)
		status, color := m.t("pre.ok"), t.Success
		if warned {
			status, color = m.t("pre.warn"), t.Warning
		}
		p.add(p.st(color).Bold(true).Render("  "+status) + p.st(t.Gray).Render("   enter "+m.t("k.next")))
	}
}

// reviewRow is a section of the review.
type reviewRow struct{ id, label, summary string }

func (m *Model) reviewRows() []reviewRow {
	o := m.opts
	var rows []reviewRow
	selected := setup.Selected(m.catalog, o)
	contents := m.t("p." + o.Content)
	if !o.Intent.WantsClient() {
		contents = "MVDSV · KTX"
	}
	rows = append(rows, reviewRow{"comps", m.t("sec.comps"), fmt.Sprintf("%s · %d · %s", contents, len(selected), size(m.totalBytes()))})
	if o.Intent.WantsClient() {
		keys := strings.ToUpper(o.Keys.Preset)
		if o.Keys.Preset == "custom" {
			keys = m.t("l.custom")
		}
		rows = append(rows, reviewRow{"config", m.t("sec.config"), fmt.Sprintf("%s · %s · %.1f · FOV %d", o.Nick, keys, o.Mouse.Sensitivity, o.Video.FOV)})
	}
	if o.Intent.WantsServer() {
		ports := o.Server.Ports()
		where := o.Server.Hostname
		if where == "" {
			where = fmt.Sprintf("UDP %d–%d", ports[0], ports[len(ports)-1])
		}
		rows = append(rows, reviewRow{"server", m.t("sec.server"), fmt.Sprintf("%d× KTX · %s", len(ports), where)})
	}
	var integ []string
	for _, item := range []struct {
		on  bool
		key string
	}{{o.Integration.Shortcuts, "i.short"}, {o.Integration.Path, "i.path"}, {o.Integration.QWLinks, "i.qw"}, {o.Integration.Demos, "i.demo"}} {
		if item.on {
			integ = append(integ, strings.Fields(m.t(item.key))[0])
		}
	}
	rows = append(rows, reviewRow{"integ", m.t("sec.integ"), strings.Join(integ, " · ")})
	rows = append(rows, reviewRow{"folder", m.t("sec.folder"), shortPath(o.Target)})
	rows = append(rows, reviewRow{"recipe", m.t("sec.recipe"), "x86qw setup --intent " + string(o.Intent) + " …"})
	return append(rows, reviewRow{"install", m.t("rev.go"), ""})
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

func (m *Model) reviewPage(p *page) {
	t := m.theme
	m.route(p)
	p.title(m.t("rev.title"))
	p.lead(m.t("rev.lead"))
	p.gap(1)
	total := m.totalBytes()
	seconds := max(4, int(float64(total)/(34*1048576)))
	sep := p.st(t.Dim).Render("  ·  ")
	figure := func(value, label string) string {
		return p.st(t.Text).Bold(true).Render(value) + p.st(t.Gray).Render(" "+label)
	}
	p.add(p.st(t.Base).Render("  ") + figure(size(total), m.t("rev.download")) + sep + figure(size(total*108/100), m.t("rev.disk")) + sep +
		figure(fmt.Sprint(len(setup.Selected(m.catalog, m.opts))+1), m.t("rev.items")) + sep + figure("~"+duration(time.Duration(seconds)*time.Second), m.t("rev.time")))
	p.gap(1)
	rows := m.reviewRows()
	for index, row := range rows {
		focused := index == m.focus
		hovered := m.hover == "row:"+row.id
		if row.id == "install" {
			p.gap(1)
			bg := t.Base
			switch {
			case focused:
				bg = Blend(t.Base, t.Brand, 0.28)
			case hovered:
				bg = Blend(t.Base, t.Brand, 0.14)
			}
			base := lipgloss.NewStyle().Background(bg)
			left := base.Render("  ") + base.Foreground(t.Brand).Bold(true).Render(m.glyphs.Chevron+" "+row.label)
			right := base.Foreground(t.Gray).Render(size(total)+"  ↵") + base.Render("  ")
			p.band(spread(left, right, p.width, bg), bg, "row:install")
			continue
		}
		bg := p.rowBG(focused, hovered)
		base := lipgloss.NewStyle().Background(bg)
		label := base.Foreground(t.Text2)
		if focused {
			label = base.Foreground(t.Text).Bold(true)
		}
		left := base.Render("  ") + label.Render(padRight(row.label, 20))
		right := base.Foreground(t.Gray).Render(row.summary) + base.Render("  ") + base.Foreground(t.Dim).Render(m.glyphs.Chevron) + base.Render("  ")
		p.band(spread(left, right, p.width, bg), bg, "row:"+row.id)
	}
}

func (m *Model) activateReview(id string) tea.Cmd {
	switch id {
	case "install":
		if err := m.opts.Validate(); err != nil {
			m.say(err.Error())
			return nil
		}
		return m.startInstall()
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
		left := p.st(t.Base).Render("  ") + digitStyle.Render(padRight(digits[index], 24))
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
			rows[index] += line + strings.Repeat(" ", width-len([]rune(line)))
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
	case "enter", " ", "right":
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
		m.screen, m.focus = scReview, 0
	case "apply":
		return m.startInstall()
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
