package tui

import (
	"context"
	"fmt"
	"slices"
	"strconv"
	"strings"
	"time"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/charmbracelet/x/ansi"

	"github.com/x86dx2/x86qw/go/internal/setup"
)

// The app side of the interface: the home screen of an installation, the
// match, the public servers and the installation's own server.

// gameState is what those screens know.
type gameState struct {
	catalog *setup.MatchCatalog
	err     error
	match   setup.Match
	maps    *setup.MapList
	mapNote string // why the map changed on its own

	servers        []setup.PublicServer
	serversErr     error
	serversLoading bool
	serversAt      time.Time
	filter         Field

	stack       *setup.Stack
	stackLoaded bool
	polling     bool // the stack poll is scheduled

	// One action runs at a time; its spinner and its error belong to the
	// screen that started it.
	acting    string // play, join, watch, favorite, start, stop
	actingOn  screen
	actionErr error
	errOn     screen
	subject   string // what the action is about, for the toast after it
}

type matchCatalogMsg struct {
	catalog *setup.MatchCatalog
	err     error
}

type mapsMsg struct {
	list  *setup.MapList
	err   error
	match setup.Match
}

type serversMsg struct {
	servers []setup.PublicServer
	err     error
}

type stackMsg struct {
	stack *setup.Stack
	err   error
}

type stackPollMsg struct{}

type actionMsg struct {
	what string
	err  error
}

// actingHere is an action running that this screen started.
func (m *Model) actingHere() bool { return m.game.acting != "" && m.game.actingOn == m.screen }

// errorHere is the last action's error, when this screen started it.
func (m *Model) errorHere() error {
	if m.game.errOn == m.screen {
		return m.game.actionErr
	}
	return nil
}

func (m *Model) loadMatch() tea.Cmd {
	backend, target := m.cfg.Backend, m.target()
	return func() tea.Msg {
		catalog, err := backend.MatchCatalog(context.Background(), target)
		return matchCatalogMsg{catalog, err}
	}
}

func (m *Model) loadMaps() tea.Cmd {
	backend, target, match := m.cfg.Backend, m.target(), m.game.match
	return func() tea.Msg {
		list, err := backend.Maps(context.Background(), target, match)
		return mapsMsg{list, err, match}
	}
}

func (m *Model) loadServers() tea.Cmd {
	m.game.serversLoading = true
	backend, target := m.cfg.Backend, m.target()
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		servers, err := backend.Servers(ctx, target)
		return serversMsg{servers, err}
	}
}

func (m *Model) loadStack() tea.Cmd {
	backend, target := m.cfg.Backend, m.target()
	return func() tea.Msg {
		stack, err := backend.Stack(context.Background(), target)
		return stackMsg{stack, err}
	}
}

// pollStack refreshes the server's state while its screen is open; there
// is one poll at most.
func (m *Model) pollStack() tea.Cmd {
	if m.game.polling {
		return nil
	}
	m.game.polling = true
	return tea.Tick(stackInterval, func(time.Time) tea.Msg { return stackPollMsg{} })
}

// target is the installation the app works on.
func (m *Model) target() string {
	if m.existing != nil {
		return m.existing.Target
	}
	return m.opts.Target
}

// act runs an action off the update loop; the dock shows it running.
func (m *Model) act(what string, run func(ctx context.Context) error) tea.Cmd {
	m.game.acting, m.game.actingOn, m.game.actionErr = what, m.screen, nil
	return tea.Batch(func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
		defer cancel()
		return actionMsg{what, run(ctx)}
	})
}

// gameUpdate handles the app's messages; handled is false for others.
func (m *Model) gameUpdate(msg tea.Msg) (tea.Cmd, bool) {
	g := &m.game
	switch msg := msg.(type) {
	case matchCatalogMsg:
		g.catalog, g.err = msg.catalog, msg.err
		if msg.catalog != nil {
			m.defaultMatch()
			return m.loadMaps(), true
		}
		return nil, true
	case mapsMsg:
		if msg.match != g.match {
			return nil, true // the match changed while the list was on its way
		}
		g.maps, g.err = msg.list, msg.err
		if msg.list != nil && !slices.Contains(msg.list.Maps, g.match.Map) {
			if g.match.Map != "" {
				g.mapNote = fmt.Sprintf(m.t("match.map.changed"), g.match.Map, msg.list.Default)
			}
			g.match.Map = msg.list.Default
		}
		return nil, true
	case serversMsg:
		g.servers, g.serversErr, g.serversLoading, g.serversAt = msg.servers, msg.err, false, time.Now()
		return nil, true
	case stackMsg:
		g.stack, g.stackLoaded = msg.stack, true
		if msg.err != nil {
			g.actionErr, g.errOn = msg.err, scHost
		}
		return nil, true
	case stackPollMsg:
		g.polling = false
		if m.screen == scHost {
			return tea.Batch(m.loadStack(), m.pollStack()), true
		}
		return nil, true
	case actionMsg:
		g.acting, g.actionErr, g.errOn = "", msg.err, g.actingOn
		if msg.err != nil {
			return nil, true
		}
		switch msg.what {
		case "play":
			m.say(fmt.Sprintf(m.t("played"), m.matchTitle()))
			if g.catalog != nil {
				last := g.match
				g.catalog.Last = &last
			}
		case "join":
			m.say(fmt.Sprintf(m.t("joined"), g.subject))
		case "watch":
			m.say(fmt.Sprintf(m.t("watching"), g.subject))
		case "favorite", "unfavorite":
			for index := range g.servers {
				if g.servers[index].Address == g.subject {
					g.servers[index].Favorite = msg.what == "favorite"
				}
			}
			m.say(fmt.Sprintf(m.t(msg.what+"d"), g.subject))
		case "start", "stop":
			m.focus = 0
			return m.loadStack(), true
		}
		return nil, true
	}
	return nil, false
}

