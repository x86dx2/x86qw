package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/download"
	"github.com/x86dx2/x86qw/go/internal/receipt"
	"github.com/x86dx2/x86qw/go/internal/state"
)

// The commands end to end against an installation laid out on disk: what
// they print, what they change and how they exit.

func writeFile(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func digest(body string) string {
	sum := sha256.Sum256([]byte(body))
	return hex.EncodeToString(sum[:])
}

// fixtureInstall lays out what an install leaves behind: the PAKs, a Linux
// nightly ezQuake with its receipt, one component, the CLI and state.json,
// plus a personal config and a demo. HOME and the cache are isolated.
func fixtureInstall(t *testing.T) string {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv(download.CacheEnv, filepath.Join(t.TempDir(), "cache"))
	withoutTerminal(t)
	root := filepath.Join(t.TempDir(), "x86qw")
	writeFile(t, filepath.Join(root, "id1", "pak0.pak"), "PACK pak0")
	writeFile(t, filepath.Join(root, "id1", "pak1.pak"), "PACK pak1")
	writeFile(t, filepath.Join(root, "ezquake", "configs", "config.cfg"), "name jogador\n")
	writeFile(t, filepath.Join(root, "qw", "demos", "partida.mvd"), "demo")
	spec := catalog.ClientSpecs[1] // Linux x86_64
	version := "20260101-000000_abcdef0"
	writeFile(t, filepath.Join(root, spec.Runtime("nightly")), "\x7fELF nightly")
	client := &receipt.Receipt{
		Keys: []string{"format", "platform", "architecture", "channel", "selection", "install_name",
			"bundle_version", "artifact_name", "artifact_url", "artifact_sha256", "binary_sha256"},
		Values: map[string]string{
			"format": "1", "platform": spec.Platform, "architecture": spec.Architecture, "channel": "nightly",
			"selection": version, "install_name": spec.Runtime("nightly"), "bundle_version": version,
			"artifact_name": version + spec.NightlySuffix, "artifact_url": "https://example.invalid/" + version + spec.NightlySuffix,
			"artifact_sha256": digest("artefato"), "binary_sha256": digest("\x7fELF nightly"),
		},
	}
	writeFile(t, filepath.Join(root, filepath.FromSlash(spec.Receipt("nightly"))), string(client.Marshal()))
	files := map[string]string{"qw/maps/x86qw-a.bsp": "mapa a", "qw/maps/x86qw-b.bsp": "mapa b"}
	var entries []receipt.Entry
	for relative, body := range files {
		writeFile(t, filepath.Join(root, filepath.FromSlash(relative)), body)
		entries = append(entries, receipt.Entry{Path: relative, SHA256: digest(body)})
	}
	inventory := receipt.MarshalInventory(entries)
	component := &receipt.Receipt{
		Keys: []string{"format", "component", "selection", "source", "inventory_sha256"},
		Values: map[string]string{"format": "1", "component": "nquake-maps", "selection": "1.0.0", "source": "teste",
			"inventory_sha256": digest(string(inventory))},
	}
	records := filepath.Join(root, ".x86qw", "components", "nquake-maps")
	writeFile(t, filepath.Join(records, "inventory"), string(inventory))
	writeFile(t, filepath.Join(records, "receipt"), string(component.Marshal()))
	writeFile(t, filepath.Join(root, ".x86qw", "cli", "receipt"), `{"version":"2.3.2"}`)
	writeFile(t, filepath.Join(root, "x86qw.sh"), "#!/bin/sh\n")
	payload, err := state.MarshalDocument(map[string]any{
		"capabilities": []string{}, "component_fingerprint": state.Fingerprint([]string{"nquake-maps"}), "format": 2,
		"known_components": []string{"nquake-maps"}, "min_cli_version": "0.7.0", "profile": "essential", "project": "x86qw",
		"recorded_components": []string{"nquake-maps"}, "requested_components": []string{}, "state_version": 2,
	})
	if err != nil {
		t.Fatal(err)
	}
	writeFile(t, filepath.Join(root, ".x86qw", "state.json"), string(payload))
	if runtime.GOOS != "windows" {
		os.Chmod(filepath.Join(root, ".x86qw"), 0o700)
	}
	return root
}

// runOK runs x86qw with args and requires exit code want.
func runOK(t *testing.T, want int, args ...string) string {
	t.Helper()
	var stdout, stderr bytes.Buffer
	if code := run(args, &stdout, &stderr); code != want {
		t.Fatalf("x86qw %v: código %d, esperado %d\n%s%s", args, code, want, stdout.String(), stderr.String())
	}
	return stdout.String() + stderr.String()
}

func TestLibraryAddsListsAndRemoves(t *testing.T) {
	root := fixtureInstall(t)
	if out := runOK(t, 0, "library", root, "--add", "QW.Example.com:27500"); !strings.Contains(out, "favoritos: 1\n  qw.example.com:27500") {
		t.Fatalf("depois de --add: %s", out)
	}
	if out := runOK(t, 0, "library", "--remove", "qw.example.com:27500", "--target", root); !strings.Contains(out, "favoritos: 0") {
		t.Fatalf("depois de --remove: %s", out)
	}
	runOK(t, 1, "library", root, "--add", "não é endereço")
	runOK(t, 2, "library", root, "--add", "a:1", "--remove", "b:2")
}

func TestProfileBacksUpAndRestores(t *testing.T) {
	root := fixtureInstall(t)
	bundle := filepath.Join(t.TempDir(), "perfil.zip")
	runOK(t, 0, "profile", root, "--backup", bundle)
	if _, err := os.Stat(bundle); err != nil {
		t.Fatal(err)
	}
	config := filepath.Join(root, "ezquake", "configs", "config.cfg")
	writeFile(t, config, "name outro\n")
	if out := runOK(t, 0, "profile", "--restore", bundle, root); !strings.Contains(out, "ezquake/configs/config.cfg") {
		t.Fatalf("restauração: %s", out)
	}
	if data, _ := os.ReadFile(config); string(data) != "name jogador\n" {
		t.Fatalf("config.cfg não voltou: %q", data)
	}
	runOK(t, 1, "profile", root, "--backup", filepath.Join(root, "sub", "dentro.zip"))
}

func TestChangesShowsWhatTheUserEdited(t *testing.T) {
	root := fixtureInstall(t)
	writeFile(t, filepath.Join(root, "qw", "maps", "x86qw-a.bsp"), "mapa editado")
	if out := runOK(t, 0, "changes", root); !strings.Contains(out, "M  qw/maps/x86qw-a.bsp") {
		t.Fatalf("changes: %s", out)
	}
}

func TestCleanupMigrateAndStatusLeaveTheInstallation(t *testing.T) {
	root := fixtureInstall(t)
	runOK(t, 0, "cleanup", root)
	if out := runOK(t, 0, "migrate", root, "--dry-run"); !strings.Contains(out, "Migração da instalação") || strings.Contains(out, "Migração concluída") {
		t.Fatalf("migrate: %s", out)
	}
	if out := runOK(t, 0, "status", root); !strings.Contains(out, "Nenhum serviço x86QW está ativo") {
		t.Fatalf("status: %s", out)
	}
	var envelope map[string]any
	if err := json.Unmarshal([]byte(runOK(t, 0, "status", "--json", root)), &envelope); err != nil || envelope["ok"] != true {
		t.Fatalf("status --json: %v %v", envelope, err)
	}
	runOK(t, 1, "status", "--stop", "--yes", root)
	runOK(t, 2, "status", "--json", "--stop", root)
	for _, kept := range []string{"id1/pak0.pak", "qw/demos/partida.mvd", ".x86qw/state.json"} {
		if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(kept))); err != nil {
			t.Fatalf("%s sumiu: %v", kept, err)
		}
	}
}

