package tui

import (
	"context"
	_ "embed"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	tea "charm.land/bubbletea/v2"
	"charm.land/lipgloss/v2"
	"github.com/charmbracelet/x/ansi"

	"github.com/x86dx2/x86qw/go/internal/setup"
)

type screen int

const (
	scIntent screen = iota
	scConfigure
	scInstall
	scDone
	scPanel // the home screen of an installation
	scMatch
	scServers
	scHost
)

// Config starts the installer.
type Config struct {
	Backend setup.Backend
	Target  string
	Lang    Lang
	Demo    bool
	Theme   string // "", "dark" or "light"; "" follows the terminal
	// Start opens an installation on one of its screens instead of home:
	// match, servers or host.
	Start string
}

// Result is how the interface ended: the x86qw command to run next, if
// any, and whether the interface should open again after it, in the
// language and theme it had.
type Result struct {
	Next  []string
	Back  bool
	Lang  Lang
	Theme string
}

// Layout and timing.
const (
	maxWidth      = 74 // the widest the body grows
	minWidth      = 60 // smaller terminals get a note instead
	minHeight     = 18
	toastFor      = 2600 * time.Millisecond
	armedFor      = time.Second // how long a double press waits
	logoInterval  = 83 * time.Millisecond
	spinInterval  = 33 * time.Millisecond
	advanceAfter  = 900 * time.Millisecond // the finished install stays on screen
	dnsDebounce   = 600 * time.Millisecond
	stackInterval = 2 * time.Second
	serversFresh  = time.Minute
)

// Model is the installer program.
type Model struct {
	cfg     Config
	next    []string // the x86qw command to run once the interface closes
	ticking bool     // the tick loop is scheduled
	back    bool     // and open again once it ends
	game    gameState
	// cancelling is a cancelled installation still rolling back.
	cancelling bool
	// reloading is a finished installation being read again.
	reloading bool
	theme     Theme
	glyphs    Glyphs
	lang      Lang
	themed    bool // the theme was chosen explicitly or detected

	w, h int

	screen   screen
	catalog  *setup.Catalog
	existing *setup.Existing
	opts     setup.Options
	err      error

	intent   int
	nick     Field
	nickFlag bool

	checks    []setup.Check
	preDone   bool
	preRun    int  // counts preflight runs, so late checks of an old run are ignored
	preOn     bool // Install was pressed: the preflight shows on the setup screen
	preCh     <-chan setup.Check
	preCancel context.CancelFunc
	// confirmErr is why Install refused the choices; it stays under the
	// setup until they change.
	confirmErr error

	focus     int  // focused row on the current screen
	fromPanel bool // the setup was opened from the app, not the installer
	// returnTo and returnRow are where esc from the setup goes back to, and
	// saved the choices to restore then.
	returnTo  screen
	returnRow string
	saved     *setup.Options

	modal *modal

	progress setup.Progress
	instCh   <-chan setup.Progress
	control  chan bool
	cancel   context.CancelFunc
	showLog  bool
	instAt   time.Time

	network    *setup.Network
	dns        *setup.DNSCheck
	dnsFor     string
	dnsPending string

	panelView string
	// The panel's repair and uninstall: running, then what came back.
	busy         bool
	report       *setup.Report
	panelErr     error
	removed      bool
	keepPersonal bool

	toast      string
	toastUntil time.Time
	armed      string
	armedKey   string // the key that armed it, as the footer names it
	armedUntil time.Time

	pending tea.Cmd
	scroll  map[screen]int
	tops    map[screen]int // where each screen's body starts; it only moves up
	speeds  []float64

	hits    []hit
	hover   string
	started time.Time
}

type modal struct {
	kind    string
	tab     int
	focus   int
	capture string // movement action waiting for a key
	field   map[string]*Field
	json    bool
	height  int // the tallest the dialog has been, so it never jumps
	// before is what the choices were when the dialog opened; esc puts
	// them back.
	before     setup.Options
	match      setup.Match // the match as it was, for the match editors
	dns        *setup.DNSCheck
	dnsPending string
}

// New builds the installer model.
func New(cfg Config) *Model {
	if cfg.Lang == "" {
		cfg.Lang = PT
	}
	m := &Model{cfg: cfg, glyphs: DetectGlyphs(), lang: cfg.Lang, keepPersonal: true, started: time.Now(), scroll: map[screen]int{}, tops: map[screen]int{}}
	switch cfg.Theme {
	case "light":
		m.theme, m.themed = Day(), true
	case "dark":
		m.theme, m.themed = Night(), true
	default:
		m.theme = Night()
	}
	m.opts = setup.Defaults(setup.Play, cfg.Target)
	m.nick = NewField("", 15)
	m.nick.Accept = func(r rune) bool { return r > ' ' && setup.NameRune(r) }
	return m
}

type catalogMsg struct {
	catalog  *setup.Catalog
	existing *setup.Existing
	err      error
}

// reloadMsg is the installation read again after an install.
type reloadMsg catalogMsg
type checkMsg struct {
	check setup.Check
	ok    bool
	run   int
}

// panelMsg is how a repair or an uninstall from the panel ended.
type panelMsg struct {
	report  *setup.Report
	removed bool
	err     error
}