// defaultMatch starts from the last match, else a duel of KTX.
func (m *Model) defaultMatch() {
	c, g := m.game.catalog, &m.game
	if c.Last != nil {
		g.match = *c.Last
		return
	}
	g.match = setup.Match{Ruleset: c.Ruleset, BotSkill: "5", BotName: "default"}
	if len(c.Channels) > 0 {
		g.match.Channel = c.Channels[0]
	}
	if len(c.Games) > 0 {
		m.setGame(c.Games[0].ID)
		for _, game := range c.Games {
			if game.ID == "ktx" {
				m.setGame("ktx")
			}
		}
	}
}

func (m *Model) gameInfo() setup.Game {
	if m.game.catalog == nil {
		return setup.Game{}
	}
	for _, game := range m.game.catalog.Games {
		if game.ID == m.game.match.Game {
			return game
		}
	}
	return setup.Game{}
}

func (m *Model) matchMode() *setup.Mode {
	if m.game.catalog == nil {
		return nil
	}
	for index, mode := range m.game.catalog.Modes {
		if mode.ID == m.game.match.Mode {
			return &m.game.catalog.Modes[index]
		}
	}
	return nil
}

// setGame switches games: a KTX match starts as a duel.
func (m *Model) setGame(id string) {
	g := &m.game
	g.match.Game, g.match.Map = id, ""
	g.match.Mode = ""
	if m.gameInfo().Modes {
		g.match.Mode = "duel"
	}
	m.clearOptions()
}

// clearOptions drops the options a mode change makes meaningless.
func (m *Model) clearOptions() {
	g := &m.game.match
	keep := setup.Match{Game: g.Game, Mode: g.Mode, Map: g.Map, Ruleset: g.Ruleset, Channel: g.Channel, BotSkill: "5", BotName: "default"}
	*g = keep
}

// hasOptions reports whether the mode asks anything beyond the map.
func (m *Model) hasOptions() bool {
	mode := m.matchMode()
	return mode != nil && (mode.Bots || mode.Kind == "race" || mode.Kind == "ctf")
}

// matchTitle is the match in a few words: mode and map.
func (m *Model) matchTitle() string {
	match := m.game.match
	what := m.gameInfo().Label
	if mode := m.matchMode(); mode != nil {
		what = mode.Label
	}
	return what + " · " + match.Map
}

func (m *Model) botsText() string {
	match := m.game.match
	switch {
	case match.FillBots:
		return fmt.Sprintf(m.t("bots.fill"), match.BotSkill)
	case match.Bots == 1:
		return fmt.Sprintf(m.t("bots.one"), match.BotSkill)
	case match.Bots > 1:
		return fmt.Sprintf(m.t("bots.many"), match.Bots, match.BotSkill)
	}
	return m.t("bots.none.match")
}

func (m *Model) optionsSummary() string {
	mode, match := m.matchMode(), m.game.match
	switch {
	case mode == nil:
		return ""
	case mode.Kind == "race":
		return m.t("race." + or(match.RaceStyle, "default"))
	case mode.Kind == "ctf":
		return fmt.Sprintf("%s %s · %s %s", m.t("ctf.hook"), or(match.CTFHook, m.t("mode.default")), m.t("ctf.runes"), or(match.CTFRunes, m.t("mode.default")))
	}
	return m.botsText()
}

func or(value, fallback string) string {
	if value == "" {
		return fallback
	}
	return value
}

// matchRows are the match screen: its sections, then Play.
func (m *Model) matchRows() []reviewRow {
	g := m.game
	match := g.match
	game := m.gameInfo()
	// The game's own facts come last and start shut: the choices come first.
	var rows []reviewRow
	if game.Modes {
		summary := ""
		if mode := m.matchMode(); mode != nil {
			summary = mode.Label + " · " + playersLabel(m, mode.Players)
		}
		rows = append(rows, reviewRow{"m.mode", m.t("m.mode"), summary})
	}
	if m.hasOptions() {
		rows = append(rows, reviewRow{"m.opts", m.t("m.opts"), m.optionsSummary()})
	}
	rows = append(rows, reviewRow{"m.map", m.t("m.map"), match.Map})
	rules := m.ruleset(match.Ruleset)
	if g.catalog != nil && len(g.catalog.Channels) > 1 {
		rules += " · ezQuake " + match.Channel
	}
	rows = append(rows, reviewRow{"m.rules", m.t("m.rules"), rules})
	if m.opts.Intent.WantsClient() {
		rows = append(rows, reviewRow{"m.player", m.t("m.player"), m.sectionSummary("config")})
	}
	rows = append(rows, reviewRow{"m.game", m.t("m.game"), game.Label})
	return append(rows, reviewRow{"play", m.t("m.play"), ""})
}

func playersLabel(m *Model, players string) string {
	if players == "" {
		return ""
	}
	if strings.HasSuffix(players, "+") {
		return fmt.Sprintf(m.t("players.open"), strings.TrimSuffix(players, "+"))
	}
	return fmt.Sprintf(m.t("players.fixed"), players)
}

