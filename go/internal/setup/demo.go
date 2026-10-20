package setup

import (
	"context"
	"fmt"
	"math"
	"math/rand/v2"
	"sort"
	"sync"
	"time"
)

// Demo is a Backend that installs nothing: the catalog is the real x86QW
// content, the network is detected for real, and preflight and install are
// simulated. It drives prototypes, screenshots and interface tests.
type Demo struct {
	// ExistingInstall makes Existing report an installation.
	ExistingInstall bool
	// Speed scales the simulated install (1 = about 14 s for any size).
	Speed float64

	mu      sync.Mutex
	running bool   // the demo server
	solo    string // a service the demo runs alone
}

var demoComponents = []Component{
	{"x86qw-client-bootstrap", "Base do cliente x86QW", "Inicialização, defaults e camadas globais sobre o nQuake.", "core", 28919, nil},
	{"nquake-visual-core", "Interface nQuake", "Pacotes principais do ezQuake e a identidade visual nQuake.", "core", 15351608, []string{"x86qw-client-bootstrap"}},
	{"ktx", "KTX x86QW", "KTX 1.47 com 24 modos, Frogbots e Coach de Quad.", "gameplay", 2542965, []string{"x86qw-client-bootstrap"}},
	{"nquake-maps", "Mapas nQuake", "Os mapas da distribuição nQuake com ENT e textos.", "content", 20471003, []string{"x86qw-client-bootstrap"}},
	{"nquake-base-textures", "Texturas base", "Pacote de texturas base selecionado pelo nQuake.", "content", 21149469, []string{"nquake-visual-core"}},
	{"nquake-player-skins", "Skins de jogadores", "Skins GPL e comunitárias para jogadores e times.", "content", 14552089, []string{"nquake-visual-core"}},
	{"nquake-models", "Modelos", "Modelos atualizados empacotados pelo nQuake.", "content", 4045436, []string{"nquake-visual-core"}},
	{"nquake-skyboxes", "Skyboxes", "Céus de alta resolução.", "content", 3988263, []string{"nquake-visual-core"}},
	{"nquake-external-textures", "Texturas externas", "Texturas avulsas mantidas pelo nQuake.", "content", 3835861, []string{"nquake-visual-core"}},
	{"nquake-scoreboard-flags", "Bandeiras do placar", "Bandeiras de países no placar.", "content", 368135, []string{"nquake-visual-core"}},
	{"nquake-crosshairs", "Miras", "A coleção de miras do nQuake.", "content", 14979, []string{"nquake-visual-core"}},
	{"nquake-documentation", "Documentação e licenças", "Licenças upstream e guias x86QW.", "documentation", 13000, nil},
	{"nquake-matchinfo", "Demos históricas", "Demos de partidas e treino mantidas pelo nQuake.", "content", 7411045, []string{"x86qw-client-bootstrap"}},
	{"qrp-hires", "QRP alta resolução", "Texturas e brush models do Quake Retexturing Project.", "addon", 404142164, []string{"nquake-visual-core"}},
	{"final-arena", "Final Arena", "Duelos em fila com perfil x86QW.", "addon", 6984652, []string{"x86qw-client-bootstrap"}},
	{"pro-x", "Pro-X", "Rounds, times e votações.", "addon", 9081189, []string{"x86qw-client-bootstrap"}},
	{"team-fortress", "Team Fortress", "TF 2.9 com assets e mapas nQuake.", "addon", 8566807, []string{"x86qw-client-bootstrap"}},
	{"total-destruction-2", "Total Destruction 2", "Armas, magias e runas do TD2QW 2.22.", "addon", 502676, []string{"x86qw-client-bootstrap"}},
	{"mvdsv", "MVDSV dedicado", "Servidor QuakeWorld 1.11 com MVD e QTV.", "server", 1121507, nil},
	{"qtv", "QTV", "Transmissão ao vivo e demos MVD.", "server", 12079507, nil},
	{"qwfwd", "QWFWD", "Proxy QuakeWorld 1.30.", "server", 287467, nil},
}

