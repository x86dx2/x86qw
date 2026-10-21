// Package state reads and writes .x86qw/state.json (formats 1 and 2), byte
// for byte as existing installations have it.
package state

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"regexp"
	"sort"
	"strings"
)

const (
	// Path is the state file relative to the installation root.
	Path          = ".x86qw/state.json"
	CurrentFormat = 2
	maxBytes      = 256 * 1024
)

var (
	componentID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$`)
	// Profiles accepted in persisted state.
	Profiles = map[string]bool{"none": true, "custom": true, "essential": true, "recommended": true, "complete": true}
	// legacyReplacements and legacyRemovals are the component ids renamed or
	// dropped since the nQuake era.
	legacyReplacements = map[string]string{"nquake-bootstrap": "x86qw-client-bootstrap", "nquake-ktx": "ktx"}
	legacyRemovals     = map[string]bool{"nquake-sounds": true}
)

// Error describes an invalid state document.
type Error struct {
	Code  string
	Field string
	msg   string
}

func (e *Error) Error() string { return e.msg }

func fail(code, field, format string, args ...any) error {
	return &Error{Code: code, Field: field, msg: fmt.Sprintf(format, args...)}
}

// State is a validated state document. Unknown fields are preserved.
type State struct {
	Format               int
	Profile              string
	RequestedComponents  []string
	RecordedComponents   []string
	KnownComponents      []string
	Capabilities         []string
	ComponentFingerprint string
	document             map[string]any
}

// Fingerprint is sha256 over sorted identifiers, each followed by "\n".
func Fingerprint(components []string) string {
	sorted := append([]string(nil), components...)
	sort.Strings(sorted)
	var payload strings.Builder
	for _, id := range sorted {
		payload.WriteString(id)
		payload.WriteByte('\n')
	}
	digest := sha256.Sum256([]byte(payload.String()))
	return hex.EncodeToString(digest[:])
}

// Read loads and validates the state file of an installation root.
func Read(root string) (*State, error) {
	file, err := os.Open(root + string(os.PathSeparator) + Path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, fail("identity", "", "the installation state is not a regular file")
	}
	payload, err := io.ReadAll(io.LimitReader(file, maxBytes+1))
	if err != nil {
		return nil, err
	}
	if len(payload) > maxBytes {
		return nil, fail("identity", "", "the installation state is over %d bytes", maxBytes)
	}
	return Parse(payload)
}

// Parse validates a state document.
func Parse(payload []byte) (*State, error) {
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.UseNumber()
	var document map[string]any
	if err := decoder.Decode(&document); err != nil {
		return nil, fail("identity", "", "invalid state JSON: %v", err)
	}
	format, ok := integer(document["format"])
	profile, profileOK := document["profile"].(string)
	if !ok || (format != 1 && format != CurrentFormat) || document["project"] != "x86qw" || !profileOK || !Profiles[profile] {
		return nil, fail("identity", "", "invalid installation state identity")
	}
	state := &State{Format: format, Profile: profile, document: document}
	var err error
	if state.RequestedComponents, err = components(document, "requested_components"); err != nil {
		return nil, err
	}
	if state.RecordedComponents, err = components(document, "recorded_components"); err != nil {
		return nil, err
	}
	if state.KnownComponents, err = components(document, "known_components"); err != nil {
		return nil, err
	}
	if profile != "custom" && len(state.RequestedComponents) > 0 {
		return nil, fail("custom_requested", "", "only the custom profile can record explicit choices")
	}
	if format == CurrentFormat {
		if state.Capabilities, err = components(document, "capabilities"); err != nil {
			return nil, err
		}
		fingerprint, _ := document["component_fingerprint"].(string)
		// No capability is currently defined (INSTALLATION_CAPABILITIES is empty).
		if len(state.Capabilities) > 0 || fingerprint != Fingerprint(state.RecordedComponents) {
			return nil, fail("capabilities", "", "invalid capabilities or fingerprint in the installation state")
		}
		state.ComponentFingerprint = fingerprint
	}
	return state, nil
}

func integer(value any) (int, bool) {
	number, ok := value.(json.Number)
	if !ok || strings.ContainsAny(number.String(), ".eE") {
		return 0, false
	}
	parsed, err := number.Int64()
	return int(parsed), err == nil
}

func components(document map[string]any, field string) ([]string, error) {
	raw, ok := document[field].([]any)
	if !ok {
		return nil, fail("component_field", field, "invalid %s field in the installation state", field)
	}
	seen := map[string]bool{}
	values := make([]string, 0, len(raw))
	for _, item := range raw {
		id, ok := item.(string)
		if !ok || !componentID.MatchString(id) || seen[id] {
			return nil, fail("component_field", field, "invalid %s field in the installation state", field)
		}
		seen[id] = true
		values = append(values, id)
	}
	return values, nil
}

// Installed reports the current (post-legacy-migration) recorded components.
func (s *State) Installed() []string {
	var result []string
	seen := map[string]bool{}
	for _, id := range s.RecordedComponents {
		if legacyRemovals[id] {
			continue
		}
		if replacement, ok := legacyReplacements[id]; ok {
			id = replacement
		}
		if !seen[id] {
			seen[id] = true
			result = append(result, id)
		}
	}
	return result
}

// MarshalDocument validates and encodes a state document: two-space
// indent, sorted keys, no HTML escaping and a final newline.
func MarshalDocument(document map[string]any) ([]byte, error) {
	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(document); err != nil {
		return nil, err
	}
	if _, err := Parse(buffer.Bytes()); err != nil {
		return nil, err
	}
	return buffer.Bytes(), nil
}

// Document returns a shallow copy of the persisted document.
func (s *State) Document() map[string]any {
	copied := make(map[string]any, len(s.document))
	for key, value := range s.document {
		copied[key] = value
	}
	return copied
}

// Migrate brings a state document up to date: legacy component ids
// replaced or dropped, format 2, fingerprint recomputed, one
// installation_version marker (targetVersion when given).
func (s *State) Migrate(targetVersion string) map[string]any {
	document := s.Document()
	replace := func(ids []string) []any {
		result := []any{}
		seen := map[string]bool{}
		for _, id := range ids {
			if legacyRemovals[id] {
				continue
			}
			if replacement, ok := legacyReplacements[id]; ok {
				id = replacement
			}
			if !seen[id] {
				seen[id] = true
				result = append(result, id)
			}
		}
		return result
	}
	recorded := replace(s.RecordedComponents)
	recordedIDs := make([]string, len(recorded))
	for index, id := range recorded {
		recordedIDs[index] = id.(string)
	}
	capabilities := []any{}
	for _, capability := range s.Capabilities {
		capabilities = append(capabilities, capability)
	}
	document["format"] = CurrentFormat
	document["requested_components"] = replace(s.RequestedComponents)
	document["recorded_components"] = recorded
	document["known_components"] = replace(s.KnownComponents)
	document["capabilities"] = capabilities
	document["component_fingerprint"] = Fingerprint(recordedIDs)
	marker := ""
	for _, key := range []string{"installation_version", "installer_version", "version"} {
		if value, ok := document[key].(string); ok {
			marker = value
		}
	}
	delete(document, "version")
	delete(document, "installer_version")
	if targetVersion != "" {
		document["installation_version"] = targetVersion
	} else if marker != "" {
		document["installation_version"] = marker
	}
	return document
}
