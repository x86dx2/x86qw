package tui

import (
	"fmt"
	"net/netip"
	"os"
	"reflect"
	"strconv"
	"strings"
	"time"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"

	"github.com/x86dx2/x86qw/go/internal/setup"
)

func (m *Model) openModal(kind string) {
	m.modal = &modal{kind: kind, field: map[string]*Field{}, before: cloneOptions(m.opts), match: m.game.match, dns: m.dns, dnsPending: m.dnsPending}
	add := func(name, value string, limit int) {
		field := NewField(value, limit)
		m.modal.field[name] = &field
	}
	add("srv.name", m.opts.Server.Name, 40)
	add("hostname", m.opts.Server.Hostname, 253)
	add("rcon", m.opts.Server.Rcon, 32)
	add("password", m.opts.Server.Password, 32)
	add("spectator", m.opts.Server.Spectators, 32)
	add("folder", m.opts.Target, 400)
	m.modal.field["hostname"].Accept = func(r rune) bool {
		return r == '.' || r == '-' || (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9')
	}
	// The game's console reads these between quotes.
	for _, name := range []string{"srv.name", "rcon", "password", "spectator"} {
		m.modal.field[name].Accept = setup.NameRune
	}
	if picker(kind) {
		add("filter", "", 30)
	}
	// A list opens on what is chosen.
	for index, row := range focusables(m.modalRows()) {
		if row.kind == rowRadio && row.on {
			m.modal.focus = index
		}
	}
}

// picker reports whether a dialog is a list searched by typing.
func picker(kind string) bool { return kind == "m.mode" || kind == "m.map" }

// closeModal keeps the changes (enter) or puts the choices back as they
// were (esc). With the preflight open, kept changes are checked again.
func (m *Model) closeModal(keep bool) tea.Cmd {
	if m.modal == nil {
		return nil
	}
	if m.modal.kind == "tool" {
		m.modal, m.toolPanel = nil, nil
		return nil
	}
	if !keep {
		m.opts, m.dns, m.dnsPending = m.modal.before, m.modal.dns, m.modal.dnsPending
		m.game.match = m.modal.match
		m.modal = nil
		return nil
	}
	if strings.HasPrefix(m.modal.kind, "m.") {
		// A match editor: the maps follow the game, mode and bots.
		changed := m.game.match != m.modal.match
		m.modal = nil
		if changed {
			return m.loadMaps()
		}
		return nil
	}
	if m.modal.kind == "tools" {
		m.modal = nil
		return m.takePending()
	}
	cmd := m.fieldChanged()
	if m.modal.kind == "server" {
		// A server that cannot start keeps its dialog open, on the tab
		// where the problem is.
		if err := m.opts.Server.Validate(); err != nil {
			if tab, ok := serverTab(setup.FieldOf(err)); ok {
				m.modal.tab, m.modal.focus = tab, 0
				return cmd
			}
		}
	}
	changed := !reflect.DeepEqual(m.opts, m.modal.before)
	m.modal = nil
	if changed {
		m.confirmErr = nil
	}
	if changed && m.screen == scConfigure && m.preOn {
		return tea.Batch(cmd, m.startPreflight(false))
	}
	return cmd
}

// serverTab is the server dialog's tab that holds a choice.
func serverTab(field string) (int, bool) {
	switch field {
	case "server.name", "server.instances", "server.ports":
		return 0, true
	case "server.slots", "server.password":
		return 1, true
	case "server.rcon", "server.qtv", "server.qwfwd":
		return 3, true
	}
	return 0, false
}

// serverProblem is the row under a server tab: why the server cannot
// start, when the problem is on that tab, else an empty line that keeps
// the dialog's height.
func (m *Model) serverProblem(tab int) formRow {
	row := formRow{kind: rowInfo, tone: "error"}
	if err := m.opts.Server.Validate(); err != nil {
		if owner, ok := serverTab(setup.FieldOf(err)); ok && owner == tab {
			row.value = m.explain(err)
		}
	}
	return row
}

// cloneOptions copies o without sharing its lists.
func cloneOptions(o setup.Options) setup.Options {
	o.Components = append([]string(nil), o.Components...)
	o.Server.Instances = append([]setup.Instance(nil), o.Server.Instances...)
	return o
}

// fieldChanged copies the dialog fields into the options; a new hostname
// is resolved once typing pauses.
func (m *Model) fieldChanged() tea.Cmd {
	if m.modal == nil {
		return nil
	}
	f := m.modal.field
	m.opts.Server.Name = f["srv.name"].String()
	m.opts.Server.Rcon = f["rcon"].String()
	m.opts.Server.Password, m.opts.Server.Spectators = f["password"].String(), f["spectator"].String()
	if m.existing == nil {
		m.opts.Target = setup.ExpandHome(f["folder"].String())
	}
	host := strings.ToLower(f["hostname"].String())
	if host != m.opts.Server.Hostname {
		m.opts.Server.Hostname, m.dns, m.dnsPending = host, nil, host
		if host != "" {
			return tea.Tick(dnsDebounce, func(time.Time) tea.Msg { return dnsDebounceMsg(host) })
		}
	}
	return nil
}

func (m *Model) modalTabs() []string {
	switch m.modal.kind {
	case "config":
		return []string{m.t("tab.keys"), m.t("tab.mouse"), m.t("tab.exp"), m.t("tab.video")}
	case "server":
		return []string{m.t("tab.general"), m.t("tab.access"), m.t("tab.network"), m.t("tab.services")}
	}
	return nil
}

func (m *Model) modalTitle() string {
	switch m.modal.kind {
	case "comps":
		return m.t("sec.comps")
	case "config":
		return m.t("sec.config")
	case "server":
		return m.t("sec.server")
	case "integ":
		return m.t("sec.integ")
	case "folder":
		return m.t("sec.folder")
	case "recipe":
		return m.t("sec.recipe")
	case "tools":
		return m.t("h.tools")
	case "tool":
		return m.t("tool." + m.toolPanel.spec.name)
	case "solo":
		return m.t("host.solo")
	case "m.game", "m.mode", "m.opts", "m.map", "m.rules":
		return m.t(m.modal.kind)
	}
	return ""
}

// modalKey handles a key inside a dialog.
func (m *Model) modalKey(msg tea.KeyPressMsg) tea.Cmd {
	key := msg.String()
	if m.modal.capture != "" {
		if key != "esc" {
			m.bindKey(m.modal.capture, key)
		}
		m.modal.capture = ""
		return nil
	}
	if m.modal.kind == "tool" {
		return m.toolKey(key)
	}
	if picker(m.modal.kind) {
		switch key {
		case "up", "down", "enter", "esc", "pgup", "pgdown", "space", "tab", "shift+tab":
		default:
			// Typing searches the list.
			if m.modal.field["filter"].Key(msg) {
				m.modal.focus = 0
				return nil
			}
		}
	}
	switch key {
	case "esc":
		return m.closeModal(false)
	case "tab", "shift+tab":
		if tabs := m.modalTabs(); len(tabs) > 1 {
			delta := 1
			if key == "shift+tab" {
				delta = len(tabs) - 1
			}
			m.modal.tab, m.modal.focus = (m.modal.tab+delta)%len(tabs), 0
			return nil
		}
	}
	rows := focusables(m.modalRows())
	if m.modal.focus < len(rows) && rows[m.modal.focus].kind == rowText {
		switch key {
		case "up", "down", "enter", "tab", "shift+tab":
		default:
			if rows[m.modal.focus].field.Key(msg) {
				return m.fieldChanged()
			}
		}
	}
	return m.modalNav(key)
}

func (m *Model) modalNav(key string) tea.Cmd {
	rows := focusables(m.modalRows())
	if len(rows) == 0 {
		if key == "enter" {
			return m.closeModal(true)
		}
		return nil
	}
	m.modal.focus = min(m.modal.focus, len(rows)-1)
	row := rows[m.modal.focus]
	switch key {
	case "up", "k":
		m.modal.focus = (m.modal.focus + len(rows) - 1) % len(rows)
	case "down", "j":
		m.modal.focus = (m.modal.focus + 1) % len(rows)
	case "left", "h", "right", "l":
		delta := 1
		if key == "left" || key == "h" {
			delta = -1
		}
		switch tabs := m.modalTabs(); {
		case row.step != nil:
			row.step(delta)
		case len(tabs) > 1:
			m.modal.tab, m.modal.focus = (m.modal.tab+delta+len(tabs))%len(tabs), 0
		}
	case "shift+left", "shift+right":
		// Numbers go by ten.
		delta := 1
		if row.coarse {
			delta = 10
		}
		if key == "shift+left" {
			delta = -delta
		}
		if row.step != nil {
			row.step(delta)
		}
	case "space", " ":
		// Space changes the row in focus.
		switch {
		case row.kind == rowText:
		case row.activate != nil:
			row.activate()
		case row.step != nil:
			row.step(+1)
		}
	case "enter":
		// Enter presses a button; anywhere else it keeps the changes and
		// closes, picking the option in focus on the way.
		if row.kind == rowAction && row.activate != nil {
			row.activate()
			return m.takePending()
		}
		if row.kind == rowRadio && row.activate != nil {
			row.activate()
		}
		return tea.Batch(m.takePending(), m.closeModal(true))
	}
	return m.takePending()
}

// takePending returns a command an activated row asked for.
func (m *Model) takePending() tea.Cmd {
	cmd := m.pending
	m.pending = nil
	return cmd
}

func (m *Model) clickRow(id string) tea.Cmd {
	if m.modal != nil {
		for index, row := range focusables(m.modalRows()) {
			if row.id == id {
				if m.modal.focus == index || row.kind != rowText {
					m.modal.focus = index
					switch {
					case row.activate != nil:
						row.activate()
					case row.step != nil:
						row.step(+1)
					}
				}
				m.modal.focus = index
				return m.takePending()
			}
		}
		return nil
	}
	switch m.screen {
	case scConfigure:
		for index, row := range m.confirmRows() {
			if row.id == id {
				// A closed section opens first; a second click edits it.
				if m.focus != index && id != "confirm" {
					m.focus = index
					return nil
				}
				m.focus = index
				return m.activateConfirm(id)
			}
		}
	case scMatch, scHost:
		if m.screen == scMatch && m.game.catalog == nil {
			return nil
		}
		rows, activate := m.matchRows(), m.activateMatch
		if m.screen == scHost {
			rows, activate = m.hostRows(), m.activateHost
		}
		for index, row := range rows {
			if row.id == id {
				if m.focus != index && index < len(rows)-1 {
					m.focus = index
					return nil
				}
				m.focus = index
				return activate(id)
			}
		}
	case scServers:
		if index, err := strconv.Atoi(strings.TrimPrefix(id, "srv")); err == nil && strings.HasPrefix(id, "srv") {
			if m.focus == index {
				return m.joinServer(false)
			}
			m.focus = index
		}
	case scDone:
		// Rows: the first click focuses, the second acts.
		for index, row := range m.doneRows() {
			if row.id == id {
				if m.focus != index {
					m.focus = index
					return nil
				}
				return m.activateDone(id)
			}
		}
	case scPanel:
		if m.removed || m.busy {
			return nil
		}
		for index, row := range m.panelRows() {
			if row.id == id {
				if m.focus != index {
					m.focus = index
					return nil
				}
				return m.activatePanel(id)
			}
		}
	}
	return nil
}

// bindKey assigns a terminal key to a movement action.
func (m *Model) bindKey(action, key string) {
	names := map[string]string{"up": "uparrow", "down": "downarrow", "left": "leftarrow", "right": "rightarrow", " ": "space", "space": "space"}
	if name, ok := names[key]; ok {
		key = name
	}
	if len([]rune(key)) != 1 && !strings.HasSuffix(key, "arrow") && key != "space" {
		m.say(fmt.Sprintf(m.t("key.unknown"), key))
		return
	}
	k := &m.opts.Keys
	slots := map[string]*string{"fwd": &k.Forward, "back": &k.Back, "left": &k.Left, "right": &k.Right, "jump": &k.Jump}
	for _, slot := range slots {
		if *slot == key {
			*slot = "—"
		}
	}
	*slots[action] = key
	k.Preset = "custom"
}

// modalRows builds the rows of the open dialog from the current state.
func (m *Model) modalRows() []formRow {
	o := &m.opts
	if strings.HasPrefix(m.modal.kind, "m.") {
		return m.gameModalRows()
	}
	switch m.modal.kind {
	case "tool":
		return m.toolRowsView()
	case "tools":
		return m.toolRows()
	case "solo":
		return m.soloRows()
	case "comps":
		return m.compRows()
	case "config":
		switch m.modal.tab {
		case 0:
			return m.keyRows()
		case 1:
			dpis := []int{400, 800, 1000, 1200, 1600, 2400, 3200}
			return []formRow{
				{id: "sens", kind: rowStepper, label: m.t("sens"), value: m.num(o.Mouse.Sensitivity), step: func(d int) {
					o.Mouse.Sensitivity = clampF(o.Mouse.Sensitivity+float64(d)*0.1, 0.5, 10)
				}},
				{id: "dpi", kind: rowStepper, label: m.t("dpi"), value: fmt.Sprint(o.Mouse.DPI), step: func(d int) { o.Mouse.DPI = cycleInt(dpis, o.Mouse.DPI, d) }},
				{id: "cm", kind: rowInfo, label: m.t("cm360"), value: m.num(o.Mouse.CentimetersPer360()) + " " + m.t("unit.cm"), tone: "dim"},
				{id: "invert", kind: rowToggle, label: m.t("invert"), on: o.Mouse.Invert, desc: map[bool]string{false: "m_pitch 0.022", true: "m_pitch -0.022"}[o.Mouse.Invert], activate: func() { o.Mouse.Invert = !o.Mouse.Invert }},
			}
		case 2:
			var rows []formRow
			for _, key := range setup.RulesetKeys {
				key := key
				rows = append(rows, formRow{id: "rs." + key, kind: rowRadio, label: m.ruleset(key), desc: m.t("rs." + key + ".d"), on: o.Experience == key, activate: func() { o.Experience = key }})
			}
			return rows
		default:
			resolutions := [][2]int{{0, 0}, {1920, 1080}, {2560, 1440}, {3440, 1440}, {3840, 2160}, {1280, 720}}
			current := 0
			for index, r := range resolutions {
				if r[0] == o.Video.Width && r[1] == o.Video.Height {
					current = index
				}
			}
			resolution := m.t("res.auto")
			if o.Video.Width > 0 {
				resolution = fmt.Sprintf("%d×%d", o.Video.Width, o.Video.Height)
			}
			displays := []string{"fullscreen", "borderless", "windowed"}
			return []formRow{
				{id: "res", kind: rowStepper, label: m.t("res"), value: resolution, step: func(d int) {
					r := resolutions[(current+d+len(resolutions))%len(resolutions)]
					o.Video.Width, o.Video.Height = r[0], r[1]
				}},
				{id: "display", kind: rowStepper, label: m.t("display"), value: m.t("d." + o.Video.Display), step: func(d int) { o.Video.Display = cycleString(displays, o.Video.Display, d) }},
				{id: "fov", kind: rowStepper, label: m.t("fov"), value: fmt.Sprintf("%d°", o.Video.FOV), step: func(d int) { o.Video.FOV = clampI(o.Video.FOV+d*5, 80, 130) }},
				{id: "volume", kind: rowStepper, label: m.t("volume"), value: fmt.Sprintf("%d%%", o.Volume), step: func(d int) { o.Volume = clampI(o.Volume+d*5, 0, 100) }},
			}
		}
	case "server":
		switch m.modal.tab {
		case 0:
			return append(m.serverGeneralRows(), m.serverProblem(0))
		case 1:
			return append(m.accessRows(), m.serverProblem(1))
		case 2:
			return m.networkRows()
		default:
			s := &o.Server
			rows := []formRow{
				{id: "qtv", kind: rowToggle, label: "QTV", on: s.QTVPort != 0, desc: m.t("qtv.d"), activate: func() { s.QTVPort = toggledPort(s.QTVPort, setup.DefaultQTVPort) }},
			}
			if s.QTVPort != 0 {
				rows = append(rows, formRow{id: "qtvport", kind: rowStepper, coarse: true, label: "    " + m.t("port.tcp"), value: fmt.Sprint(s.QTVPort), step: func(d int) { s.QTVPort = clampI(s.QTVPort+d, 1025, 65535) }})
			}
			rows = append(rows, formRow{id: "qwfwd", kind: rowToggle, label: "QWFWD", on: s.QWFWDPort != 0, desc: m.t("qwfwd.d"), activate: func() { s.QWFWDPort = toggledPort(s.QWFWDPort, setup.DefaultQWFWDPort) }})
			if s.QWFWDPort != 0 {
				rows = append(rows, formRow{id: "qwfwdport", kind: rowStepper, coarse: true, label: "    " + m.t("port.udp"), value: fmt.Sprint(s.QWFWDPort), step: func(d int) { s.QWFWDPort = clampI(s.QWFWDPort+d, 1025, 65535) }})
			}
			return append(rows,
				formRow{id: "rcon", kind: rowText, label: m.t("rcon"), field: m.modal.field["rcon"]},
				formRow{id: "rcon.new", kind: rowAction, label: m.t("rcon.new"), activate: func() {
					m.modal.field["rcon"].Set(setup.Password(14))
					m.fieldChanged()
				}},
				m.serverProblem(3))
		}
	case "integ":
		in := &o.Integration
		short := m.t("i.short." + platform())
		path := m.t("i.path.unix")
		if platform() == "windows" {
			path = m.t("i.path.windows")
		}
		// Only what this system can do is offered.
		can := setup.SupportedIntegration()
		var rows []formRow
		if can.Shortcuts {
			rows = append(rows, formRow{id: "short", kind: rowToggle, label: m.t("i.short"), desc: short, on: in.Shortcuts, activate: func() { in.Shortcuts = !in.Shortcuts }})
		}
		if can.Path {
			rows = append(rows, formRow{id: "path", kind: rowToggle, label: m.t("i.path"), desc: path, on: in.Path, activate: func() { in.Path = !in.Path }})
		}
		if can.QWLinks {
			rows = append(rows, formRow{id: "qw", kind: rowToggle, label: m.t("i.qw"), desc: m.t("i.qw.d"), on: in.QWLinks, activate: func() { in.QWLinks = !in.QWLinks }})
		}
		if can.Demos {
			rows = append(rows, formRow{id: "demo", kind: rowToggle, label: m.t("i.demo"), desc: m.t("i.demo.d"), on: in.Demos, activate: func() { in.Demos = !in.Demos }})
		}
		return rows
	case "folder":
		if m.existing != nil {
			// The installation lives where it is; the setup changes it there.
			return []formRow{
				{kind: rowInfo, label: m.t("sec.folder"), value: shortPath(m.existing.Target)},
				{kind: rowInfo, label: "", value: m.t("folder.locked"), tone: "dim"},
			}
		}
		return []formRow{
			{id: "folder", kind: rowText, label: m.t("sec.folder"), field: m.modal.field["folder"], desc: m.t("folder.d")},
		}
	case "recipe":
		return m.recipeRows()
	}
	return nil
}

func (m *Model) compRows() []formRow {
	o := &m.opts
	selected := map[string]bool{}
	for _, id := range setup.Selected(m.catalog, *o) {
		selected[id] = true
	}
	rows := []formRow{{kind: rowHeading, label: m.t("profile")}}
	if o.Intent.WantsClient() {
		for _, profile := range []string{"essential", "recommended", "complete", "custom"} {
			profile := profile
			rows = append(rows, formRow{id: "p." + profile, kind: rowRadio, label: m.t("p." + profile), desc: m.t("p." + profile + ".d"), on: o.Content == profile, activate: func() {
				if profile == "custom" && o.Content != "custom" {
					o.Components = m.clientSelection()
				}
				o.Content = profile
			}})
		}
	}
	rows = append(rows, formRow{kind: rowHeading, label: m.t("comps.items")})
	for _, component := range m.catalog.Components {
		component := component
		label := m.label("component", component.ID, component.Label)
		if component.Kind == "server" {
			if o.Intent.WantsServer() && selected[component.ID] {
				rows = append(rows, formRow{id: component.ID, kind: rowInfo, label: label, value: m.t("needed.host") + "  " + m.size(component.Size), tone: "dim"})
			}
			continue
		}
		if !o.Intent.WantsClient() {
			continue
		}
		if component.ID == "x86qw-client-bootstrap" {
			rows = append(rows, formRow{id: component.ID, kind: rowInfo, label: label, value: m.t("always") + "  " + m.size(component.Size), tone: "dim"})
			continue
		}
		if m.existing != nil && contains(m.existing.Installed, component.ID) {
			// The setup adds to an installation; removing is uninstalling.
			rows = append(rows, formRow{id: component.ID, kind: rowInfo, label: label, value: m.t("installed") + "  " + m.size(component.Size), tone: "dim"})
			continue
		}
		rows = append(rows, formRow{id: component.ID, kind: rowToggle, label: label, desc: m.size(component.Size), on: selected[component.ID], activate: func() {
			if o.Content != "custom" {
				o.Components, o.Content = m.clientSelection(), "custom"
			}
			if contains(o.Components, component.ID) {
				o.Components = remove(o.Components, component.ID)
			} else {
				o.Components = append(o.Components, component.ID)
			}
		}})
	}
	return rows
}

func (m *Model) clientSelection() []string {
	var ids []string
	for _, id := range setup.Selected(m.catalog, m.opts) {
		for _, component := range m.catalog.Components {
			if component.ID == id && component.Kind != "server" {
				ids = append(ids, id)
			}
		}
	}
	return ids
}

func (m *Model) keyRows() []formRow {
	o := &m.opts
	rows := []formRow{{kind: rowHeading, label: m.t("keys.layout")}}
	for _, preset := range []string{"wasd", "esdf", "arrows", "custom"} {
		preset := preset
		rows = append(rows, formRow{id: "l." + preset, kind: rowRadio, label: m.t("l." + preset), desc: m.t("l." + preset + ".d"), on: o.Keys.Preset == preset, activate: func() {
			if keys, ok := setup.KeyPresets[preset]; ok {
				o.Keys = keys
			} else {
				o.Keys.Preset = "custom"
			}
		}})
	}
	rows = append(rows, formRow{kind: rowCustom, lines: m.keyboard()})
	if o.Keys.Preset == "custom" {
		actions := [][3]string{{"fwd", "a.fwd", o.Keys.Forward}, {"back", "a.back", o.Keys.Back}, {"left", "a.left", o.Keys.Left}, {"right", "a.right", o.Keys.Right}, {"jump", "a.jump", o.Keys.Jump}}
		for _, action := range actions {
			action := action
			value := m.keyName(action[2])
			if m.modal.capture == action[0] {
				value = m.t("press.key") + "…"
			}
			rows = append(rows, formRow{id: "bind." + action[0], kind: rowAction, label: m.t(action[1]), desc: value, activate: func() { m.modal.capture = action[0] }})
		}
	}
	return rows
}

// keyboard draws a small QWERTY with the movement keys lit.
func (m *Model) keyboard() []string {
	t := m.theme
	o := m.opts.Keys
	bound := map[string]bool{o.Forward: true, o.Back: true, o.Left: true, o.Right: true, o.Jump: true}
	cap := func(name, label string) string {
		style := lipgloss.NewStyle().Background(t.Sunken).Foreground(t.Gray)
		if bound[name] {
			style = lipgloss.NewStyle().Background(Blend(t.Base, t.Brand, 0.42)).Foreground(t.Text).Bold(true)
		}
		return style.Render(" "+label+" ") + t.Style(t.Base).Render(" ")
	}
	var lines []string
	rows := []string{"1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm"}
	indent := []int{0, 2, 3, 5}
	arrows := [][]string{nil, nil, {"", "uparrow"}, {"leftarrow", "downarrow", "rightarrow"}}
	for index, row := range rows {
		var line strings.Builder
		line.WriteString(t.Style(t.Base).Render(strings.Repeat(" ", 4+indent[index])))
		for _, r := range row {
			line.WriteString(cap(string(r), strings.ToUpper(string(r))))
		}
		pad := 46 - lipgloss.Width(line.String())
		line.WriteString(t.Style(t.Base).Render(strings.Repeat(" ", max(1, pad))))
		for _, arrow := range arrows[index] {
			if arrow == "" {
				line.WriteString(t.Style(t.Base).Render("    "))
				continue
			}
			line.WriteString(cap(arrow, keyLabel(arrow)))
		}
		lines = append(lines, line.String())
	}
	space := lipgloss.NewStyle().Background(t.Sunken).Foreground(t.Gray)
	if bound["space"] {
		space = lipgloss.NewStyle().Background(Blend(t.Base, t.Brand, 0.42)).Foreground(t.Text).Bold(true)
	}
	lines = append(lines, t.Style(t.Base).Render(strings.Repeat(" ", 13))+space.Render(fmt.Sprintf("%-19s", "        "+m.t("key.space"))))
	legend := []string{}
	for _, pair := range [][2]string{{o.Forward, "a.fwd"}, {o.Back, "a.back"}, {o.Left, "a.left"}, {o.Right, "a.right"}, {o.Jump, "a.jump"}} {
		legend = append(legend, t.Style(t.Text2).Bold(true).Render(m.keyName(pair[0]))+t.Style(t.Gray).Render(" "+m.t(pair[1])))
	}
	return append([]string{""}, append(lines, "", t.Style(t.Base).Render("    ")+strings.Join(legend, t.Style(t.Dim).Render("  ·  ")))...)
}

// keyName is a bound key as the interface names it.
func (m *Model) keyName(name string) string {
	if name == "space" {
		return m.t("key.space")
	}
	return keyLabel(name)
}

func keyLabel(name string) string {
	switch name {
	case "uparrow":
		return "↑"
	case "downarrow":
		return "↓"
	case "leftarrow":
		return "←"
	case "rightarrow":
		return "→"
	case "space":
		return "space"
	}
	return strings.ToUpper(name)
}

func (m *Model) serverGeneralRows() []formRow {
	s := &m.opts.Server
	rows := []formRow{
		{id: "srv.name", kind: rowText, label: m.t("srv.name"), field: m.modal.field["srv.name"]},
		{id: "base", kind: rowStepper, coarse: true, label: m.t("srv.base"), value: fmt.Sprint(s.BasePort), step: func(d int) { s.BasePort = clampI(s.BasePort+d, 1025, 65000) }},
		{kind: rowHeading, label: m.t("srv.inst")},
	}
	for index := range s.Instances {
		index := index
		instance := &s.Instances[index]
		mode := m.mode(instance.Mode)
		rows = append(rows,
			formRow{id: fmt.Sprintf("mode%d", index), kind: rowStepper, label: fmt.Sprintf("#%d  %s %d", index+1, m.t("port.udp"), s.BasePort+index), value: mode.Label, step: func(d int) {
				next := m.catalog.Modes[(m.modeIndex(instance.Mode)+d+len(m.catalog.Modes))%len(m.catalog.Modes)]
				instance.Mode, instance.Map = next.ID, next.DefaultMap
			}},
			formRow{id: fmt.Sprintf("map%d", index), kind: rowStepper, label: "      " + m.t("map"), value: instance.Map, step: func(d int) { instance.Map = cycleString(mode.Maps, instance.Map, d) }},
		)
		if mode.Bots {
			rows = append(rows, formRow{id: fmt.Sprintf("bots%d", index), kind: rowStepper, label: "      " + m.t("bots"), value: map[bool]string{true: m.t("bots.on"), false: m.t("bots.off")}[instance.Bots], step: func(int) { instance.Bots = !instance.Bots }})
		} else {
			instance.Bots = false
			rows = append(rows, formRow{id: fmt.Sprintf("bots%d", index), kind: rowInfo, label: "   " + m.t("bots"), value: m.t("bots.none"), tone: "dim"})
		}
	}
	if len(s.Instances) < setup.MaxInstances {
		rows = append(rows, formRow{id: "add", kind: rowAction, label: m.t("srv.add"), activate: func() {
			s.Instances = append(s.Instances, setup.Instance{Mode: "ffa", Map: "dm6", Bots: true})
		}})
	}
	if len(s.Instances) > 1 {
		rows = append(rows, formRow{id: "del", kind: rowAction, label: fmt.Sprintf(m.t("srv.del"), len(s.Instances)), activate: func() { s.Instances = s.Instances[:len(s.Instances)-1] }})
	}
	return rows
}

func (m *Model) mode(id string) setup.Mode {
	return m.catalog.Modes[m.modeIndex(id)]
}

func (m *Model) modeIndex(id string) int {
	for index, mode := range m.catalog.Modes {
		if mode.ID == id {
			return index
		}
	}
	return 0
}

// cloudflare lists the ranges Cloudflare proxies through; a hostname that
// resolves there cannot carry QuakeWorld's UDP.
var cloudflare = func() []netip.Prefix {
	var out []netip.Prefix
	for _, s := range []string{"173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22", "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32"} {
		out = append(out, netip.MustParsePrefix(s))
	}
	return out
}()

func proxied(addresses []netip.Addr) bool {
	for _, address := range addresses {
		for _, prefix := range cloudflare {
			if prefix.Contains(address) {
				return true
			}
		}
	}
	return false
}

func (m *Model) networkRows() []formRow {
	var rows []formRow
	if m.network == nil {
		return []formRow{{kind: rowInfo, label: m.t("net.public"), value: m.spinner() + " " + m.t("net.detecting"), tone: "dim"}}
	}
	n := m.network
	for _, address := range n.Private {
		rows = append(rows, formRow{kind: rowInfo, label: m.t("net.private"), value: fmt.Sprintf("%s  (%s)", address.IP, address.Interface)})
	}
	v4, v6 := m.t("net.none"), m.t("net.none")
	v4tone, v6tone := "dim", "dim"
	if n.PublicV4.IsValid() {
		v4, v4tone = n.PublicV4.String(), ""
	}
	if n.PublicV6.IsValid() {
		v6, v6tone = n.PublicV6.String(), ""
	}
	rows = append(rows,
		formRow{kind: rowInfo, label: m.t("net.public"), value: v4 + "  IPv4", tone: v4tone},
		formRow{kind: rowInfo, label: "", value: v6 + "  IPv6", tone: v6tone},
		formRow{kind: rowCustom, lines: []string{""}},
		formRow{id: "hostname", kind: rowText, label: m.t("net.hostname"), field: m.modal.field["hostname"], placeholder: m.t("net.hostname.ph")},
	)
	host := m.opts.Server.Hostname
	switch {
	case host == "":
	case m.dns == nil:
		rows = append(rows, formRow{kind: rowInfo, label: "DNS", value: m.spinner() + " " + m.t("net.detecting"), tone: "dim"})
	case m.dns.Err != nil:
		rows = append(rows, formRow{kind: rowInfo, label: "DNS", value: m.glyphs.Fail + " " + m.t("dns.fail"), tone: "error"})
	default:
		resolved := make([]string, 0, len(m.dns.Resolved))
		for _, address := range m.dns.Resolved {
			resolved = append(resolved, address.String())
		}
		value := strings.Join(resolved, ", ")
		switch {
		case m.dns.Matches:
			rows = append(rows, formRow{kind: rowInfo, label: "DNS", value: m.glyphs.Ok + " " + value, tone: "ok", desc: m.t("dns.match")})
		case proxied(m.dns.Resolved):
			rows = append(rows, formRow{kind: rowInfo, label: "DNS", value: m.glyphs.Warn + " " + value, tone: "warn", desc: m.t("dns.proxied")})
		default:
			rows = append(rows, formRow{kind: rowInfo, label: "DNS", value: m.glyphs.Warn + " " + value, tone: "warn", desc: m.t("dns.other")})
		}
	}
	rows = append(rows, formRow{kind: rowHeading, label: m.t("connect.as")})
	for _, port := range m.opts.Server.Ports() {
		rows = append(rows, formRow{kind: rowInfo, label: m.t("port.udp"), value: fmt.Sprintf("%s:%d", m.connectHost(), port)})
	}
	return rows
}

func (m *Model) recipeRows() []formRow {
	t := m.theme
	text := shellJoin(m.opts.Recipe())
	if m.modal.json {
		data, _ := m.opts.RecipeJSON()
		text = string(data)
	}
	width := min(m.w-8, 84) - 8
	var lines []string
	style := lipgloss.NewStyle().Background(t.Sunken).Foreground(t.Text2)
	lines = append(lines, style.Render(strings.Repeat(" ", width)))
	for _, line := range strings.Split(text, "\n") {
		for _, wrapped := range strings.Split(lipgloss.Wrap(line, width-4, " "), "\n") {
			lines = append(lines, style.Render("  "+fill(wrapped, width-2, t.Sunken)))
		}
	}
	lines = append(lines, style.Render(strings.Repeat(" ", width)))
	label := m.t("recipe.json")
	if m.modal.json {
		label = m.t("recipe.shell")
	}
	return []formRow{
		{kind: rowInfo, label: "", value: m.t("recipe.d"), tone: "dim"},
		{kind: rowCustom, lines: lines},
		{id: "copy", kind: rowAction, label: m.t("k.copy"), activate: func() { m.pending = m.copyRecipe() }},
		{id: "json", kind: rowAction, label: label, activate: func() { m.modal.json = !m.modal.json }},
	}
}

// modalView frames the open dialog: title and close mark on the top edge,
// tabs, then the rows; long dialogs scroll with the focus.
func (m *Model) modalView() ([]string, []hit, *[2]int) {
	t := m.theme
	width := max(minWidth, min(m.w-8, modalWidth))
	inner := width - 4
	p := newPage(t, m.glyphs, inner)
	p.gap(1)
	if tabs := m.modalTabs(); len(tabs) > 1 {
		var line strings.Builder
		x := 0
		for index, tab := range tabs {
			style := t.Style(t.Gray)
			if index == m.modal.tab {
				style = lipgloss.NewStyle().Background(t.Visual).Foreground(t.Text).Bold(true)
			} else if m.hover == fmt.Sprintf("tab:%d", index) {
				style = lipgloss.NewStyle().Background(t.Hover).Foreground(t.Text2)
			}
			label := style.Render(" " + tab + " ")
			p.hits = append(p.hits, hit{y: 1, x0: x, x1: x + lipgloss.Width(label), id: fmt.Sprintf("tab:%d", index)})
			line.WriteString(label + t.Style(t.Base).Render("  "))
			x += lipgloss.Width(label) + 2
		}
		p.add(line.String())
		p.add(t.Style(t.Faint()).Render(strings.Repeat("─", inner)))
		p.gap(1)
	}
	rows := m.modalRows()
	if f := focusables(rows); m.modal.focus >= len(f) {
		m.modal.focus = max(0, len(f)-1)
	}
	start := len(p.lines)
	p.renderRows(rows, m.modal.focus, m.hover)
	p.gap(1)
	// Every tab gets the height of the tallest, and the dialog never
	// shrinks while open: switching tabs or options leaves it in place.
	if m.modal.height == 0 {
		tab := m.modal.tab
		for index := range m.modalTabs() {
			m.modal.tab = index
			probe := newPage(t, m.glyphs, inner)
			probe.renderRows(m.modalRows(), -1, "")
			m.modal.height = max(m.modal.height, start+len(probe.lines)+1)
		}
		m.modal.tab = tab
	}
	m.modal.height = max(m.modal.height, len(p.lines))
	p.gap(m.modal.height - len(p.lines))

	// Scroll so the focused row stays visible.
	room := m.h - 6
	lines, hits := p.lines, p.hits
	offset := 0
	if len(lines) > room {
		focusY := start
		for _, h := range hits {
			if strings.HasPrefix(h.id, "row:") {
				if row := focusables(rows); m.modal.focus < len(row) && h.id == "row:"+row[m.modal.focus].id {
					focusY = h.y
				}
			}
		}
		offset = max(0, min(len(lines)-room, focusY-room/2))
		lines = lines[offset : offset+room]
	}
	edge := t.Style(t.Border)
	title := t.Style(t.Text).Bold(true).Render(" " + m.modalTitle() + " ")
	closeStyle := t.Style(t.Gray)
	if m.hover == "close" {
		closeStyle = t.Style(t.Text).Bold(true)
	}
	closeMark := closeStyle.Render("[" + m.glyphs.Fail + "]")
	top := edge.Render("╭─") + title + edge.Render(strings.Repeat("─", max(0, width-lipgloss.Width(title)-7))) + closeMark + edge.Render("─╮")
	out := []string{top}
	var outHits []hit
	outHits = append(outHits, hit{y: 0, x0: width - 5, x1: width - 2, id: "close"})
	for index, line := range lines {
		out = append(out, edge.Render("│ ")+fill(line, inner, t.Base)+edge.Render(" │"))
		_ = index
	}
	// The bottom edge carries the two ways out, for the mouse.
	okStyle, cancelStyle := t.Style(t.Gray), t.Style(t.Gray)
	if m.hover == "confirm-modal" {
		okStyle = t.Style(t.Text).Bold(true)
	}
	if m.hover == "close" {
		cancelStyle = t.Style(t.Text).Bold(true)
	}
	okLabel, cancelLabel := m.t("k.ok"), m.t("k.cancel")
	if m.modal.kind == "tool" {
		// A tool's panel: enter presses the button in focus; without one
		// esc is the only way out.
		okLabel, cancelLabel = "", m.t("k.close")
		if actions := m.toolActions(); len(actions) > 0 {
			okLabel = strings.ToLower(m.t("tool." + m.toolPanel.spec.name + "." + actions[min(m.modal.focus, len(actions)-1)].id))
		}
	}
	ok := ""
	if okLabel != "" {
		ok = okStyle.Render(" ↵ "+okLabel+" ") + edge.Render("─")
	}
	cancel := cancelStyle.Render(" esc " + cancelLabel + " ")
	tail := edge.Render("─") + ok + cancel + edge.Render("─╯")
	out = append(out, edge.Render("╰"+strings.Repeat("─", max(0, width-1-lipgloss.Width(tail))))+tail)
	bottom := len(out) - 1
	cancelX := width - 2 - lipgloss.Width(cancel)
	// ok carries the edge after it, which is no part of the button.
	okX := cancelX - lipgloss.Width(ok)
	outHits = append(outHits, hit{y: bottom, x0: okX, x1: okX + max(0, lipgloss.Width(ok)-1), id: "confirm-modal"},
		hit{y: bottom, x0: cancelX, x1: cancelX + lipgloss.Width(cancel), id: "close"})
	for _, h := range hits {
		y := h.y - offset
		if y >= 0 && y < len(lines) {
			outHits = append(outHits, hit{y: y + 1, x0: h.x0 + 2, x1: h.x1 + 2, id: h.id})
		}
	}
	var caret *[2]int
	if p.caret != nil {
		y := p.caret[1] - offset
		if y >= 0 && y < len(lines) {
			caret = &[2]int{p.caret[0] + 2, y + 1}
		}
	}
	return out, outHits, caret
}

func clampI(v, lo, hi int) int { return max(lo, min(hi, v)) }

func clampF(v, lo, hi float64) float64 {
	v = float64(int(v*10+0.5)) / 10
	return max(lo, min(hi, v))
}

func cycleInt(values []int, current, delta int) int {
	for index, v := range values {
		if v == current {
			return values[(index+delta+len(values))%len(values)]
		}
	}
	return values[0]
}

func cycleString(values []string, current string, delta int) string {
	for index, v := range values {
		if v == current {
			return values[(index+delta+len(values))%len(values)]
		}
	}
	return values[0]
}

func toggledPort(port, fallback int) int {
	if port != 0 {
		return 0
	}
	return fallback
}

func contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

func remove(list []string, s string) []string {
	var out []string
	for _, v := range list {
		if v != s {
			out = append(out, v)
		}
	}
	return out
}

func homeDir() string {
	home, _ := os.UserHomeDir()
	return home
}

// modalWidth is the widest a dialog grows.
const modalWidth = 84

// accessRows decide who reaches the server and how many play.
func (m *Model) accessRows() []formRow {
	s := &m.opts.Server
	slots := s.Slots
	if slots == 0 {
		slots = setup.DefaultSlots
	}
	return []formRow{
		{kind: rowHeading, label: m.t("access.who")},
		{id: "public", kind: rowRadio, label: m.t("access.public"), desc: m.t("access.public.d"), on: !s.Local, activate: func() { s.Local = false }},
		{id: "local", kind: rowRadio, label: m.t("access.local"), desc: m.t("access.local.d"), on: s.Local, activate: func() { s.Local = true }},
		{kind: rowHeading, label: m.t("access.match")},
		{id: "slots", kind: rowStepper, coarse: true, label: m.t("access.slots"), value: fmt.Sprint(slots), step: func(d int) { s.Slots = clampI(slots+d, setup.MinSlots, setup.MaxSlots) }},
		{id: "record", kind: rowToggle, label: m.t("access.record"), desc: m.t("access.record.d"), on: !s.NoRecord, activate: func() { s.NoRecord = !s.NoRecord }},
		{kind: rowHeading, label: m.t("access.passwords")},
		{id: "password", kind: rowText, label: m.t("access.password"), field: m.modal.field["password"], desc: m.t("access.open")},
		{id: "spectator", kind: rowText, label: m.t("access.spectator"), field: m.modal.field["spectator"], desc: m.t("access.open")},
	}
}
