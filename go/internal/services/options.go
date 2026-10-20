package services

import (
	"fmt"
	"strconv"
	"strings"

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

// HostOptions mirrors the `host` argparse namespace.
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
}

// ProxyOptions mirrors the `proxy` namespace.
type ProxyOptions struct {
	Common
	ProxyBind string
	ProxyPort int
}

// QtvOptions mirrors the `qtv` namespace.
type QtvOptions struct {
	Common
	Bind, Hostname, Upstream string
	Port                     int
	QtvPassword              string
	PromptQtvPassword        bool
	QtvPasswordFile          string
}

// StatusOptions mirrors the `status` namespace.
type StatusOptions struct {
	Common
	Stop, Yes bool
}

type flagSpec struct {
	value bool
	apply func(string) error
}

type parser struct {
	specs      map[string]flagSpec
	positional []string
	groups     [][]string
	seen       map[string]bool
}

func newParser() *parser { return &parser{specs: map[string]flagSpec{}, seen: map[string]bool{}} }

func (p *parser) boolFlag(target *bool, names ...string) {
	for _, name := range names {
		p.specs[name] = flagSpec{apply: func(string) error { *target = true; return nil }}
	}
}

func (p *parser) valueFlag(apply func(string) error, names ...string) {
	for _, name := range names {
		p.specs[name] = flagSpec{value: true, apply: apply}
	}
}

func (p *parser) exclusive(names ...string) { p.groups = append(p.groups, names) }

func (p *parser) parse(args []string) error {
	for index := 0; index < len(args); index++ {
		arg := args[index]
		if arg == "--" {
			p.positional = append(p.positional, args[index+1:]...)
			break
		}
		if !strings.HasPrefix(arg, "-") || arg == "-" {
			p.positional = append(p.positional, arg)
			continue
		}
		name, inline, hasInline := strings.Cut(arg, "=")
		spec, ok := p.specs[name]
		if !ok {
			return usagef("argumentos não reconhecidos: %s", arg)
		}
		value := ""
		if spec.value {
			if hasInline {
				value = inline
			} else {
				if index+1 >= len(args) {
					return usagef("argumento %s: esperado um argumento", name)
				}
				index++
				value = args[index]
			}
		} else if hasInline {
			return usagef("argumento %s: argumento explícito ignorado: '%s'", name, inline)
		}
		for _, group := range p.groups {
			for _, member := range group {
				if member != name {
					continue
				}
				for _, other := range group {
					if other != name && p.seen[other] {
						return usagef("argumento %s: não permitido com o argumento %s", name, other)
					}
				}
			}
		}
		p.seen[name] = true
		if err := spec.apply(value); err != nil {
			if IsUsage(err) {
				return usagef("argumento %s: %s", name, err.Error())
			}
			return err
		}
	}
	return nil
}

