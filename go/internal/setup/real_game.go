package setup

import (
	"context"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/hub"
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/play"
	"github.com/x86dx2/x86qw/go/internal/services"
)

// modeOf describes a KTX mode for the interface.
func modeOf(mode play.Mode) Mode {
	out := Mode{ID: mode.Key, Label: mode.Label, DefaultMap: mode.DefaultMap, Maps: mode.SuggestedMaps, Bots: mode.Bots,
		Players: mode.RecommendedPlayers, Description: mode.Description}
	if limit, fixed := play.BotLimit(mode); fixed {
		out.BotLimit = limit
	}
	switch mode.Key {
	case "race", "ctf", "tot":
		out.Kind = mode.Key
	}
	return out
}

// MatchCatalog lists the installed games, the KTX modes and clients.
func (r *Real) MatchCatalog(ctx context.Context, target string) (*MatchCatalog, error) {
	games, err := play.Installed(target)
	if err != nil {
		return nil, err
	}
	out := &MatchCatalog{}
	for _, game := range games {
		out.Games = append(out.Games, Game{ID: game.ID, Label: game.Label, Version: install.InstalledVersion(target, game),
			Description: game.Description, Modes: game.UsesModeCatalog()})
	}
	modes, err := play.LoadModes()
	if err != nil {
		return nil, err
	}
	for _, mode := range modes {
		out.Modes = append(out.Modes, modeOf(mode))
	}
	groups, err := play.LoadMenuGroups()
	if err != nil {
		return nil, err
	}
	for _, group := range groups {
		out.Groups = append(out.Groups, ModeGroup{Key: group.Key, Label: group.Label, Modes: group.Modes})
	}
	if runtimes, err := install.HostRuntimes(target); err == nil {
		for _, runtime := range runtimes {
			out.Channels = append(out.Channels, runtime.Channel)
		}
	}
	if out.Ruleset, err = play.SavedRuleset(target); err != nil || out.Ruleset == "" {
		out.Ruleset = "x86qw"
	}
	if last, _ := LoadLastMatch(target); last != nil && out.valid(*last) {
		out.Last = last
	}
	return out, nil
}

// valid reports whether m still fits what is installed.
func (c *MatchCatalog) valid(m Match) bool {
	for _, game := range c.Games {
		if game.ID != m.Game {
			continue
		}
		if !game.Modes {
			return m.Mode == ""
		}
		for _, mode := range c.Modes {
			if mode.ID == m.Mode {
				return true
			}
		}
	}
	return false
}

// playOptions is the match's options as the play package takes them.
func playOptions(m Match) play.Options {
	o := play.DefaultOptions()
	o.Bots, o.FillBots = m.Bots, m.FillBots
	if m.BotSkill != "" {
		o.BotSkill = m.BotSkill
	}
	if m.BotName != "" {
		o.BotNamesProfile = m.BotName
	}
	text := func(value string) *string {
		if value == "" {
			return nil
		}
		return &value
	}
	o.BotWeapon = text(m.BotWeapon)
	if m.BotHealth > 0 {
		health := m.BotHealth
		o.BotHealth = &health
	}
	if m.BotBreak != "" {
		on := m.BotBreak == "on"
		o.BotBreakOnDeath = &on
	}
	o.CTFHook, o.CTFRunes, o.CTFBasedSpawn = text(m.CTFHook), text(m.CTFRunes), m.CTFBased
	o.RaceStyle, o.RaceScoring, o.RaceHidePlayers = text(m.RaceStyle), text(m.RaceScoring), m.RaceHide
	if m.RacePacemaker > 0 {
		pacemaker := m.RacePacemaker
		o.RacePacemaker = &pacemaker
	}
	return o
}

// Maps lists the maps the match can run on.
func (r *Real) Maps(ctx context.Context, target string, m Match) (*MapList, error) {
	games, err := play.Installed(target)
	if err != nil {
		return nil, err
	}
	var game *play.Game
	for index := range games {
		if games[index].ID == m.Game {
			game = &games[index]
		}
	}
	if game == nil {
		return nil, problem("", "err.game", m.Game)
	}
	var mode *play.Mode
	if game.UsesModeCatalog() {
		modes, err := play.LoadModes()
		if err != nil {
			return nil, err
		}
		found, err := play.FindMode(modes, m.Mode)
		if err != nil {
			return nil, err
		}
		mode = &found
	}
	list, err := play.Maps(target, *game, mode, playOptions(m))
	if err != nil {
		return nil, err
	}
	return &MapList{Maps: list.Maps, Suggested: list.Suggested, Default: list.Default, Narrowed: list.Requirements}, nil
}

