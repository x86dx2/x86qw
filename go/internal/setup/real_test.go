package setup

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/play"
	"github.com/x86dx2/x86qw/go/internal/services"
	"github.com/x86dx2/x86qw/go/internal/trust"
)

// realBackend talks to the published catalog; set X86QW_NETWORK_TESTS=1.
func realBackend(t *testing.T) *Real {
	t.Helper()
	if os.Getenv("X86QW_NETWORK_TESTS") == "" {
		t.Skip("defina X86QW_NETWORK_TESTS=1 para usar o catálogo publicado")
	}
	return &Real{Trust: trust.Options{CacheDir: t.TempDir()}}
}

func TestRealCatalogHasSizesAndModes(t *testing.T) {
	r := realBackend(t)
	cat, err := r.Catalog(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if cat.CoreSize == 0 || cat.ClientSize[install.HostPlatform()] == 0 || len(cat.Modes) == 0 || len(cat.Profiles["recommended"]) == 0 {
		t.Fatalf("catálogo incompleto: core %d, cliente %v, %d modos", cat.CoreSize, cat.ClientSize, len(cat.Modes))
	}
	for _, component := range cat.Components {
		if component.Size == 0 {
			t.Fatalf("%s sem tamanho", component.ID)
		}
	}
	// A player never gets the server programs, even from "complete".
	options := Defaults(Play, "")
	options.Content = "complete"
	for _, id := range Selected(cat, options) {
		for _, component := range cat.Components {
			if component.ID == id && component.Kind == "server" {
				t.Fatalf("jogador recebeu %s", id)
			}
		}
	}
}

// TestRealInstall installs the essential profile into a temporary folder
// with a private download cache; set X86QW_REAL_INSTALL=1 as well.
func TestRealInstall(t *testing.T) {
	r := realBackend(t)
	if os.Getenv("X86QW_REAL_INSTALL") == "" {
		t.Skip("defina X86QW_REAL_INSTALL=1 para instalar de verdade")
	}
	t.Setenv("X86QW_CACHE_DIR", t.TempDir())
	t.Setenv("X86QW_NO_MACOS_PREFERENCES", "1")
	target := filepath.Join(t.TempDir(), "x86qw")
	options := Defaults(Both, target)
	options.Nick, options.Content = "tester", "essential"
	options.Server.BasePort = 27610
	if err := options.Validate(); err != nil {
		t.Fatal(err)
	}
	for check := range r.Preflight(context.Background(), options) {
		t.Logf("pré-voo %s: %v %s — %s", check.ID, check.State, check.Value, check.Detail)
		if check.State == CheckFailed {
			t.Fatalf("pré-voo falhou: %+v", check)
		}
	}
	control := make(chan bool, 2)
	var last Progress
	paused := false
	started := time.Now()
	for progress := range r.Install(context.Background(), options, control) {
		// Pause once a quarter in, to see it hold between packages.
		if !paused && progress.Total > 0 && progress.Done > progress.Total/4 {
			control <- true
			paused = true
		}
		if progress.Holding {
			control <- false
		}
		last = progress
	}
	if last.Err != nil {
		t.Fatal(last.Err)
	}
	t.Logf("%d bytes, %d faixas, %d linhas de log em %s; avisos: %v", last.Total, len(last.Lanes), len(last.Log), time.Since(started).Round(time.Second), last.Warnings)
	again := options
	again.Content = "recommended"
	before := len(existingOrNil(t, r, target).Installed)
	var applied Progress
	for progress := range r.Install(context.Background(), again, nil) {
		if applied.Total != 0 && progress.Total != applied.Total && !progress.Finished {
			t.Fatalf("o total do apply mudou no caminho: %d → %d", applied.Total, progress.Total)
		}
		applied = progress
	}
	if applied.Err != nil || !applied.Finished || len(existingOrNil(t, r, target).Installed) <= before {
		t.Fatalf("apply: %+v", applied.Err)
	}
	if !last.Finished || last.Done != last.Total || last.Total == 0 {
		t.Fatalf("terminou em %d de %d", last.Done, last.Total)
	}
	for _, lane := range last.Lanes {
		if lane.FileCount == 0 || lane.Files != lane.FileCount {
			t.Fatalf("%s: %d de %d arquivos", lane.ID, lane.Files, lane.FileCount)
		}
	}
	if _, err := install.Verify(target); err != nil {
		t.Fatal(err)
	}
	user, _ := os.ReadFile(filepath.Join(target, "qw", "x86qw-user.cfg"))
	setupCfg, _ := os.ReadFile(filepath.Join(target, filepath.FromSlash(SetupConfig)))
	if !strings.Contains(string(user), `"exec x86qw-setup.cfg"`) || !strings.Contains(string(setupCfg), `name                     "tester"`) {
		t.Fatalf("configuração do cliente ausente:\n%s\n%s", user, setupCfg)
	}
	presets, err := services.LoadPresets(target)
	if err != nil || presets[PresetName(1)] == nil {
		t.Fatalf("presets: %v %v", presets, err)
	}
	existing, err := r.Existing(context.Background(), target)
	if err != nil || existing == nil || existing.Options.Nick != "tester" {
		t.Fatalf("instalação não reconhecida: %+v %v", existing, err)
	}
	report, err := r.Repair(context.Background(), target)
	if err != nil || report.Problem != "" || report.Files == 0 {
		t.Fatalf("verificação: %+v %v", report, err)
	}
}

// TestDeepFolderWarnsAServer: the long path is a check of its own, so a
// folder that is not empty, or already installed, does not hide it.
func TestDeepFolderWarnsAServer(t *testing.T) {
	r := &Real{}
	deep := filepath.Join(t.TempDir(), strings.Repeat("pasta-funda/", 9), "x86qw")
	os.MkdirAll(deep, 0o755)
	os.WriteFile(filepath.Join(deep, "alheio.txt"), nil, 0o644)
	options := Defaults(Host, deep)
	ids := func() []string {
		var out []string
		for _, step := range r.steps(options) {
			out = append(out, step.id)
		}
		return out
	}
	if !slices.Contains(ids(), "path") {
		t.Fatalf("servidor sem a conferência do caminho: %v", ids())
	}
	if check := r.checkFolder(options); check.State != CheckWarning || check.Value.Key != "folder.full" {
		t.Fatalf("pasta: %+v", check)
	}
	if check := checkPath(options); check.State != CheckWarning || !strings.Contains(check.Detail.In(LangEN), "MVDSV") {
		t.Fatalf("caminho: %+v", check)
	}
	options.Intent = Play
	if slices.Contains(ids(), "path") {
		t.Fatal("jogador não precisa do aviso")
	}
}

// TestPreflightSaysEachCheckStarts: a slow check shows running first.
func TestPreflightSaysEachCheckStarts(t *testing.T) {
	r := &Real{CatalogFile: filepath.Join(t.TempDir(), "sem-catalogo.json")}
	options := Defaults(Play, filepath.Join(t.TempDir(), "x86qw"))
	var states []CheckState
	var ids []string
	for check := range r.Preflight(AnnounceChecks(context.Background()), options) {
		states, ids = append(states, check.State), append(ids, check.ID)
	}
	if len(states) != 8 || states[0] != CheckRunning || states[1] == CheckRunning || ids[0] != ids[1] {
		t.Fatalf("%v %v", ids, states)
	}
	// A printed list, the command line's, gets each check once.
	count := 0
	for range r.Preflight(context.Background(), options) {
		count++
	}
	if count != 4 {
		t.Fatalf("%d linhas sem anúncio", count)
	}
}

// TestOfflineInstallationStillOpens: without the published catalog the
// installation is read from disk and the catalog comes from the inventory.
func TestOfflineInstallationStillOpens(t *testing.T) {
	target := t.TempDir()
	os.MkdirAll(filepath.Join(target, ".x86qw"), 0o755)
	os.WriteFile(filepath.Join(target, ".x86qw", "state.json"), []byte("{}"), 0o644)
	os.MkdirAll(filepath.Join(target, ".x86qw", "setup"), 0o755)
	os.WriteFile(filepath.Join(target, ".x86qw", "setup", "choices.json"), []byte("{"), 0o600)
	r := &Real{CatalogFile: filepath.Join(t.TempDir(), "sem-catalogo.json")}
	existing, err := r.Existing(context.Background(), target)
	if err != nil || existing == nil || existing.UpdatesErr == nil || existing.Note.IsZero() {
		t.Fatalf("instalação offline: %+v %v", existing, err)
	}
	cat, err := r.Catalog(context.Background())
	if err != nil || cat.Unavailable == nil || len(cat.Components) == 0 || len(cat.Modes) == 0 {
		t.Fatalf("catálogo offline: %+v %v", cat, err)
	}
}

func existingOrNil(t *testing.T, r *Real, target string) *Existing {
	t.Helper()
	existing, err := r.Existing(context.Background(), target)
	if err != nil || existing == nil {
		t.Fatalf("instalação: %v", err)
	}
	return existing
}

func TestCancelledWorkStillReportsHowItEnded(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	release := make(chan struct{})
	out := track(ctx, newTracker([]Lane{{ID: "a", Label: "A", Size: 10}}, nil), nil, func(ctx context.Context) ([]Text, error) {
		<-ctx.Done()
		<-release // the rollback takes a while
		return nil, ctx.Err()
	})
	time.Sleep(300 * time.Millisecond)
	cancel()
	time.Sleep(200 * time.Millisecond)
	close(release)
	var last Progress
	for progress := range out {
		last = progress
	}
	if !errors.Is(last.Err, context.Canceled) || last.Finished {
		t.Fatalf("o fim deveria dizer cancelado: %+v", last)
	}
}

// TestRealCancelThenInstallAgain cancels a real install once its first
// package is in, then installs into the same folder: the retry must start
// fresh, not treat the leftovers as an installation.
func TestRealCancelThenInstallAgain(t *testing.T) {
	r := realBackend(t)
	if os.Getenv("X86QW_REAL_INSTALL") == "" {
		t.Skip("defina X86QW_REAL_INSTALL=1 para instalar de verdade")
	}
	t.Setenv("X86QW_CACHE_DIR", t.TempDir())
	t.Setenv("X86QW_NO_MACOS_PREFERENCES", "1")
	target := filepath.Join(t.TempDir(), "x86qw")
	options := Defaults(Play, target)
	options.Nick, options.Content = "tester", "essential"
	ctx, cancel := context.WithCancel(context.Background())
	var last Progress
	for progress := range r.Install(ctx, options, nil) {
		if len(progress.Lanes) > 0 && progress.Lanes[0].FileCount > 0 && progress.Lanes[0].Files >= progress.Lanes[0].FileCount {
			cancel()
		}
		last = progress
	}
	cancel()
	if !errors.Is(last.Err, context.Canceled) {
		t.Fatalf("deveria terminar cancelada: %+v", last.Err)
	}
	if Installed(target) {
		t.Fatal("o que sobrou do cancelamento passou por instalação")
	}
	for progress := range r.Install(context.Background(), options, nil) {
		last = progress
	}
	if last.Err != nil || !last.Finished {
		t.Fatalf("segunda tentativa: %v", last.Err)
	}
	if _, err := install.Verify(target); err != nil {
		t.Fatal(err)
	}
}

// TestRealResolve checks play.Resolve on a real essential installation:
// defaults, narrowing by bots and CTF, and what it refuses.
func TestRealResolve(t *testing.T) {
	r := realBackend(t)
	if os.Getenv("X86QW_REAL_INSTALL") == "" {
		t.Skip("defina X86QW_REAL_INSTALL=1 para instalar de verdade")
	}
	t.Setenv("X86QW_CACHE_DIR", t.TempDir())
	t.Setenv("X86QW_NO_MACOS_PREFERENCES", "1")
	target := filepath.Join(t.TempDir(), "x86qw")
	options := Defaults(Play, target)
	options.Nick, options.Content = "tester", "essential"
	var last Progress
	for progress := range r.Install(context.Background(), options, nil) {
		last = progress
	}
	if last.Err != nil {
		t.Fatal(last.Err)
	}
	if _, err := play.Resolve(target, play.Request{}); err == nil {
		t.Fatal("KTX sem modo deveria ser recusado")
	}
	duel, err := play.Resolve(target, play.Request{Mode: "duel"})
	if err != nil || duel.Map != "dm6" || duel.Ruleset != "x86qw" || duel.Runtime.Channel != "stable" {
		t.Fatalf("duel padrão: %+v %v", duel, err)
	}
	all, _ := r.Maps(context.Background(), target, Match{Game: "ktx", Mode: "duel"})
	bots, _ := r.Maps(context.Background(), target, Match{Game: "ktx", Mode: "duel", Bots: 1})
	ctf, _ := r.Maps(context.Background(), target, Match{Game: "ktx", Mode: "ctf"})
	if all == nil || bots == nil || ctf == nil || !bots.Narrowed || len(bots.Maps) >= len(all.Maps) || !ctf.Narrowed || len(ctf.Maps) >= len(all.Maps) {
		t.Fatalf("mapas: todos %v, bots %v, ctf %v", all, bots, ctf)
	}
	for _, refused := range []play.Request{{Mode: "duel", Map: "nao-existe"}, {Mode: "duel", Channel: "nightly"}, {Game: "pro-x"}} {
		if _, err := play.Resolve(target, refused); err == nil {
			t.Fatalf("%+v deveria ser recusado", refused)
		}
	}
}