var demoModes = []Mode{
	{ID: "duel", Label: "Duel", DefaultMap: "dm6", Maps: []string{"dm6", "dm2", "dm4", "aerowalk", "ztndm3"}, Bots: true, Players: "2", BotLimit: 1, Description: "1 contra 1, sem times"},
	{ID: "2on2", Label: "2on2", DefaultMap: "dm6", Maps: []string{"dm6", "dm4", "aerowalk", "ztndm3"}, Bots: true, Players: "4", BotLimit: 3, Description: "duas duplas"},
	{ID: "4on4", Label: "4on4", DefaultMap: "dm3", Maps: []string{"dm3", "dm2", "e1m2", "schloss"}, Bots: true, Players: "8", BotLimit: 7, Description: "dois times de quatro"},
	{ID: "ffa", Label: "Free For All", DefaultMap: "dm6", Maps: []string{"dm6", "dm3", "dm4", "e1m1"}, Bots: true, Players: "2+", Description: "todos contra todos"},
	{ID: "clan-arena", Label: "Clan Arena", DefaultMap: "dm6", Maps: []string{"dm6", "dm3", "aerowalk"}, Bots: true, Players: "2+", Description: "rodadas por times, sem itens"},
	{ID: "ctf", Label: "Capture The Flag", DefaultMap: "e2m2", Maps: []string{"e2m2", "e1m5", "e1m3", "e2m5"}, Players: "4+", Kind: "ctf", Description: "capture a bandeira inimiga"},
	{ID: "midair", Label: "Midair", DefaultMap: "dm4", Maps: []string{"dm4", "dm6"}, Bots: true, Players: "2+", Description: "só acertos no ar valem"},
	{ID: "instagib", Label: "Instagib", DefaultMap: "dm4", Maps: []string{"dm4", "dm3", "dm6"}, Bots: true, Players: "2+", Description: "um tiro, uma morte"},
	{ID: "race", Label: "Race", DefaultMap: "dm6", Maps: []string{"dm6", "dm4"}, Players: "1+", Kind: "race", Description: "percorra a rota no menor tempo"},
	{ID: "tot", Label: "Train of Thought", DefaultMap: "dm6", Maps: []string{"dm6", "dm2"}, Bots: true, Players: "1+", Kind: "tot", Description: "treino contra frogbots"},
	{ID: "practice", Label: "Practice", DefaultMap: "dm6", Maps: []string{"dm6", "dm2", "dm4", "dm3"}, Bots: true, Players: "1+", Description: "treino livre"},
}

var demoGroups = []ModeGroup{
	{Key: "recommended", Label: "Recomendados", Modes: []string{"duel", "4on4", "ffa", "ctf"}},
	{Key: "individual", Label: "Individuais", Modes: []string{"duel", "ffa", "midair", "instagib"}},
	{Key: "teams", Label: "Times", Modes: []string{"2on2", "4on4", "clan-arena", "ctf"}},
	{Key: "training", Label: "Treino", Modes: []string{"race", "tot", "practice"}},
}

// Catalog returns the real x86QW content.
func (d *Demo) Catalog(context.Context) (*Catalog, error) {
	profiles := map[string][]string{
		"essential":   {"x86qw-client-bootstrap", "nquake-visual-core", "ktx"},
		"recommended": {"x86qw-client-bootstrap", "nquake-visual-core", "ktx", "nquake-player-skins", "nquake-crosshairs", "nquake-skyboxes", "nquake-models", "nquake-scoreboard-flags", "nquake-external-textures", "nquake-base-textures", "nquake-maps", "nquake-documentation"},
	}
	for _, component := range demoComponents {
		if component.Kind != "server" {
			profiles["complete"] = append(profiles["complete"], component.ID)
		}
	}
	return &Catalog{
		Components: demoComponents, Profiles: profiles, CoreSize: 23817401,
		ClientSize: map[string]int64{"macos": 23400000, "linux": 9800000, "windows": 11900000}, Modes: demoModes,
	}, nil
}