// run runs an x86qw command through the command line's dispatcher.
func (r *Real) run(args []string) error {
	if r.Run == nil {
		return problem("", "err.run")
	}
	return r.Run(args)
}

// Play launches the match and records it.
func (r *Real) Play(ctx context.Context, target string, m Match) error {
	if err := r.run(m.Args(target)); err != nil {
		return err
	}
	return SaveLastMatch(target, m)
}

// Servers lists the hub's servers, marking the favorites.
func (r *Real) Servers(ctx context.Context, target string) ([]PublicServer, error) {
	servers, err := hub.Servers(ctx, target)
	if err != nil {
		return nil, err
	}
	favorites := map[string]bool{}
	if library, err := maint.LoadLibrary(target); err == nil && library != nil {
		for _, entry := range library.Favorites {
			favorites[strings.ToLower(entry.Address)] = true
		}
	}
	var out []PublicServer
	for _, server := range servers {
		item := PublicServer{Address: server.Address, Title: server.Title, Map: server.Map, Mode: server.Mode,
			Humans: server.Players.Humans, Bots: server.Players.Bots, Favorite: favorites[strings.ToLower(server.Address)]}
		if server.QTVStream != nil {
			item.QTV = *server.QTVStream
		}
		out = append(out, item)
	}
	return out, nil
}

// Favorite pins address in the library (on) or unpins it.
func (r *Real) Favorite(ctx context.Context, target, address string, on bool) error {
	if on {
		return maint.AddFavorite(target, address, time.Now())
	}
	return maint.RemoveFavorite(target, address)
}

// Open connects ezQuake to address.
func (r *Real) Open(ctx context.Context, target, address string) error {
	if !strings.HasPrefix(strings.ToLower(address), "qw://") {
		address = "qw://" + address
	}
	return r.run([]string{"open", "--target", target, address})
}

// Stack reports the running session.
func (r *Real) Stack(ctx context.Context, target string) (*Stack, error) {
	active, err := services.Active(target)
	if err != nil || active == nil {
		return nil, err
	}
	stack := &Stack{Status: active.Status}
	for _, process := range active.Processes {
		stack.Processes = append(stack.Processes, StackProcess{Label: process.Label, Address: process.Address, Port: process.Port, PID: process.PID,
			Alive: process.Alive, Map: process.Parameters["map"], Mode: process.Parameters["mode"]})
	}
	return stack, nil
}

// StartServer records the server choices and starts every instance. Only
// the server's choices matter here: an installation that never recorded a
// nick still hosts.
func (r *Real) StartServer(ctx context.Context, target string, options Options) error {
	if err := options.Server.Validate(); err != nil {
		return err
	}
	if PathTooLong(target) {
		return problem("", "v.path", len(target), MVDSVPathRoom)
	}
	if err := SaveChoices(target, options); err != nil {
		return err
	}
	// The host command reads the passwords from files, whatever the
	// recorded intent said.
	if err := saveSecrets(target, options.Server); err != nil {
		return err
	}
	if err := SaveServerPresets(target, options); err != nil {
		return err
	}
	return r.run(HostCommand(target, options))
}

// StopServer stops the running session and waits for it.
func (r *Real) StopServer(ctx context.Context, target string) error {
	return services.RequestStop(target, 20*time.Second)
}

// StartService records the choices and starts QTV or QWFWD alone.
func (r *Real) StartService(ctx context.Context, target, kind string, options Options) error {
	if err := options.Server.Validate(); err != nil {
		return err
	}
	if err := SaveChoices(target, options); err != nil {
		return err
	}
	return r.run(ServiceCommand(target, options, kind))
}

// Tool runs an x86qw command and returns what it printed.
func (r *Real) Tool(ctx context.Context, args []string) (string, error) {
	if r.Capture == nil {
		return "", problem("", "err.run")
	}
	return r.Capture(args)
}
