package services

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/play"
)

// Common holds flags every service command accepts.
type Common struct {
	Target          string
	Verbose         bool
	NoColor         bool
	Menu            bool
	Background      bool
	BackgroundChild bool
	BackgroundLog   string
}

// HostOptions are the options of `host`.
type HostOptions struct {
	Common
	Selection, Game, Mode, Map          string
	Bind, Hostname                      string
	Port, Maxclients                    int
	NoMVD, WithQTV, WithProxy           bool
	QtvBind, ProxyBind                  string
	QtvPort, ProxyPort                  int
	Preset, SavePreset                  string
	Password, SpectatorPassword         string
	RconPassword, QtvPassword           string
	PromptPassword                      bool
	PromptSpectatorPassword             bool
	PromptRconPassword                  bool
	PromptQtvPassword                   bool
	PasswordFile, SpectatorPasswordFile string
	RconPasswordFile, QtvPasswordFile   string
	KtxOptions                          play.Options
	ModeGiven                           bool
	// Instances are more KTX servers the session runs beside the first,
	// each on its own port; QTV and QWFWD stay with the first.
	Instances []Instance
}

// Instance is one more KTX server of a host session.
type Instance struct {
	Mode, Map string
	Port      int
	Bots      bool // fill the server with bots
}

// String is the --instance value: MODE:MAP:PORT, with :bots when it has them.
func (i Instance) String() string {
	value := fmt.Sprintf("%s:%s:%d", i.Mode, i.Map, i.Port)
	if i.Bots {
		value += ":bots"
	}
	return value
}

func parseInstance(value string) (Instance, error) {
	parts := strings.Split(value, ":")
	if len(parts) < 3 || len(parts) > 4 || parts[0] == "" || parts[1] == "" || (len(parts) == 4 && parts[3] != "bots") {
		return Instance{}, usagef("use MODE:MAP:PORT or MODE:MAP:PORT:bots, not %q", value)
	}
	instance := Instance{Mode: parts[0], Map: parts[1], Bots: len(parts) == 4}
	if err := boundedInteger(1024, 65535, &instance.Port)(parts[2]); err != nil {
		return Instance{}, err
	}
	return instance, nil
}

// ProxyOptions are the options of `proxy`.
type ProxyOptions struct {
	Common
	ProxyBind string
	ProxyPort int
}

// QtvOptions are the options of `qtv`.
type QtvOptions struct {
	Common
	Bind, Hostname, Upstream string
	Port                     int
	QtvPassword              string
	PromptQtvPassword        bool
	QtvPasswordFile          string
}

// StatusOptions are the options of `status`.
type StatusOptions struct {
	Common
	Stop, Yes bool
}

// Parsers. Each Parse* returns cliargs.ErrHelp when asked for help;
// the *Parser functions build the same parsers for the help text.

func boundedInteger(minimum, maximum int, target *int) func(string) error {
	return func(value string) error {
		parsed, err := strconv.Atoi(strings.TrimSpace(value))
		if err != nil {
			return usagef("must be a whole number")
		}
		if parsed < minimum || parsed > maximum {
			return usagef("must be between %d and %d", minimum, maximum)
		}
		*target = parsed
		return nil
	}
}

func bindFlag(target *string) func(string) error {
	return func(value string) error {
		normalized, err := BindAddress(value)
		if err != nil {
			return err
		}
		*target = normalized
		return nil
	}
}

// choicePointer stores value in *target when it is one of choices.
func choicePointer(target **string, choices ...string) func(string) error {
	return func(value string) error {
		for _, choice := range choices {
			if choice == value {
				copied := value
				*target = &copied
				return nil
			}
		}
		return usagef("invalid choice: '%s' (use %s)", value, strings.Join(choices, ", "))
	}
}

// common declares what every service command takes; service adds what
// only host, qtv and proxy do: the app screen and the background run.
func common(p *cliargs.Parser, c *Common, service bool) {
	p.String(&c.Target, "TARGET", "installation folder", "--target")
	p.Bool(&c.Verbose, "technical details", "-v", "--verbose")
	p.Bool(&c.NoColor, "no colors", "--no-color")
	if service {
		p.Bool(&c.Menu, "opens the app's server screen", "--menu")
		p.Bool(&c.Background, "runs in the background", "--background")
		p.Bool(&c.BackgroundChild, "", "--background-child")
		p.String(&c.BackgroundLog, "FILE", "", "--background-log")
		p.Hidden("--background-child")
		p.Hidden("--background-log")
	}
}

