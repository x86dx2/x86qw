package content

import (
	"fmt"
	"os"
	"regexp"
	"slices"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Runtime, game and compatibility inventories.

var (
	inventoryID              = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)
	variantPattern           = regexp.MustCompile(`^[a-z0-9]+(?:[-_][a-z0-9]+)*$`)
	platformSupportStates    = setOf("supported", "conditional", "preview", "deprecated", "unavailable")
	platformValidationStates = setOf("portable-contract", "native-evidence", "not-run")
	requiredRuntimeFields    = []string{
		"id", "label", "kind", "protocols", "capabilities", "component", "channels",
		"platforms", "architectures", "executable", "runtime_path", "configuration",
		"personal_configuration", "logs", "demos", "arguments", "environment",
		"dependencies", "readiness", "smoke_test",
	}
	requiredPlatformFields = []string{
		"system", "architecture", "variant", "executable", "runtime_path", "format",
		"permissions", "support", "validation", "origin", "test_required",
	}
	supportTargetFields = []string{"variant", "support", "validation"}
	requiredGameFields  = []string{
		"id", "label", "protocol", "component", "gamedir", "profile", "marker",
		"gamecode", "gamecode_type", "default_map", "suggested_maps", "client_runtimes",
		"server_runtimes", "pre_map_arguments", "post_map_arguments", "managed_config",
		"personal_config", "required_capabilities", "smoke_test",
	}
)

// Inventory holds the four runtime inventory documents.
type Inventory struct {
	Capabilities, Runtimes, Games, Compatibility *Object
}

func hasAll(object *Object, fields []string) bool {
	for _, field := range fields {
		if !object.Has(field) {
			return false
		}
	}
	return true
}

// readCatalogObject reads an inventory file that must hold a JSON object.
func readCatalogObject(path, label string) (*Object, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("cannot read %s catalog: %s", label, path)
	}
	value, err := pyjson.Decode(raw, pyjson.RejectDuplicates)
	if err != nil {
		return nil, fmt.Errorf("cannot read %s catalog: %s", label, path)
	}
	document, ok := value.(*Object)
	if !ok {
		return nil, fmt.Errorf("invalid %s catalog identity", label)
	}
	return document, nil
}

func loadInventoryDocument(path, collection string) (*Object, error) {
	document, err := readCatalogObject(path, collection)
	if err != nil {
		return nil, err
	}
	if !pyjson.IsExactInt(document.Value("format")) || !pyjson.EqInt(document.Value("format"), 1) ||
		!pyjson.Eq(document.Value("project"), "x86qw") {
		return nil, fmt.Errorf("invalid %s catalog identity", collection)
	}
	if collection != "capabilities" {
		if _, ok := document.Value(collection).([]any); !ok {
			return nil, fmt.Errorf("invalid %s catalog identity", collection)
		}
	}
	if validateCatalogVersions(document, false) != nil {
		return nil, fmt.Errorf("invalid %s catalog contract", collection)
	}
	return document, nil
}

// LoadInventory loads and cross-checks the runtime, game and compatibility
// inventories.
func (p *Project) LoadInventory(componentCatalog *Object, publicCatalog *Object) (*Inventory, error) {
	var inventory Inventory
	for _, item := range []struct {
		target **Object
		name   string
	}{
		{&inventory.Capabilities, "capabilities"},
		{&inventory.Runtimes, "runtimes"},
		{&inventory.Games, "games"},
		{&inventory.Compatibility, "compatibility"},
	} {
		document, err := loadInventoryDocument(p.Path(InventoryDir+"/"+item.name+".json"), item.name)
		if err != nil {
			return nil, err
		}
		*item.target = document
	}
	if err := p.validateInventory(&inventory, componentCatalog, publicCatalog); err != nil {
		return nil, err
	}
	return &inventory, nil
}