// matchDetails is everything one section of the match holds.
func (m *Model) matchDetails(id string) [][2]string {
	match := m.game.match
	switch id {
	case "m.game":
		game := m.gameInfo()
		return [][2]string{{m.t("m.game"), game.Label}, {m.t("d.version"), game.Version}, {"", m.label("game", game.ID+".d", game.Description)}}
	case "m.mode":
		if mode := m.matchMode(); mode != nil {
			return [][2]string{{m.t("m.mode"), mode.Label}, {m.t("d.players"), playersLabel(m, mode.Players)}, {"", m.label("mode", mode.ID+".d", mode.Description)}}
		}
	case "m.opts":
		mode := m.matchMode()
		if mode == nil {
			return nil
		}
		switch mode.Kind {
		case "race":
			rows := [][2]string{{m.t("race.style"), m.t("race." + or(match.RaceStyle, "default"))}}
			if match.RaceStyle == "match" {
				rows = append(rows, [2]string{m.t("race.scoring"), or(match.RaceScoring, m.t("mode.default"))})
			}
			pacemaker := m.t("off")
			if match.RacePacemaker > 0 {
				pacemaker = strconv.Itoa(match.RacePacemaker)
			}
			return append(rows, [2]string{m.t("race.pacemaker"), pacemaker})
		case "ctf":
			based := m.t("off")
			if match.CTFBased {
				based = m.t("on")
			}
			return [][2]string{{m.t("ctf.hook"), or(match.CTFHook, m.t("mode.default"))}, {m.t("ctf.runes"), or(match.CTFRunes, m.t("mode.default"))}, {m.t("ctf.based"), based}}
		}
		rows := [][2]string{{m.t("bots"), m.botsText()}}
		if match.HasBots() {
			rows = append(rows, [2]string{m.t("bot.names"), m.t("names." + or(match.BotName, "default"))})
			if mode.Kind == "tot" {
				rows = append(rows, [2]string{m.t("bot.weapon"), or(match.BotWeapon, m.t("mode.default"))})
			}
		}
		return rows
	case "m.map":
		rows := [][2]string{{m.t("m.map"), match.Map}}
		if list := m.game.maps; list != nil {
			note := fmt.Sprintf(m.t("maps.count"), len(list.Maps))
			if list.Narrowed {
				note = fmt.Sprintf(m.t("maps.narrowed"), len(list.Maps))
			}
			rows = append(rows, [2]string{"", note})
		}
		return rows
	case "m.player":
		return [][2]string{{m.t("m.player"), m.sectionSummary("config")}, {"", m.t("m.player.d")}}
	case "m.rules":
		rows := [][2]string{{m.t("m.rules"), m.ruleset(match.Ruleset)}}
		if _, ok := texts["rs."+match.Ruleset+".d"]; ok {
			rows = append(rows, [2]string{"", m.t("rs." + match.Ruleset + ".d")})
		}
		if match.Channel != "" {
			rows = append(rows, [2]string{m.t("d.client"), "ezQuake " + match.Channel})
		}
		return rows
	}
	return nil
}

func (m *Model) matchPage(p *page) {
	t := m.theme
	p.title(m.t("match.title"))
	p.lead(m.t("match.lead"))
	p.gap(1)
	switch {
	case m.game.catalog == nil && m.game.err == nil:
		p.add(p.st(t.Gray).Render("  " + m.spinner() + "  " + m.t("match.loading")))
		return
	case m.game.catalog == nil:
		p.add(p.st(t.Error).Render("  " + m.glyphs.Fail + "  " + m.explain(m.game.err)))
		return
	case len(m.game.catalog.Games) == 0:
		p.lead(m.t("match.none"))
		return
	}
	rows := m.matchRows()
	m.accordion(p, rows[:len(rows)-1])
}

// gameDock is the fixed bar of the match, server and servers screens: a
// note (an error, what is running) over the screen's main action.
func (m *Model) gameDock(width int) *page {
	t := m.theme
	p := newPage(t, m.glyphs, width)
	p.add(p.st(t.Faint()).Render(strings.Repeat("─", width)))
	g := m.game
	note, color := "", t.Gray
	switch {
	case m.errorHere() != nil:
		note, color = m.explain(m.errorHere()), t.Error
	case m.screen == scMatch && g.err != nil && g.catalog != nil:
		note, color = m.explain(g.err), t.Error
	case m.screen == scMatch && g.mapNote != "":
		note = g.mapNote
	case m.screen == scServers && m.actingHere():
		note = m.spinner() + "  " + fmt.Sprintf(m.t("servers."+g.acting), g.subject)
	}
	if m.screen == scServers {
		// The servers' dock: a note line, always there so the list never
		// moves, then the action of the focused server.
		p.add(p.st(color).Render("  " + ansi.Truncate(note, width-4, "…")))
		p.gap(1)
		m.serverBar(p)
		return p
	}
	if note != "" {
		for _, line := range strings.Split(lipgloss.Wrap(note, width-6, " "), "\n") {
			p.add(p.st(color).Render("  " + line))
		}
	}
	p.gap(1)
	var rows []reviewRow
	right, enabled := "", g.acting == ""
	switch m.screen {
	case scMatch:
		if g.catalog == nil {
			// The match catalog is still on its way: the button waits.
			m.primary(p, reviewRow{"play", m.t("m.play"), ""}, true, false, g.err == nil, "")
			return p
		}
		rows = m.matchRows()
		label := m.t("m.play")
		if g.acting == "play" {
			label = m.t("m.opening")
		}
		enabled = enabled && g.match.Map != ""
		if g.match.Channel != "" {
			right = "ezQuake " + g.match.Channel + "  ↵"
		}
		rows[len(rows)-1].label = label
	case scHost:
		rows = m.hostRows()
		right = rows[len(rows)-1].summary
		enabled = enabled && !m.stackChanging()
	}
	if len(rows) == 0 {
		return p
	}
	last := rows[len(rows)-1]
	m.primary(p, last, m.focus == len(rows)-1, enabled, m.actingHere() || m.stackChanging(), right)
	return p
}

