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
		return Instance{}, usagef("use MODO:MAPA:PORTA ou MODO:MAPA:PORTA:bots, não %q", value)
	}
	instance := Instance{Mode: parts[0], Map: parts[1], Bots: len(parts) == 4}
	if err := boundedInteger(1024, 65535, &instance.Port)(parts[2]); err != nil {
		return Instance{}, err
	}
	return instance, nil
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

// Parsers. Each Parse* returns cliargs.ErrHelp when asked for help;
// the *Parser functions build the same parsers for the help text.

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
		return usagef("escolha inválida: '%s' (use %s)", value, strings.Join(choices, ", "))
	}
}

func common(p *cliargs.Parser, c *Common, background bool) {
	p.String(&c.Target, "DESTINO", "pasta da instalação", "--target")
	p.Bool(&c.Verbose, "detalhes técnicos", "-v", "--verbose")
	p.Bool(&c.NoColor, "sem cores", "--no-color")
	p.Bool(&c.Menu, "abre a tela de servidor do app", "--menu")
	if background {
		p.Bool(&c.Background, "roda em segundo plano", "--background")
		p.Bool(&c.BackgroundChild, "", "--background-child")
		p.String(&c.BackgroundLog, "ARQUIVO", "", "--background-log")
		p.Hidden("--background-child")
		p.Hidden("--background-log")
	}
}

func passwordSource(p *cliargs.Parser, what string, value *string, prompt *bool, file *string, names []string, promptFlag, fileFlag string) {
	p.String(value, "SENHA", "senha "+what+" (aparece na lista de processos; prefira o arquivo)", names...)
	p.Bool(prompt, "pergunta a senha "+what, promptFlag)
	p.String(file, "ARQUIVO", "lê a senha "+what+" de um arquivo", fileFlag)
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
	p.Value(func(value string) error { o.Mode, o.ModeGiven = value, true; return nil }, "MODO", "modo KTX: duel, 2on2, 4on4, ffa, ctf, race...", "--mode")
	p.String(&o.Map, "MAPA", "mapa inicial", "--map")
	p.Value(func(value string) error { f.botsGiven = true; return boundedInteger(1, 31, &f.bots)(value) }, "N", "quantos Frogbots entram (1 a 31)", "--bots")
	p.Bool(&k.FillBots, "completa as vagas com Frogbots", "--fill-bots")
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
	}, "1-20|random", "habilidade dos bots", "--bot-skill")
	p.Choice(&f.weapon, []string{"random", "1", "2", "3", "4", "5", "6", "7", "8"}, "arma inicial dos bots", "--bot-weapon")
	p.Value(func(value string) error { f.healthGiven = true; return boundedInteger(1, 300, &f.health)(value) }, "N", "vida dos bots (1 a 300)", "--bot-health")
	p.Bool(&f.on, "bots largam a mochila ao morrer", "--bot-break-on-death")
	p.Bool(&f.off, "bots não largam a mochila", "--no-bot-break-on-death")
	p.Exclusive("--bot-break-on-death", "--no-bot-break-on-death")
	p.Choice(&f.names, []string{"default", "x86qw", "one-piece", "personal"}, "nomes dos bots", "--bot-names")
	p.Value(choicePointer(&k.CTFHook, "smooth", "fast", "classic", "crhook", "off"), "smooth|fast|classic|crhook|off", "gancho do CTF", "--ctf-hook")
	p.Value(choicePointer(&k.CTFRunes, "on", "off"), "on|off", "runas do CTF", "--ctf-runes")
	p.Bool(&k.CTFBasedSpawn, "CTF nasce na base", "--ctf-based-spawn")
	p.Value(choicePointer(&k.RaceStyle, "solo", "simultaneous", "match"), "solo|simultaneous|match", "estilo da Race", "--race-style")
	p.Value(choicePointer(&k.RaceScoring, "win", "scaled", "formula1"), "win|scaled|formula1", "pontuação da Race", "--race-scoring")
	p.Value(bindFlag(&o.Bind), "IP", "endereço onde o servidor escuta (padrão 127.0.0.1)", "--bind")
	p.Int(&o.Port, 1024, 65535, "PORTA", "porta UDP do servidor (padrão 28501)", "--port")
	p.String(&o.Hostname, "NOME", "nome do servidor na lista", "--hostname")
	p.Int(&o.Maxclients, 1, 32, "N", "vagas (1 a 32, padrão 16)", "--maxclients")
	passwordSource(p, "de jogador", &o.Password, &o.PromptPassword, &o.PasswordFile, []string{"--password"}, "--prompt-password", "--password-file")
	passwordSource(p, "de espectador", &o.SpectatorPassword, &o.PromptSpectatorPassword, &o.SpectatorPasswordFile, []string{"--spectator-password"}, "--prompt-spectator-password", "--spectator-password-file")
	passwordSource(p, "de rcon", &o.RconPassword, &o.PromptRconPassword, &o.RconPasswordFile, []string{"--rcon-password"}, "--prompt-rcon-password", "--rcon-password-file")
	p.Bool(&o.NoMVD, "não grava demos MVD", "--no-mvd")
	p.Bool(&o.WithQTV, "inicia o relay QTV junto", "--with-qtv")
	p.Value(bindFlag(&o.QtvBind), "IP", "endereço do QTV", "--qtv-bind")
	p.Int(&o.QtvPort, 1024, 65535, "PORTA", "porta TCP do QTV (padrão 28000)", "--qtv-port")
	passwordSource(p, "do QTV", &o.QtvPassword, &o.PromptQtvPassword, &o.QtvPasswordFile, []string{"--qtv-password"}, "--prompt-qtv-password", "--qtv-password-file")
	p.Bool(&o.WithProxy, "inicia o proxy QWFWD junto", "--with-proxy")
	p.Value(bindFlag(&o.ProxyBind), "IP", "endereço do QWFWD", "--proxy-bind")
	p.Int(&o.ProxyPort, 1024, 65535, "PORTA", "porta UDP do QWFWD (padrão 30000)", "--proxy-port")
	p.String(&o.Preset, "NOME", "usa um preset salvo", "--preset")
	p.Value(func(value string) error {
		instance, err := parseInstance(value)
		if err == nil {
			o.Instances = append(o.Instances, instance)
		}
		return err
	}, "MODO:MAPA:PORTA[:bots]", "mais um servidor KTX na mesma sessão (repetível)", "--instance")
	p.String(&o.SavePreset, "NOME", "salva estas opções como preset", "--save-preset")
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
		return nil, usagef("argumentos a mais: %s", strings.Join(positional[1:], " "))
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
	// A dedicated server takes no --bot-team, pacemaker or hidden runners.
	if o.ModeGiven {
		if o.Game != "" && o.Game != "ktx" {
			return nil, usagef("--mode só pode ser usado com o jogo KTX")
		}
		o.Game = "ktx"
	}
	modifiers := f.skillGiven || k.BotWeapon != nil || k.BotHealth != nil || k.BotBreakOnDeath != nil || k.BotNamesProfile != "default"
	if modifiers && !(k.Bots > 0 || k.FillBots) {
		return nil, usagef("opções --bot-* exigem --bots ou --fill-bots")
	}
	ktxSpecific := k.Bots > 0 || k.FillBots || f.skillGiven || k.BotWeapon != nil || k.BotHealth != nil ||
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