// Existing reports the demo installation when ExistingInstall is set.
func (d *Demo) Existing(_ context.Context, target string) (*Existing, error) {
	if !d.ExistingInstall {
		return nil, nil
	}
	options := Defaults(Both, target)
	options.Nick = "x86dx2"
	catalog, _ := d.Catalog(context.Background())
	return &Existing{Target: target, CLIVersion: "2.0.3", Components: 12, Installed: append(catalog.Profiles["recommended"], "mvdsv", "qtv", "qwfwd"), Options: options, Updates: []Update{
		{"x86qw-client-bootstrap", "Base do cliente x86QW", "e4cb23d40aa2+x86qw.4", "dfb2ac9299f7+x86qw.4", "nQuake"},
		{"ktx", "KTX x86QW", "1.47+x86qw.19", "1.47+x86qw.20", ""},
		{"x86qw-core-id1", "Dados do Quake", "0.1.0", "0.1.1", ""},
	}}, nil
}

// Network detects the real addresses.
func (d *Demo) Network(ctx context.Context) Network { return DetectNetwork(ctx) }

// CheckHostname resolves for real.
func (d *Demo) CheckHostname(ctx context.Context, hostname string, network Network) DNSCheck {
	return CheckHostname(ctx, hostname, network)
}

// Preflight simulates the checks with plausible values.
func (d *Demo) Preflight(ctx context.Context, options Options) <-chan Check {
	out := make(chan Check)
	checks := []Check{
		{ID: "folder", Title: "Pasta de instalação", Detail: "Vazia e com permissão de escrita.", Value: options.Target, State: CheckOK},
		{ID: "disk", Title: "Espaço em disco", Detail: "Sobra espaço de sobra.", Value: "412 GB livres", State: CheckOK},
		{ID: "net", Title: "Conexão", Detail: "Medida baixando 4 MB do catálogo.", Value: "41,3 MB/s", State: CheckOK},
		{ID: "catalog", Title: "Catálogo assinado", Detail: "TUF v51, válido até 18/01/2027.", Value: "33 pacotes", State: CheckOK},
	}
	if options.Intent.WantsClient() {
		checks = append(checks, Check{ID: "screen", Title: "Tela", Detail: "O vídeo começa ajustado a ela.", Value: "2560×1440 · 144 Hz", State: CheckOK})
	}
	if options.Intent.WantsServer() {
		ports := options.Server.Ports()
		check := Check{ID: "ports", Title: "Portas do servidor", Detail: fmt.Sprintf("UDP %d–%d livres.", ports[0], ports[len(ports)-1]), Value: "livres", State: CheckOK}
		if options.Server.BasePort == 27500 {
			// The demo shows the warning path: something already holds 27500.
			check = Check{ID: "ports", Title: "Portas do servidor", Detail: "UDP 27500 está em uso por outro processo.", Value: "27500 ocupada",
				Fix: "Mude a porta inicial para 27510 em Servidor, ou instale mesmo assim.", State: CheckWarning}
		}
		checks = append(checks, check)
	}
	go func() {
		defer close(out)
		for _, check := range checks {
			running := check
			running.State, running.Value = CheckRunning, ""
			select {
			case out <- running:
			case <-ctx.Done():
				return
			}
			select {
			case <-time.After(time.Duration(280+rand.IntN(260)) * time.Millisecond):
			case <-ctx.Done():
				return
			}
			select {
			case out <- check:
			case <-ctx.Done():
				return
			}
		}
	}()
	return out
}