// serverBar is the servers' action row: the focused server's main action,
// with the others beside it and their keys.
func (m *Model) serverBar(p *page) {
	server := m.focusedServer()
	right := "ctrl+o " + m.t("k.observe") + "   ctrl+f " + m.t("k.favorite")
	if server != nil && server.QTV != "" {
		right = "ctrl+w " + m.t("k.qtv") + "   " + right
	}
	enabled := server != nil && m.game.acting == ""
	label := m.t("k.join")
	if server != nil {
		label += " " + ansi.Truncate(server.Title, 40, "…")
	}
	m.primary(p, reviewRow{"act:join", label, ""}, true, enabled, m.actingHere(), right)
}

func (m *Model) activateMatch(id string) tea.Cmd {
	if id == "m.player" {
		// The player's section of the setup, with the way back to here.
		return m.openSetupAt(scMatch, "m.player", "config")
	}
	if id != "play" {
		m.openModal(id)
		return nil
	}
	if m.game.acting != "" || m.game.match.Map == "" {
		return nil
	}
	backend, target, match := m.cfg.Backend, m.target(), m.game.match
	m.game.mapNote = ""
	return m.act("play", func(ctx context.Context) error { return backend.Play(ctx, target, match) })
}

// gameModalRows are the editors of the match sections.
func (m *Model) gameModalRows() []formRow {
	c, match := m.game.catalog, &m.game.match
	switch m.modal.kind {
	case "m.game":
		var rows []formRow
		for _, game := range c.Games {
			game := game
			rows = append(rows, formRow{id: "g." + game.ID, kind: rowRadio, label: game.Label, desc: m.label("game", game.ID+".d", game.Description), on: match.Game == game.ID,
				activate: func() { m.setGame(game.ID) }})
		}
		return rows
	case "m.mode":
		var recommended []string
		for _, group := range c.Groups {
			if group.Key == "recommended" {
				recommended = group.Modes
			}
		}
		var first, rest []formRow
		for _, mode := range c.Modes {
			mode := mode
			if !m.matchesFilter(mode.Label, mode.ID, m.label("mode", mode.ID+".d", mode.Description)) {
				continue
			}
			row := formRow{id: "mode." + mode.ID, kind: rowRadio, label: mode.Label, desc: playersLabel(m, mode.Players), on: match.Mode == mode.ID,
				activate: func() {
					if match.Mode != mode.ID {
						match.Mode = mode.ID
						m.clearOptions()
					}
				}}
			if slices.Contains(recommended, mode.ID) {
				first = append(first, row)
			} else {
				rest = append(rest, row)
			}
		}
		return m.picked(first, rest, "pick.recommended", "pick.all")
	case "m.map":
		var first, rest []formRow
		if list := m.game.maps; list != nil {
			for _, name := range list.Maps {
				name := name
				if !m.matchesFilter(name) {
					continue
				}
				row := formRow{id: "map." + name, kind: rowRadio, label: name, on: match.Map == name, activate: func() { match.Map = name; m.game.mapNote = "" }}
				if list.Suggested[name] {
					first = append(first, row)
				} else {
					rest = append(rest, row)
				}
			}
		}
		return m.picked(first, rest, "pick.suggested", "pick.other")
	case "m.opts":
		return m.optionRows()
	case "m.rules":
		rows := []formRow{{kind: rowHeading, label: m.t("m.rules")}}
		for _, key := range setup.RulesetKeys {
			key := key
			rows = append(rows, formRow{id: "rs." + key, kind: rowRadio, label: m.ruleset(key), desc: m.t("rs." + key + ".d"), on: match.Ruleset == key, activate: func() { match.Ruleset = key }})
		}
		if len(c.Channels) > 1 {
			rows = append(rows, formRow{kind: rowHeading, label: m.t("d.client")})
			for _, channel := range c.Channels {
				channel := channel
				rows = append(rows, formRow{id: "ch." + channel, kind: rowRadio, label: "ezQuake " + channel, on: match.Channel == channel, activate: func() { match.Channel = channel }})
			}
		}
		return rows
	}
	return nil
}

// picked lays a searchable list out: the filter, then two groups.
func (m *Model) picked(first, rest []formRow, firstLabel, restLabel string) []formRow {
	filter := m.modal.field["filter"].String()
	hint := m.t("pick.type")
	if filter != "" {
		hint = filter + "▏"
	}
	rows := []formRow{{kind: rowInfo, label: m.t("pick.search"), value: hint, tone: "dim"}}
	if len(first) > 0 {
		rows = append(append(rows, formRow{kind: rowHeading, label: m.t(firstLabel)}), first...)
	}
	if len(rest) > 0 {
		rows = append(append(rows, formRow{kind: rowHeading, label: m.t(restLabel)}), rest...)
	}
	if len(first)+len(rest) == 0 {
		rows = append(rows, formRow{kind: rowInfo, label: "", value: m.t("pick.nothing"), tone: "dim"})
	}
	return rows
}