func boundedInteger(minimum, maximum int, target *int) func(string) error {
	return func(value string) error {
		parsed, err := strconv.Atoi(strings.TrimSpace(value))
		if err != nil {
			return usagef("deve ser um número inteiro")
		}
		if parsed < minimum || parsed > maximum {
			return usagef("deve estar entre %d e %d", minimum, maximum)
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

func stringFlag(target *string) func(string) error {
	return func(value string) error { *target = value; return nil }
}

func choiceFlag(target **string, choices ...string) func(string) error {
	return func(value string) error {
		for _, choice := range choices {
			if choice == value {
				copied := value
				*target = &copied
				return nil
			}
		}
		return usagef("escolha inválida: '%s'", value)
	}
}

func (p *parser) common(c *Common, background bool) {
	p.valueFlag(stringFlag(&c.Target), "--target")
	p.boolFlag(&c.Verbose, "-v", "--verbose")
	p.boolFlag(&c.NoColor, "--no-color")
	p.boolFlag(&c.Menu, "--menu")
	if background {
		p.boolFlag(&c.Background, "--background")
		p.boolFlag(&c.BackgroundChild, "--background-child")
		p.valueFlag(stringFlag(&c.BackgroundLog), "--background-log")
	}
}

func (p *parser) passwordSource(value *string, prompt *bool, file *string, legacy []string, promptFlag, fileFlag string) {
	p.valueFlag(stringFlag(value), legacy...)
	p.boolFlag(prompt, promptFlag)
	p.valueFlag(stringFlag(file), fileFlag)
	p.exclusive(append(append([]string{}, legacy...), promptFlag, fileFlag)...)
}

// ParseHost parses `host` arguments (dedicated game-launch flags included).
func ParseHost(args []string, games []play.Game) (*HostOptions, error) {
	o := &HostOptions{Bind: "127.0.0.1", Port: 28501, Maxclients: 16, QtvBind: "127.0.0.1", QtvPort: 28000,
		ProxyBind: "127.0.0.1", ProxyPort: 30000}
	k := play.DefaultOptions()
	p := newParser()
	p.common(&o.Common, true)
	var bots, health int
	var skill, weapon, names string
	var botsGiven, healthGiven, skillGiven, breakOn, breakOff bool
	p.valueFlag(func(value string) error { o.Mode, o.ModeGiven = value, true; return nil }, "--mode")
	p.valueFlag(stringFlag(&o.Map), "--map")
	p.valueFlag(func(value string) error { botsGiven = true; return boundedInteger(1, 31, &bots)(value) }, "--bots")
	p.boolFlag(&k.FillBots, "--fill-bots")
	p.exclusive("--bots", "--fill-bots")
	p.valueFlag(func(value string) error {
		skillGiven = true
		if strings.EqualFold(value, "random") {
			skill = "random"
			return nil
		}
		var parsed int
		if err := boundedInteger(1, 20, &parsed)(value); err != nil {
			return err
		}
		skill = strconv.Itoa(parsed)
		return nil
	}, "--bot-skill")
	p.valueFlag(func(value string) error {
		var target *string
		if err := choiceFlag(&target, "random", "1", "2", "3", "4", "5", "6", "7", "8")(value); err != nil {
			return err
		}
		weapon = value
		return nil
	}, "--bot-weapon")
	p.valueFlag(func(value string) error { healthGiven = true; return boundedInteger(1, 300, &health)(value) }, "--bot-health")
	p.boolFlag(&breakOn, "--bot-break-on-death")
	p.boolFlag(&breakOff, "--no-bot-break-on-death")
	p.exclusive("--bot-break-on-death", "--no-bot-break-on-death")
	p.valueFlag(func(value string) error {
		var target *string
		if err := choiceFlag(&target, "default", "x86qw", "one-piece", "personal")(value); err != nil {
			return err
		}
		names = value
		return nil
	}, "--bot-names")
	p.valueFlag(choiceFlag(&k.CTFHook, "smooth", "fast", "classic", "crhook", "off"), "--ctf-hook")
	p.valueFlag(choiceFlag(&k.CTFRunes, "on", "off"), "--ctf-runes")
	p.boolFlag(&k.CTFBasedSpawn, "--ctf-based-spawn")
	p.valueFlag(choiceFlag(&k.RaceStyle, "solo", "simultaneous", "match"), "--race-style")
	p.valueFlag(choiceFlag(&k.RaceScoring, "win", "scaled", "formula1"), "--race-scoring")
	p.valueFlag(bindFlag(&o.Bind), "--bind")
	p.valueFlag(boundedInteger(1024, 65535, &o.Port), "--port")
	p.valueFlag(stringFlag(&o.Hostname), "--hostname")
	p.valueFlag(boundedInteger(1, 32, &o.Maxclients), "--maxclients")
	p.passwordSource(&o.Password, &o.PromptPassword, &o.PasswordFile, []string{"--password"}, "--prompt-password", "--password-file")
	p.passwordSource(&o.SpectatorPassword, &o.PromptSpectatorPassword, &o.SpectatorPasswordFile, []string{"--spectator-password"}, "--prompt-spectator-password", "--spectator-password-file")
	p.passwordSource(&o.RconPassword, &o.PromptRconPassword, &o.RconPasswordFile, []string{"--rcon-password"}, "--prompt-rcon-password", "--rcon-password-file")
	p.boolFlag(&o.NoMVD, "--no-mvd")
	p.boolFlag(&o.WithQTV, "--with-qtv")
	p.valueFlag(bindFlag(&o.QtvBind), "--qtv-bind")
	p.valueFlag(boundedInteger(1024, 65535, &o.QtvPort), "--qtv-port")
	p.passwordSource(&o.QtvPassword, &o.PromptQtvPassword, &o.QtvPasswordFile, []string{"--qtv-password"}, "--prompt-qtv-password", "--qtv-password-file")
	p.boolFlag(&o.WithProxy, "--with-proxy")
	p.valueFlag(bindFlag(&o.ProxyBind), "--proxy-bind")
	p.valueFlag(boundedInteger(1024, 65535, &o.ProxyPort), "--proxy-port")
	p.valueFlag(stringFlag(&o.Preset), "--preset")
	p.valueFlag(stringFlag(&o.SavePreset), "--save-preset")
	if err := p.parse(args); err != nil {
		return nil, err
	}
	if len(p.positional) > 1 {
		return nil, usagef("argumentos não reconhecidos: %s", strings.Join(p.positional[1:], " "))
	}
	if len(p.positional) == 1 {
		o.Selection = p.positional[0]
	}
	if botsGiven {
		k.Bots = bots
	}
	if skillGiven {
		k.BotSkill = skill
	}
	if weapon != "" {
		k.BotWeapon = &weapon
	}
	if healthGiven {
		k.BotHealth = &health
	}
	if breakOn || breakOff {
		value := breakOn
		k.BotBreakOnDeath = &value
	}
	if names != "" {
		k.BotNamesProfile = names
	}
	if o.Preset != "" && o.SavePreset != "" {
		return nil, usagef("--preset e --save-preset não podem ser combinados")
	}
	if o.Preset != "" {
		if o.Selection != "" {
			return nil, usagef("--preset não pode ser combinado com o jogo na linha de comando")
		}
		presets, err := LoadPresets(o.Target)
		if err != nil {
			return nil, usagef("%s", err.Error())
		}
		preset, ok := presets[o.Preset]
		if !ok {
			return nil, usagef("preset de hospedagem não encontrado: %s", o.Preset)
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
			return nil, usagef("jogo desconhecido: %s", o.Selection)
		}
	}
	// resolve_ktx_launch_options (dedicated: no --bot-team, pacemaker or hide).
	if o.ModeGiven {
		if o.Game != "" && o.Game != "ktx" {
			return nil, usagef("--mode só pode ser usado com o jogo KTX")
		}
		o.Game = "ktx"
	}
	modifiers := skillGiven || k.BotWeapon != nil || k.BotHealth != nil || k.BotBreakOnDeath != nil || k.BotNamesProfile != "default"
	if modifiers && !(k.Bots > 0 || k.FillBots) {
		return nil, usagef("opções --bot-* exigem --bots ou --fill-bots")
	}
	ktxSpecific := k.Bots > 0 || k.FillBots || skillGiven || k.BotWeapon != nil || k.BotHealth != nil ||
		(k.BotBreakOnDeath != nil && *k.BotBreakOnDeath) || k.BotNamesProfile != "default" ||
		k.CTFHook != nil || k.CTFRunes != nil || k.CTFBasedSpawn || k.RaceStyle != nil || k.RaceScoring != nil
	if ktxSpecific {
		if o.Game != "" && o.Game != "ktx" {
			return nil, usagef("opções de bots, CTF e Race só podem ser usadas com o jogo KTX")
		}
		o.Game = "ktx"
	}
	o.KtxOptions = k
	return o, nil
}

// ParseProxy parses `proxy`.
func ParseProxy(args []string) (*ProxyOptions, error) {
	o := &ProxyOptions{ProxyBind: "127.0.0.1", ProxyPort: 30000}
	p := newParser()
	p.common(&o.Common, true)
	p.valueFlag(bindFlag(&o.ProxyBind), "--bind")
	p.valueFlag(boundedInteger(1024, 65535, &o.ProxyPort), "--port")
	if err := p.parse(args); err != nil {
		return nil, err
	}
	if len(p.positional) > 0 {
		return nil, usagef("argumentos não reconhecidos: %s", strings.Join(p.positional, " "))
	}
	return o, nil
}

// ParseQtv parses `qtv`.
func ParseQtv(args []string) (*QtvOptions, error) {
	o := &QtvOptions{Bind: "127.0.0.1", Port: 28000, Hostname: "x86QW QTV"}
	p := newParser()
	p.common(&o.Common, true)
	p.valueFlag(bindFlag(&o.Bind), "--bind")
	p.valueFlag(boundedInteger(1024, 65535, &o.Port), "--port")
	p.valueFlag(stringFlag(&o.Hostname), "--hostname")
	p.valueFlag(func(value string) error {
		normalized, err := ParseNetworkEndpoint(value)
		if err != nil {
			return err
		}
		o.Upstream = normalized
		return nil
	}, "--upstream")
	p.passwordSource(&o.QtvPassword, &o.PromptQtvPassword, &o.QtvPasswordFile, []string{"--password", "--qtv-password"}, "--prompt-qtv-password", "--qtv-password-file")
	if err := p.parse(args); err != nil {
		return nil, err
	}
	if len(p.positional) > 0 {
		return nil, usagef("argumentos não reconhecidos: %s", strings.Join(p.positional, " "))
	}
	return o, nil
}

// ParseStatus parses `status` (human form).
func ParseStatus(args []string) (*StatusOptions, error) {
	o := &StatusOptions{}
	p := newParser()
	p.common(&o.Common, false)
	p.boolFlag(&o.Stop, "--stop")
	p.boolFlag(&o.Yes, "--yes")
	if err := p.parse(args); err != nil {
		return nil, err
	}
	// The Go CLI also accepts the installation as a positional argument.
	if len(p.positional) > 1 || (len(p.positional) == 1 && o.Target != "" && o.Target != p.positional[0]) {
		return nil, usagef("argumentos não reconhecidos: %s", strings.Join(p.positional, " "))
	}
	if len(p.positional) == 1 {
		o.Target = p.positional[0]
	}
	return o, nil
}

var _ = fmt.Sprint
