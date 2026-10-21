package setup

import (
	"context"
	"errors"
	"fmt"
	"math"
	"math/rand/v2"
	"os"
	"slices"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/x86dx2/x86qw/go/internal/install"
)

// Demo is a Backend that installs nothing: the catalog is the real x86QW
// content, the network, the disk and the ports are read for real, and the
// rest of the preflight and the install are simulated with the shapes the
// real backend reports. It drives prototypes, screenshots and interface
// tests.
type Demo struct {
	// ExistingInstall makes Existing report an installation.
	ExistingInstall bool
	// Speed scales the simulated install (1 = about 14 s for any size).
	Speed float64

	mu        sync.Mutex
	running   *Options // the demo server, as it was started
	solo      string   // a service the demo runs alone
	favorites map[string]bool
}

var demoComponents = []Component{
	{"x86qw-client-bootstrap", "Base e inicialização do cliente x86QW", "Compõe a base nQuake com a inicialização, os defaults e as camadas globais do cliente x86QW.", "core", 28919, nil},
	{"nquake-visual-core", "Interface e recursos visuais nQuake", "Pacotes principais do ezQuake e da identidade visual nQuake.", "core", 15351608, []string{"x86qw-client-bootstrap"}},
	{"ktx", "KTX x86QW", "KTX 1.47 com 24 modos, Frogbots e Coach de Quad.", "gameplay", 2542965, []string{"x86qw-client-bootstrap"}},
	{"nquake-maps", "Mapas selecionados nQuake", "Somente os mapas, ENT e textos que fazem parte da distribuição nQuake.", "content", 20471003, []string{"x86qw-client-bootstrap"}},
	{"nquake-base-textures", "Texturas base", "Pacote de texturas base selecionado pelo nQuake.", "content", 21149469, []string{"nquake-visual-core"}},
	{"nquake-player-skins", "Skins de jogadores", "Skins GPL e comunitárias para jogadores e equipes.", "content", 14552089, []string{"nquake-visual-core"}},
	{"nquake-models", "Modelos", "Modelos atualizados empacotados pelo nQuake.", "content", 4045436, []string{"nquake-visual-core"}},
	{"nquake-skyboxes", "Skyboxes", "Céus de alta resolução e documentação correspondente.", "content", 3988263, []string{"nquake-visual-core"}},
	{"nquake-external-textures", "Texturas externas", "Texturas individuais mantidas pelo nQuake.", "content", 3835861, []string{"nquake-visual-core"}},
	{"nquake-scoreboard-flags", "Bandeiras do placar", "Bandeiras de países usadas no placar.", "content", 368135, []string{"nquake-visual-core"}},
	{"nquake-crosshairs", "Miras", "Coleção de miras incluída pelo nQuake.", "content", 14979, []string{"nquake-visual-core"}},
	{"nquake-documentation", "Documentação e licenças", "Licenças upstream e documentação atual da distribuição x86QW.", "documentation", 13000, nil},
	{"nquake-matchinfo", "Informações de partidas", "Demos históricas e de treinamento mantidas pelo nQuake.", "content", 7411045, []string{"x86qw-client-bootstrap"}},
	{"qrp-hires", "QRP alta resolução", "Texturas e brush models em alta resolução do Quake Retexturing Project.", "addon", 404142164, []string{"nquake-visual-core"}},
	{"final-arena", "Final Arena", "Final Arena 1.20, com duelos individuais em fila e perfil próprio x86QW.", "addon", 6984652, []string{"x86qw-client-bootstrap"}},
	{"pro-x", "Pro-X", "Pro-X QW 1.1, com rounds, equipes, votações e perfil próprio x86QW.", "addon", 9081189, []string{"x86qw-client-bootstrap"}},
	{"team-fortress", "Team Fortress", "Team Fortress 2.9 com assets e mapas nQuake harmonizados pelo x86QW.", "addon", 8566807, []string{"x86qw-client-bootstrap"}},
	{"total-destruction-2", "Total Destruction 2", "TD2QW 2.22 com armas, magias, runas, modelos, sons e gamecode.", "addon", 502676, []string{"x86qw-client-bootstrap"}},
	{"mvdsv", "MVDSV dedicado", "Servidor QuakeWorld MVDSV 1.11 para os gamecodes declarados, MVD e transmissão QTV.", "server", 1121507, nil},
	{"qtv", "QTV broadcast", "QTV HTTP fixado por commit para streams locais ou upstream remoto e demos MVD.", "server", 12079507, nil},
	{"qwfwd", "QWFWD proxy", "Proxy QuakeWorld QWFWD 1.30, local por padrão e exposição externa opcional.", "server", 287467, nil},
}