// matchesFilter reports whether any of the words contains the filter.
func (m *Model) matchesFilter(words ...string) bool {
	if m.modal == nil || m.modal.field["filter"] == nil {
		return true
	}
	filter := strings.ToLower(strings.TrimSpace(m.modal.field["filter"].String()))
	if filter == "" {
		return true
	}
	for _, word := range words {
		if strings.Contains(strings.ToLower(word), filter) {
			return true
		}
	}
	return false
}

// optionRows are the mode's options: bots, or the race or CTF rules.
func (m *Model) optionRows() []formRow {
	mode, match := m.matchMode(), &m.game.match
	if mode == nil {
		return nil
	}
	cycle := func(values []string, current string, delta int) string {
		index := slices.Index(values, current)
		if index < 0 {
			index = 0
		}
		return values[(index+delta+len(values))%len(values)]
	}
	show := func(value string) string { return or(value, m.t("mode.default")) }
	switch mode.Kind {
	case "race":
		rows := []formRow{
			{id: "race.style", kind: rowStepper, label: m.t("race.style"), value: m.t("race." + or(match.RaceStyle, "default")), step: func(d int) {
				match.RaceStyle = cycle([]string{"", "solo", "simultaneous", "match"}, match.RaceStyle, d)
				if match.RaceStyle != "match" {
					match.RaceScoring = ""
				}
			}},
		}
		if match.RaceStyle == "match" {
			rows = append(rows, formRow{id: "race.scoring", kind: rowStepper, label: m.t("race.scoring"), value: show(match.RaceScoring), step: func(d int) {
				match.RaceScoring = cycle([]string{"", "win", "scaled", "formula1"}, match.RaceScoring, d)
			}})
		}
		pacemaker := m.t("off")
		if match.RacePacemaker > 0 {
			pacemaker = strconv.Itoa(match.RacePacemaker)
		}
		return append(rows,
			formRow{id: "race.pacemaker", kind: rowStepper, label: m.t("race.pacemaker"), value: pacemaker, step: func(d int) { match.RacePacemaker = (match.RacePacemaker + d + 11) % 11 }},
			formRow{id: "race.hide", kind: rowToggle, label: m.t("race.hide"), on: match.RaceHide, activate: func() { match.RaceHide = !match.RaceHide }})
	case "ctf":
		return []formRow{
			{id: "ctf.hook", kind: rowStepper, label: m.t("ctf.hook"), value: show(match.CTFHook), step: func(d int) {
				match.CTFHook = cycle([]string{"", "smooth", "fast", "classic", "crhook", "off"}, match.CTFHook, d)
			}},
			{id: "ctf.runes", kind: rowStepper, label: m.t("ctf.runes"), value: show(match.CTFRunes), step: func(d int) {
				match.CTFRunes = cycle([]string{"", "on", "off"}, match.CTFRunes, d)
			}},
			{id: "ctf.based", kind: rowToggle, label: m.t("ctf.based"), on: match.CTFBased, activate: func() { match.CTFBased = !match.CTFBased }},
		}
	}
	// Frogbots: a fixed roster fills its places; an open mode takes any
	// number up to 31, or fills the server.
	counts := []string{"0", strconv.Itoa(mode.BotLimit)}
	if mode.BotLimit == 0 {
		counts = []string{"0"}
		for number := 1; number <= 31; number++ {
			counts = append(counts, strconv.Itoa(number))
		}
		counts = append(counts, "fill")
	}
	current := strconv.Itoa(match.Bots)
	if match.FillBots {
		current = "fill"
	}
	rows := []formRow{{id: "bots", kind: rowStepper, label: m.t("bots"), value: m.botsCount(), step: func(d int) {
		next := cycle(counts, current, d)
		match.FillBots = next == "fill"
		match.Bots, _ = strconv.Atoi(next)
	}}}
	if !match.HasBots() {
		return rows
	}
	skills := []string{"random"}
	for number := 1; number <= 20; number++ {
		skills = append(skills, strconv.Itoa(number))
	}
	rows = append(rows,
		formRow{id: "bot.skill", kind: rowStepper, label: m.t("bot.skill"), value: match.BotSkill, step: func(d int) { match.BotSkill = cycle(skills, match.BotSkill, d) }},
		formRow{id: "bot.names", kind: rowStepper, label: m.t("bot.names"), value: m.t("names." + or(match.BotName, "default")), step: func(d int) {
			match.BotName = cycle([]string{"default", "x86qw", "one-piece", "personal"}, or(match.BotName, "default"), d)
		}})
	if mode.Kind == "tot" {
		weapons := []string{"", "random", "1", "2", "3", "4", "5", "6", "7", "8"}
		healths := []string{"0", "50", "100", "150", "200", "250", "300"}
		health := m.t("mode.default")
		if match.BotHealth > 0 {
			health = strconv.Itoa(match.BotHealth)
		}
		rows = append(rows,
			formRow{id: "bot.weapon", kind: rowStepper, label: m.t("bot.weapon"), value: show(match.BotWeapon), step: func(d int) { match.BotWeapon = cycle(weapons, match.BotWeapon, d) }},
			formRow{id: "bot.health", kind: rowStepper, label: m.t("bot.health"), value: health, step: func(d int) {
				match.BotHealth, _ = strconv.Atoi(cycle(healths, strconv.Itoa(match.BotHealth), d))
			}},
			formRow{id: "bot.break", kind: rowStepper, label: m.t("bot.break"), value: show(match.BotBreak), step: func(d int) {
				match.BotBreak = cycle([]string{"", "on", "off"}, match.BotBreak, d)
			}})
	}
	return rows
}