func passwordSource(p *cliargs.Parser, what string, value *string, prompt *bool, file *string, names []string, promptFlag, fileFlag string) {
	p.String(value, "PASSWORD", what+" password (shows in the process list; prefer the file)", names...)
	p.Bool(prompt, "asks for the "+what+" password", promptFlag)
	p.String(file, "FILE", "reads the "+what+" password from a file", fileFlag)
	p.Exclusive(append(append([]string{}, names...), promptFlag, fileFlag)...)
}

// hostFlags collects what host flags set before validation.
type hostFlags struct {
	bots, health                                int
	skill, weapon, names                        string
	botsGiven, healthGiven, skillGiven, on, off bool
}

func hostParser(o *HostOptions, k *play.Options, f *hostFlags) *cliargs.Parser {
	p := cliargs.New()
	p.Value(func(value string) error { o.Mode, o.ModeGiven = value, true; return nil }, "MODE", "KTX mode: duel, 2on2, 4on4, ffa, ctf, race...", "--mode")
	p.String(&o.Map, "MAP", "starting map", "--map")
	p.Value(func(value string) error { f.botsGiven = true; return boundedInteger(1, 31, &f.bots)(value) }, "N", "how many Frogbots join (1 to 31)", "--bots")
	p.Bool(&k.FillBots, "fills the free slots with Frogbots", "--fill-bots")
	p.Exclusive("--bots", "--fill-bots")
	p.Value(func(value string) error {
		f.skillGiven = true
		if strings.EqualFold(value, "random") {
			f.skill = "random"
			return nil
		}
		var parsed int
		if err := boundedInteger(1, 20, &parsed)(value); err != nil {
			return err
		}
		f.skill = strconv.Itoa(parsed)
		return nil
	}, "1-20|random", "bot skill", "--bot-skill")
	p.Choice(&f.weapon, []string{"random", "1", "2", "3", "4", "5", "6", "7", "8"}, "bots' starting weapon", "--bot-weapon")
	p.Value(func(value string) error { f.healthGiven = true; return boundedInteger(1, 300, &f.health)(value) }, "N", "bot health (1 to 300)", "--bot-health")
	p.Bool(&f.on, "bots drop their backpack when they die", "--bot-break-on-death")
	p.Bool(&f.off, "bots keep their backpack", "--no-bot-break-on-death")
	p.Exclusive("--bot-break-on-death", "--no-bot-break-on-death")
	p.Choice(&f.names, []string{"default", "x86qw", "one-piece", "personal"}, "bot names", "--bot-names")
	p.Value(choicePointer(&k.CTFHook, "smooth", "fast", "classic", "crhook", "off"), "smooth|fast|classic|crhook|off", "CTF hook", "--ctf-hook")
	p.Value(choicePointer(&k.CTFRunes, "on", "off"), "on|off", "CTF runes", "--ctf-runes")
	p.Bool(&k.CTFBasedSpawn, "CTF players spawn at their base", "--ctf-based-spawn")
	p.Value(choicePointer(&k.RaceStyle, "solo", "simultaneous", "match"), "solo|simultaneous|match", "Race style", "--race-style")
	p.Value(choicePointer(&k.RaceScoring, "win", "scaled", "formula1"), "win|scaled|formula1", "Race scoring", "--race-scoring")
	p.Value(bindFlag(&o.Bind), "IP", "address the server listens on (default 127.0.0.1)", "--bind")
	p.Int(&o.Port, 1024, 65535, "PORT", "server UDP port (default 28501)", "--port")
	p.String(&o.Hostname, "NAME", "server name in the list", "--hostname")
	p.Int(&o.Maxclients, 1, 32, "N", "slots (1 to 32, default 16)", "--maxclients")
	passwordSource(p, "player", &o.Password, &o.PromptPassword, &o.PasswordFile, []string{"--password"}, "--prompt-password", "--password-file")
	passwordSource(p, "spectator", &o.SpectatorPassword, &o.PromptSpectatorPassword, &o.SpectatorPasswordFile, []string{"--spectator-password"}, "--prompt-spectator-password", "--spectator-password-file")
	passwordSource(p, "rcon", &o.RconPassword, &o.PromptRconPassword, &o.RconPasswordFile, []string{"--rcon-password"}, "--prompt-rcon-password", "--rcon-password-file")
	p.Bool(&o.NoMVD, "does not record MVD demos", "--no-mvd")
	p.Bool(&o.WithQTV, "starts the QTV relay too", "--with-qtv")
	p.Value(bindFlag(&o.QtvBind), "IP", "QTV address", "--qtv-bind")
	p.Int(&o.QtvPort, 1024, 65535, "PORT", "QTV TCP port (default 28000)", "--qtv-port")
	passwordSource(p, "QTV", &o.QtvPassword, &o.PromptQtvPassword, &o.QtvPasswordFile, []string{"--qtv-password"}, "--prompt-qtv-password", "--qtv-password-file")
	p.Bool(&o.WithProxy, "starts the QWFWD proxy too", "--with-proxy")
	p.Value(bindFlag(&o.ProxyBind), "IP", "QWFWD address", "--proxy-bind")
	p.Int(&o.ProxyPort, 1024, 65535, "PORT", "QWFWD UDP port (default 30000)", "--proxy-port")
	p.String(&o.Preset, "NAME", "uses a saved preset", "--preset")
	p.Value(func(value string) error {
		instance, err := parseInstance(value)
		if err == nil {
			o.Instances = append(o.Instances, instance)
		}
		return err
	}, "MODE:MAP:PORT[:bots]", "one more KTX server in the same session (repeatable)", "--instance")
	p.String(&o.SavePreset, "NAME", "saves these options as a preset", "--save-preset")
	p.Exclusive("--preset", "--save-preset")
	common(p, &o.Common, true)
	return p
}