type progressMsg struct {
	progress setup.Progress
	ok       bool
}
type networkMsg setup.Network
type dnsMsg setup.DNSCheck
type dnsDebounceMsg string
type tickMsg time.Time
type advanceMsg screen

// Init loads the catalog, looks for an installation, detects the network
// and asks the terminal for its background.
func (m *Model) Init() tea.Cmd {
	backend := m.cfg.Backend
	cmds := []tea.Cmd{
		m.loadCatalog(),
		func() tea.Msg {
			ctx, cancel := context.WithTimeout(context.Background(), 12*time.Second)
			defer cancel()
			return networkMsg(backend.Network(ctx))
		},
		m.tick(),
	}
	if !m.themed {
		cmds = append(cmds, tea.RequestBackgroundColor)
	}
	return tea.Batch(cmds...)
}

// loadCatalog reads the catalog and the installation at the target. A
// catalog that cannot be published still lists the components, and an
// installation opens without it.
func (m *Model) loadCatalog() tea.Cmd {
	backend, target := m.cfg.Backend, m.cfg.Target
	return func() tea.Msg {
		ctx := context.Background()
		catalog, err := backend.Catalog(ctx)
		if err != nil {
			return catalogMsg{err: err}
		}
		existing, err := backend.Existing(ctx, target)
		return catalogMsg{catalog, existing, err}
	}
}

// reload reads the installation again once an install ends.
func (m *Model) reload() tea.Cmd {
	m.reloading = true
	backend, target := m.cfg.Backend, m.opts.Target
	return func() tea.Msg {
		ctx := context.Background()
		catalog, err := backend.Catalog(ctx)
		if err != nil {
			return reloadMsg{err: err}
		}
		existing, err := backend.Existing(ctx, target)
		return reloadMsg{catalog, existing, err}
	}
}

// tick drives animation only while something moves: the logo shimmer at
// about 12 fps, spinners and progress at 30 fps.
// There is one tick loop at most: it runs while something on screen
// moves and stops when the screen is still.
func (m *Model) tick() tea.Cmd {
	if m.ticking {
		return nil
	}
	m.ticking = true
	interval := logoInterval
	if m.spinning() {
		interval = spinInterval
	}
	return tea.Tick(interval, func(t time.Time) tea.Msg { return tickMsg(t) })
}

// spinning is a spinner or a progress bar on screen.
func (m *Model) spinning() bool {
	return (m.screen == scConfigure && m.preOn && !m.preDone) || m.screen == scInstall || (m.screen == scPanel && m.busy) || m.game.acting != "" || m.reloading ||
		m.game.serversLoading || (m.screen == scMatch && m.game.catalog == nil) || (m.screen == scHost && !m.game.stackLoaded) ||
		(m.catalog == nil && m.err == nil)
}

// moving is anything that changes without input: the logo's shimmer,
// a spinner or progress, a toast or an armed key waiting to expire.
func (m *Model) moving() bool {
	now := time.Now()
	logo := m.err == nil && (m.screen == scIntent || (m.screen == scPanel && m.panelView == "")) && m.modal == nil
	return logo || m.spinning() || now.Before(m.toastUntil) || (m.armed != "" && now.Before(m.armedUntil))
}

func (m *Model) t(key string) string { return T(m.lang, key) }

func (m *Model) say(text string) {
	m.toast, m.toastUntil = text, time.Now().Add(toastFor)
}

// explain is an error in the interface language.
func (m *Model) explain(err error) string { return setup.Explain(err, string(m.lang)) }

// in is a backend text in the interface language.
func (m *Model) in(text setup.Text) string { return text.In(string(m.lang)) }

// size writes bytes the way the interface language writes decimals.
func (m *Model) size(b int64) string { return setup.FormatSize(b, string(m.lang)) }

// label is a data text (component, game, mode) in the interface language.
func (m *Model) label(kind, id, text string) string {
	return setup.Localize(string(m.lang), kind, id, text)
}

// Update handles one message.
func (m *Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	if _, ok := msg.(tickMsg); ok {
		m.ticking = false
	}
	model, cmd := m.update(msg)
	if m.moving() && !m.ticking {
		cmd = tea.Batch(cmd, m.tick())
	}
	return model, cmd
}

