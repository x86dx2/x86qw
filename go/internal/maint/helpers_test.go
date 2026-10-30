package maint

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/cli"
	"github.com/x86dx2/x86qw/go/internal/download"
	"github.com/x86dx2/x86qw/go/internal/receipt"
	"github.com/x86dx2/x86qw/go/internal/state"
)

// isolate points HOME and the installer cache at temp dirs, so uninstall,
// purge and cleanup never reach the developer's real ones.
func isolate(t *testing.T) (cache string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("XDG_CACHE_HOME", filepath.Join(home, ".cache"))
	t.Setenv("LOCALAPPDATA", filepath.Join(home, "AppData", "Local"))
	cache = filepath.Join(t.TempDir(), "x86qw-cache")
	t.Setenv(download.CacheEnv, cache)
	temporary := t.TempDir()
	for _, name := range []string{"TMPDIR", "TMP", "TEMP"} {
		t.Setenv(name, temporary)
	}
	return cache
}

// ownedCache creates an installer cache with its ownership marker.
func ownedCache(t *testing.T, root string) {
	t.Helper()
	write(t, filepath.Join(root, "components", "package.zip"), "zip")
	if err := os.WriteFile(filepath.Join(root, cacheMarker), []byte(cacheMarkerValue+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
}

func write(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func read(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

func sha(body string) string {
	digest := sha256.Sum256([]byte(body))
	return hex.EncodeToString(digest[:])
}

// record writes a component receipt and inventory owning files, which are
// created with their contents.
func record(t *testing.T, root, id string, files map[string]string) {
	t.Helper()
	var entries []receipt.Entry
	for relative, body := range files {
		write(t, filepath.Join(root, filepath.FromSlash(relative)), body)
		entries = append(entries, receipt.Entry{Path: relative, SHA256: sha(body)})
	}
	inventory := receipt.MarshalInventory(entries)
	digest := sha256.Sum256(inventory)
	rec := &receipt.Receipt{
		Keys: []string{"format", "component", "selection", "source", "inventory_sha256"},
		Values: map[string]string{"format": "1", "component": id, "selection": "1.0.0", "source": "teste",
			"inventory_sha256": hex.EncodeToString(digest[:])},
	}
	dir := filepath.Join(root, filepath.FromSlash(componentsRelative), id)
	write(t, filepath.Join(dir, "inventory"), string(inventory))
	write(t, filepath.Join(dir, "receipt"), string(rec.Marshal()))
}

// managedInstall lays out what an install leaves behind: the PAKs, a Linux
// nightly ezQuake with its receipt, one component, the CLI and state.json,
// plus a personal config and a demo that must survive uninstall.
func managedInstall(t *testing.T) string {
	t.Helper()
	root := filepath.Join(t.TempDir(), "x86qw")
	write(t, filepath.Join(root, "id1", "pak0.pak"), "PACK pak0")
	write(t, filepath.Join(root, "id1", "pak1.pak"), "PACK pak1")
	write(t, filepath.Join(root, "ezquake", "configs", "config.cfg"), "name player\n")
	write(t, filepath.Join(root, "qw", "demos", "match.mvd"), "demo")
	spec := catalog.ClientSpecs[1] // Linux x86_64
	version := "20260101-000000_abcdef0"
	write(t, filepath.Join(root, spec.Runtime("nightly")), "\x7fELF nightly")
	client := &receipt.Receipt{
		Keys: []string{"format", "platform", "architecture", "channel", "selection", "install_name",
			"bundle_version", "artifact_name", "artifact_url", "artifact_sha256", "binary_sha256"},
		Values: map[string]string{
			"format": "1", "platform": spec.Platform, "architecture": spec.Architecture, "channel": "nightly",
			"selection": version, "install_name": spec.Runtime("nightly"), "bundle_version": version,
			"artifact_name": version + spec.NightlySuffix, "artifact_url": "https://example.invalid/" + version + spec.NightlySuffix,
			"artifact_sha256": sha("artifact"), "binary_sha256": sha("\x7fELF nightly"),
		},
	}
	if _, err := receipt.ParseClient(client.Marshal(), spec.Context("nightly")); err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(root, filepath.FromSlash(spec.Receipt("nightly"))), string(client.Marshal()))
	record(t, root, "nquake-maps", map[string]string{
		"qw/maps/x86qw-a.bsp": "map a",
		"qw/maps/x86qw-b.bsp": "map b",
	})
	write(t, filepath.Join(root, ".x86qw", "cli", "receipt"), `{"version":"2.3.2"}`)
	write(t, filepath.Join(root, "x86qw.sh"), "#!/bin/sh\n")
	write(t, cli.Command(root), "#!/bin/sh\n")
	payload, err := state.MarshalDocument(map[string]any{
		"capabilities": []string{}, "component_fingerprint": state.Fingerprint([]string{"nquake-maps"}), "format": 2,
		"known_components": []string{"nquake-maps"}, "min_cli_version": "0.7.0", "profile": "essential", "project": "x86qw",
		"recorded_components": []string{"nquake-maps"}, "requested_components": []string{}, "state_version": 2,
	})
	if err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(root, filepath.FromSlash(stateRelative)), string(payload))
	if runtime.GOOS != "windows" {
		// Real installs keep .x86qw private; ManagedIdentity reads it as is.
		os.Chmod(filepath.Join(root, ".x86qw"), 0o700)
	}
	return root
}
