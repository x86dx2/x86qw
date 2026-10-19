package content

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"os"
	"regexp"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// KTX mode catalog cross-checks (runtime_catalog.validate_ktx_mode_catalog).

const (
	ktxModesPath  = "dist/mods/ktx/1.47/x86qw/catalog/modes.json"
	ktxSourcePath = "dist/mods/ktx/1.47/source/ktx-1.47.tar.gz"
)

var (
	pakMapName = regexp.MustCompile(`^maps/([A-Za-z0-9][A-Za-z0-9_.-]{0,63})\.bsp$`)
	// Python's \s on str also matches \v, \x1c-\x1f, \x85 and \xa0.
	pyspace          = `[\t\n\v\f\r \x{1c}-\x{1f}\x{85}\x{a0}]`
	ktxCommandEntry  = regexp.MustCompile(`(?m)^` + pyspace + `*\{` + pyspace + `*"([A-Za-z0-9_]+)"` + pyspace + `*,`)
	ktxUsermode      = regexp.MustCompile(`/resources/example-configs/ktx/configs/usermodes/([^/]+)/default\.cfg$`)
	ctfEntityPattern = regexp.MustCompile(`^id1/maps/ctf/[^/]+\.ent$`)
	ktxEntryConfig   = regexp.MustCompile(`^qw/x86qw-ktx-mode-[a-z0-9-]+\.cfg$`)
	nquakeMapPath    = regexp.MustCompile(`^distributions/nquake/[^/]+/non-gpl/qw/maps/[^/]+\.bsp$`)
)

var (
	botCommands = setOf("CreateMarker", "SetGoal", "SetMarkerFlag", "SetMarkerPath",
		"SetMarkerPathAngleHint", "SetMarkerPathFlags", "SetMarkerViewOfs", "SetRocketJumpPathFields", "SetZone")
	raceCommands = setOf("race_add_route_node", "race_route_add_end", "race_route_add_start", "race_set_node_size",
		"race_set_route_falsestart_mode", "race_set_route_name", "race_set_route_timeout",
		"race_set_route_weapon_mode", "race_set_teleport_flags_by_name")
	directClientCommands = setOf("break", "hmstats", "join", "observe", "toggleready")
)

func latin1(payload []byte) string {
	runes := make([]rune, len(payload))
	for index, b := range payload {
		runes[index] = rune(b)
	}
	return string(runes)
}

func pyIsSpace(r rune) bool {
	switch {
	case r >= '\t' && r <= '\r', r >= 0x1c && r <= 0x20, r == 0x85, r == 0xa0:
		return true
	}
	return r > 0xff && strings.TrimSpace(string(r)) == ""
}

func pyStrip(value string) string { return strings.TrimFunc(value, pyIsSpace) }

func pyLStrip(value string) string { return strings.TrimLeftFunc(value, pyIsSpace) }

// pySplitlines mirrors str.splitlines() for latin-1 text.
func pySplitlines(value string) []string {
	var lines []string
	runes := []rune(value)
	start := 0
	for index := 0; index < len(runes); index++ {
		switch runes[index] {
		case '\n', '\v', '\f', 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029:
			lines = append(lines, string(runes[start:index]))
			start = index + 1
		case '\r':
			lines = append(lines, string(runes[start:index]))
			if index+1 < len(runes) && runes[index+1] == '\n' {
				index++
			}
			start = index + 1
		}
	}
	if start < len(runes) {
		lines = append(lines, string(runes[start:]))
	}
	return lines
}

// pySplit mirrors str.split() with no arguments.
func pySplit(value string) []string { return strings.FieldsFunc(value, pyIsSpace) }

type tarMember struct {
	header  *tar.Header
	regular bool
	data    []byte
}

func tarName(header *tar.Header) string {
	name := header.Name
	if header.Typeflag == tar.TypeDir {
		name = strings.TrimRight(name, "/")
	}
	return name
}