func (m *Model) update(msg tea.Msg) (tea.Model, tea.Cmd) {
	if cmd, handled := m.gameUpdate(msg); handled {
		return m, cmd
	}
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.w, m.h = msg.Width, msg.Height
		clear(m.tops)
	case tea.BackgroundColorMsg:
		if !m.themed {
			if msg.IsDark() {
				m.theme = Night()
			} else {
				m.theme = Day()
			}
			m.themed = true
		}
	case catalogMsg:
		if msg.err == nil && msg.existing == nil && msg.catalog.Unavailable != nil {
			// Nothing installed and nothing to install from: say why; r
			// tries again.
			msg.err = msg.catalog.Unavailable
		}
		if msg.err != nil {
			m.err = msg.err
			break
		}
		m.catalog, m.existing = msg.catalog, msg.existing
		if m.existing != nil {
			m.opts = m.existing.Options
			m.nick.Set(m.opts.Nick)
			m.screen = scPanel
			if !m.existing.Note.IsZero() {
				m.say(m.in(m.existing.Note))
			}
			// Home shows the last match, the public servers and the
			// server's state; the screen asked for opens at once.
			cmds := []tea.Cmd{m.loadMatch(), m.loadStack(), m.loadServers()}
			switch m.cfg.Start {
			case "match":
				m.screen = scMatch
			case "servers":
				cmds = append(cmds, m.openScreen(scServers))
			case "host":
				cmds = append(cmds, m.openScreen(scHost))
			}
			return m, tea.Batch(cmds...)
		}
	case networkMsg:
		network := setup.Network(msg)
		m.network = &network
		if m.opts.Server.Hostname != "" {
			return m, m.resolve(m.opts.Server.Hostname)
		}
	case dnsDebounceMsg:
		if string(msg) == m.dnsPending && string(msg) == m.opts.Server.Hostname {
			return m, m.resolve(string(msg))
		}
	case dnsMsg:
		check := setup.DNSCheck(msg)
		if check.Hostname == m.opts.Server.Hostname {
			m.dns = &check
		}
	case reloadMsg:
		m.reloading = false
		if msg.err == nil && msg.existing != nil {
			m.catalog, m.existing = msg.catalog, msg.existing
			m.opts = m.existing.Options
		}
		if m.screen != scInstall {
			break
		}
		// From the app, the run ends at home; a new installation, or one
		// with notes to read, ends on the last step of the installer.
		if m.fromPanel && m.existing != nil && len(m.progress.Notes) == 0 {
			m.screen, m.panelView, m.saved, m.opts.Upgrade = m.returnTo, "", nil, false
			m.focusRow(m.returnRow)
			m.say(m.t("done.applied"))
			return m, tea.Batch(m.loadMatch(), m.loadStack())
		}
		m.screen, m.focus, m.saved = scDone, 0, nil
	case checkMsg:
		if !msg.ok {
			if msg.run == m.preRun {
				m.preDone = true
			}
			break
		}
		if msg.run != m.preRun {
			break // a check from a run that was restarted
		}
		m.recordCheck(msg.check)
		return m, m.nextCheck()
	case panelMsg:
		m.busy, m.report, m.panelErr = false, msg.report, msg.err
		if msg.removed && msg.err == nil {
			m.removed = true
		}
	case progressMsg:
		if !msg.ok {
			break
		}
		if m.screen != scInstall {
			break
		}
		if m.cancelling {
			// Wait for the rollback: the installation ends with the
			// folder as it was, then the interface steps back.
			if msg.ok && !msg.progress.Finished && msg.progress.Err == nil {
				return m, m.nextProgress()
			}
			m.cancelling = false
			if !msg.progress.Finished {
				m.leaveInstall()
				m.say(m.t("cancelled"))
				break
			}
		}
		m.progress = msg.progress
		if m.progress.Err != nil {
			break
		}
		if m.progress.Finished {
			return m, tea.Tick(advanceAfter, func(time.Time) tea.Msg { return advanceMsg(scDone) })
		}
		return m, m.nextProgress()
	case advanceMsg:
		if m.screen == scInstall && !m.reloading {
			return m, m.reload()
		}
	case tickMsg:
		return m, nil
	case tea.PasteMsg:
		if field := m.activeField(); field != nil {
			field.Insert(msg.Content)
			return m, m.fieldChanged()
		}
	case tea.MouseMotionMsg:
		m.hover = m.hitAt(msg.Mouse().X, msg.Mouse().Y)
	case tea.MouseClickMsg:
		if msg.Mouse().Button == tea.MouseLeft {
			return m, m.click(m.hitAt(msg.Mouse().X, msg.Mouse().Y))
		}
	case tea.MouseWheelMsg:
		// The wheel moves through lists; the intent is a choice, not a list.
		key := "down"
		if msg.Mouse().Button == tea.MouseWheelUp {
			key = "up"
		}
		switch {
		case m.modal != nil:
			return m, m.modalNav(key)
		case m.screen == scIntent:
			return m, nil
		case m.screen == scMatch || m.screen == scServers || m.screen == scHost:
			return m, m.gameKey(tea.KeyPressMsg(keyFor(key)))
		}
		return m, m.key(key)
	case tea.KeyPressMsg:
		return m, m.keyPress(msg)
	}
	return m, nil
}

func (m *Model) recordCheck(check setup.Check) {
	for index := range m.checks {
		if m.checks[index].ID == check.ID {
			m.checks[index] = check
			return
		}
	}
	m.checks = append(m.checks, check)
}

func (m *Model) nextCheck() tea.Cmd {
	ch, run := m.preCh, m.preRun
	return func() tea.Msg {
		check, ok := <-ch
		return checkMsg{check, ok, run}
	}
}

func (m *Model) nextProgress() tea.Cmd {
	ch := m.instCh
	return func() tea.Msg {
		progress, ok := <-ch
		return progressMsg{progress, ok}
	}
}

func (m *Model) resolve(hostname string) tea.Cmd {
	backend, network := m.cfg.Backend, setup.Network{}
	if m.network != nil {
		network = *m.network
	}
	m.dnsFor = hostname
	return func() tea.Msg {
		ctx, cancel := context.WithTimeout(context.Background(), 6*time.Second)
		defer cancel()
		return dnsMsg(backend.CheckHostname(ctx, hostname, network))
	}
}