var demoModes = []Mode{
	{ID: "duel", Label: "Duel", DefaultMap: "dm6", Maps: []string{"dm6", "dm2", "dm4", "aerowalk", "ztndm3"}, Bots: true, Players: "2", BotLimit: 1, Description: "Duelo competitivo entre dois jogadores."},
	{ID: "2on2", Label: "2on2", DefaultMap: "dm6", Maps: []string{"dm6", "dm4", "aerowalk", "ztndm3"}, Bots: true, Players: "4", BotLimit: 3, Description: "Teamplay competitivo com duas duplas."},
	{ID: "4on4", Label: "4on4", DefaultMap: "dm3", Maps: []string{"dm3", "dm2", "e1m2", "schloss"}, Bots: true, Players: "8", BotLimit: 7, Description: "Teamplay clássico com duas equipes de quatro."},
	{ID: "ffa", Label: "Free For All", DefaultMap: "dm6", Maps: []string{"dm6", "dm3", "dm4", "e1m1"}, Bots: true, Players: "2+", Description: "Todos contra todos com respawn contínuo."},
	{ID: "clan-arena", Label: "Clan Arena", DefaultMap: "dm6", Maps: []string{"dm6", "dm3", "aerowalk"}, Bots: true, Players: "4+", Description: "Rounds eliminatórios sem coleta de itens."},
	{ID: "ctf", Label: "Capture The Flag", DefaultMap: "e2m2", Maps: []string{"e2m2", "e1m5", "e1m3", "e2m5"}, Players: "4+", Kind: "ctf", Description: "Duas equipes defendem sua bandeira e capturam a adversária."},
	{ID: "midair", Label: "Midair", DefaultMap: "dm4", Maps: []string{"dm4", "dm6"}, Bots: true, Players: "2+", Description: "Combate aéreo com rockets e pontuação por altura."},
	{ID: "instagib", Label: "Instagib", DefaultMap: "dm4", Maps: []string{"dm4", "dm3", "dm6"}, Bots: true, Players: "2+", Description: "Eliminação instantânea com shaft em DMM4."},
	{ID: "race", Label: "Race", DefaultMap: "dm6", Maps: []string{"dm6", "dm4"}, Players: "1+", Kind: "race", Description: "Percursos cronometrados usando rotas oficiais do KTX."},
	{ID: "tot", Label: "ThunderWalker ToT", DefaultMap: "dm6", Maps: []string{"dm6", "dm2"}, Bots: true, Players: "1+", Kind: "tot", Description: "Combate de sobrevivência com progressão e bots Frogbot."},
	{ID: "practice", Label: "Practice", DefaultMap: "dm6", Maps: []string{"dm6", "dm2", "dm4", "dm3"}, Bots: true, Players: "1+", Description: "Servidor destravado para praticar itens, armas e movimento."},
}

var demoGroups = []ModeGroup{
	{Key: "recommended", Label: "Recomendados", Modes: []string{"duel", "4on4", "ffa", "ctf"}},
	{Key: "individual", Label: "Competitivo individual", Modes: []string{"duel", "ffa", "midair", "instagib"}},
	{Key: "teams", Label: "Equipes", Modes: []string{"2on2", "4on4", "clan-arena", "ctf"}},
	{Key: "training", Label: "Treino e movimento", Modes: []string{"race", "tot", "practice"}},
}

// demoCore and demoClient are the sizes of the core data and of ezQuake per
// platform.
const demoCore = 23817401

var demoClient = map[string]int64{"macos": 23400000, "linux": 9800000, "windows": 11900000}

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
		Components: demoComponents, Profiles: profiles, CoreSize: demoCore,
		ClientSize: demoClient, Modes: demoModes,
	}, nil
}

