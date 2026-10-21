package services

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/play"
)

// StartupRcon is MVDSV's post-map bootstrap (models.StartupRcon).
type StartupRcon struct {
	Address         string
	Port            int
	Password        string
	ConfigName      string
	ExpectedMap     string
	ExpectedGamedir string
}

// Readiness is a QTV (http) or QWFWD (udp) readiness probe.
type Readiness struct {
	Kind     string
	Address  string
	Port     int
	Upstream string
}

// Spec is one supervised process (models.ProcessSpec).
type Spec struct {
	Label          string
	Arguments      []string
	Cwd            string
	StartupRcon    *StartupRcon
	Readiness      *Readiness
	Parameters     [][2]string
	ExpectedSHA256 string
}

// Session collects what one service run created.
type Session struct {
	Temporary    []*TemporaryConfig
	Materialized []*Materialized
	// published holds the PK3s already materialized, so several servers
	// of one game share a single copy.
	published map[string]bool
}

var dedicatedModeCvars = map[string][][2]string{
	"midair":       {{"deathmatch", "4"}, {"k_midair", "1"}},
	"dmm4":         {{"deathmatch", "4"}},
	"instagib":     {{"deathmatch", "4"}, {"k_instagib", "1"}},
	"lgc":          {{"deathmatch", "4"}, {"k_lgcmode", "1"}},
	"rocket-arena": {{"k_rocketarena", "1"}},
	"race":         {{"k_race", "1"}, {"srv_practice_mode", "1"}, {"lock_practice", "1"}, {"allow_toggle_practice", "0"}, {"qtv_sayenabled", "1"}},
	"practice":     {{"srv_practice_mode", "1"}},
}

var runtimePaths = map[string]map[string]string{
	"mvdsv": {"macos-arm64": "mvdsv", "linux-amd64": "mvdsv", "windows-x64": "mvdsv.exe"},
	"qtv":   {"macos-arm64": "qtv/qtv", "linux-amd64": "qtv/qtv", "windows-x64": "qtv/qtv.exe"},
	"qwfwd": {"macos-arm64": "qwfwd/qwfwd", "linux-amd64": "qwfwd/qwfwd", "windows-x64": "qwfwd/qwfwd.exe"},
}

// runtimeBinary is a runtime's launch target: verified component, host
// variant, executable file and the inventory hash used at exec time.
func runtimeBinary(target, component string) (string, string, error) {
	record, err := install.VerifyComponentRecord(target, component)
	if err != nil {
		return "", "", err
	}
	if record == nil {
		return "", "", errorf("O componente %s não está instalado. Execute install.sh e selecione o perfil completo ou esse componente.", component)
	}
	variant, err := install.HostVariant()
	if err != nil {
		return "", "", err
	}
	relative, ok := runtimePaths[component][variant]
	if !ok {
		return "", "", errorf("Runtime %s indisponível na variante %s.", component, variant)
	}
	binary := filepath.Join(target, filepath.FromSlash(relative))
	info, err := os.Lstat(binary)
	if err != nil || !info.Mode().IsRegular() {
		return "", "", errorf("Executável gerenciado ausente ou inseguro: %s", binary)
	}
	if runtime.GOOS != "windows" && info.Mode().Perm()&0o111 == 0 {
		return "", "", errorf("Executável gerenciado sem permissão de execução: %s. Execute repair.", binary)
	}
	for _, entry := range record.Inventory.Entries {
		if entry.Path == relative {
			return binary, entry.SHA256, nil
		}
	}
	return "", "", errorf("O inventário de %s não registra seu executável: %s", component, relative)
}

// Hosted is the selected game, mode and map (services.HostedGame).
type Hosted struct {
	Game    play.Game
	Mode    *play.Mode
	Map     string
	Assets  map[string]bool
	Options play.Options
}