// startPreflight runs the checks for the current options under the setup,
// right above the confirmation; the Install button takes the focus when
// it asked for them, not when an edit runs them again.
func (m *Model) startPreflight(focusConfirm bool) tea.Cmd {
	m.stopPreflight()
	m.screen, m.preOn, m.checks, m.preDone, m.confirmErr = scConfigure, true, nil, false, nil
	if focusConfirm {
		m.focus = len(m.confirmRows()) - 1
	}
	var ctx context.Context
	ctx, m.preCancel = context.WithCancel(context.Background())
	m.preCh = m.cfg.Backend.Preflight(ctx, m.opts)
	return m.nextCheck()
}

// stopPreflight abandons the checks running, so their goroutine ends.
func (m *Model) stopPreflight() {
	m.preRun++
	if m.preCancel != nil {
		m.preCancel()
		m.preCancel = nil
	}
}

func (m *Model) startInstall() tea.Cmd {
	m.stopPreflight()
	ctx, cancel := context.WithCancel(context.Background())
	m.cancel, m.control, m.cancelling = cancel, make(chan bool, 1), false
	m.screen, m.progress, m.instAt, m.showLog, m.speeds = scInstall, setup.Progress{}, time.Now(), false, nil
	m.instCh = m.cfg.Backend.Install(ctx, m.opts, m.control)
	return m.nextProgress()
}

// leaveInstall returns from the cockpit to where the installation was
// confirmed: the setup, or the app it was opened from.
func (m *Model) leaveInstall() {
	m.cancelling = false
	m.screen, m.focus, m.preOn, m.preDone, m.checks = scConfigure, 0, false, false, nil
	if m.fromPanel && m.existing != nil {
		m.backFromSetup()
	}
}

// openSetup opens the setup from the app, remembering where it came from
// and the choices to put back if it is left.
func (m *Model) openSetup(from screen, row string) {
	saved := cloneOptions(m.opts)
	m.saved, m.returnTo, m.returnRow = &saved, from, row
	m.screen, m.focus, m.fromPanel, m.preOn, m.confirmErr = scConfigure, 0, true, false, nil
}

// backFromSetup returns to the app screen the setup was opened from, with
// the choices as they were.
func (m *Model) backFromSetup() {
	m.stopPreflight()
	if m.saved != nil {
		m.opts = *m.saved
	}
	m.saved, m.preOn, m.preDone, m.checks, m.confirmErr = nil, false, false, nil, nil
	m.opts.Upgrade = false
	m.screen, m.panelView = m.returnTo, ""
	m.focusRow(m.returnRow)
}

// focusRow puts the focus on the row id of the current screen.
func (m *Model) focusRow(id string) {
	var rows []reviewRow
	switch m.screen {
	case scPanel:
		rows = m.panelRows()
	case scHost:
		rows = m.hostRows()
	}
	m.focus = 0
	for index, row := range rows {
		if row.id == id {
			m.focus = index
		}
	}
}

// armedFor implements double-press confirmation for destructive keys.
func (m *Model) armedFor(what string) bool {
	if m.armed == what && time.Now().Before(m.armedUntil) {
		m.armed = ""
		return true
	}
	m.armed, m.armedUntil = what, time.Now().Add(armedFor)
	return false
}

// quit is the one way out: the key pressed twice.
func (m *Model) quit(key string) tea.Cmd {
	if m.armed == "quit" && m.armedKey != key {
		m.armed = ""
	}
	m.armedKey = key
	if m.armedFor("quit") {
		return tea.Quit
	}
	return nil
}

func (m *Model) keyPress(msg tea.KeyPressMsg) tea.Cmd {
	key := msg.String()
	switch key {
	case "ctrl+c":
		// A failed install is over: ctrl+c quits like anywhere else.
		if m.screen == scInstall && !m.progress.Finished && m.progress.Err == nil {
			if !m.cancelling && m.armedFor("cancel") {
				m.cancelling = true
				m.cancel()
			}
			return nil
		}
		return m.quit(key)
	case "ctrl+t":
		if m.theme.Dark {
			m.theme = Day()
		} else {
			m.theme = Night()
		}
		return nil
	case "ctrl+l":
		if m.lang == PT {
			m.lang = EN
		} else {
			m.lang = PT
		}
		return nil
	}
	if m.modal != nil {
		return m.modalKey(msg)
	}
	if m.err != nil {
		switch key {
		case "r":
			m.err = nil
			return m.loadCatalog()
		case "esc":
			return m.quit(key)
		}
		return nil
	}
	switch m.screen {
	case scMatch, scServers, scHost:
		return m.gameKey(msg)
	}
	if m.screen == scIntent && m.opts.Intent.WantsClient() {
		switch key {
		case "up", "down", "enter", "esc", "tab", "shift+tab":
		default:
			if m.nick.Key(msg) {
				m.nickFlag = false
				return nil
			}
		}
	}
	return m.key(key)
}