func TestPlansRunDryWithAFileCatalog(t *testing.T) {
	root := fixtureInstall(t)
	published := filepath.Join("..", "..", "..", "site", "public", "api", "v1", "catalog.json")
	for _, command := range []string{"update", "upgrade", "repair"} {
		var stdout, stderr bytes.Buffer
		code := run([]string{command, root, "--dry-run", "--json", "--catalog-file", published}, &stdout, &stderr)
		var envelope map[string]any
		if err := json.Unmarshal(stdout.Bytes(), &envelope); err != nil || envelope["dry_run"] != true || envelope["command"] != command {
			t.Fatalf("%s: %d %s %s", command, code, stdout.String(), stderr.String())
		}
	}
	// Without --dry-run and without a terminal, applying needs --yes.
	var stdout, stderr bytes.Buffer
	if code := run([]string{"upgrade", root, "--catalog-file", published}, &stdout, &stderr); code == 0 && !strings.Contains(stdout.String(), "Nenhuma atualização") {
		t.Fatalf("upgrade sem --yes aplicou: %s", stdout.String())
	}
}

func TestUninstallWithYesKeepsWhatIsYours(t *testing.T) {
	root := fixtureInstall(t)
	runOK(t, 2, "uninstall", root)
	runOK(t, 0, "uninstall", root, "--yes")
	for _, kept := range []string{"id1/pak0.pak", "ezquake/configs/config.cfg", "qw/demos/partida.mvd"} {
		if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(kept))); err != nil {
			t.Fatalf("%s deveria ficar: %v", kept, err)
		}
	}
	for _, gone := range []string{".x86qw/state.json", "qw/maps/x86qw-a.bsp", "x86qw.sh"} {
		if _, err := os.Stat(filepath.Join(root, filepath.FromSlash(gone))); err == nil {
			t.Fatalf("%s deveria sair", gone)
		}
	}
}
