package state

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const sample = `{
  "capabilities": [],
  "component_fingerprint": "%s",
  "format": 2,
  "known_components": [
    "ktx",
    "nquake-maps"
  ],
  "min_cli_version": "0.7.0",
  "profile": "essential",
  "project": "x86qw",
  "recorded_components": [
    "ktx",
    "nquake-maps"
  ],
  "requested_components": [],
  "state_version": 2
}
`

func sampleBytes() []byte {
	return []byte(strings.Replace(sample, "%s", Fingerprint([]string{"nquake-maps", "ktx"}), 1))
}

func TestFingerprintMatchesPython(t *testing.T) {
	// hashlib.sha256(b"a\nb\n").hexdigest()
	if got := Fingerprint([]string{"b", "a"}); got != "911169ddaaf146aff539f58c26c489af3b892dff0fe283c1c264c65ae5aa59a2" {
		t.Fatalf("fingerprint %s", got)
	}
}

func TestParseAndRoundTrip(t *testing.T) {
	payload := sampleBytes()
	state, err := Parse(payload)
	if err != nil {
		t.Fatal(err)
	}
	if state.Profile != "essential" || len(state.RecordedComponents) != 2 {
		t.Fatalf("estado inesperado: %+v", state)
	}
	encoded, err := state.Marshal()
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(encoded, payload) {
		t.Fatalf("serialização diverge do Python:\n%s", encoded)
	}
}

func TestRejectsInvalidStates(t *testing.T) {
	cases := map[string]string{
		"wrong fingerprint": strings.Replace(sample, "%s", strings.Repeat("0", 64), 1),
		"bad project":       strings.Replace(string(sampleBytes()), `"x86qw"`, `"other"`, 1),
		"requested without custom": strings.Replace(string(sampleBytes()),
			`"requested_components": []`, `"requested_components": ["ktx"]`, 1),
		"duplicate component": strings.Replace(string(sampleBytes()),
			"\"known_components\": [\n    \"ktx\",", "\"known_components\": [\n    \"ktx\",\n    \"ktx\",", 1),
		"float format": strings.Replace(string(sampleBytes()), `"format": 2`, `"format": 2.0`, 1),
	}
	for name, payload := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := Parse([]byte(payload)); err == nil || !IsInvalid(err) {
				t.Fatalf("estado inválido aceito: %v", err)
			}
		})
	}
}

func TestInstalledAppliesLegacyMigration(t *testing.T) {
	state := &State{RecordedComponents: []string{"nquake-bootstrap", "nquake-sounds", "ktx", "nquake-ktx"}}
	got := strings.Join(state.Installed(), ",")
	if got != "x86qw-client-bootstrap,ktx" {
		t.Fatalf("migração legada: %s", got)
	}
}

// TestRealInstallation round-trips the developer's installation when present.
func TestRealInstallation(t *testing.T) {
	home, _ := os.UserHomeDir()
	root := filepath.Join(home, "Games", "x86qw")
	payload, err := os.ReadFile(filepath.Join(root, Path))
	if err != nil {
		t.Skip("sem instalação local")
	}
	state, err := Read(root)
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := state.Marshal()
	if !bytes.Equal(encoded, payload) {
		t.Fatal("round-trip da instalação real diverge")
	}
}