func pakMapNames(path string) (map[string]bool, error) {
	payload, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if len(payload) < 12 || string(payload[:4]) != "PACK" {
		return nil, fmt.Errorf("invalid Quake PAK: %s", path)
	}
	offset := int(binary.LittleEndian.Uint32(payload[4:8]))
	size := int(binary.LittleEndian.Uint32(payload[8:12]))
	if size%64 != 0 || offset+size > len(payload) {
		return nil, fmt.Errorf("invalid Quake PAK directory: %s", path)
	}
	maps := map[string]bool{}
	for cursor := offset; cursor < offset+size; cursor += 64 {
		raw := payload[cursor : cursor+56]
		if index := bytes.IndexByte(raw, 0); index >= 0 {
			raw = raw[:index]
		}
		for _, b := range raw {
			if b >= 0x80 {
				return nil, fmt.Errorf("invalid Quake PAK member: %s", path)
			}
		}
		if match := pakMapName.FindStringSubmatch(string(raw)); match != nil {
			maps[strings.ToLower(match[1])] = true
		}
	}
	return maps, nil
}

func foldedSet(values []string) map[string]bool {
	result := map[string]bool{}
	for _, value := range values {
		result[Casefold(value)] = true
	}
	return result
}

func equalSets(a, b map[string]bool) bool {
	if len(a) != len(b) {
		return false
	}
	for key := range a {
		if !b[key] {
			return false
		}
	}
	return true
}

type ktxAssets struct {
	botRoutes, raceRoutes, totConfigs, usermodes, serverCommands map[string]bool
}

var errKTXSource = errors.New("cannot inspect KTX 1.47 source assets")