func (m *Model) botsCount() string {
	match := m.game.match
	switch {
	case match.FillBots:
		return m.t("bots.fill.short")
	case match.Bots == 0:
		return m.t("bots.none.match")
	}
	return strconv.Itoa(match.Bots)
}

// serversPage lists the public servers, filtered as the user types.
func (m *Model) serversPage(p *page) {
	t, g := m.theme, m.game
	p.title(m.t("servers.title"))
	switch {
	case g.serversLoading && g.servers == nil:
		p.lead(m.spinner() + "  " + m.t("servers.loading"))
		return
	case g.serversErr != nil:
		p.add(p.st(t.Error).Render("  " + m.glyphs.Fail + "  " + m.explain(g.serversErr)))
		return
	}
	p.lead(fmt.Sprintf(m.t("servers.lead"), len(g.servers)))
	p.gap(1)
	filter := g.filter.String()
	label := p.st(t.Gray).Render("  " + m.t("pick.search") + "  ")
	if filter == "" {
		p.add(label + p.st(t.Dim).Render(m.t("pick.type")))
	} else {
		p.add(label + p.st(t.Text).Render(filter+"▏"))
	}
	p.gap(1)
	servers := m.visibleServers()
	if len(servers) == 0 {
		p.add(p.st(t.Dim).Render("  " + m.t("pick.nothing")))
		return
	}
	m.focus = min(m.focus, len(servers)-1)
	for index, server := range servers {
		focused, hovered := index == m.focus, m.hover == fmt.Sprintf("row:srv%d", index)
		bg := p.rowBG(focused, hovered)
		base := lipgloss.NewStyle().Background(bg)
		mark := base.Render("  ")
		if server.Favorite {
			mark = base.Foreground(t.Warning).Render("★ ")
		}
		titleColor := t.Text2
		if focused {
			titleColor = t.Text
		}
		players := fmt.Sprintf("%d", server.Humans)
		if server.Bots > 0 {
			players += fmt.Sprintf("+%d", server.Bots)
		}
		right := base.Foreground(t.Gray).Render(fmt.Sprintf("%-9s %-6s %5s", server.Map, server.Mode, players))
		if server.QTV != "" {
			right += base.Foreground(t.Success).Render("  " + m.t("qtv.badge"))
		} else {
			right += base.Render(strings.Repeat(" ", 2+lipgloss.Width(m.t("qtv.badge"))))
		}
		right += base.Render("  ")
		room := p.width - lipgloss.Width(right) - 6
		left := base.Render("  ") + mark + base.Foreground(titleColor).Bold(focused).Render(ansi.Truncate(server.Title, room, "…"))
		start := len(p.lines)
		p.band(spread(left, right, p.width, bg), bg, fmt.Sprintf("row:srv%d", index))
		if focused {
			p.add(p.st(t.Base).Render("    ") + p.st(t.Dim).Render(server.Address))
			p.keep(start, len(p.lines)-1)
		}
	}
}

// visibleServers are the servers matching the filter, favorites first.
func (m *Model) visibleServers() []setup.PublicServer {
	filter := strings.ToLower(strings.TrimSpace(m.game.filter.String()))
	var out []setup.PublicServer
	for _, server := range m.game.servers {
		text := strings.ToLower(server.Title + " " + server.Address + " " + server.Map + " " + server.Mode)
		if filter == "" || strings.Contains(text, filter) {
			out = append(out, server)
		}
	}
	slices.SortStableFunc(out, func(a, b setup.PublicServer) int {
		switch {
		case a.Favorite && !b.Favorite:
			return -1
		case b.Favorite && !a.Favorite:
			return 1
		}
		return 0
	})
	return out
}

// focusedServer is the server in focus, nil when there is none.
func (m *Model) focusedServer() *setup.PublicServer {
	servers := m.visibleServers()
	if m.focus >= len(servers) {
		return nil
	}
	return &servers[m.focus]
}

// joinServer connects to the focused server: to play, or to spectate on
// the game server itself.
func (m *Model) joinServer(observe bool) tea.Cmd {
	server := m.focusedServer()
	if server == nil || m.game.acting != "" {
		return nil
	}
	address := server.Address
	if observe {
		address += "/observe"
	}
	backend, target := m.cfg.Backend, m.target()
	m.game.subject = server.Title
	return m.act("join", func(ctx context.Context) error { return backend.Open(ctx, target, address) })
}

// watchQTV opens the focused server's QTV stream.
func (m *Model) watchQTV() tea.Cmd {
	server := m.focusedServer()
	if server == nil || m.game.acting != "" {
		return nil
	}
	if server.QTV == "" {
		m.say(m.t("qtv.none"))
		return nil
	}
	backend, target, stream := m.cfg.Backend, m.target(), server.QTV+"/qtvplay"
	m.game.subject = server.Title
	return m.act("watch", func(ctx context.Context) error { return backend.Open(ctx, target, stream) })
}