// ParseHost parses `host` arguments (dedicated game-launch flags included).
func ParseHost(args []string, games []play.Game) (*HostOptions, error) {
	o := &HostOptions{Bind: "127.0.0.1", Port: 28501, Maxclients: 16, QtvBind: "127.0.0.1", QtvPort: 28000,
		ProxyBind: "127.0.0.1", ProxyPort: 30000}
	k := play.DefaultOptions()
	f := &hostFlags{}
	p := hostParser(o, &k, f)
	if err := p.Parse(args); err != nil {
		return nil, err
	}
	positional := p.Positional()
	if len(positional) > 1 {
		return nil, usagef("unexpected arguments: %s", strings.Join(positional[1:], " "))
	}
	if len(positional) == 1 {
		o.Selection = positional[0]
	}
	if f.botsGiven {
		k.Bots = f.bots
	}
	if f.skillGiven {
		k.BotSkill = f.skill
	}
	if f.weapon != "" {
		k.BotWeapon = &f.weapon
	}
	if f.healthGiven {
		k.BotHealth = &f.health
	}
	if f.on || f.off {
		value := f.on
		k.BotBreakOnDeath = &value
	}
	if f.names != "" {
		k.BotNamesProfile = f.names
	}
	if o.Preset != "" {
		if o.Selection != "" {
			return nil, usagef("--preset cannot be combined with a game on the command line")
		}
		presets, err := LoadPresets(o.Target)
		if err != nil {
			return nil, usagef("%s", err.Error())
		}
		preset, ok := presets[o.Preset]
		if !ok {
			return nil, usagef("host preset not found: %s", o.Preset)
		}
		ApplyPreset(o, preset)
		o.ModeGiven = o.Mode != ""
	}
	o.Game = ""
	if o.Selection != "" {
		for _, game := range games {
			if strings.EqualFold(game.ID, o.Selection) {
				o.Game = game.ID
			}
		}
		if o.Game == "" {
			return nil, usagef("unknown game: %s", o.Selection)
		}
	}
	// A dedicated server takes no --bot-team, pacemaker or hidden runners.
	if o.ModeGiven {
		if o.Game != "" && o.Game != "ktx" {
			return nil, usagef("--mode can only be used with the KTX game")
		}
		o.Game = "ktx"
	}
	modifiers := f.skillGiven || k.BotWeapon != nil || k.BotHealth != nil || k.BotBreakOnDeath != nil || k.BotNamesProfile != "default"
	if modifiers && !(k.Bots > 0 || k.FillBots) {
		return nil, usagef("--bot-* options need --bots or --fill-bots")
	}
	ktxSpecific := k.Bots > 0 || k.FillBots || f.skillGiven || k.BotWeapon != nil || k.BotHealth != nil ||
		(k.BotBreakOnDeath != nil && *k.BotBreakOnDeath) || k.BotNamesProfile != "default" ||
		k.CTFHook != nil || k.CTFRunes != nil || k.CTFBasedSpawn || k.RaceStyle != nil || k.RaceScoring != nil
	if ktxSpecific {
		if o.Game != "" && o.Game != "ktx" {
			return nil, usagef("bot, CTF and Race options can only be used with the KTX game")
		}
		o.Game = "ktx"
	}
	o.KtxOptions = k
	return o, nil
}