var demoFiles = map[string][]string{
	"core":                 {"id1/pak0.pak"},
	"ezquake":              {"ezquake/ezquake.pk3", "ezQuake"},
	"nquake-maps":          {"qw/maps/dm6.bsp", "qw/maps/dm4.bsp", "qw/maps/aerowalk.bsp", "qw/maps/ztndm3.bsp", "qw/maps/e1m2.bsp", "qw/maps/schloss.bsp"},
	"nquake-base-textures": {"qw/textures.pk3"},
	"nquake-visual-core":   {"qw/nquake.pk3", "ezquake/ezquake.pk3"},
	"ktx":                  {"qw/ktx.pk3", "qw/qwprogs.qvm"},
	"qrp-hires":            {"qw/qrp_maps_textures_1.pk3", "qw/qrp_maps_textures_2.pk3", "qw/qrp_maps_textures_3.pk3", "qw/qrp_b-models.pk3"},
	"nquake-player-skins":  {"qw/skins/base.png", "qw/skins/blue.png", "qw/skins/red.png"},
	"nquake-skyboxes":      {"qw/env/tron_up.tga", "qw/env/tron_dn.tga"},
	"mvdsv":                {"mvdsv"},
	"qtv":                  {"qtv/qtv.bin", "qtv/levelshots/dm6.jpg"},
}

// Install simulates the download with a fluctuating speed; sending on
// control pauses (true) or resumes (false).
func (d *Demo) Install(ctx context.Context, options Options, control <-chan bool) <-chan Progress {
	out := make(chan Progress)
	catalog, _ := d.Catalog(ctx)
	lanes := []Lane{{ID: "core", Label: "Dados do Quake", Size: catalog.CoreSize}}
	if options.Intent.WantsClient() {
		lanes = append(lanes, Lane{ID: "ezquake", Label: "ezQuake 3.6.9", Size: catalog.ClientSize["macos"]})
	}
	for _, id := range Selected(catalog, options) {
		for _, component := range catalog.Components {
			if component.ID == id {
				lanes = append(lanes, Lane{ID: id, Label: component.Label, Size: component.Size})
			}
		}
	}
	sort.SliceStable(lanes, func(i, j int) bool { return lanes[i].Size > lanes[j].Size })
	for index := range lanes {
		lanes[index].FileCount = max(1, len(demoFiles[lanes[index].ID]))
	}
	var total int64
	for _, lane := range lanes {
		total += lane.Size
	}
	speedFactor := d.Speed
	if speedFactor <= 0 {
		speedFactor = 1
	}
	scale := math.Max(1, float64(total)/(14*38e6)) * speedFactor
	go func() {
		defer close(out)
		progress := Progress{Lanes: lanes, Total: total}
		ticker := time.NewTicker(50 * time.Millisecond)
		defer ticker.Stop()
		var elapsed time.Duration
		smoothed := 0.0
		for {
			select {
			case <-ctx.Done():
				progress.Err = ctx.Err()
				out <- progress
				return
			case paused := <-control:
				progress.Paused, progress.Holding = paused, paused
			case <-ticker.C:
			}
			if progress.Paused {
				out <- snapshot(progress)
				continue
			}
			elapsed += 50 * time.Millisecond
			t := elapsed.Seconds()
			mbs := 26 + 14*math.Sin(t*1.7) + 6*math.Sin(t*4.3) + rand.Float64()*4
			smoothed = smoothed*0.8 + mbs*0.2
			budget := mbs * 1048576 * 0.05 * scale
			var active []int
			for index := range progress.Lanes {
				if progress.Lanes[index].Done < progress.Lanes[index].Size && len(active) < 4 {
					active = append(active, index)
				}
			}
			for _, index := range active {
				lane := &progress.Lanes[index]
				take := int64(budget / float64(len(active)))
				if lane.Size < 2e6 {
					take = lane.Size
				}
				if take > lane.Size-lane.Done {
					take = lane.Size - lane.Done
				}
				lane.Done += take
				progress.Done += take
				files := demoFiles[lane.ID]
				if len(files) == 0 {
					files = []string{lane.ID + ".pk3"}
				}
				per := lane.Size / int64(len(files))
				for lane.Files < len(files) && lane.Done >= per*int64(lane.Files+1)-1 {
					kind := byte('+')
					if lane.ID == "nquake-crosshairs" {
						kind, lane.Kept = '=', lane.Kept+1
					}
					progress.Log = append(progress.Log, LogLine{At: elapsed, Kind: kind, Path: files[lane.Files], Size: per})
					lane.Files++
				}
			}
			progress.Speed = smoothed * 1048576 * scale
			if progress.Speed > 0 {
				progress.ETA = time.Duration(float64(progress.Total-progress.Done) / progress.Speed * float64(time.Second))
			}
			if progress.Done >= progress.Total {
				progress.Done, progress.Finished, progress.ETA = progress.Total, true, 0
				progress.Log = append(progress.Log, LogLine{At: elapsed, Kind: '+', Path: "configs geradas: preset.cfg, servidores e integração"})
				out <- snapshot(progress)
				return
			}
			select {
			case out <- snapshot(progress):
			case <-ctx.Done():
				return
			}
		}
	}()
	return out
}