// SelectHosted picks the installed game, mode and map a host request names.
func SelectHosted(target string, o *HostOptions) (*Hosted, error) {
	if err := install.CheckPaks(target); err != nil {
		return nil, err
	}
	games, err := install.LoadGames()
	if err != nil {
		return nil, err
	}
	available := install.AvailableGames(target, games)
	if len(available) == 0 {
		return nil, errorf("Nenhum mod gerenciado está instalado. Execute o bootstrap e selecione um jogo.")
	}
	if o.Game == "" {
		ids := make([]string, len(available))
		for index, game := range available {
			ids[index] = game.ID
		}
		return nil, errorf("Informe o jogo a hospedar (%s) ou use --menu.", strings.Join(ids, ", "))
	}
	var installed *install.Game
	for index := range available {
		if strings.EqualFold(available[index].ID, o.Game) {
			installed = &available[index]
		}
	}
	var game play.Game
	for _, candidate := range games {
		if candidate.ID == o.Game {
			game = candidate
		}
	}
	if installed == nil {
		return nil, errorf("%s não está instalado. Adicione o componente %s pelo bootstrap x86QW.", game.Label, game.Component)
	}
	component := installed.Component
	if !hasRecord(target, component) {
		for _, legacy := range installed.LegacyComponents {
			if hasRecord(target, legacy) {
				component = legacy
			}
		}
	}
	if _, err := install.VerifyComponentRecord(target, component); err != nil {
		return nil, err
	}
	issues, _, err := install.PlaySupportIssues(target, available)
	if err != nil {
		return nil, err
	}
	if len(issues) > 0 {
		return nil, errorf("Suporte de execução incompleto (%s). Execute update, upgrade ou repair antes de jogar ou hospedar.", strings.Join(issues, "; "))
	}
	hosted := &Hosted{Game: game, Options: o.KtxOptions}
	if game.UsesModeCatalog() {
		if !o.ModeGiven {
			return nil, errorf("Informe --mode para hospedar KTX ou use --menu.")
		}
		modes, err := play.LoadModes()
		if err != nil {
			return nil, err
		}
		mode, err := play.FindMode(modes, o.Mode)
		if err != nil {
			return nil, err
		}
		hosted.Mode = &mode
		if hosted.Options, err = play.ResolveNames(target, game, o.KtxOptions, &mode); err != nil {
			return nil, err
		}
		pk3 := filepath.Join(target, "qw", "ktx.pk3")
		if info, err := os.Lstat(pk3); err != nil || !info.Mode().IsRegular() {
			return nil, errorf("Pacote KTX ausente ou inseguro: %s", pk3)
		}
		if hosted.Assets, err = play.KtxAssets(target); err != nil {
			return nil, errorf("Pacote KTX inválido: %s", pk3)
		}
		if o.Map == "" {
			return nil, errorf("Informe --map ou use --menu.")
		}
		hosted.Map, err = play.ResolveMap(target, game, o.Map, play.ActiveRequirements(mode, hosted.Options), hosted.Assets, "KTX · "+mode.Label)
		if err != nil {
			return nil, err
		}
		return hosted, nil
	}
	if o.Map == "" {
		return nil, errorf("Informe --map ou use --menu.")
	}
	hosted.Map, err = play.ResolveMap(target, game, o.Map, nil, nil, game.Label)
	return hosted, err
}

func hasRecord(target, id string) bool {
	_, err := os.Stat(filepath.Join(target, ".x86qw", "components", id, "receipt"))
	return err == nil
}

func gameMarkerPath(target string, game install.Game) string {
	marker := filepath.Join(target, filepath.FromSlash(game.Marker))
	if info, err := os.Stat(marker); (err != nil || !info.Mode().IsRegular()) && game.LegacyMarker != "" {
		legacy := filepath.Join(target, filepath.FromSlash(game.LegacyMarker))
		if info, err := os.Stat(legacy); err == nil && info.Mode().IsRegular() {
			return legacy
		}
	}
	return marker
}