// key handles navigation keys outside dialogs.
func (m *Model) key(key string) tea.Cmd {
	if m.modal != nil {
		return m.modalNav(key)
	}
	switch m.screen {
	case scIntent:
		switch key {
		case "up", "k", "shift+tab":
			m.setIntent((m.intent + 2) % 3)
		case "down", "j", "tab":
			m.setIntent((m.intent + 1) % 3)
		case "enter", "right":
			return m.leaveIntent()
		case "esc":
			return m.quit(key)
		}
	case scConfigure:
		rows := m.confirmRows()
		switch key {
		case "up", "k":
			m.focus = (m.focus + len(rows) - 1) % len(rows)
		case "down", "j", "tab":
			m.focus = (m.focus + 1) % len(rows)
		case "enter", "right", "space", " ":
			return m.activateConfirm(rows[m.focus].id)
		case "c":
			return m.copyRecipe()
		case "i":
			return m.activateConfirm("confirm")
		case "esc", "left", "backspace":
			switch {
			case m.preOn:
				// Fold the preflight away; Install brings it back.
				m.stopPreflight()
				m.preOn, m.preDone, m.checks = false, false, nil
			case m.fromPanel:
				m.backFromSetup()
			default:
				m.screen, m.focus = scIntent, 0
			}
		}
	case scInstall:
		if m.progress.Err != nil {
			if key == "enter" || key == "esc" {
				m.leaveInstall()
			}
			return nil
		}
		switch key {
		case "p", "space", " ":
			if !m.progress.Finished {
				select {
				case m.control <- !m.progress.Paused:
				default:
				}
			}
		case "l":
			m.showLog = !m.showLog
		}
	case scDone:
		rows := m.doneRows()
		if listNav(key, &m.focus, len(rows)) {
			return nil
		}
		switch key {
		case "enter", "space", " ":
			return m.activateDone(rows[m.focus].id)
		case "esc":
			// The installation exists now: esc goes to its home.
			if m.existing == nil {
				return m.quit(key)
			}
			m.screen, m.panelView = scPanel, ""
			m.focusRow("play")
			return tea.Batch(m.loadMatch(), m.loadStack(), m.loadServers())
		}
	case scPanel:
		if m.busy {
			return nil
		}
		return m.panelKey(key)
	}
	return nil
}

func (m *Model) setIntent(index int) {
	m.intent = index
	m.opts.Intent = []setup.Intent{setup.Play, setup.Host, setup.Both}[index]
}

func (m *Model) leaveIntent() tea.Cmd {
	if m.opts.Intent.WantsClient() && strings.TrimSpace(m.nick.String()) == "" {
		m.nickFlag = true
		return nil
	}
	m.opts.Nick = strings.TrimSpace(m.nick.String())
	m.screen, m.focus, m.preOn = scConfigure, 0, false
	return nil
}

// copyRecipe copies the recipe as the dialog shows it: the command, or
// the JSON file for x86qw setup --recipe.
func (m *Model) copyRecipe() tea.Cmd {
	m.say(m.t("recipe.copied"))
	if m.modal != nil && m.modal.json {
		data, _ := m.opts.RecipeJSON()
		return tea.SetClipboard(string(data))
	}
	return tea.SetClipboard(shellJoin(m.opts.Recipe()))
}

// listNav moves the focus through n rows; it reports whether key did.
func listNav(key string, focus *int, n int) bool {
	if n == 0 {
		return false
	}
	switch key {
	case "up", "k", "shift+tab":
		*focus = (*focus + n - 1) % n
	case "down", "j", "tab":
		*focus = (*focus + 1) % n
	default:
		return false
	}
	return true
}

// click routes a mouse click on a region.
func (m *Model) click(id string) tea.Cmd {
	switch {
	case id == "":
		return nil
	case strings.HasPrefix(id, "hint:"):
		return m.hintKey(strings.TrimPrefix(id, "hint:"))
	case strings.HasPrefix(id, "intent:"):
		// Like every row: the first click picks, the second goes on.
		var index int
		fmt.Sscanf(id, "intent:%d", &index)
		if index == m.intent {
			return m.leaveIntent()
		}
		m.setIntent(index)
	case strings.HasPrefix(id, "tab:"):
		var index int
		fmt.Sscanf(id, "tab:%d", &index)
		if m.modal != nil {
			m.modal.tab, m.modal.focus = index, 0
		}
	case id == "close":
		return m.closeModal(false)
	case id == "confirm-modal":
		return m.closeModal(true)
	case id == "lang":
		return m.keyPress(tea.KeyPressMsg(keyFor("ctrl+l")))
	case strings.HasPrefix(id, "row:"):
		return m.clickRow(strings.TrimPrefix(id, "row:"))
	}
	return nil
}

// hintKey turns a clicked footer hint into its key.
func (m *Model) hintKey(label string) tea.Cmd {
	keys := map[string]string{"↑↓": "down", "enter": "enter", "esc": "esc", "←": "left", "tab": "tab", "space": " ", "p": "p", "l": "l", "c": "c", "i": "i",
		"f": "f", "v": "v", "ctrl+c": "ctrl+c", "ctrl+l": "ctrl+l", "ctrl+t": "ctrl+t", "ctrl+o": "ctrl+o", "ctrl+r": "ctrl+r", "r": "r", "←→": "right"}
	key, ok := keys[label]
	if !ok {
		return nil
	}
	return m.keyPress(tea.KeyPressMsg(keyFor(key)))
}

