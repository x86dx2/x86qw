package install

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/download"
	"github.com/x86dx2/x86qw/go/internal/plan"
	"github.com/x86dx2/x86qw/go/internal/receipt"
	"github.com/x86dx2/x86qw/go/internal/state"
)

// The offline tests run Install, Apply and Repair end to end against a
// fixture catalog whose packages a local HTTPS server serves: two fixture
// PAKs in the core package, a Linux nightly ezQuake (a bare ELF header,
// which is all the installer inspects) and small content components.

const fixtureRevision = "0123456789abcdef0123456789abcdef01234567"

// bsp is a map file with the BSP 29 header Verify checks.
func bsp(text string) []byte { return append([]byte{29, 0, 0, 0}, text...) }

func digestOf(payload []byte) string {
	sum := sha256.Sum256(payload)
	return hex.EncodeToString(sum[:])
}

// fixture is a catalog served from an httptest.NewTLSServer.
type fixture struct {
	t         *testing.T
	server    *httptest.Server
	ctx       context.Context
	mu        sync.Mutex
	files     map[string][]byte
	hits      map[string]int
	packages  []catalog.Package
	inventory *catalog.Inventory
}

func newFixture(t *testing.T) *fixture {
	t.Helper()
	t.Setenv(download.CacheEnv, filepath.Join(t.TempDir(), "cache"))
	t.Setenv("X86QW_NO_MACOS_PREFERENCES", "1")
	pak0 := []byte("PACK" + strings.Repeat("0", 60))
	pak1 := []byte("PACK" + strings.Repeat("1", 60))
	saved0, saved1 := pak0SHA256, pak1SHA256
	pak0SHA256, pak1SHA256 = digestOf(pak0), digestOf(pak1)
	t.Cleanup(func() { pak0SHA256, pak1SHA256 = saved0, saved1 })
	f := &fixture{t: t, files: map[string][]byte{}, hits: map[string]int{}}
	f.server = httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		body, ok := f.files[r.URL.Path]
		f.hits[r.URL.Path]++
		f.mu.Unlock()
		if !ok {
			http.NotFound(w, r)
			return
		}
		w.Write(body)
	}))
	t.Cleanup(f.server.Close)
	f.ctx = download.WithClient(context.Background(), f.server.Client())
	f.setInventory(map[string][]string{"essential": {"fixture-maps"}})
	f.component("x86qw-core-id1", "core", "1", map[string][]byte{"id1/pak0.pak": pak0, "id1/pak1.pak": pak1})
	f.component("fixture-maps", "x86qw", "1.0.0", map[string][]byte{
		"qw/x86qw-fixture.pk3":          []byte("fixture pk3 1.0.0"),
		"qw/maps/x86qw-fixture.bsp":     bsp("fixture map 1.0.0"),
		"qw/maps/x86qw-retired.bsp":     bsp("dropped in 1.1.0"),
		"docs/licenses/fixture/LICENSE": []byte("fixture license\n"),
	})
	f.nightly("20260101-000000_abcdef0")
	return f
}

// setInventory replaces the components.json the operations see.
func (f *fixture) setInventory(profiles map[string][]string) {
	f.t.Helper()
	document := map[string]any{
		"content_namespaces": []string{"x86qw"},
		"profiles":           profiles,
		"profile_history":    map[string][]string{},
		"components": []map[string]any{
			{"id": "fixture-maps", "label": "Test maps", "kind": "maps", "requires": []string{}},
			{"id": "fixture-extra", "label": "Test extra", "kind": "maps", "requires": []string{"fixture-maps"}},
		},
	}
	raw, _ := json.Marshal(document)
	inventory, err := catalog.ParseInventory(raw)
	if err != nil {
		f.t.Fatal(err)
	}
	f.inventory = inventory
}

// publish serves body and lists pkg in the catalog, replacing any entry of
// the same package and channel.
func (f *fixture) publish(pkg catalog.Package, body []byte) {
	pkg.Size, pkg.SHA256 = int64(len(body)), digestOf(body)
	pkg.URLs = []string{f.server.URL + "/" + pkg.Filename}
	pkg.RedistributionReviewed = true
	f.mu.Lock()
	f.files["/"+pkg.Filename] = body
	f.mu.Unlock()
	kept := f.packages[:0]
	for _, existing := range f.packages {
		if existing.Package != pkg.Package || existing.Channel != pkg.Channel {
			kept = append(kept, existing)
		}
	}
	f.packages = append(kept, pkg)
}