// idEntries indexes a list of entries by id; it returns ids in document
// order.
func idEntries(document *Object, key, label string) (map[string]*Object, []string, error) {
	entries, ok := document.Value(key).([]any)
	if !ok || len(entries) == 0 {
		return nil, nil, fmt.Errorf("%s catalog is empty", label)
	}
	result := map[string]*Object{}
	var order []string
	for _, raw := range entries {
		var identifier any
		entry, isObject := raw.(*Object)
		if isObject {
			identifier = entry.Value("id")
		}
		text, ok := identifier.(string)
		if !ok || !inventoryID.MatchString(text) || result[text] != nil {
			return nil, nil, fmt.Errorf("invalid or duplicate %s id: %s", label, reprScalar(identifier))
		}
		result[text] = entry
		order = append(order, text)
	}
	return result, order, nil
}

// stringList is value as a list of strings, empty only when allowEmpty.
func stringList(value any, label string, allowEmpty bool) ([]string, error) {
	raw, ok := value.([]any)
	if !ok || (!allowEmpty && len(raw) == 0) {
		return nil, fmt.Errorf("invalid %s", label)
	}
	values, ok := pyjson.StrList(raw)
	if !ok || slices.Contains(values, "") || !uniqueStrings(values) {
		return nil, fmt.Errorf("invalid %s", label)
	}
	return values, nil
}

// hashSet is the set of values, false when one is not a scalar.
func hashSet(values []any) (map[string]bool, bool) {
	result := map[string]bool{}
	for _, value := range values {
		if !pyjson.Hashable(value) {
			return nil, false
		}
		result[pyjson.HashKey(value)] = true
	}
	return result, true
}

func stringHashSet(values []string) map[string]bool {
	result := map[string]bool{}
	for _, value := range values {
		result[pyjson.HashKey(value)] = true
	}
	return result
}