// demoInstalled is what the demo installation holds.
func demoInstalled() []string {
	catalog, _ := (&Demo{}).Catalog(context.Background())
	return append(append([]string(nil), catalog.Profiles["recommended"]...), "mvdsv", "qtv", "qwfwd")
}

// demoUpdates are the demo installation's pending updates.
var demoUpdates = []Update{
	{ID: "x86qw-client-bootstrap", Label: "Base e inicialização do cliente x86QW", From: Raw("e4cb23d40aa2+x86qw.4"), To: "dfb2ac9299f7+x86qw.4", Note: Say("plan.component")},
	{ID: "ktx", Label: "KTX x86QW", From: Raw("1.47+x86qw.19"), To: "1.47+x86qw.20", Note: Say("plan.component")},
	{ID: corePackage, Label: "Dados do Quake", From: Raw("0.1.0"), To: "0.1.1", Note: Say("plan.component")},
}

// Existing reports the demo installation when ExistingInstall is set.
func (d *Demo) Existing(_ context.Context, target string) (*Existing, error) {
	if !d.ExistingInstall {
		return nil, nil
	}
	options := Defaults(Both, target)
	options.Nick = "x86dx2"
	installed := demoInstalled()
	return &Existing{Target: target, CLIVersion: "2.0.3", Components: len(installed), Installed: installed, Options: options,
		Updates: append([]Update(nil), demoUpdates...)}, nil
}

// Network detects the real addresses.
func (d *Demo) Network(ctx context.Context) Network { return DetectNetwork(ctx) }

// CheckHostname resolves for real.
func (d *Demo) CheckHostname(ctx context.Context, hostname string, network Network) DNSCheck {
	return CheckHostname(ctx, hostname, network)
}

// download is what installing options downloads in the demo.
func (d *Demo) download(options Options) int64 {
	var total int64
	for _, lane := range d.lanes(options) {
		total += lane.Size
	}
	return total
}

// Preflight runs the real backend's checks, in its order and with its
// units: the folder is only read, the disk, the path and the ports are
// measured, and the connection and the catalog are simulated.
func (d *Demo) Preflight(ctx context.Context, options Options) <-chan Check {
	out := make(chan Check)
	steps := []checkStep{{"folder", d.checkFolder}}
	if options.Intent.WantsServer() {
		steps = append(steps, checkStep{"path", checkPath})
	}
	steps = append(steps,
		checkStep{"disk", func(o Options) found { return diskCheck(existingParent(o.Target), d.download(o)) }},
		checkStep{"net", func(Options) found {
			return found{ID: "net", Title: Say("check.net"), State: CheckOK, Value: Say("net.ms", 180+rand.IntN(240)), Detail: Say("net.ok.d")}
		}},
		checkStep{"catalog", func(Options) found {
			return found{ID: "catalog", Title: Say("check.catalog"), State: CheckOK, Value: Say("catalog.count", 33),
				Detail: Say("catalog.tuf", 55, Date(time.Now().Add(30*24*time.Hour)))}
		}})
	if options.Intent.WantsServer() {
		steps = append(steps, checkStep{"ports", checkPorts})
	}
	go func() {
		defer close(out)
		for _, step := range steps {
			select {
			case out <- (found{ID: step.id, Title: Say("check." + step.id), State: CheckRunning}).check():
			case <-ctx.Done():
				return
			}
			select {
			case <-time.After(time.Duration(float64(280+rand.IntN(260))/max(d.Speed, 1)) * time.Millisecond):
			case <-ctx.Done():
				return
			}
			select {
			case out <- step.run(options).check():
			case <-ctx.Done():
				return
			}
		}
	}()
	return out
}