// toggleFavorite pins or unpins the focused server.
func (m *Model) toggleFavorite() tea.Cmd {
	server := m.focusedServer()
	if server == nil || m.game.acting != "" {
		return nil
	}
	backend, target, address, on := m.cfg.Backend, m.target(), server.Address, !server.Favorite
	what := "favorite"
	if !on {
		what = "unfavorite"
	}
	m.game.subject = address
	return m.act(what, func(ctx context.Context) error { return backend.Favorite(ctx, target, address, on) })
}

// serversHints are the keys of the servers screen as it stands.
func (m *Model) serversHints() [][2]string {
	g := m.game
	back := [2]string{"esc", m.t("k.contexts")}
	if g.filter.String() != "" {
		back = [2]string{"esc", m.t("k.clear")}
	}
	switch {
	case g.serversLoading && g.servers == nil:
		return [][2]string{back}
	case g.serversErr != nil:
		return [][2]string{{"ctrl+r", m.t("k.retry")}, back}
	case len(m.visibleServers()) == 0:
		return [][2]string{{"abc", m.t("k.filter")}, back}
	}
	// The arrows go without saying here: the footer keeps room for esc.
	items := [][2]string{{"enter", m.t("k.join")}, {"ctrl+f", m.t("k.favorite")}}
	if server := m.focusedServer(); server != nil && server.QTV != "" {
		items = append(items, [2]string{"ctrl+w", m.t("k.qtv")})
	}
	return append(items, [2]string{"ctrl+o", m.t("k.observe")}, [2]string{"ctrl+r", m.t("k.refresh")}, back)
}

// serverInstalled reports whether the installation has MVDSV.
func (m *Model) serverInstalled() bool {
	return m.existing == nil || slices.Contains(m.existing.Installed, "mvdsv")
}

// stackChanging is a session starting or stopping on its own.
func (m *Model) stackChanging() bool {
	return m.game.stack != nil && (m.game.stack.Status == "starting" || m.game.stack.Status == "stopping")
}

// instancesUp counts the game servers that answer.
func (m *Model) instancesUp() int {
	up := 0
	for _, process := range m.game.stack.Processes {
		if process.Alive && strings.HasPrefix(process.Label, "MVDSV") {
			up++
		}
	}
	return up
}

// hostRows are the server screen: its sections, then its action.
func (m *Model) hostRows() []reviewRow {
	switch {
	case !m.serverInstalled():
		return []reviewRow{{"h.add", m.t("host.add"), ""}}
	case m.game.stack != nil:
		label := m.t("host.stop")
		switch {
		case m.game.acting == "stop" || m.game.stack.Status == "stopping":
			label = m.t("host.stopping")
		case m.game.stack.Status == "starting":
			label = m.t("host.starting")
		}
		return []reviewRow{{"h.stop", label, ""}}
	}
	label := m.t("host.start")
	if m.game.acting == "start" {
		label = m.t("host.starting")
	}
	rows := m.sectionRows()
	var out []reviewRow
	for _, row := range rows {
		if row.id == "server" {
			out = append(out, row)
		}
	}
	out = append(out, reviewRow{"h.solo", m.t("host.solo"), m.t("host.solo.d")})
	return append(out, reviewRow{"h.start", label, fmt.Sprintf("%d %s", len(m.opts.Server.Instances), m.t("d.instancesN"))})
}

func (m *Model) hostPage(p *page) {
	t, g := m.theme, m.game
	p.title(m.t("host.title"))
	switch {
	case !m.serverInstalled():
		p.lead(m.t("host.missing"))
		return
	case !g.stackLoaded:
		p.lead(m.spinner() + "  " + m.t("host.loading"))
		return
	case g.stack != nil:
		switch g.stack.Status {
		case "starting":
			p.lead(m.t("host.state.starting"))
		case "stopping":
			p.lead(m.t("host.state.stopping"))
		default:
			p.lead(fmt.Sprintf(m.t("host.running"), m.instancesUp(), m.connectHost()))
		}
		p.gap(1)
		host := m.connectHost()
		for _, process := range g.stack.Processes {
			glyph, color := m.glyphs.Ok, t.Success
			if !process.Alive {
				glyph, color = m.glyphs.Fail, t.Error
			}
			what := strings.TrimSpace(strings.Join([]string{process.Mode, process.Map}, " · "))
			what = strings.Trim(what, " ·")
			left := p.st(t.Base).Render("  ") + p.st(color).Bold(true).Render(glyph) + p.st(t.Text2).Render("  "+process.Label)
			right := p.st(t.Gray).Render(what+"  ") + p.st(t.Text).Render(fmt.Sprintf("%s:%d", host, process.Port)) + p.st(t.Base).Render("  ")
			p.add(spread(left, right, p.width, t.Base))
		}
		return
	}
	p.lead(m.t("host.lead"))
	p.gap(1)
	rows := m.hostRows()
	m.accordion(p, rows[:len(rows)-1])
}