func readKTXSource(path string) (*ktxAssets, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, errKTXSource
	}
	defer file.Close()
	compressed, err := gzip.NewReader(file)
	if err != nil {
		return nil, errKTXSource
	}
	reader := tar.NewReader(compressed)
	var names []string
	members := map[string]*tarMember{}
	isRoute := func(name string) bool {
		return (strings.Contains(name, "/resources/example-configs/ktx/bots/maps/") && strings.HasSuffix(name, ".bot")) ||
			(strings.Contains(name, "/resources/example-configs/ktx/race/routes/") && strings.HasSuffix(name, ".route"))
	}
	for {
		header, err := reader.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, errKTXSource
		}
		name := tarName(header)
		names = append(names, name)
		member := &tarMember{header: header}
		switch header.Typeflag {
		case tar.TypeReg, '\x00', tar.TypeCont, tar.TypeGNUSparse:
			member.regular = true
		}
		if member.regular && (isRoute(name) || strings.HasSuffix(name, "/src/commands.c")) && header.Size <= 64*1024*1024 {
			if member.data, err = io.ReadAll(reader); err != nil {
				return nil, errKTXSource
			}
		}
		members[name] = member
	}
	assets := &ktxAssets{
		botRoutes: map[string]bool{}, raceRoutes: map[string]bool{}, totConfigs: map[string]bool{},
		usermodes: map[string]bool{}, serverCommands: map[string]bool{},
	}
	commandSource := ""
	for _, name := range names {
		stem := Casefold(pathStem(posixName(name)))
		switch {
		case strings.Contains(name, "/resources/example-configs/ktx/bots/maps/") && strings.HasSuffix(name, ".bot"):
			assets.botRoutes[stem] = true
		case strings.Contains(name, "/resources/example-configs/ktx/race/routes/") && strings.HasSuffix(name, ".route"):
			assets.raceRoutes[stem] = true
		}
		if strings.Contains(name, "/resources/example-configs/ktx/configs/usermodes/tot/") && strings.HasSuffix(name, ".cfg") {
			assets.totConfigs[stem] = true
		}
		if match := ktxUsermode.FindStringSubmatch(name); match != nil {
			assets.usermodes[Casefold(match[1])] = true
		}
		if commandSource == "" && strings.HasSuffix(name, "/src/commands.c") {
			commandSource = name
		}
	}
	if commandSource == "" {
		return nil, errors.New("KTX command registry is missing")
	}
	if member := members[commandSource]; !member.regular {
		return nil, errors.New("cannot read KTX command registry")
	}
	for _, match := range ktxCommandEntry.FindAllStringSubmatch(latin1(members[commandSource].data), -1) {
		assets.serverCommands[Casefold(match[1])] = true
	}
	for _, name := range names {
		if !isRoute(name) {
			continue
		}
		member := members[name]
		if !member.regular || member.header.Size <= 0 || member.header.Size > 1024*1024 {
			return nil, fmt.Errorf("unsafe KTX route member: %s", name)
		}
		payload := member.data
		for _, b := range payload {
			if b == 0 || (b < 32 && b != 9 && b != 10 && b != 13) {
				return nil, fmt.Errorf("KTX route contains control bytes: %s", name)
			}
		}
		var commands []string
		count := map[string]int{}
		separator := false
		for _, line := range pySplitlines(latin1(payload)) {
			if pyStrip(line) == "" || strings.HasPrefix(pyLStrip(line), "//") {
				continue
			}
			command := pySplit(line)[0]
			commands = append(commands, command)
			count[command]++
			separator = separator || strings.Contains(line, ";")
		}
		if separator {
			return nil, fmt.Errorf("KTX route contains a command separator: %s", name)
		}
		allowed := raceCommands
		if strings.HasSuffix(name, ".bot") {
			allowed = botCommands
		}
		unknown := len(commands) == 0
		for _, command := range commands {
			unknown = unknown || !allowed[command]
		}
		if unknown {
			return nil, fmt.Errorf("KTX route contains an unknown directive: %s", name)
		}
		if strings.HasSuffix(name, ".bot") && count["CreateMarker"] == 0 {
			return nil, fmt.Errorf("KTX Frogbot route has no markers: %s", name)
		}
		if strings.HasSuffix(name, ".route") && (count["race_route_add_start"] != count["race_route_add_end"] || count["race_add_route_node"] == 0) {
			return nil, fmt.Errorf("KTX Race route is structurally incomplete: %s", name)
		}
	}
	return assets, nil
}