// dedicatedKtxSettings are the KTX settings a dedicated server starts with.
func dedicatedKtxSettings(mode play.Mode, mapName string, assets map[string]bool, o play.Options, maxclients int) ([][2]string, error) {
	if _, err := play.LaunchCommands(mode, mapName, assets, o); err != nil {
		return nil, err
	}
	settings := append([][2]string{}, dedicatedModeCvars[mode.Key]...)
	if play.BotOptionsRequested(o) {
		targetClients := play.RequestedNames(o, &mode) + 1
		if targetClients > maxclients {
			return nil, errorf("%s com os Frogbots selecionados exige --maxclients de pelo menos %d.", mode.Label, targetClients)
		}
		skill, random := o.BotSkill, "0"
		if o.BotSkill == "random" {
			skill, random = "5", "1"
		}
		settings = append(settings,
			[2]string{"k_fb_enabled", "1"}, [2]string{"k_fb_skill", skill}, [2]string{"k_fb_skill_random", random},
			[2]string{"k_fb_autoadd_limit", fmt.Sprint(targetClients)}, [2]string{"k_fb_autoremove_at", fmt.Sprint(targetClients)})
		names, err := play.BotNameBinarySettings(o, &mode)
		if err != nil {
			return nil, err
		}
		settings = append(settings, names...)
		if o.BotWeapon != nil {
			weapon := *o.BotWeapon
			if weapon == "random" {
				weapon = "0"
			}
			settings = append(settings, [2]string{"k_fb_weapon", weapon})
		}
		if o.BotHealth != nil {
			settings = append(settings, [2]string{"k_fb_health", fmt.Sprint(*o.BotHealth)})
		}
		if o.BotBreakOnDeath != nil {
			value := "0"
			if *o.BotBreakOnDeath {
				value = "1"
			}
			settings = append(settings, [2]string{"k_fb_break_on_death", value})
		}
	}
	if mode.Key == "ctf" {
		if o.CTFHook != nil {
			enabled := "1"
			if *o.CTFHook == "off" {
				enabled = "0"
			}
			settings = append(settings, [2]string{"k_ctf_hook", enabled})
			if *o.CTFHook != "off" {
				style := map[string]string{"smooth": "1", "fast": "2", "classic": "3", "crhook": "4"}[*o.CTFHook]
				settings = append(settings, [2]string{"k_ctf_hookstyle", style})
			}
		}
		if o.CTFRunes != nil {
			value := "0"
			if *o.CTFRunes == "on" {
				value = "1"
			}
			settings = append(settings, [2]string{"k_ctf_runes", value})
		}
		if o.CTFBasedSpawn {
			settings = append(settings, [2]string{"k_ctf_based_spawn", "1"})
		}
	}
	if mode.Key == "race" {
		if o.RaceStyle != nil {
			simultaneous, match := "0", "0"
			if *o.RaceStyle == "simultaneous" {
				simultaneous = "1"
			}
			if *o.RaceStyle == "match" {
				match = "1"
			}
			settings = append(settings, [2]string{"k_race_simultaneous", simultaneous}, [2]string{"k_race_match", match})
		}
		if o.RaceScoring != nil {
			scoring := map[string]string{"win": "0", "scaled": "1", "formula1": "2"}[*o.RaceScoring]
			settings = append(settings, [2]string{"k_race_match", "1"}, [2]string{"k_race_scoring_system", scoring})
		}
	}
	return settings, nil
}

func isBinaryName(name string) bool {
	return strings.HasPrefix(name, "k_fb_name_") || strings.HasPrefix(name, "k_fb_name_team_") || strings.HasPrefix(name, "k_fb_name_enemy_")
}

