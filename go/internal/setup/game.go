package setup

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strconv"
)

// The game side of the app: what a local match can be, the public
// servers and the installation's own server stack.

// Game is a local game the installation can play.
type Game struct {
	ID, Label, Version, Description string
	Modes                           bool // KTX: the match picks a mode
}

// ModeGroup is a family of modes, as the mode picker groups them.
type ModeGroup struct {
	Key, Label string
	Modes      []string
}

// MatchCatalog is everything the match screen offers.
type MatchCatalog struct {
	Games    []Game
	Modes    []Mode // KTX modes, with their options
	Groups   []ModeGroup
	Channels []string // installed ezQuake channels, stable first
	Ruleset  string   // the saved client ruleset
	Last     *Match   // the last match launched, still valid
}

// Match is one local match: what `x86qw play` is told.
type Match struct {
	Game, Mode, Map, Ruleset, Channel string
	// Frogbots: Bots added, or FillBots for an open mode; skill 1–20 or
	// "random"; names default, x86qw, one-piece or personal.
	Bots              int
	FillBots          bool
	BotSkill, BotName string
	// Bot rules of the tot mode.
	BotWeapon string // "" map default, random or 1–8
	BotHealth int    // 0 map default
	BotBreak  string // "", on or off
	// Capture the flag.
	CTFHook, CTFRunes string
	CTFBased          bool
	// Race.
	RaceStyle, RaceScoring string
	RacePacemaker          int
	RaceHide               bool
}

// HasBots reports whether the match adds frogbots.
func (m Match) HasBots() bool { return m.Bots > 0 || m.FillBots }

// Args is the `x86qw play` command line for the match.
func (m Match) Args(target string) []string {
	args := []string{"play", "--target", target}
	if m.Game != "" && m.Game != "ktx" {
		args = append(args, m.Game)
	}
	add := func(flag, value string) {
		if value != "" {
			args = append(args, flag, value)
		}
	}
	add("--mode", m.Mode)
	add("--map", m.Map)
	add("--ruleset", m.Ruleset)
	add("--channel", m.Channel)
	switch {
	case m.FillBots:
		args = append(args, "--fill-bots")
	case m.Bots > 0:
		add("--bots", strconv.Itoa(m.Bots))
	}
	if m.HasBots() {
		if m.BotSkill != "" && m.BotSkill != "5" {
			add("--bot-skill", m.BotSkill)
		}
		if m.BotName != "" && m.BotName != "default" {
			add("--bot-names", m.BotName)
		}
		add("--bot-weapon", m.BotWeapon)
		if m.BotHealth > 0 {
			add("--bot-health", strconv.Itoa(m.BotHealth))
		}
		switch m.BotBreak {
		case "on":
			args = append(args, "--bot-break-on-death")
		case "off":
			args = append(args, "--no-bot-break-on-death")
		}
	}
	add("--ctf-hook", m.CTFHook)
	add("--ctf-runes", m.CTFRunes)
	if m.CTFBased {
		args = append(args, "--ctf-based-spawn")
	}
	add("--race-style", m.RaceStyle)
	add("--race-scoring", m.RaceScoring)
	if m.RacePacemaker > 0 {
		add("--race-pacemaker", strconv.Itoa(m.RacePacemaker))
	}
	if m.RaceHide {
		args = append(args, "--race-hide-players")
	}
	return args
}

// MapList is what a match can be played on.
type MapList struct {
	Maps      []string
	Suggested map[string]bool
	Default   string
	Narrowed  bool // the options leave only maps with the resources they need
}

// PublicServer is a server from the QuakeWorld hub, or a favorite.
type PublicServer struct {
	Address, Title, Map, Mode string
	Humans, Bots              int
	QTV                       string // the QTV stream, "" when none
	Favorite                  bool
}

// Stack is the installation's running service session.
type Stack struct {
	Status    string // starting, running or stopping
	Processes []StackProcess
}

// StackProcess is one running service.
type StackProcess struct {
	Label, Address, Map, Mode string
	Port, PID                 int
	Alive                     bool
}

const lastMatchFile = ".x86qw/launcher/last-match.json"

// SaveLastMatch records the match launched last.
func SaveLastMatch(target string, m Match) error {
	payload, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return err
	}
	path := filepath.Join(target, filepath.FromSlash(lastMatchFile))
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, append(payload, '\n'), 0o644)
}

// LoadLastMatch reads the last match, nil when there is none.
func LoadLastMatch(target string) (*Match, error) {
	payload, err := os.ReadFile(filepath.Join(target, filepath.FromSlash(lastMatchFile)))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var m Match
	if err := json.Unmarshal(payload, &m); err != nil {
		return nil, nil // unreadable: start from the defaults
	}
	return &m, nil
}