func snapshot(progress Progress) Progress {
	progress.Lanes = append([]Lane(nil), progress.Lanes...)
	progress.Log = append([]LogLine(nil), progress.Log...)
	return progress
}

// Repair pretends to verify every file.
func (d *Demo) Repair(ctx context.Context, target string) (Report, error) {
	select {
	case <-ctx.Done():
		return Report{}, ctx.Err()
	case <-time.After(time.Duration(1200/max(d.Speed, 0.1)) * time.Millisecond):
	}
	return Report{Files: 2216, Components: 12}, nil
}

// Uninstall pretends to remove the installation.
func (d *Demo) Uninstall(ctx context.Context, target string, keepPersonal bool) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-time.After(600 * time.Millisecond):
	}
	return nil
}

var _ Backend = (*Demo)(nil)

// MatchCatalog offers KTX and two mods, with the demo modes.
func (d *Demo) MatchCatalog(context.Context, string) (*MatchCatalog, error) {
	return &MatchCatalog{
		Games: []Game{
			{ID: "ktx", Label: "KTX x86QW", Version: "1.47+x86qw.20", Description: "deathmatch, times e treino", Modes: true},
			{ID: "final-arena", Label: "Final Arena", Version: "1.20", Description: "duelos em fila"},
			{ID: "pro-x", Label: "Pro-X", Version: "1.1", Description: "rodadas, times e votações"},
		},
		Modes: demoModes, Groups: demoGroups, Channels: []string{"stable"}, Ruleset: "x86qw",
	}, nil
}

// Maps returns the mode's suggested maps, fewer with bots.
func (d *Demo) Maps(_ context.Context, _ string, match Match) (*MapList, error) {
	maps := []string{"dm6", "dm4", "dm2", "dm3", "aerowalk", "ztndm3", "e1m2", "schloss", "e2m2", "e1m5", "end", "skull"}
	list := &MapList{Suggested: map[string]bool{}}
	for _, mode := range demoModes {
		if mode.ID == match.Mode {
			for _, name := range mode.Maps {
				list.Suggested[name] = true
			}
			list.Default = mode.DefaultMap
		}
	}
	if match.Game != "" && match.Game != "ktx" {
		list.Default, list.Suggested = "dm6", map[string]bool{"dm6": true}
	}
	for name := range list.Suggested {
		list.Maps = append(list.Maps, name)
	}
	sort.Strings(list.Maps)
	for _, name := range maps {
		if !list.Suggested[name] && !(match.HasBots() && len(name) > 4) {
			list.Maps = append(list.Maps, name)
		}
	}
	list.Narrowed = match.HasBots()
	return list, nil
}

// Play pretends to open ezQuake.
func (d *Demo) Play(ctx context.Context, _ string, _ Match) error { return d.wait(ctx, 700) }

// Open pretends to connect.
func (d *Demo) Open(ctx context.Context, _, _ string) error { return d.wait(ctx, 500) }