// HostSpec is how to start MVDSV for a hosted game.
func HostSpec(target string, o *HostOptions, hosted *Hosted, session *Session, journal *Journal) (*Spec, error) {
	binary, digest, err := runtimeBinary(target, "mvdsv")
	if err != nil {
		return nil, err
	}
	game := hosted.Game
	if marker := gameMarkerPath(target, game); strings.EqualFold(filepath.Ext(marker), ".pk3") && !session.published[marker] {
		if session.published == nil {
			session.published = map[string]bool{}
		}
		session.published[marker] = true
		gamedir := filepath.Join(target, game.Gamedir)
		materialized, err := MaterializePK3(marker, gamedir, game.Label, journal)
		if err != nil {
			return nil, err
		}
		materialized.SuppressAutoexec(gamedir)
		session.Materialized = append(session.Materialized, materialized)
	}
	hostname := o.Hostname
	if hostname == "" {
		hostname = "x86QW - " + game.Label
	}
	userConfig := filepath.Base(filepath.FromSlash(game.PersonalConfig))
	var postMap [][2]string
	if hosted.Mode != nil {
		if postMap, err = dedicatedKtxSettings(*hosted.Mode, hosted.Map, hosted.Assets, hosted.Options, o.Maxclients); err != nil {
			return nil, err
		}
		if play.BotOptionsRequested(hosted.Options) && IsExternalBind(o.Bind) {
			postMap = append(postMap, [2]string{"k_fb_admin_only", "1"})
		}
	}
	bootstrap := tokenHex(12)
	safeHostname, err := SafeText(hostname, "hostname", 96)
	if err != nil {
		return nil, err
	}
	mvd := "1"
	if o.NoMVD {
		mvd = "0"
	}
	lines := []string{
		"exec " + userConfig,
		"hostname " + q(safeHostname),
		fmt.Sprintf("maxclients %d", o.Maxclients),
		"password " + q(o.Password),
		"spectator_password " + q(o.SpectatorPassword),
		"rcon_password " + q(bootstrap),
		"sv_crypt_rcon 0",
		"set demo_tmp_record " + mvd,
	}
	if hosted.Mode != nil {
		mode := hosted.Mode
		lines = append(lines, "sv_progtype 2", "sv_mintic 0.01", "sv_maxtic 0.03", "pm_ktjump 1",
			"set k_defmode "+mode.Usermode, "set k_defmap "+hosted.Map, "set x86qw_ktx_preset "+mode.Key)
		for _, setting := range mode.LaunchSettings {
			lines = append(lines, setting[0]+" "+setting[1])
		}
		for _, setting := range postMap {
			if setting[0] == "k_fb_admin_only" {
				lines = append(lines, "k_fb_admin_only 1")
				break
			}
		}
	} else {
		lines = append(lines, "sv_progtype 0", "sv_gamedir "+game.Gamedir, "sv_progsname x86qw_"+game.Gamedir,
			"sv_mintic 0", "sv_maxtic 0.1", "pm_ktjump 0.5")
		for _, setting := range game.DedicatedSettings {
			lines = append(lines, setting[0]+" "+setting[1])
		}
	}
	if o.WithQTV {
		lines = append(lines, fmt.Sprintf("qtv_streamport %d", o.Port), "qtv_password "+q(o.QtvPassword))
	}
	lines = append(lines, "map "+q(hosted.Map))
	gameDirectory := filepath.Join(target, game.Gamedir)
	sessionConfig, err := WriteTemporaryConfig(gameDirectory, "xh_", toBytes(lines), journal)
	if err != nil {
		return nil, err
	}
	session.Temporary = append(session.Temporary, sessionConfig)
	var postLines [][]byte
	for _, setting := range postMap {
		if isBinaryName(setting[0]) {
			postLines = append(postLines, []byte("set "+setting[0]+` "`+setting[1]+`"`))
		} else {
			postLines = append(postLines, []byte("set "+setting[0]+" "+setting[1]))
		}
	}
	postLines = append(postLines, []byte("rcon_password "+q(o.RconPassword)))
	if IsExternalBind(o.Bind) {
		postLines = append(postLines, []byte("sv_crypt_rcon 1"))
	}
	postConfig, err := WriteTemporaryConfig(gameDirectory, "xp_", postLines, journal)
	if err != nil {
		return nil, err
	}
	session.Temporary = append(session.Temporary, postConfig)
	arguments := []string{binary, "-basedir", target}
	arguments = append(arguments, game.DedicatedArguments...)
	arguments = append(arguments, "-ip", o.Bind, "-port", fmt.Sprint(o.Port), "-mem", "64", "+exec", filepath.Base(sessionConfig.Path))
	mvdLabel := "automática"
	if o.NoMVD {
		mvdLabel = "desativada"
	}
	parameters := [][2]string{
		{"game", game.Label}, {"map", hosted.Map}, {"hostname", hostname}, {"bind", o.Bind},
		{"port", fmt.Sprint(o.Port)}, {"maxclients", fmt.Sprint(o.Maxclients)}, {"memory", "64 MiB"}, {"mvd", mvdLabel},
	}
	if hosted.Mode != nil {
		parameters = append(parameters[:1], append([][2]string{{"mode", hosted.Mode.Label}}, parameters[1:]...)...)
		k := hosted.Options
		if k.FillBots {
			parameters = append(parameters, [2]string{"bots", "preencher servidor"})
		} else {
			parameters = append(parameters, [2]string{"bots", fmt.Sprint(k.Bots)})
		}
		if k.Bots > 0 || k.FillBots {
			names := map[string]string{"default": "KTX Default", "x86qw": "x86QW aleatório", "one-piece": "One Piece (opcional)", "personal": "lista pessoal"}[k.BotNamesProfile]
			if names == "" {
				names = k.BotNamesProfile
			}
			parameters = append(parameters, [2]string{"bot_skill", k.BotSkill}, [2]string{"bot_names", names})
			if k.BotTeam != nil {
				parameters = append(parameters, [2]string{"bot_team", *k.BotTeam})
			}
			if k.BotWeapon != nil {
				parameters = append(parameters, [2]string{"bot_weapon", *k.BotWeapon})
			}
			if k.BotHealth != nil {
				parameters = append(parameters, [2]string{"bot_health", fmt.Sprint(*k.BotHealth)})
			}
		}
		for _, item := range []struct {
			key   string
			value *string
		}{{"ctf_hook", k.CTFHook}, {"ctf_runes", k.CTFRunes}, {"race_style", k.RaceStyle}, {"race_scoring", k.RaceScoring}} {
			if item.value != nil {
				parameters = append(parameters, [2]string{item.key, *item.value})
			}
		}
	}
	var configured []string
	for _, item := range []struct{ label, value string }{{"jogadores", o.Password}, {"espectadores", o.SpectatorPassword}, {"RCON", o.RconPassword}} {
		if item.value != "" {
			configured = append(configured, item.label)
		}
	}
	secrets := "nenhum"
	if len(configured) > 0 {
		secrets = strings.Join(configured, ", ")
	}
	parameters = append(parameters, [2]string{"secrets", secrets})
	return &Spec{
		Label: "MVDSV", Arguments: arguments, Cwd: target, Parameters: parameters, ExpectedSHA256: digest,
		StartupRcon: &StartupRcon{LocalServiceAddress(o.Bind), o.Port, bootstrap, filepath.Base(postConfig.Path), hosted.Map, game.Gamedir},
	}, nil
}