func proxyParser(o *ProxyOptions) *cliargs.Parser {
	p := cliargs.New()
	p.Value(bindFlag(&o.ProxyBind), "IP", "endereço onde o proxy escuta (padrão 127.0.0.1)", "--bind")
	p.Int(&o.ProxyPort, 1024, 65535, "PORTA", "porta UDP (padrão 30000)", "--port")
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
		return nil, usagef("argumentos a mais: %s", strings.Join(p.Positional(), " "))
	}
	return o, nil
}

func qtvParser(o *QtvOptions) *cliargs.Parser {
	p := cliargs.New()
	p.Value(bindFlag(&o.Bind), "IP", "endereço onde o QTV escuta (padrão 127.0.0.1)", "--bind")
	p.Int(&o.Port, 1024, 65535, "PORTA", "porta TCP (padrão 28000)", "--port")
	p.String(&o.Hostname, "NOME", "nome do relay", "--hostname")
	p.Value(func(value string) error {
		normalized, err := ParseNetworkEndpoint(value)
		if err != nil {
			return err
		}
		o.Upstream = normalized
		return nil
	}, "HOST:PORTA", "servidor que o QTV transmite", "--upstream")
	passwordSource(p, "do QTV", &o.QtvPassword, &o.PromptQtvPassword, &o.QtvPasswordFile, []string{"--password", "--qtv-password"}, "--prompt-qtv-password", "--qtv-password-file")
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
		return nil, usagef("argumentos a mais: %s", strings.Join(p.Positional(), " "))
	}
	return o, nil
}

func statusParser(o *StatusOptions) *cliargs.Parser {
	p := cliargs.New()
	p.Bool(&o.Stop, "encerra a stack ativa", "--stop")
	p.Bool(&o.Yes, "encerra sem perguntar", "--yes")
	common(p, &o.Common, false)
	p.Hidden("--menu")
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
		return nil, usagef("argumentos a mais: %s", strings.Join(positional, " "))
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