var demoServers = []PublicServer{
	{Address: "qw.x86.com.br:27500", Title: "x86QW · Duel #1", Map: "dm6", Mode: "1on1", Humans: 2, QTV: "2@qw.x86.com.br:28000", Favorite: true},
	{Address: "qw.x86.com.br:27501", Title: "x86QW · 4on4", Map: "dm3", Mode: "4on4", Humans: 6, Bots: 2},
	{Address: "nl.quakeworld.nu:28501", Title: "QuakeWorld.nu · Duel", Map: "aerowalk", Mode: "1on1", Humans: 1},
	{Address: "de.qwsv.net:27500", Title: "Berlin KTX 4on4", Map: "e1m2", Mode: "4on4", Humans: 8, QTV: "1@de.qwsv.net:28000"},
	{Address: "fi.qw.zone:28502", Title: "Helsinki FFA", Map: "dm4", Mode: "ffa", Humans: 5},
	{Address: "us-east.qw.gg:27500", Title: "NA East · Clan Arena", Map: "dm6", Mode: "ca", Humans: 4, Bots: 4},
	{Address: "br.quake.world:27500", Title: "São Paulo · Duel", Map: "ztndm3", Mode: "1on1", Humans: 2, QTV: "3@br.quake.world:28000"},
	{Address: "au.qwfwd.net:30000", Title: "Sydney Instagib", Map: "dm4", Mode: "ffa", Humans: 3},
}

// Servers lists a fixed sample of public servers.
func (d *Demo) Servers(ctx context.Context, _ string) ([]PublicServer, error) {
	if err := d.wait(ctx, 400); err != nil {
		return nil, err
	}
	return append([]PublicServer(nil), demoServers...), nil
}

// Stack reports the demo server while it "runs".
func (d *Demo) Stack(context.Context, string) (*Stack, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	switch d.solo {
	case "qtv":
		return &Stack{Status: "running", Processes: []StackProcess{{Label: "QTV", Address: "0.0.0.0", Port: 28000, PID: 4250, Alive: true}}}, nil
	case "proxy":
		return &Stack{Status: "running", Processes: []StackProcess{{Label: "QWFWD", Address: "0.0.0.0", Port: 30000, PID: 4251, Alive: true}}}, nil
	}
	if !d.running {
		return nil, nil
	}
	return &Stack{Status: "running", Processes: []StackProcess{
		{Label: "MVDSV", Address: "0.0.0.0", Port: 27500, PID: 4242, Alive: true, Mode: "Duel", Map: "dm6"},
		{Label: "MVDSV #2 · 27501", Address: "0.0.0.0", Port: 27501, PID: 4243, Alive: true, Mode: "4on4", Map: "dm3"},
		{Label: "QTV", Address: "0.0.0.0", Port: 28000, PID: 4244, Alive: true},
		{Label: "QWFWD", Address: "0.0.0.0", Port: 30000, PID: 4245, Alive: true},
	}}, nil
}

// StartServer pretends to start the instances.
func (d *Demo) StartServer(ctx context.Context, _ string, _ Options) error {
	if err := d.wait(ctx, 900); err != nil {
		return err
	}
	d.mu.Lock()
	d.running = true
	d.mu.Unlock()
	return nil
}

// StopServer pretends to stop them.
func (d *Demo) StopServer(ctx context.Context, _ string) error {
	if err := d.wait(ctx, 600); err != nil {
		return err
	}
	d.mu.Lock()
	d.running, d.solo = false, ""
	d.mu.Unlock()
	return nil
}

// StartService pretends to start QTV or QWFWD alone.
func (d *Demo) StartService(ctx context.Context, _, kind string, _ Options) error {
	if err := d.wait(ctx, 600); err != nil {
		return err
	}
	d.mu.Lock()
	d.solo = kind
	d.mu.Unlock()
	return nil
}

func (d *Demo) wait(ctx context.Context, milliseconds int) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-time.After(time.Duration(float64(milliseconds)/max(d.Speed, 0.1)) * time.Millisecond):
		return nil
	}
}