func keyFor(name string) tea.Key {
	switch name {
	case "enter":
		return tea.Key{Code: tea.KeyEnter}
	case "esc":
		return tea.Key{Code: tea.KeyEscape}
	case "down":
		return tea.Key{Code: tea.KeyDown}
	case "left":
		return tea.Key{Code: tea.KeyLeft}
	case "tab":
		return tea.Key{Code: tea.KeyTab}
	case "ctrl+c":
		return tea.Key{Code: 'c', Mod: tea.ModCtrl}
	case "ctrl+l":
		return tea.Key{Code: 'l', Mod: tea.ModCtrl}
	case "ctrl+t":
		return tea.Key{Code: 't', Mod: tea.ModCtrl}
	case "ctrl+o":
		return tea.Key{Code: 'o', Mod: tea.ModCtrl}
	case "ctrl+r":
		return tea.Key{Code: 'r', Mod: tea.ModCtrl}
	case "up":
		return tea.Key{Code: tea.KeyUp}
	case "right":
		return tea.Key{Code: tea.KeyRight}
	case " ":
		return tea.Key{Code: tea.KeySpace, Text: " "}
	}
	r := []rune(name)[0]
	return tea.Key{Code: r, Text: name}
}

func (m *Model) hitAt(x, y int) string {
	for index := len(m.hits) - 1; index >= 0; index-- {
		h := m.hits[index]
		if h.y == y && x >= h.x0 && x < h.x1 {
			return h.id
		}
	}
	return ""
}

func (m *Model) activeField() *Field {
	if m.modal != nil {
		rows := focusables(m.modalRows())
		if m.modal.focus < len(rows) && rows[m.modal.focus].kind == rowText {
			return rows[m.modal.focus].field
		}
		return nil
	}
	if m.screen == scIntent && m.opts.Intent.WantsClient() {
		return &m.nick
	}
	return nil
}

// subtitle names what the interface is: the installer, or the app of an
// installation.
func (m *Model) subtitle() string {
	if m.existing != nil {
		return "app.subtitle"
	}
	return "subtitle"
}

