package services

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A preset edited by hand or restored from a backup holds what a flag
// would refuse: an address that is not a literal reached MVDSV and, on
// Windows, a bind on every interface that asks the firewall about x86QW.
func TestPresetsHoldLiteralAddresses(t *testing.T) {
	target := t.TempDir()
	options := &HostOptions{Game: "ktx", Map: "dm6", Bind: "127.0.0.1", Port: 28501, Maxclients: 16,
		QtvBind: "127.0.0.1", QtvPort: 28000, ProxyBind: "127.0.0.1", ProxyPort: 30000}
	if _, err := SavePreset(target, "duel", options); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadPresets(target); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(target, filepath.FromSlash(hostPresetsPath))
	saved, _ := os.ReadFile(path)
	for _, edit := range []string{`"bind": ""`, `"qtv_bind": "localhost"`, `"proxy_bind": "0.0.0.0 "`} {
		field := strings.SplitN(edit, ":", 2)[0]
		edited := strings.Replace(string(saved), field+`: "127.0.0.1"`, edit, 1)
		if edited == string(saved) {
			t.Fatalf("%s not found in %s", field, saved)
		}
		os.WriteFile(path, []byte(edited), 0o644)
		if _, err := LoadPresets(target); err == nil {
			t.Errorf("a preset with %s loaded", edit)
		}
	}
}