func toBytes(lines []string) [][]byte {
	result := make([][]byte, len(lines))
	for index, line := range lines {
		result[index] = []byte(line)
	}
	return result
}

// ProxySpec is how to start QWFWD.
func ProxySpec(target, bind string, port int) (*Spec, error) {
	binary, digest, err := runtimeBinary(target, "qwfwd")
	if err != nil {
		return nil, err
	}
	directory := filepath.Join(target, "qwfwd")
	config := filepath.Join(directory, "qwfwd.cfg")
	if info, err := os.Lstat(config); err != nil || !info.Mode().IsRegular() {
		return nil, errorf("Configuração QWFWD ausente ou insegura: %s", config)
	}
	return &Spec{
		Label: "QWFWD", Arguments: []string{binary, fmt.Sprint(port), bind}, Cwd: directory, ExpectedSHA256: digest,
		Readiness:  &Readiness{Kind: "udp", Address: LocalServiceAddress(bind), Port: port},
		Parameters: [][2]string{{"bind", bind}, {"port", fmt.Sprint(port)}, {"protocol", "UDP QuakeWorld"}},
	}, nil
}

// QtvSpec is how to start QTV.
func QtvSpec(target, bind string, port int, hostname, upstream, password string, session *Session, journal *Journal) (*Spec, error) {
	binary, digest, err := runtimeBinary(target, "qtv")
	if err != nil {
		return nil, err
	}
	directory := filepath.Join(target, "qtv")
	config := filepath.Join(directory, "qtv.cfg")
	if info, err := os.Lstat(config); err != nil || !info.Mode().IsRegular() {
		return nil, errorf("Configuração QTV ausente ou insegura: %s", config)
	}
	if err := ensurePrivateDirectory(filepath.Join(directory, "demos")); err != nil {
		return nil, err
	}
	safeHostname, err := SafeText(hostname, "hostname QTV", 96)
	if err != nil {
		return nil, err
	}
	lines := []string{
		"hostname " + q(safeHostname),
		"listen_address " + q(Endpoint(bind, port)),
		`masters ""`,
		"http_enabled 1",
		"http_upload_enabled 0",
	}
	if upstream != "" {
		if upstream, err = ParseNetworkEndpoint(upstream); err != nil {
			return nil, err
		}
		if password != "" {
			if _, err := SafeText(password, "senha QTV", 96); err != nil {
				return nil, err
			}
		}
		lines = append(lines, "qtv "+q(upstream)+" "+q(password))
	}
	sessionConfig, err := WriteTemporaryConfig(directory, "x86qw-session-", toBytes(lines), journal)
	if err != nil {
		return nil, err
	}
	session.Temporary = append(session.Temporary, sessionConfig)
	upstreamLabel, secret := upstream, "não configurado"
	if upstreamLabel == "" {
		upstreamLabel = "nenhum"
	}
	if password != "" {
		secret = "configurado"
	}
	return &Spec{
		Label: "QTV", Arguments: []string{binary, "exec", filepath.Base(sessionConfig.Path)}, Cwd: directory, ExpectedSHA256: digest,
		Readiness: &Readiness{Kind: "http", Address: LocalServiceAddress(bind), Port: port, Upstream: upstream},
		Parameters: [][2]string{{"hostname", hostname}, {"bind", bind}, {"port", fmt.Sprint(port)},
			{"http", "http://" + Endpoint(LocalServiceAddress(bind), port) + "/"}, {"upstream", upstreamLabel}, {"upstream_secret", secret}},
	}, nil
}