func (m *Model) activateHost(id string) tea.Cmd {
	backend, target := m.cfg.Backend, m.target()
	switch id {
	case "server":
		m.openModal("server")
	case "h.solo":
		m.openModal("solo")
	case "h.add":
		// The server programs come with the components of a host; esc
		// puts the choices back and returns here.
		m.openSetup(scHost, "h.add")
		m.opts.Intent = setup.Both
	case "h.start":
		if m.game.acting == "" {
			if err := m.canHost(); err != nil {
				m.game.actionErr, m.game.errOn = err, scHost
				return nil
			}
			options := m.opts
			return m.act("start", func(ctx context.Context) error { return backend.StartServer(ctx, target, options) })
		}
	case "h.stop":
		// Stopping drops every player: it asks for a second press.
		if m.game.acting == "" && !m.stackChanging() && m.armedFor("stop") {
			return m.act("stop", func(ctx context.Context) error { return backend.StopServer(ctx, target) })
		}
	}
	return nil
}

// canHost is why the server cannot start from here, nil when it can.
func (m *Model) canHost() error {
	if err := m.opts.Server.Validate(); err != nil {
		return err
	}
	if target := m.target(); setup.PathTooLong(target) {
		return &setup.Problem{Text: setup.Say("v.path", len(target), setup.MVDSVPathRoom)}
	}
	return nil
}

// soloRows start QTV or QWFWD without a game server.
func (m *Model) soloRows() []formRow {
	backend, target, options := m.cfg.Backend, m.target(), m.opts
	start := func(kind string) func() {
		return func() {
			closed := m.closeModal(true)
			if err := options.Server.Validate(); err != nil {
				m.game.actionErr, m.game.errOn, m.pending = err, scHost, closed
				return
			}
			m.pending = tea.Batch(closed, m.act("start", func(ctx context.Context) error {
				return backend.StartService(ctx, target, kind, options)
			}))
		}
	}
	return []formRow{
		{kind: rowInfo, label: "", value: m.t("host.solo.lead"), tone: "dim"},
		{id: "solo.qtv", kind: rowAction, label: m.t("host.solo.qtv"), desc: m.t("qtv.d"), activate: start("qtv")},
		{id: "solo.proxy", kind: rowAction, label: m.t("host.solo.proxy"), desc: m.t("qwfwd.d"), activate: start("proxy")},
	}
}

// toolRows list the tools; each opens its floating panel (tools.go).
func (m *Model) toolRows() []formRow {
	var rows []formRow
	for index := range tools {
		spec := &tools[index]
		rows = append(rows, formRow{id: "tool." + spec.name, kind: rowAction, label: m.t("tool." + spec.name), desc: m.t("tool." + spec.name + ".d"), activate: func() {
			m.pending = m.openTool(spec)
		}})
	}
	return rows
}

// openScreen moves to one of the app's screens and loads what it shows.
func (m *Model) openScreen(s screen) tea.Cmd {
	m.screen, m.focus = s, 0
	if m.game.errOn == s {
		m.game.actionErr = nil
	}
	switch s {
	case scMatch:
		if m.game.catalog == nil {
			return m.loadMatch()
		}
		return m.loadMaps()
	case scServers:
		m.game.filter = NewField("", 40)
		if m.game.servers == nil || time.Since(m.game.serversAt) > serversFresh {
			return m.loadServers()
		}
	case scHost:
		return tea.Batch(m.loadStack(), m.pollStack())
	}
	return nil
}

// gameKey handles the keys of the app's screens.
func (m *Model) gameKey(msg tea.KeyPressMsg) tea.Cmd {
	key := msg.String()
	switch m.screen {
	case scMatch, scHost:
		var rows []reviewRow
		activate := m.activateMatch
		if m.screen == scMatch {
			if m.game.catalog == nil {
				if key == "esc" {
					return m.toRail()
				}
				return nil
			}
			rows = m.matchRows()
		} else {
			rows, activate = m.hostRows(), m.activateHost
		}
		if m.screen == scHost && !m.game.stackLoaded {
			if key == "esc" {
				return m.toRail()
			}
			return nil
		}
		m.focus = min(m.focus, len(rows)-1)
		if listNav(key, &m.focus, len(rows)) {
			return nil
		}
		switch key {
		case "enter", "space", " ", "right":
			return activate(rows[m.focus].id)
		case "p":
			if m.screen == scMatch {
				return m.activateMatch("play")
			}
		case "c":
			if m.screen == scMatch {
				return m.copyText(shellJoin(append([]string{"x86qw"}, m.game.match.Args(m.target())...)))
			}
		case "esc", "left", "backspace":
			return m.toRail()
		}
	case scServers:
		servers := m.visibleServers()
		switch key {
		case "up":
			if len(servers) > 0 {
				m.focus = (m.focus + len(servers) - 1) % len(servers)
			}
		case "down", "tab":
			if len(servers) > 0 {
				m.focus = (m.focus + 1) % len(servers)
			}
		case "enter":
			return m.joinServer(false)
		case "ctrl+o":
			return m.joinServer(true)
		case "ctrl+f":
			return m.toggleFavorite()
		case "ctrl+w":
			return m.watchQTV()
		case "ctrl+r":
			return m.loadServers()
		case "esc":
			if m.game.filter.String() != "" {
				m.game.filter.Set("")
				m.focus = 0
				return nil
			}
			return m.toRail()
		default:
			if m.game.filter.Key(msg) {
				m.focus = 0
			}
		}
	}
	return nil
}

func (m *Model) copyText(text string) tea.Cmd {
	m.say(m.t("copied"))
	return tea.SetClipboard(text)
}