// View draws the frame.
func (m *Model) View() tea.View {
	view := tea.View{AltScreen: true, MouseMode: tea.MouseModeAllMotion, WindowTitle: "x86QW", BackgroundColor: m.theme.Base}
	if m.w == 0 {
		return view
	}
	m.hits = nil
	t := m.theme
	canvas := lipgloss.NewCanvas(m.w, m.h)
	base := lipgloss.NewStyle().Background(t.Base).Width(m.w).Height(m.h).Render("")
	layers := []*lipgloss.Layer{lipgloss.NewLayer(base)}
	place := func(content string, x, y int) { layers = append(layers, lipgloss.NewLayer(content).X(x).Y(y)) }
	if m.w < minWidth || m.h < minHeight {
		msg := t.Style(t.Gray).Render(m.t("small"))
		place(msg, max(0, (m.w-lipgloss.Width(msg))/2), m.h/2)
		canvas.Compose(lipgloss.NewCompositor(layers...))
		view.Content = canvas.Render()
		return view
	}
	width := min(m.w-6, maxWidth)
	body := m.body(width)
	footer, footerHits := hints(t, m.footerHints(), m.w-4, m.hover)

	// Top bar: brand on the left, mode on the right.
	brand := t.Style(t.Text).Bold(true).Render("x86") + t.Style(t.Brand).Bold(true).Render("QW") + t.Style(t.Gray).Render("  "+m.t(m.subtitle()))
	if m.cfg.Demo {
		brand += t.Style(t.Dim).Render("  ·  " + m.t("demo"))
	}
	langHint := t.Style(t.Dim).Render("ctrl+l ")
	pt, en := t.Style(t.Gray), t.Style(t.Gray)
	if m.lang == PT {
		pt = t.Style(t.Text).Bold(true)
	} else {
		en = t.Style(t.Text).Bold(true)
	}
	if m.hover == "lang" {
		langHint = t.Style(t.Gray).Render("ctrl+l ")
	}
	right := langHint + pt.Render("PT") + t.Style(t.Dim).Render(" │ ") + en.Render("EN")
	place(spread(brand, right, m.w-4, t.Base), 2, 0)
	langX := m.w - 2 - lipgloss.Width(right)
	m.hits = append(m.hits, hit{y: 0, x0: langX, x1: m.w - 2, id: "lang"})

	// The dock sits on the footer and takes its rows from the body.
	room := m.h - 4
	dock := m.dock(width)
	if dock != nil {
		room -= len(dock.lines) + 1
	}
	// Body: in the upper third of the free space, centred.
	// A body that changes height (a field showing up, a section opening)
	// keeps its place: the top only ever moves up, until the next resize.
	top := 2 + max(0, (room-len(body.lines))/3)
	if dock != nil {
		top = 3
	}
	if last, ok := m.tops[m.screen]; ok {
		top = min(top, last)
	}
	m.tops[m.screen] = top
	left := (m.w - width) / 2
	offset := 0
	if total := len(body.lines); total > room {
		// Scroll just enough to keep the focused block on screen.
		top = 2
		offset = min(m.scroll[m.screen], total-room)
		if body.focusBottom >= offset+room {
			offset = body.focusBottom - room + 1
		}
		if body.focusTop < offset {
			offset = body.focusTop
		}
		offset = max(0, min(offset, total-room))
		m.scroll[m.screen] = offset
		body.lines = body.lines[offset : offset+room]
		thumb := max(1, room*room/total)
		at := offset * (room - thumb) / max(1, total-room)
		var bar strings.Builder
		for y := range room {
			glyph, color := "│", t.Faint()
			if y >= at && y < at+thumb {
				glyph, color = "┃", t.Gray
			}
			if y > 0 {
				bar.WriteString("\n")
			}
			bar.WriteString(t.Style(color).Render(glyph))
		}
		place(bar.String(), left+width+2, top)
	}
	place(strings.Join(body.lines, "\n"), left, top)
	for _, h := range body.hits {
		if y := h.y - offset; y >= 0 && y < len(body.lines) {
			m.hits = append(m.hits, hit{y: y + top, x0: h.x0 + left, x1: h.x1 + left, id: h.id})
		}
	}
	if body.caret != nil && m.modal == nil {
		view.Cursor = tea.NewCursor(left+body.caret[0], top+body.caret[1]-offset)
		view.Cursor.Color, view.Cursor.Shape, view.Cursor.Blink = t.Brand, tea.CursorBar, true
	}

	if dock != nil {
		y := m.h - 2 - len(dock.lines)
		place(strings.Join(dock.lines, "\n"), left, y)
		for _, h := range dock.hits {
			m.hits = append(m.hits, hit{y: h.y + y, x0: h.x0 + left, x1: h.x1 + left, id: h.id})
		}
	}

	footerX := max(2, (m.w-lipgloss.Width(footer))/2)

	canvas.Compose(lipgloss.NewCompositor(layers...))
	layers = nil
	if m.modal != nil {
		dim(canvas, t)
		m.hits = m.hits[:0]
		lines, hits, caret := m.modalView()
		mw := lipgloss.Width(lines[0])
		mx, my := (m.w-mw)/2, max(1, (m.h-1-len(lines))/2)
		place(strings.Join(lines, "\n"), mx, my)
		for _, h := range hits {
			m.hits = append(m.hits, hit{y: h.y + my, x0: h.x0 + mx, x1: h.x1 + mx, id: h.id})
		}
		if caret != nil {
			view.Cursor = tea.NewCursor(mx+caret[0], my+caret[1])
			view.Cursor.Color, view.Cursor.Shape, view.Cursor.Blink = t.Brand, tea.CursorBar, true
		}
	}

	// Footer: hints centred on the last row, never dimmed.
	place(footer, footerX, m.h-1)
	for _, h := range footerHits {
		m.hits = append(m.hits, hit{y: m.h - 1, x0: h.x0 + footerX, x1: h.x1 + footerX, id: h.id})
	}
	if time.Now().Before(m.toastUntil) {
		// The row above the footer is kept free for it: nothing else sits
		// there, so it never covers a button.
		toast := lipgloss.NewStyle().Foreground(t.Text).Background(t.Visual).Bold(true).Render(" " + m.toast + " ")
		if lipgloss.Width(toast) > m.w-4 {
			toast = ansi.Truncate(toast, m.w-4, "…")
		}
		place(toast, m.w-lipgloss.Width(toast)-2, m.h-2)
	}
	if len(layers) > 0 {
		canvas.Compose(lipgloss.NewCompositor(layers...))
	}
	if m.screen == scInstall && m.progress.Total > 0 {
		state := tea.ProgressBarDefault
		if m.progress.Paused {
			state = tea.ProgressBarWarning
		}
		view.ProgressBar = tea.NewProgressBar(state, int(m.progress.Done*100/m.progress.Total))
	}
	view.Content = canvas.Render()
	return view
}

// dim pulls every cell halfway to the canvas colour behind a dialog.
func dim(canvas *lipgloss.Canvas, t Theme) {
	for y := 0; y < canvas.Height(); y++ {
		for x := 0; x < canvas.Width(); x++ {
			cell := canvas.CellAt(x, y)
			if cell == nil {
				continue
			}
			if cell.Style.Fg != nil {
				cell.Style.Fg = Blend(cell.Style.Fg, t.Base, 0.6)
			}
			if cell.Style.Bg != nil {
				cell.Style.Bg = Blend(cell.Style.Bg, t.Base, 0.6)
			}
			cell.Style.Attrs = 0
		}
	}
}

