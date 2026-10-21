package maint

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/state"
)

// cluttered adds regenerable data and personal data to an installation.
func cluttered(t *testing.T, root string) {
	t.Helper()
	write(t, filepath.Join(root, "ezquake", "sb", "cache", "servidores"), "cache")
	write(t, filepath.Join(root, "ezquake", "temp", "x"), "temp")
	write(t, filepath.Join(root, "fortress", "baixando.tmp"), "parcial")
	write(t, filepath.Join(root, "fortress", "progs", "baixado.mdl"), "downloaded from the server")
	write(t, filepath.Join(root, "td2", "demos", "vazia.mvd"), "")
	write(t, filepath.Join(root, "td2", "demos", "boa.mvd"), "demo")
	write(t, filepath.Join(root, "qw", "qconsole.log"), "log")
	write(t, filepath.Join(root, "logs", "servidor.log"), "log")
	record(t, root, "team-fortress", map[string]string{"fortress/progs/gerenciado.mdl": "do pacote"})
}

func TestCleanupRemovesOnlyRegenerableData(t *testing.T) {
	cache := isolate(t)
	ownedCache(t, cache)
	root := managedInstall(t)
	cluttered(t, root)
	result, err := Cleanup(root, true, false)
	if err != nil {
		t.Fatal(err)
	}
	for _, gone := range []string{"ezquake/sb/cache", "ezquake/temp", "fortress/baixando.tmp", "fortress/progs/baixado.mdl", "td2/demos/vazia.mvd"} {
		if exists(filepath.Join(root, filepath.FromSlash(gone))) {
			t.Errorf("%s should be gone", gone)
		}
	}
	for _, kept := range []string{"fortress/progs/gerenciado.mdl", "td2/demos/boa.mvd", "qw/qconsole.log", "logs/servidor.log", "id1/pak0.pak"} {
		if !exists(filepath.Join(root, filepath.FromSlash(kept))) {
			t.Errorf("%s should have stayed", kept)
		}
	}
	if result.Cache != 5 || result.Personal != 0 || len(result.Caches) != 1 || exists(cache) {
		t.Fatalf("result %+v, cache exists: %v", result, exists(cache))
	}
}

func TestCleanupWithPersonalDataTakesLogsAndDemos(t *testing.T) {
	isolate(t)
	root := managedInstall(t)
	cluttered(t, root)
	result, err := Cleanup(root, false, true)
	if err != nil {
		t.Fatal(err)
	}
	for _, gone := range []string{"qw/qconsole.log", "logs", "td2/demos"} {
		if exists(filepath.Join(root, filepath.FromSlash(gone))) {
			t.Errorf("%s should be gone", gone)
		}
	}
	// Without --downloads, files the client fetched stay.
	if !exists(filepath.Join(root, "fortress", "progs", "baixado.mdl")) {
		t.Error("without downloads, the downloaded file should stay")
	}
	if result.Personal != 3 {
		t.Fatalf("result %+v", result)
	}
}

func TestCleanupRefusesAFolderWithoutX86QW(t *testing.T) {
	isolate(t)
	plain := t.TempDir()
	write(t, filepath.Join(plain, "ezquake", "temp", "x"), "temp")
	if _, err := Cleanup(plain, true, true); err == nil {
		t.Fatal("a folder without x86QW should be refused")
	}
	if !exists(filepath.Join(plain, "ezquake", "temp", "x")) {
		t.Fatal("the refusal deleted files")
	}
}

// legacyState writes a format 1 state.json with the nQuake-era ids.
func legacyState(t *testing.T, root string) {
	t.Helper()
	document := map[string]any{
		"format": 1, "project": "x86qw", "profile": "essential", "version": "0.7.0",
		"requested_components": []string{}, "known_components": []string{"nquake-ktx", "nquake-sounds"},
		"recorded_components": []string{"nquake-bootstrap", "nquake-ktx", "nquake-sounds"},
	}
	payload, _ := json.MarshalIndent(document, "", "  ")
	write(t, filepath.Join(root, filepath.FromSlash(stateRelative)), string(payload)+"\n")
}

func TestMigrateRewritesALegacyState(t *testing.T) {
	root := managedInstall(t)
	legacyState(t, root)
	planned, _, err := PlanMigration(root)
	if err != nil || len(planned) != 1 {
		t.Fatalf("plan: %v %v", planned, err)
	}
	ops, err := Migrate(root)
	if err != nil || len(ops) != 1 {
		t.Fatalf("migration: %v %v", ops, err)
	}
	current, err := state.Read(root)
	if err != nil {
		t.Fatal(err)
	}
	if current.Format != state.CurrentFormat || strings.Join(current.RecordedComponents, ",") != "x86qw-client-bootstrap,ktx" {
		t.Fatalf("migrated state: %+v", current)
	}
	if current.Document()["installation_version"] != migrationTarget {
		t.Fatalf("marker: %v", current.Document())
	}
	if again, _, err := PlanMigration(root); err != nil || len(again) != 0 {
		t.Fatalf("migrating again should be a no-op: %v %v", again, err)
	}
}

func TestMigrateRefusesPreOneLayoutsAndBrokenStates(t *testing.T) {
	root := managedInstall(t)
	write(t, filepath.Join(root, ".x86qw", "ktx.receipt"), "format=1\n")
	if _, err := Migrate(root); err == nil || !strings.Contains(err.Error(), "pre-1.0") {
		t.Fatalf("pre-1.0 layout: %v", err)
	}
	broken := managedInstall(t)
	write(t, filepath.Join(broken, filepath.FromSlash(stateRelative)), `{"format": 9}`)
	if _, err := Migrate(broken); err == nil || !strings.Contains(err.Error(), "preserved conflicts") {
		t.Fatalf("invalid state: %v", err)
	}
	if read(t, filepath.Join(broken, filepath.FromSlash(stateRelative))) != `{"format": 9}` {
		t.Fatal("the refused migration changed the state")
	}
	missing := managedInstall(t)
	os.Remove(filepath.Join(missing, filepath.FromSlash(stateRelative)))
	if ops, err := Migrate(missing); err != nil || len(ops) != 0 {
		t.Fatalf("without a state there is nothing to migrate: %v %v", ops, err)
	}
}