// component publishes a content package: payload files plus the internal
// manifest PreparePackage checks.
func (f *fixture) component(id, namespace, version string, payload map[string][]byte) {
	f.t.Helper()
	var buffer bytes.Buffer
	writer := zip.NewWriter(&buffer)
	add := func(name string, body []byte) {
		out, err := writer.CreateHeader(&zip.FileHeader{Name: name, Method: zip.Store})
		if err == nil {
			_, err = out.Write(body)
		}
		if err != nil {
			f.t.Fatal(err)
		}
	}
	names := make([]string, 0, len(payload))
	for name := range payload {
		names = append(names, name)
	}
	sort.Strings(names)
	type member struct {
		Path   string `json:"path"`
		SHA256 string `json:"sha256"`
	}
	manifest := struct {
		Format         int      `json:"format"`
		Project        string   `json:"project"`
		Package        string   `json:"package"`
		SourceRevision string   `json:"source_revision"`
		Version        string   `json:"version"`
		Members        []member `json:"members"`
	}{1, "x86qw", id, fixtureRevision, version, []member{}}
	for _, name := range names {
		manifest.Members = append(manifest.Members, member{"payload/" + name, digestOf(payload[name])})
	}
	encoded, _ := json.Marshal(manifest)
	add("_x86qw/component.json", encoded)
	for _, name := range names {
		add("payload/"+name, payload[name])
	}
	if err := writer.Close(); err != nil {
		f.t.Fatal(err)
	}
	f.publish(catalog.Package{
		Component: namespace, Package: id, Version: version, Channel: "content", Platform: "any", Architecture: "any",
		Filename: id + "-" + version + ".zip", SourceRevision: fixtureRevision,
	}, buffer.Bytes())
}

// nightly publishes a Linux nightly ezQuake: an x86-64 ELF header and a
// version-specific tail.
func (f *fixture) nightly(version string) []byte {
	header := make([]byte, 64)
	copy(header, "\x7fELF\x02\x01\x01")
	binary.LittleEndian.PutUint16(header[18:20], 62)
	body := append(header, []byte("ezQuake "+version)...)
	f.publish(catalog.Package{
		Component: "ezquake", Package: "ezquake", Version: version, Channel: "nightly", Platform: "linux", Architecture: "x86_64",
		Filename: version + "_ezQuake-x86_64.AppImage",
	}, body)
	return body
}

func (f *fixture) catalog() *catalog.Catalog {
	f.t.Helper()
	raw, err := json.Marshal(map[string]any{"format": 1, "project": "x86qw", "packages": f.packages})
	if err != nil {
		f.t.Fatal(err)
	}
	parsed, err := catalog.Parse(raw)
	if err != nil {
		f.t.Fatal(err)
	}
	return parsed
}

func (f *fixture) input(root string) plan.Input {
	return plan.Input{Root: root, Catalog: f.catalog(), Inventory: f.inventory}
}

func (f *fixture) install(root, profile string) *Result {
	f.t.Helper()
	result, err := Install(f.ctx, root, f.catalog(), f.inventory, InstallOptions{Platform: "linux", Channel: "nightly", Profile: profile})
	if err != nil {
		f.t.Fatal(err)
	}
	return result
}

func (f *fixture) requests(name string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.hits["/"+name]
}

func selection(t *testing.T, root, id string) string {
	t.Helper()
	record, err := receipt.Load(filepath.Join(root, receipt.ComponentsDir, id))
	if err != nil {
		t.Fatal(err)
	}
	return record.Receipt.Get("selection")
}

func TestOfflineInstallBuildsAVerifiedInstallation(t *testing.T) {
	f := newFixture(t)
	root := filepath.Join(t.TempDir(), "x86qw")
	result := f.install(root, "essential")
	if strings.Join(result.Installed, ",") != "fixture-maps" || result.Files != 4 {
		t.Fatalf("result: %+v", result)
	}
	report, err := Verify(root)
	if err != nil {
		t.Fatal(err)
	}
	if report.Clients != 1 || strings.Join(report.Components, ",") != "fixture-maps" {
		t.Fatalf("verification: %+v", report)
	}
	order, _ := os.ReadFile(filepath.Join(root, "qw", "pak.lst"))
	if string(order) != "x86qw-fixture.pk3\n" {
		t.Fatalf("qw/pak.lst: %q", order)
	}
	current, err := state.Read(root)
	if err != nil || current.Profile != "essential" || strings.Join(current.Installed(), ",") != "fixture-maps" {
		t.Fatalf("state.json: %+v %v", current, err)
	}
	if runtime.GOOS != "windows" && executableMissing(filepath.Join(root, "ezquake-nightly-x86_64.AppImage")) {
		t.Fatal("the AppImage should be executable")
	}
	// A second installation comes from the shared cache, not the network.
	before := f.requests("fixture-maps-1.0.0.zip")
	f.install(filepath.Join(t.TempDir(), "other"), "essential")
	if f.requests("fixture-maps-1.0.0.zip") != before {
		t.Fatal("the second installation downloaded again what was cached")
	}
}