// footerHints are the keys that work right now.
func (m *Model) footerHints() [][2]string {
	if m.armed != "" && time.Now().Before(m.armedUntil) {
		switch m.armed {
		case "quit":
			return [][2]string{{m.armedKey, m.t("quit.again")}}
		case "cancel":
			return [][2]string{{"ctrl+c", m.t("cancel.again")}}
		case "remove":
			return [][2]string{{"enter", m.t("remove.again")}}
		case "stop":
			return [][2]string{{"enter", m.t("stop.again")}}
		}
	}
	if m.modal != nil {
		items := [][2]string{{"↑↓", m.t("k.choose")}}
		if rows := focusables(m.modalRows()); m.modal.focus < len(rows) {
			switch row := rows[m.modal.focus]; {
			case row.step != nil:
				items = append(items, [2]string{"←→", m.t("k.change")})
			case row.kind == rowAction:
				items = append(items, [2]string{"space", m.t("k.run")})
			case row.kind != rowText:
				items = append(items, [2]string{"space", m.t("k.change")})
			}
		}
		if len(m.modalTabs()) > 1 {
			items = append(items, [2]string{"tab", m.t("k.tab")})
		}
		return append(items, [2]string{"enter", m.t("k.ok")}, [2]string{"esc", m.t("k.cancel")})
	}
	if m.err != nil {
		return [][2]string{{"r", m.t("k.retry")}, {"esc", m.t("k.quit")}}
	}
	if m.catalog == nil {
		return [][2]string{{"ctrl+c", m.t("k.quit")}}
	}
	switch m.screen {
	case scIntent:
		return [][2]string{{"↑↓", m.t("k.choose")}, {"enter", m.t("k.next")}, {"ctrl+t", m.t("k.theme")}, {"esc", m.t("k.quit")}}
	case scConfigure:
		// The main action keeps its i shortcut; on its row, enter is it.
		action := ""
		switch {
		case !m.preOn:
			action = m.t("k.install")
		case m.preDone && m.preflightOutcome() != setup.CheckFailed:
			action = m.t("k.confirm")
		}
		items := [][2]string{{"↑↓", m.t("k.choose")}}
		rows := m.confirmRows()
		switch {
		case rows[m.focus].id != "confirm":
			items = append(items, [2]string{"enter", m.t("k.edit")})
			if action != "" {
				items = append(items, [2]string{"i", action})
			}
		case action != "":
			items = append(items, [2]string{"enter", action})
		}
		return append(items, [2]string{"c", m.t("k.copy")}, [2]string{"esc", m.t("k.back")})
	case scInstall:
		switch {
		case m.progress.Err != nil:
			return [][2]string{{"enter", m.t("k.back")}}
		case m.cancelling:
			return [][2]string{{m.spinner(), m.t("inst.cancelling")}}
		case m.progress.Finished:
			return [][2]string{{m.spinner(), m.t("inst.finishing")}}
		}
		pause := m.t("k.pause")
		if m.progress.Paused {
			pause = m.t("k.resume")
		}
		return [][2]string{{"p", pause}, {"l", m.t("k.log")}, {"ctrl+c", m.t("k.cancel")}}
	case scDone:
		if m.existing == nil {
			return [][2]string{{"↑↓", m.t("k.choose")}, {"enter", m.t("k.open")}, {"esc", m.t("k.quit")}}
		}
		return [][2]string{{"↑↓", m.t("k.choose")}, {"enter", m.t("k.open")}, {"esc", m.t("k.home")}}
	case scPanel:
		switch {
		case m.removed:
			return [][2]string{{"esc", m.t("k.quit")}}
		case m.busy && m.panelView == "repair":
			return [][2]string{{m.spinner(), m.t("verify.running")}}
		case m.busy:
			return [][2]string{{m.spinner(), m.t("remove.running")}}
		case m.panelView == "update":
			return [][2]string{{"enter", m.t("k.apply")}, {"esc", m.t("k.back")}}
		case m.panelView == "repair":
			return [][2]string{{"enter", m.t("k.verify")}, {"esc", m.t("k.back")}}
		case m.panelView == "remove":
			return [][2]string{{"↑↓", m.t("k.choose")}, {"enter", m.t("k.run")}, {"esc", m.t("k.back")}}
		}
		return [][2]string{{"↑↓", m.t("k.choose")}, {"enter", m.t("k.open")}, {"ctrl+t", m.t("k.theme")}, {"esc", m.t("k.quit")}}
	case scMatch:
		if m.game.catalog == nil || len(m.game.catalog.Games) == 0 {
			return [][2]string{{"esc", m.t("k.home")}}
		}
		return [][2]string{{"↑↓", m.t("k.choose")}, {"enter", m.t("k.edit")}, {"p", m.t("k.play")}, {"c", m.t("k.copycmd")}, {"esc", m.t("k.home")}}
	case scServers:
		return m.serversHints()
	case scHost:
		if !m.game.stackLoaded {
			return [][2]string{{"esc", m.t("k.home")}}
		}
		action := m.t("k.open")
		if rows := m.hostRows(); m.focus >= len(rows)-1 {
			action = m.t("k.run")
		}
		return [][2]string{{"↑↓", m.t("k.choose")}, {"enter", action}, {"esc", m.t("k.home")}}
	}
	return [][2]string{{"↑↓", m.t("k.choose")}, {"enter", m.t("k.next")}, {"esc", m.t("k.back")}}
}

// DefaultTarget is where x86QW installs unless told otherwise, on every
// system.
func DefaultTarget() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, "Games", "x86qw")
}

// Run shows the interface until it quits and says what comes next.
func Run(cfg Config) (Result, error) {
	final, err := tea.NewProgram(New(cfg)).Run()
	if m, ok := final.(*Model); ok && err == nil {
		theme := "dark"
		if !m.theme.Dark {
			theme = "light"
		}
		return Result{Next: m.next, Back: m.back, Lang: m.lang, Theme: theme}, nil
	}
	return Result{}, err
}

// preflightOutcome is the worst state among the finished checks.
func (m *Model) preflightOutcome() setup.CheckState {
	worst := setup.CheckOK
	for _, check := range m.checks {
		if check.State == setup.CheckFailed || (check.State == setup.CheckWarning && worst != setup.CheckFailed) {
			worst = check.State
		}
	}
	return worst
}