// ValidateKTXModeCatalog mirrors validate_ktx_mode_catalog; modeCatalog nil
// reads the shipped catalog.
func (p *Project) ValidateKTXModeCatalog(componentCatalog *Object, modeCatalog any) error {
	catalogPath := p.Path(ktxModesPath)
	if modeCatalog == nil {
		value, err := ReadJSON(catalogPath)
		if err != nil {
			return fmt.Errorf("cannot read KTX mode catalog: %s", catalogPath)
		}
		modeCatalog = value
	}
	catalog, ok := modeCatalog.(*Object)
	if !ok {
		return fmt.Errorf("invalid KTX mode catalog identity")
	}
	policies, policiesOK := catalog.Value("map_asset_policies").(*Object)
	defaults, defaultsOK := catalog.Value("defaults").(*Object)
	modes, modesOK := catalog.Value("modes").([]any)
	if !pyjson.EqInt(catalog.Value("format"), 1) || !pyjson.Eq(catalog.Value("game"), "ktx") || !policiesOK || !defaultsOK || !modesOK || len(modes) == 0 {
		return fmt.Errorf("invalid KTX mode catalog identity")
	}
	for _, key := range policies.Keys() {
		var asset any
		if policy, ok := policies.Value(key).(*Object); ok {
			asset = policy.Value("asset")
		}
		text, ok := asset.(string)
		if !inventoryID.MatchString(key) || !ok || !strings.Contains(text, "{map}") {
			return fmt.Errorf("invalid KTX map policy: %s", key)
		}
		if _, err := safePath(strings.ReplaceAll(text, "{map}", "map"), "KTX map policy"); err != nil {
			return err
		}
	}
	expected := map[string][2]string{
		"frogbot-route": {"bots/maps/{map}.bot", "frogbots"},
		"ctf-entities":  {"id1/maps/ctf/{map}.ent", "always"},
		"race-route":    {"race/routes/{map}.route", "always"},
	}
	consistent := len(policies.Keys()) == len(expected)
	for key, value := range expected {
		policy, ok := policies.Value(key).(*Object)
		consistent = consistent && ok && policy.Value("asset") == value[0] && policy.Value("when") == value[1]
	}
	if !consistent {
		return fmt.Errorf("KTX map policies disagree with the runtime contract")
	}

	assets, err := readKTXSource(p.Path(ktxSourcePath))
	if err != nil {
		return err
	}
	if len(assets.botRoutes) != 77 || len(assets.raceRoutes) != 54 {
		return fmt.Errorf("KTX route inventory changed without catalog review")
	}

	essential := map[string]bool{}
	for _, name := range []string{"pak0.pak", "pak1.pak"} {
		maps, err := pakMapNames(p.Path("dist/game-data/id1/" + name))
		if err != nil {
			return err
		}
		for key := range maps {
			essential[key] = true
		}
	}
	manifestValue, err := ReadJSON(p.Path(ManifestPath))
	if err != nil {
		return err
	}
	manifest, _ := manifestValue.(*Object)
	var files *Object
	if manifest != nil {
		files, _ = manifest.Value("files").(*Object)
	}
	if files == nil {
		return fmt.Errorf("invalid distribution manifest for KTX map validation")
	}
	managed := map[string]bool{}
	for key := range essential {
		managed[key] = true
	}
	for _, path := range files.Keys() {
		if nquakeMapPath.MatchString(path) {
			managed[Casefold(pathStem(posixName(path)))] = true
		}
	}

	var ktx *Object
	for _, component := range objects(componentCatalog.Value("components")) {
		if component.Value("id") == "ktx" {
			ktx = component
			break
		}
	}
	if ktx == nil {
		return fmt.Errorf("KTX component missing during mode validation")
	}
	ctfEntities, entryConfigs := map[string]bool{}, map[string]bool{}
	sources, _ := getOr(ktx, "sources", []any{}).([]any)
	for _, entry := range objects(sources) {
		destination := pyjson.Repr(entry.Value("destination"))
		if ctfEntityPattern.MatchString(destination) {
			ctfEntities[Casefold(pathStem(posixName(destination)))] = true
		}
	}
	projectSources, _ := getOr(ktx, "project_sources", []any{}).([]any)
	for _, entry := range objects(projectSources) {
		destination := pyjson.Repr(entry.Value("destination"))
		if ktxEntryConfig.MatchString(destination) {
			entryConfigs[Casefold(posixName(destination))] = true
		}
	}

	if !pyjson.Eq(defaults.Value("frogbot_map_policies"), []any{"frogbot-route"}) {
		return fmt.Errorf("KTX Frogbot map policy default is inconsistent")
	}
	modeIDs := map[string]bool{}
	for _, raw := range modes {
		if err := validateKTXMode(raw, modeIDs, policies, assets, essential, managed, ctfEntities, entryConfigs); err != nil {
			return err
		}
	}
	return nil
}