func proxyParser(o *ProxyOptions) *cliargs.Parser {
	p := cliargs.New()
	p.Value(bindFlag(&o.ProxyBind), "IP", "address the proxy listens on (default 127.0.0.1)", "--bind")
	p.Int(&o.ProxyPort, 1024, 65535, "PORT", "UDP port (default 30000)", "--port")
	common(p, &o.Common, true)
	return p
}

// ParseProxy parses `proxy`.
func ParseProxy(args []string) (*ProxyOptions, error) {
	o := &ProxyOptions{ProxyBind: "127.0.0.1", ProxyPort: 30000}
	p := proxyParser(o)
	if err := p.Parse(args); err != nil {
		return nil, err
	}
	if len(p.Positional()) > 0 {
		return nil, usagef("unexpected arguments: %s", strings.Join(p.Positional(), " "))
	}
	return o, nil
}

func qtvParser(o *QtvOptions) *cliargs.Parser {
	p := cliargs.New()
	p.Value(bindFlag(&o.Bind), "IP", "address QTV listens on (default 127.0.0.1)", "--bind")
	p.Int(&o.Port, 1024, 65535, "PORT", "TCP port (default 28000)", "--port")
	p.String(&o.Hostname, "NAME", "relay name", "--hostname")
	p.Value(func(value string) error {
		normalized, err := ParseNetworkEndpoint(value)
		if err != nil {
			return err
		}
		o.Upstream = normalized
		return nil
	}, "HOST:PORT", "server QTV relays", "--upstream")
	passwordSource(p, "QTV", &o.QtvPassword, &o.PromptQtvPassword, &o.QtvPasswordFile, []string{"--password", "--qtv-password"}, "--prompt-qtv-password", "--qtv-password-file")
	common(p, &o.Common, true)
	return p
}

// ParseQtv parses `qtv`.
func ParseQtv(args []string) (*QtvOptions, error) {
	o := &QtvOptions{Bind: "127.0.0.1", Port: 28000, Hostname: "x86QW QTV"}
	p := qtvParser(o)
	if err := p.Parse(args); err != nil {
		return nil, err
	}
	if len(p.Positional()) > 0 {
		return nil, usagef("unexpected arguments: %s", strings.Join(p.Positional(), " "))
	}
	return o, nil
}

func statusParser(o *StatusOptions) *cliargs.Parser {
	p := cliargs.New()
	p.Bool(&o.Stop, "stops the running stack", "--stop")
	p.Bool(&o.Yes, "stops without asking", "--yes")
	common(p, &o.Common, false)
	return p
}

// ParseStatus parses `status`; the installation may also come positionally.
func ParseStatus(args []string) (*StatusOptions, error) {
	o := &StatusOptions{}
	p := statusParser(o)
	if err := p.Parse(args); err != nil {
		return nil, err
	}
	positional := p.Positional()
	if len(positional) > 1 || (len(positional) == 1 && o.Target != "" && o.Target != positional[0]) {
		return nil, usagef("unexpected arguments: %s", strings.Join(positional, " "))
	}
	if len(positional) == 1 {
		o.Target = positional[0]
	}
	return o, nil
}

// HostParser, QtvParser, ProxyParser and StatusParser are the parsers of
// those commands with nothing bound, for their --help.
func HostParser() *cliargs.Parser {
	k := play.DefaultOptions()
	return hostParser(&HostOptions{}, &k, &hostFlags{})
}

func QtvParser() *cliargs.Parser { return qtvParser(&QtvOptions{}) }

func ProxyParser() *cliargs.Parser { return proxyParser(&ProxyOptions{}) }

func StatusParser() *cliargs.Parser { return statusParser(&StatusOptions{}) }