func TestOfflineInstallRollsBackWhenAPackageIsCorrupt(t *testing.T) {
	f := newFixture(t)
	f.mu.Lock()
	body := f.files["/fixture-maps-1.0.0.zip"]
	corrupt := bytes.Repeat([]byte{'x'}, len(body))
	f.files["/fixture-maps-1.0.0.zip"] = corrupt
	f.mu.Unlock()
	root := filepath.Join(t.TempDir(), "x86qw")
	_, err := Install(f.ctx, root, f.catalog(), f.inventory, InstallOptions{Platform: "linux", Channel: "nightly", Profile: "essential"})
	var integrity *download.IntegrityError
	if !errors.As(err, &integrity) {
		t.Fatalf("expected an integrity error, got %v", err)
	}
	for _, relative := range []string{"id1/pak0.pak", "ezquake-nightly-x86_64.AppImage", ".x86qw/state.json",
		".x86qw/clients/ezquake/linux/nightly.receipt", "qw/maps/x86qw-fixture.bsp"} {
		if _, err := os.Lstat(filepath.Join(root, filepath.FromSlash(relative))); err == nil {
			t.Errorf("the rollback left %s", relative)
		}
	}
	// Nothing bad reached the shared cache, so the next try works.
	f.mu.Lock()
	f.files["/fixture-maps-1.0.0.zip"] = body
	f.mu.Unlock()
	f.install(root, "essential")
	if _, err := Verify(root); err != nil {
		t.Fatal(err)
	}
}

func TestOfflineApplyUpdatesClientAndComponents(t *testing.T) {
	f := newFixture(t)
	root := filepath.Join(t.TempDir(), "x86qw")
	f.install(root, "essential")

	f.nightly("20260201-000000_bcdef01")
	f.component("fixture-maps", "x86qw", "1.1.0", map[string][]byte{
		"qw/x86qw-fixture.pk3":          []byte("fixture pk3 1.1.0"),
		"qw/maps/x86qw-fixture.bsp":     bsp("fixture map 1.1.0"),
		"docs/licenses/fixture/LICENSE": []byte("fixture license\n"),
	})
	f.component("fixture-extra", "x86qw", "1.0.0", map[string][]byte{"qw/x86qw-extra.pk3": []byte("extra")})
	f.setInventory(map[string][]string{"essential": {"fixture-maps", "fixture-extra"}})

	in := f.input(root)
	planned, err := plan.Upgrade(in)
	if err != nil {
		t.Fatal(err)
	}
	var actions []string
	for _, row := range planned.Operations {
		actions = append(actions, row.Kind+":"+row.Action)
	}
	if strings.Join(actions, " ") != "Client:Update Component:Update Component:Add" {
		t.Fatalf("plan: %v", actions)
	}
	result, err := Apply(f.ctx, "upgrade", in, planned)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Join(result.Installed, ",") != "fixture-maps,fixture-extra" || len(result.Clients) != 1 {
		t.Fatalf("result: %+v", result)
	}
	if got := selection(t, root, "fixture-maps"); got != "1.1.0" {
		t.Fatalf("fixture-maps stayed at %s", got)
	}
	if _, err := os.Lstat(filepath.Join(root, "qw", "maps", "x86qw-retired.bsp")); err == nil {
		t.Fatal("the file dropped from the package stayed installed")
	}
	rec, err := loadVerifiedClient(root, catalog.ClientSpecs[1], "nightly")
	if err != nil || rec.Get("selection") != "20260201-000000_bcdef01" {
		t.Fatalf("client: %v %v", rec, err)
	}
	order, _ := os.ReadFile(filepath.Join(root, "qw", "pak.lst"))
	if string(order) != "x86qw-extra.pk3\nx86qw-fixture.pk3\n" {
		t.Fatalf("qw/pak.lst: %q", order)
	}
	if _, err := Verify(root); err != nil {
		t.Fatal(err)
	}
	if again, err := plan.Upgrade(f.input(root)); err != nil || len(again.Operations) != 0 {
		t.Fatalf("nothing should be left after the upgrade: %+v %v", again, err)
	}
}