func validateKTXMode(raw any, modeIDs map[string]bool, policies *Object, assets *ktxAssets,
	essential, managed, ctfEntities, entryConfigs map[string]bool) error {
	mode, _ := raw.(*Object)
	var identifierValue, suggestionsValue any
	if mode != nil {
		identifierValue, suggestionsValue = mode.Value("id"), mode.Value("suggested_maps")
	}
	identifier, idOK := identifierValue.(string)
	suggestionList, listOK := suggestionsValue.([]any)
	suggestions, stringsOK := pyjson.StrList(suggestionList)
	folded := foldedSet(suggestions)
	if !idOK || modeIDs[identifier] || !listOK || len(suggestionList) == 0 ||
		!listContains(suggestionList, mode.Value("default_map")) || !stringsOK || len(folded) != len(suggestions) {
		return fmt.Errorf("invalid KTX mode map contract: %s", reprScalar(identifierValue))
	}
	modeIDs[identifier] = true
	usermode, ok := mode.Value("usermode").(string)
	if !ok || (!assets.usermodes[Casefold(usermode)] && !assets.serverCommands[Casefold(usermode)]) {
		return fmt.Errorf("KTX mode references missing usermode: %s", identifier)
	}
	if entry := mode.Value("entry_config"); entry != nil {
		text, ok := entry.(string)
		if !ok || !entryConfigs[Casefold(text)] {
			return fmt.Errorf("KTX mode references missing entry config: %s", identifier)
		}
	}
	var commands []any
	for _, field := range []struct {
		name  string
		index int
	}{{"help_commands", 0}, {"key_bindings", 1}} {
		entries, ok := mode.Value(field.name).([]any)
		if !ok {
			return fmt.Errorf("KTX mode has invalid %s: %s", field.name, identifier)
		}
		for _, entry := range entries {
			if list, ok := entry.([]any); ok && len(list) > field.index {
				commands = append(commands, list[field.index])
			}
		}
	}
	for _, raw := range commands {
		command, ok := raw.(string)
		if !ok || command == "" {
			return fmt.Errorf("KTX mode has an invalid help command: %s", identifier)
		}
		words := pySplit(command)
		if len(words) == 0 {
			return fmt.Errorf("IndexError: list index out of range")
		}
		if Casefold(words[0]) == "cmd" {
			if len(words) < 2 || !assets.serverCommands[Casefold(words[1])] {
				return fmt.Errorf("KTX mode references missing server command: %s", identifier)
			}
		} else if !directClientCommands[Casefold(command)] {
			return fmt.Errorf("KTX mode references unsupported client command: %s", identifier)
		}
	}
	defaultMap := Casefold(mode.Value("default_map").(string))
	if !essential[defaultMap] {
		return fmt.Errorf("KTX mode default is unavailable in Essential: %s", identifier)
	}
	if pyjson.Truthy(getOr(mode, "bots", true)) {
		if !assets.botRoutes[defaultMap] {
			return fmt.Errorf("KTX mode default lacks Frogbot route: %s", identifier)
		}
		shared := false
		for name := range folded {
			shared = shared || (assets.botRoutes[name] && managed[name])
		}
		if !shared {
			return fmt.Errorf("KTX mode has no managed Frogbot map: %s", identifier)
		}
	}
	modePolicies, ok := getOr(mode, "map_policies", []any{}).([]any)
	if ok {
		for _, value := range modePolicies {
			text, isText := value.(string)
			ok = ok && isText && policies.Has(text)
		}
	}
	if !ok {
		return fmt.Errorf("KTX mode references invalid map policy: %s", identifier)
	}
	expected := map[string][]any{"ctf": {"ctf-entities"}, "race": {"race-route"}}[identifier]
	if expected == nil {
		expected = []any{}
	}
	if !pyjson.Eq(modePolicies, expected) {
		return fmt.Errorf("KTX mode map policy is inconsistent: %s", identifier)
	}
	if identifier == "ctf" && !equalSets(folded, ctfEntities) {
		return fmt.Errorf("KTX CTF suggestions disagree with managed ENT files")
	}
	if identifier == "race" && !equalSets(folded, assets.raceRoutes) {
		return fmt.Errorf("KTX Race suggestions disagree with shipped routes")
	}
	if identifier == "tot" && !equalSets(folded, assets.totConfigs) {
		return fmt.Errorf("KTX ToT suggestions disagree with map-specific configs")
	}
	return nil
}