// checkFolder reads the folder without writing to it.
func (d *Demo) checkFolder(o Options) found {
	check := found{ID: "folder", Title: Say("check.folder"), Value: Raw(o.Target), State: CheckOK, Detail: Say("folder.new")}
	switch entries, err := os.ReadDir(o.Target); {
	case !strings.HasPrefix(o.Target, "/") && !strings.Contains(o.Target, `:\`):
		check.State, check.Detail = CheckFailed, Say("folder.relative")
	case d.ExistingInstall:
		check.Detail = Say("folder.existing")
	case errors.Is(err, os.ErrNotExist):
	case err == nil && len(entries) > 0:
		check.State, check.Value = CheckWarning, Say("folder.full")
		check.Detail = Say("folder.full.d", shortPath(o.Target), len(entries))
		check.Fix = Say("folder.full.fix")
	default:
		check.Detail = Say("folder.empty")
	}
	return check
}

var demoFiles = map[string][]string{
	corePackage:            {"id1/pak0.pak"},
	"client":               {"ezquake/ezquake.pk3", "ezQuake"},
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

// lanes are the packages installing options downloads, as the real
// backend lists them: core data, ezQuake and the components for a new
// folder; for the demo installation, what is added and, with Upgrade, the
// updates.
func (d *Demo) lanes(options Options) []Lane {
	catalog, _ := d.Catalog(context.Background())
	sizes, labels := map[string]int64{corePackage: demoCore, "client": demoClient[install.HostPlatform()]}, map[string]string{corePackage: "Dados do Quake", "client": "ezQuake 3.6.9"}
	for _, component := range catalog.Components {
		sizes[component.ID], labels[component.ID] = component.Size, component.Label
	}
	var lanes []Lane
	have := map[string]bool{}
	if d.ExistingInstall {
		for _, id := range demoInstalled() {
			have[id] = true
		}
		if options.Upgrade {
			for _, update := range demoUpdates {
				lanes = append(lanes, Lane{ID: update.ID, Label: labels[update.ID], Size: sizes[update.ID]})
				have[update.ID] = true
			}
		}
	} else {
		lanes = append(lanes, Lane{ID: corePackage, Label: labels[corePackage], Size: sizes[corePackage]}, Lane{ID: "client", Label: labels["client"], Size: sizes["client"]})
	}
	for _, id := range Selected(catalog, options) {
		if !have[id] {
			lanes = append(lanes, Lane{ID: id, Label: labels[id], Size: sizes[id]})
		}
	}
	return lanes
}

// Install simulates the download with a fluctuating speed; sending on
// control pauses (true) or resumes (false).
func (d *Demo) Install(ctx context.Context, options Options, control <-chan bool) <-chan Progress {
	out := make(chan Progress)
	lanes := d.lanes(options)
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
			budget := mbs * 1e6 * 0.05 * scale
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
			progress.Speed = smoothed * 1e6 * scale
			if progress.Speed > 0 {
				progress.ETA = time.Duration(float64(progress.Total-progress.Done) / progress.Speed * float64(time.Second))
			}
			if progress.Done >= progress.Total {
				progress.Done, progress.Finished, progress.ETA, progress.Speed = progress.Total, true, 0, 0
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
	return Report{Files: 2216, Components: len(demoInstalled())}, nil
}

// Uninstall pretends to remove the installation.
func (d *Demo) Uninstall(ctx context.Context, target string, keepPersonal bool) error {
	return d.wait(ctx, 600)
}

var _ Backend = (*Demo)(nil)

// MatchCatalog offers KTX and two mods, with the demo modes.
func (d *Demo) MatchCatalog(context.Context, string) (*MatchCatalog, error) {
	return &MatchCatalog{
		Games: []Game{
			{ID: "ktx", Label: "KTX", Version: "1.47+x86qw.20", Description: "QuakeWorld competitivo com o QVM KTX 1.47 derivado e reproduzível do x86QW.", Modes: true},
			{ID: "final-arena", Label: "Final Arena", Version: "1.20", Description: "Duelos individuais em fila: o vencedor permanece na arena."},
			{ID: "pro-x", Label: "Pro-X", Version: "1.1", Description: "Rounds e equipes com ready, break e votação."},
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
	{Address: "qw.x86.com.br:27500", Title: "x86QW · Duel #1", Map: "dm6", Mode: "1on1", Humans: 2, QTV: "2@qw.x86.com.br:28000"},
	{Address: "qw.x86.com.br:27501", Title: "x86QW · 4on4", Map: "dm3", Mode: "4on4", Humans: 6, Bots: 2},
	{Address: "nl.quakeworld.nu:28501", Title: "QuakeWorld.nu · Duel", Map: "aerowalk", Mode: "1on1", Humans: 1},
	{Address: "de.qwsv.net:27500", Title: "Berlin KTX 4on4", Map: "e1m2", Mode: "4on4", Humans: 8, QTV: "1@de.qwsv.net:28000"},
	{Address: "fi.qw.zone:28502", Title: "Helsinki FFA", Map: "dm4", Mode: "ffa", Humans: 5},
	{Address: "us-east.qw.gg:27500", Title: "NA East · Clan Arena", Map: "dm6", Mode: "ca", Humans: 4, Bots: 4},
	{Address: "br.quake.world:27500", Title: "São Paulo · Duel", Map: "ztndm3", Mode: "1on1", Humans: 2, QTV: "3@br.quake.world:28000"},
	{Address: "au.qwfwd.net:30000", Title: "Sydney Instagib", Map: "dm4", Mode: "ffa", Humans: 3},
}

// Servers lists a fixed sample of public servers, the favorites marked.
func (d *Demo) Servers(ctx context.Context, _ string) ([]PublicServer, error) {
	if err := d.wait(ctx, 400); err != nil {
		return nil, err
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.favorites == nil {
		d.favorites = map[string]bool{demoServers[0].Address: true}
	}
	servers := append([]PublicServer(nil), demoServers...)
	for index := range servers {
		servers[index].Favorite = d.favorites[servers[index].Address]
	}
	return servers, nil
}

// Favorite pins or unpins a demo server.
func (d *Demo) Favorite(_ context.Context, _, address string, on bool) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.favorites == nil {
		d.favorites = map[string]bool{demoServers[0].Address: true}
	}
	d.favorites[address] = on
	return nil
}

// Stack reports the demo server, as it was started, while it "runs".
func (d *Demo) Stack(context.Context, string) (*Stack, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.running == nil {
		return nil, nil
	}
	s := d.running.Server
	address := s.bind()
	var processes []StackProcess
	if d.solo == "" {
		for index, instance := range s.Instances {
			label := "MVDSV"
			if index > 0 {
				label = fmt.Sprintf("MVDSV #%d", index+1)
			}
			processes = append(processes, StackProcess{Label: label, Address: address, Port: s.BasePort + index, PID: 4242 + index, Alive: true, Mode: instance.Mode, Map: instance.Map})
		}
	}
	if s.QTVPort != 0 && d.solo != "proxy" {
		processes = append(processes, StackProcess{Label: "QTV", Address: address, Port: s.QTVPort, PID: 4250, Alive: true})
	}
	if s.QWFWDPort != 0 && d.solo != "qtv" {
		processes = append(processes, StackProcess{Label: "QWFWD", Address: address, Port: s.QWFWDPort, PID: 4251, Alive: true})
	}
	return &Stack{Status: "running", Processes: processes}, nil
}

// StartServer pretends to start the instances.
func (d *Demo) StartServer(ctx context.Context, target string, options Options) error {
	if err := options.Server.Validate(); err != nil {
		return err
	}
	if PathTooLong(target) {
		return problem("", "v.path", len(target), MVDSVPathRoom)
	}
	if err := d.wait(ctx, 900); err != nil {
		return err
	}
	d.mu.Lock()
	d.running, d.solo = &options, ""
	d.mu.Unlock()
	return nil
}

// StopServer pretends to stop them.
func (d *Demo) StopServer(ctx context.Context, _ string) error {
	if err := d.wait(ctx, 600); err != nil {
		return err
	}
	d.mu.Lock()
	d.running, d.solo = nil, ""
	d.mu.Unlock()
	return nil
}

// StartService pretends to start QTV or QWFWD alone.
func (d *Demo) StartService(ctx context.Context, _, kind string, options Options) error {
	if err := options.Server.Validate(); err != nil {
		return err
	}
	if err := d.wait(ctx, 600); err != nil {
		return err
	}
	if !slices.Contains([]string{"qtv", "proxy"}, kind) {
		return problem("", "err.run")
	}
	d.mu.Lock()
	if kind == "qtv" && options.Server.QTVPort == 0 {
		options.Server.QTVPort = DefaultQTVPort
	}
	if kind == "proxy" && options.Server.QWFWDPort == 0 {
		options.Server.QWFWDPort = DefaultQWFWDPort
	}
	d.running, d.solo = &options, kind
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