func TestOfflineApplyKeepsAModifiedManagedFile(t *testing.T) {
	f := newFixture(t)
	root := filepath.Join(t.TempDir(), "x86qw")
	f.install(root, "essential")
	edited := filepath.Join(root, "qw", "maps", "x86qw-fixture.bsp")
	if err := os.WriteFile(edited, []byte("my map"), 0o644); err != nil {
		t.Fatal(err)
	}
	f.component("fixture-maps", "x86qw", "1.1.0", map[string][]byte{
		"qw/x86qw-fixture.pk3":          []byte("fixture pk3 1.1.0"),
		"qw/maps/x86qw-fixture.bsp":     bsp("fixture map 1.1.0"),
		"docs/licenses/fixture/LICENSE": []byte("fixture license\n"),
	})
	in := f.input(root)
	planned, err := plan.Update(in)
	if err != nil {
		t.Fatal(err)
	}
	result, err := Apply(f.ctx, "update", in, planned)
	if err != nil {
		t.Fatal(err)
	}
	if data, _ := os.ReadFile(edited); string(data) != "my map" {
		t.Fatalf("the user's edit was overwritten: %q", data)
	}
	if len(result.Warnings) == 0 || !strings.Contains(strings.Join(result.Warnings, "\n"), "x86qw-fixture.bsp") {
		t.Fatalf("the kept-file warning is missing: %v", result.Warnings)
	}
}

func TestOfflineRepairRestoresWhatWasDamaged(t *testing.T) {
	f := newFixture(t)
	root := filepath.Join(t.TempDir(), "x86qw")
	f.install(root, "essential")
	if planned, _, err := RepairPlan(f.input(root)); err != nil || planned.Status != "noop" {
		t.Fatalf("an intact installation should have an empty plan: %+v %v", planned, err)
	}
	os.Remove(filepath.Join(root, "qw", "maps", "x86qw-fixture.bsp"))
	os.Remove(filepath.Join(root, "qw", "pak.lst"))
	appimage := filepath.Join(root, "ezquake-nightly-x86_64.AppImage")
	if err := os.WriteFile(appimage, []byte("\x7fELF\x02 damaged"), 0o755); err != nil {
		t.Fatal(err)
	}
	planned, _, err := RepairPlan(f.input(root))
	if err != nil {
		t.Fatal(err)
	}
	var actions []string
	for _, row := range planned.Operations {
		actions = append(actions, row.Kind+":"+row.Action)
	}
	if strings.Join(actions, " ") != "Client:Repair Component:Repair Generated:Repair" {
		t.Fatalf("plan: %v", actions)
	}
	if _, err := Repair(f.ctx, f.input(root)); err != nil {
		t.Fatal(err)
	}
	if _, err := Verify(root); err != nil {
		t.Fatal(err)
	}
	if data, _ := os.ReadFile(filepath.Join(root, "qw", "maps", "x86qw-fixture.bsp")); string(data) != string(bsp("fixture map 1.0.0")) {
		t.Fatalf("map not restored: %q", data)
	}
}

func TestOfflineRepairRebuildsAMissingState(t *testing.T) {
	f := newFixture(t)
	root := filepath.Join(t.TempDir(), "x86qw")
	f.install(root, "essential")
	if err := os.Remove(filepath.Join(root, ".x86qw", "state.json")); err != nil {
		t.Fatal(err)
	}
	if _, err := Repair(f.ctx, f.input(root)); err != nil {
		t.Fatal(err)
	}
	current, err := state.Read(root)
	if err != nil || strings.Join(current.Installed(), ",") != "fixture-maps" {
		t.Fatalf("state.json rebuilt: %+v %v", current, err)
	}
}

func TestOfflineRepairRestoresTheExecuteBit(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Windows has no POSIX execute bit")
	}
	f := newFixture(t)
	root := filepath.Join(t.TempDir(), "x86qw")
	f.install(root, "essential")
	appimage := filepath.Join(root, "ezquake-nightly-x86_64.AppImage")
	if err := os.Chmod(appimage, 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Repair(f.ctx, f.input(root)); err != nil {
		t.Fatal(err)
	}
	if executableMissing(appimage) {
		t.Fatal("repair did not restore the execute permission")
	}
}

func TestOfflineRepairRefusesAClientTheCatalogDropped(t *testing.T) {
	f := newFixture(t)
	root := filepath.Join(t.TempDir(), "x86qw")
	f.install(root, "essential")
	os.Remove(filepath.Join(root, "ezquake-nightly-x86_64.AppImage"))
	f.nightly("20260301-000000_cdef012") // replaces the installed release
	before, _ := os.ReadDir(root)
	_, err := Repair(f.ctx, f.input(root))
	if err == nil || !strings.Contains(err.Error(), "validated payload") {
		t.Fatalf("repair should refuse: %v", err)
	}
	after, _ := os.ReadDir(root)
	if len(after) != len(before) {
		t.Fatal("the refused repair changed the installation")
	}
}