func equalHashSets(a, b map[string]bool) bool {
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

// listContains reports whether list holds a value equal to value, numbers
// compared by value.
func listContains(list any, value any) bool {
	items, _ := list.([]any)
	for _, item := range items {
		if pyjson.Eq(item, value) {
			return true
		}
	}
	return false
}

func (p *Project) validateInventory(inventory *Inventory, componentCatalog, publicCatalog *Object) error {
	for _, document := range []*Object{inventory.Capabilities, inventory.Runtimes, inventory.Games, inventory.Compatibility} {
		if validateCatalogVersions(document, false) != nil {
			return fmt.Errorf("invalid runtime inventory contract")
		}
	}
	if componentCatalog != nil {
		if err := p.ValidateKTXModeCatalog(componentCatalog, nil); err != nil {
			return err
		}
	}
	capabilities, runtimes, games, compatibility := inventory.Capabilities, inventory.Runtimes, inventory.Games, inventory.Compatibility
	for _, item := range []struct {
		document *Object
		key      string
	}{{runtimes, "runtimes"}, {games, "games"}, {compatibility, "compatibility"}} {
		if _, ok := item.document.Value(item.key).([]any); !pyjson.EqInt(item.document.Value("format"), 1) || !pyjson.Eq(item.document.Value("project"), "x86qw") || !ok {
			return fmt.Errorf("invalid %s catalog identity", item.key)
		}
	}
	if !pyjson.EqInt(capabilities.Value("format"), 1) || !pyjson.Eq(capabilities.Value("project"), "x86qw") {
		return fmt.Errorf("invalid capabilities catalog identity")
	}
	lists := map[string][]string{}
	for _, item := range [][2]string{
		{"runtime_kinds", "runtime kinds"}, {"compatibility_kinds", "compatibility kinds"},
		{"systems", "systems"}, {"architectures", "architectures"},
		{"executable_formats", "executable formats"}, {"commands", "CLI commands"},
	} {
		values, err := stringList(capabilities.Value(item[0]), item[1], false)
		if err != nil {
			return err
		}
		lists[item[0]] = values
	}
	runtimeKinds, compatibilityKinds := setOf(lists["runtime_kinds"]...), setOf(lists["compatibility_kinds"]...)
	systems, architectures, formats := setOf(lists["systems"]...), setOf(lists["architectures"]...), setOf(lists["executable_formats"]...)

	hostSystems, hostOK := capabilities.Value("host_systems").(*Object)
	aliases, aliasesOK := capabilities.Value("architecture_aliases").(*Object)
	labels, labelsOK := capabilities.Value("platform_labels").(*Object)
	valid := hostOK && aliasesOK && labelsOK
	if valid {
		var values []any
		for _, key := range hostSystems.Keys() {
			values = append(values, hostSystems.Value(key))
		}
		set, hashable := hashSet(values)
		if !hashable {
			return fmt.Errorf("unhashable host system")
		}
		valid = equalHashSets(set, stringHashSet(lists["systems"])) && sameSet(aliases.Keys(), architectures)
		for _, key := range aliases.Keys() {
			list, ok := aliases.Value(key).([]any)
			valid = valid && ok && len(list) > 0
		}
	}
	if !valid {
		return fmt.Errorf("platform host mappings are invalid")
	}
	protocols, _, err := idEntries(capabilities, "protocols", "protocol")
	if err != nil {
		return err
	}
	capabilityEntries, _, err := idEntries(capabilities, "capabilities", "capability")
	if err != nil {
		return err
	}

	componentIDs := map[string]bool{}
	componentEntries := map[string]*Object{}
	if componentCatalog != nil {
		if client, ok := componentCatalog.Value("client").(*Object); ok {
			if identifier, ok := client.Value("id").(string); ok {
				componentIDs[identifier] = true
			}
		}
		raw, ok := componentCatalog.Value("components").([]any)
		if !ok {
			return fmt.Errorf("invalid component catalog for runtime validation")
		}
		for _, item := range raw {
			if component, ok := item.(*Object); ok {
				if identifier, ok := component.Value("id").(string); ok {
					componentIDs[identifier] = true
					componentEntries[identifier] = component
				}
			}
		}
	}

	runtimeEntries, runtimeOrder, err := idEntries(runtimes, "runtimes", "runtime")
	if err != nil {
		return err
	}
	dependencies := map[string][]string{}
	for _, identifier := range runtimeOrder {
		runtime := runtimeEntries[identifier]
		if err := validateRuntime(identifier, runtime, runtimeKinds, systems, architectures, formats, labels, protocols, capabilityEntries, componentIDs); err != nil {
			return err
		}
		required, err := stringList(runtime.Value("dependencies"), "runtime dependencies: "+identifier, true)
		if err != nil {
			return err
		}
		dependencies[identifier] = required
	}
	for _, identifier := range runtimeOrder {
		for _, dependency := range dependencies[identifier] {
			if runtimeEntries[dependency] == nil {
				return fmt.Errorf("runtime dependency is inconsistent: %s", identifier)
			}
		}
		if slices.Contains(dependencies[identifier], identifier) {
			return fmt.Errorf("runtime dependency is inconsistent: %s", identifier)
		}
	}
	if err := validateDependencyGraph(runtimeOrder, dependencies, "runtime dependency cycle at %s"); err != nil {
		return err
	}

	gameEntries, gameOrder, err := idEntries(games, "games", "game")
	if err != nil {
		return err
	}
	for _, identifier := range gameOrder {
		if err := validateGame(identifier, gameEntries[identifier], protocols, capabilityEntries, runtimeEntries, componentIDs, componentEntries, componentCatalog != nil); err != nil {
			return err
		}
	}

	rawCompatibility, ok := compatibility.Value("compatibility").([]any)
	if !ok || len(rawCompatibility) == 0 {
		return fmt.Errorf("compatibility catalog is empty")
	}
	seenPairs := map[[2]string]bool{}
	clientMatrix, serverMatrix := map[string]map[string]bool{}, map[string]map[string]bool{}
	for _, identifier := range gameOrder {
		clientMatrix[identifier], serverMatrix[identifier] = map[string]bool{}, map[string]bool{}
	}
	for _, raw := range rawCompatibility {
		entry, ok := raw.(*Object)
		if !ok {
			return fmt.Errorf("invalid compatibility entry")
		}
		kind, runtimeID := entry.Value("kind"), entry.Value("runtime")
		runtimeText, isText := runtimeID.(string)
		if !inSet(kind, compatibilityKinds) || !isText || runtimeEntries[runtimeText] == nil {
			return fmt.Errorf("compatibility references invalid kind or runtime")
		}
		runtime := runtimeEntries[runtimeText]
		if !pyjson.Eq(runtime.Value("kind"), kind) {
			return fmt.Errorf("compatibility kind disagrees with runtime: %s", runtimeText)
		}
		pair := [2]string{kind.(string), runtimeText}
		if seenPairs[pair] {
			return fmt.Errorf("duplicate compatibility entry: %s", runtimeText)
		}
		seenPairs[pair] = true
		listed, err := stringList(entry.Value("games"), "compatibility games: "+runtimeText, true)
		if err != nil {
			return err
		}
		for _, game := range listed {
			if gameEntries[game] == nil {
				return fmt.Errorf("compatibility references invalid game or protocol: %s", runtimeText)
			}
		}
		if !listContains(runtime.Value("protocols"), entry.Value("protocol")) {
			return fmt.Errorf("compatibility references invalid game or protocol: %s", runtimeText)
		}
		for _, game := range listed {
			switch kind {
			case "client":
				clientMatrix[game][runtimeText] = true
			case "server":
				serverMatrix[game][runtimeText] = true
			}
		}
	}
	for _, identifier := range gameOrder {
		game := gameEntries[identifier]
		clients, _ := pyjson.StrList(game.Value("client_runtimes"))
		servers, _ := pyjson.StrList(game.Value("server_runtimes"))
		if !sameSet(clients, clientMatrix[identifier]) {
			return fmt.Errorf("client compatibility disagrees with game: %s", identifier)
		}
		if !sameSet(servers, serverMatrix[identifier]) {
			return fmt.Errorf("server compatibility disagrees with game: %s", identifier)
		}
	}

	if componentCatalog != nil {
		for _, identifier := range runtimeOrder {
			runtime := runtimeEntries[identifier]
			if runtime.Value("component") == "ezquake" {
				continue
			}
			component := componentEntries[pyjson.Repr(runtime.Value("component"))]
			if component == nil {
				return fmt.Errorf("KeyError: %s", pyRepr(runtime.Value("component")))
			}
			declared := map[string]*Object{}
			sources, _ := getOr(component, "project_sources", []any{}).([]any)
			for _, raw := range sources {
				if entry, ok := raw.(*Object); ok && entry.Value("platform") != nil {
					declared[pyjson.Repr(entry.Value("platform"))] = entry
				}
			}
			for _, platform := range objects(runtime.Value("platforms")) {
				variant := pyjson.Repr(platform.Value("variant"))
				entry := declared[variant]
				path := pyjson.Repr(platform.Value("runtime_path"))
				if entry == nil || !pyjson.Eq(entry.Value("install_destination"), path) ||
					!isFile(p.Path(pyjson.Repr(entry.Value("path")))) {
					return fmt.Errorf("runtime architecture has no artifact: %s/%s", identifier, variant)
				}
			}
		}
	}
	if publicCatalog != nil {
		packages, ok := publicCatalog.Value("packages").([]any)
		if !ok {
			return fmt.Errorf("invalid public package catalog")
		}
		for _, identifier := range []string{"ezquake-stable", "ezquake-nightly"} {
			runtime := runtimeEntries[identifier]
			if runtime == nil {
				return fmt.Errorf("KeyError: '%s'", identifier)
			}
			channels, _ := runtime.Value("channels").([]any)
			channel := channels[0]
			for _, platform := range objects(runtime.Value("platforms")) {
				found := false
				for _, raw := range packages {
					pkg, ok := raw.(*Object)
					if ok && pyjson.Eq(pkg.Value("component"), "ezquake") && pyjson.Eq(pkg.Value("channel"), channel) &&
						pyjson.Eq(pkg.Value("platform"), platform.Value("system")) && pyjson.Eq(pkg.Value("architecture"), platform.Value("architecture")) {
						found = true
						break
					}
				}
				if !found {
					return fmt.Errorf("client architecture has no public artifact: %s/%s", identifier, pyjson.Repr(platform.Value("variant")))
				}
			}
		}
	}
	return nil
}

func validateRuntime(identifier string, runtime *Object, runtimeKinds, systems, architectures, formats map[string]bool,
	labels *Object, protocols, capabilityEntries map[string]*Object, componentIDs map[string]bool) error {
	if !hasAll(runtime, requiredRuntimeFields) {
		return fmt.Errorf("runtime lacks required fields: %s", identifier)
	}
	if !inSet(runtime.Value("kind"), runtimeKinds) {
		return fmt.Errorf("invalid runtime kind: %s", identifier)
	}
	if !pyjson.NonEmptyStr(runtime.Value("label")) {
		return fmt.Errorf("runtime lacks label: %s", identifier)
	}
	runtimeProtocols, err := stringList(runtime.Value("protocols"), "runtime protocols: "+identifier, false)
	if err != nil {
		return err
	}
	runtimeCapabilities, err := stringList(runtime.Value("capabilities"), "runtime capabilities: "+identifier, false)
	if err != nil {
		return err
	}
	for _, protocol := range runtimeProtocols {
		if protocols[protocol] == nil {
			return fmt.Errorf("runtime references unknown protocol or capability: %s", identifier)
		}
	}
	for _, capability := range runtimeCapabilities {
		if capabilityEntries[capability] == nil {
			return fmt.Errorf("runtime references unknown protocol or capability: %s", identifier)
		}
	}
	component, ok := runtime.Value("component").(string)
	if !ok || !inventoryID.MatchString(component) {
		return fmt.Errorf("runtime has invalid component: %s", identifier)
	}
	if len(componentIDs) > 0 && !componentIDs[component] {
		return fmt.Errorf("runtime references missing component: %s", identifier)
	}
	if _, err := stringList(runtime.Value("channels"), "runtime channels: "+identifier, false); err != nil {
		return err
	}
	platforms, ok := runtime.Value("platforms").([]any)
	if !ok || len(platforms) == 0 {
		return fmt.Errorf("runtime has no platforms: %s", identifier)
	}
	variants, declaredArchitectures := map[string]bool{}, map[string]bool{}
	for _, raw := range platforms {
		platform, ok := raw.(*Object)
		if !ok || !hasAll(platform, requiredPlatformFields) {
			return fmt.Errorf("invalid runtime platform: %s", identifier)
		}
		variant, ok := platform.Value("variant").(string)
		if !ok || !variantPattern.MatchString(variant) || variants[variant] {
			return fmt.Errorf("invalid or duplicate runtime variant: %s", identifier)
		}
		variants[variant] = true
		if !inSet(platform.Value("system"), systems) || !inSet(platform.Value("architecture"), architectures) || !inSet(platform.Value("format"), formats) {
			return fmt.Errorf("runtime platform uses an unknown value: %s/%s", identifier, variant)
		}
		declaredArchitectures[platform.Value("architecture").(string)] = true
		if _, isBool := platform.Value("test_required").(bool); !inSet(platform.Value("support"), platformSupportStates) ||
			!inSet(platform.Value("validation"), platformValidationStates) || !isBool {
			return fmt.Errorf("runtime platform support is not explicit: %s/%s", identifier, variant)
		}
		for _, field := range []string{"executable", "runtime_path"} {
			if _, err := safePath(platform.Value(field), "runtime "+field); err != nil {
				return err
			}
		}
		if !labels.Has(variant) {
			return fmt.Errorf("runtime platform lacks a public label: %s/%s", identifier, variant)
		}
		targets, ok := getOr(platform, "support_targets", []any{}).([]any)
		if !ok {
			return fmt.Errorf("runtime support targets are invalid: %s/%s", identifier, variant)
		}
		targetVariants := map[string]bool{}
		for _, rawTarget := range targets {
			target, ok := rawTarget.(*Object)
			if !ok || !hasAll(target, supportTargetFields) {
				return fmt.Errorf("runtime support target is invalid: %s/%s", identifier, variant)
			}
			targetVariant, ok := target.Value("variant").(string)
			if !ok || !variantPattern.MatchString(targetVariant) || targetVariants[targetVariant] || targetVariant == variant ||
				!labels.Has(targetVariant) || !inSet(target.Value("support"), platformSupportStates) ||
				!inSet(target.Value("validation"), platformValidationStates) {
				return fmt.Errorf("runtime support target is invalid: %s/%s", identifier, variant)
			}
			targetVariants[targetVariant] = true
		}
	}
	executable, executableOK := runtime.Value("executable").(*Object)
	runtimePath, runtimePathOK := runtime.Value("runtime_path").(*Object)
	if !executableOK || !runtimePathOK || !sameSet(executable.Keys(), variants) || !sameSet(runtimePath.Keys(), variants) {
		return fmt.Errorf("runtime variant maps are incomplete: %s", identifier)
	}
	for _, raw := range platforms {
		variant := raw.(*Object).Value("variant").(string)
		if _, err := safePath(executable.Value(variant), "runtime executable"); err != nil {
			return err
		}
		if _, err := safePath(runtimePath.Value(variant), "runtime path"); err != nil {
			return err
		}
	}
	declared, err := stringList(runtime.Value("architectures"), "runtime architectures: "+identifier, true)
	if err != nil {
		return err
	}
	if !sameSet(declared, declaredArchitectures) {
		return fmt.Errorf("runtime architectures disagree with platforms: %s", identifier)
	}
	for _, field := range []string{"configuration", "personal_configuration", "logs", "demos"} {
		values, err := stringList(runtime.Value(field), "runtime "+field+": "+identifier, true)
		if err != nil {
			return err
		}
		for _, value := range values {
			if _, err := safePath(value, "runtime "+field); err != nil {
				return err
			}
		}
	}
	paths := map[string]bool{}
	for _, key := range runtimePath.Keys() {
		paths[pyjson.Repr(runtimePath.Value(key))] = true
	}
	personal, _ := pyjson.StrList(runtime.Value("personal_configuration"))
	for _, value := range personal {
		if paths[value] {
			return fmt.Errorf("personal configuration is inside immutable runtime payload: %s", identifier)
		}
	}
	arguments, ok := runtime.Value("arguments").(*Object)
	if !ok || arguments.Value("shell") != false || !arguments.Has("shell") {
		return fmt.Errorf("runtime arguments must be typed and shell-free: %s", identifier)
	}
	if _, ok := arguments.Value("base").([]any); !ok {
		return fmt.Errorf("runtime arguments must be typed and shell-free: %s", identifier)
	}
	_, environmentOK := runtime.Value("environment").(*Object)
	_, readinessOK := runtime.Value("readiness").(*Object)
	if !environmentOK || !readinessOK {
		return fmt.Errorf("runtime environment or readiness is invalid: %s", identifier)
	}
	if !pyjson.NonEmptyStr(runtime.Value("smoke_test")) {
		return fmt.Errorf("runtime lacks smoke test: %s", identifier)
	}
	return nil
}

func validateGame(identifier string, game *Object, protocols, capabilityEntries, runtimeEntries map[string]*Object,
	componentIDs map[string]bool, componentEntries map[string]*Object, haveComponents bool) error {
	if !hasAll(game, requiredGameFields) {
		return fmt.Errorf("game lacks required fields: %s", identifier)
	}
	if protocol, ok := game.Value("protocol").(string); !ok || protocols[protocol] == nil {
		return fmt.Errorf("game has invalid protocol: %s", identifier)
	}
	component, ok := game.Value("component").(string)
	if !ok || (len(componentIDs) > 0 && !componentIDs[component]) {
		return fmt.Errorf("game references missing component: %s", identifier)
	}
	for _, field := range []string{"gamedir", "profile"} {
		if text, ok := game.Value(field).(string); !ok || !inventoryID.MatchString(text) {
			return fmt.Errorf("game has invalid %s: %s", field, identifier)
		}
	}
	for _, field := range []string{"marker", "gamecode", "managed_config", "personal_config"} {
		if _, err := safePath(game.Value(field), "game "+field); err != nil {
			return err
		}
	}
	botNames := game.Value("bot_names_personal_config")
	if botNames != nil {
		if _, err := safePath(botNames, "game bot names personal config"); err != nil {
			return err
		}
	}
	if pyjson.Eq(game.Value("managed_config"), game.Value("personal_config")) {
		return fmt.Errorf("personal configuration overlaps managed payload: %s", identifier)
	}
	clients, err := stringList(game.Value("client_runtimes"), "game client runtimes: "+identifier, false)
	if err != nil {
		return err
	}
	servers, err := stringList(game.Value("server_runtimes"), "game server runtimes: "+identifier, false)
	if err != nil {
		return err
	}
	for _, item := range clients {
		if runtimeEntries[item] == nil || runtimeEntries[item].Value("kind") != "client" {
			return fmt.Errorf("game references invalid client runtime: %s", identifier)
		}
	}
	for _, item := range servers {
		if runtimeEntries[item] == nil || runtimeEntries[item].Value("kind") != "server" {
			return fmt.Errorf("game references invalid server runtime: %s", identifier)
		}
	}
	required, err := stringList(game.Value("required_capabilities"), "game capabilities: "+identifier, false)
	if err != nil {
		return err
	}
	for _, capability := range required {
		if capabilityEntries[capability] == nil {
			return fmt.Errorf("game references unknown capability: %s", identifier)
		}
	}
	if _, ok := game.Value("suggested_maps").([]any); !ok || !listContains(game.Value("suggested_maps"), game.Value("default_map")) {
		return fmt.Errorf("game map defaults are inconsistent: %s", identifier)
	}
	for _, field := range []string{"pre_map_arguments", "post_map_arguments"} {
		if _, ok := pyjson.StrList(game.Value(field)); !ok {
			return fmt.Errorf("game arguments are invalid: %s", identifier)
		}
	}
	for _, field := range []string{"client_game_arguments", "pre_connect_arguments", "client_compatibility_arguments", "dedicated_arguments"} {
		if _, ok := pyjson.StrList(game.Value(field)); !ok {
			return fmt.Errorf("game typed arguments are invalid: %s/%s", identifier, field)
		}
	}
	if support := game.Value("play_support_gamecode"); support != nil {
		if _, err := safePath(support, "play support gamecode"); err != nil {
			return err
		}
	}
	for _, field := range []string{"local_server_settings", "dedicated_settings"} {
		values, ok := game.Value(field).([]any)
		for _, item := range values {
			pair, isPair := pyjson.StrList(item)
			ok = ok && isPair && len(pair) == 2 && pair[0] != "" && pair[1] != ""
		}
		if !ok {
			return fmt.Errorf("game typed settings are invalid: %s/%s", identifier, field)
		}
	}
	if haveComponents {
		entry := componentEntries[component]
		var sources any = []any{}
		if entry != nil {
			sources = getOr(entry, "project_sources", []any{})
		}
		list, ok := sources.([]any)
		if !ok {
			return fmt.Errorf("game component sources are invalid: %s", identifier)
		}
		destinations := map[string]any{}
		for _, raw := range list {
			if source, ok := raw.(*Object); ok {
				destinations[pyjson.HashKey(source.Value("destination"))] = source.Value("mode")
			}
		}
		if destinations[pyjson.HashKey(game.Value("personal_config"))] != "default" {
			return fmt.Errorf("personal game config is not a preserved default: %s", identifier)
		}
		if botNames != nil && destinations[pyjson.HashKey(botNames)] != "default" {
			return fmt.Errorf("personal bot names config is not a preserved default: %s", identifier)
		}
	}
	return nil
}
