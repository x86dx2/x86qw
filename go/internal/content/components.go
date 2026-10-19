package content

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"regexp"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Development component catalog (maintenance/tools/components.py).

var (
	componentID      = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)
	componentKinds   = setOf("core", "gameplay", "content", "addon", "documentation", "runtime", "service")
	sourceModes      = setOf("overlay", "default", "preserve", "archive", "archive-base")
	sourceOrigins    = setOf("reference", "release")
	servicePlatforms = setOf("macos-arm64", "linux-amd64", "windows-x64")
	profileNames     = []string{"essential", "recommended", "complete"}
)

// ProfileFingerprint mirrors profile_fingerprint.
func ProfileFingerprint(selected []string) string {
	sorted := append([]string(nil), selected...)
	sort.Strings(sorted)
	digest := sha256.New()
	for _, identifier := range sorted {
		digest.Write([]byte(identifier + "\n"))
	}
	return hex.EncodeToString(digest.Sum(nil))
}

// Components mirrors components.load_catalog on maintenance/inventory/components.json.
func (p *Project) Components() (*Object, error) {
	if !p.componentsDone {
		p.componentsDone = true
		p.components, p.componentsErr = LoadComponentCatalog(p.Path(ComponentsPath))
	}
	return p.components, p.componentsErr
}

// LoadComponentCatalog mirrors components.load_catalog.
func LoadComponentCatalog(path string) (*Object, error) {
	value, err := ReadJSON(path)
	if err != nil {
		return nil, fmt.Errorf("cannot read component catalog: %s", path)
	}
	if err := ValidateComponentCatalog(value); err != nil {
		return nil, err
	}
	return value.(*Object), nil
}

func objects(value any) []*Object {
	items, _ := value.([]any)
	result := make([]*Object, 0, len(items))
	for _, item := range items {
		if object, ok := item.(*Object); ok {
			result = append(result, object)
		}
	}
	return result
}

// listOrDefault mirrors component.get(key, []).
func listOrDefault(object *Object, key string) (any, bool) {
	if !object.Has(key) {
		return []any{}, true
	}
	value := object.Value(key)
	_, ok := value.([]any)
	return value, ok
}

func hasZipSuffix(value string) bool {
	folded := Casefold(value)
	return strings.HasSuffix(folded, ".pk3") || strings.HasSuffix(folded, ".zip")
}

// ValidateComponentCatalog mirrors components.validate_catalog.
func ValidateComponentCatalog(value any) error {
	catalog, ok := value.(*Object)
	if !ok || !pyjson.EqInt(catalog.Value("format"), 1) || !pyjson.Eq(catalog.Value("project"), "x86qw") {
		return fmt.Errorf("invalid component catalog identity")
	}
	if validateCatalogVersions(catalog, false) != nil {
		return fmt.Errorf("invalid component catalog contract")
	}
	client, ok := catalog.Value("client").(*Object)
	if !ok || !pyjson.Eq(client.Value("id"), "ezquake") || !pyjson.Eq(client.Value("channels"), []any{"stable", "nightly"}) {
		return fmt.Errorf("the active x86QW client must be ezQuake stable and nightly")
	}
	if !validNamespaces(catalog.Value("content_namespaces")) {
		return fmt.Errorf("invalid content component namespaces")
	}
	source, ok := catalog.Value("reference_source").(*Object)
	if !ok || !pyjson.IsStr(source.Value("repository")) || !pyjson.IsStr(source.Value("ref")) {
		return fmt.Errorf("invalid nQuake reference source")
	}
	components, ok := catalog.Value("components").([]any)
	if !ok || len(components) == 0 {
		return fmt.Errorf("component catalog is empty")
	}

	identifiers := map[string]bool{}
	var order []string
	dependencies := map[string][]string{}
	projectDestinations := map[string]bool{}
	projectInputs := map[string]bool{}
	for _, item := range components {
		component, ok := item.(*Object)
		if !ok {
			return fmt.Errorf("invalid component entry")
		}
		identifier, ok := component.Value("id").(string)
		if !ok || !componentID.MatchString(identifier) || identifiers[identifier] {
			return fmt.Errorf("invalid or duplicate component id: %s", reprScalar(component.Value("id")))
		}
		identifiers[identifier] = true
		order = append(order, identifier)
		if !inSet(component.Value("kind"), componentKinds) {
			return fmt.Errorf("invalid component kind: %s", identifier)
		}
		if !pyjson.NonEmptyStr(component.Value("label")) || !pyjson.NonEmptyStr(component.Value("description")) {
			return fmt.Errorf("component lacks user-facing metadata: %s", identifier)
		}
		requires, ok := pyjson.StrList(component.Value("requires"))
		if !ok {
			return fmt.Errorf("invalid dependencies: %s", identifier)
		}
		dependencies[identifier] = requires
		sources, ok := component.Value("sources").([]any)
		if !ok || len(sources) == 0 {
			return fmt.Errorf("component has no sources: %s", identifier)
		}
		for _, raw := range sources {
			entry, ok := raw.(*Object)
			if !ok {
				return fmt.Errorf("invalid source entry: %s", identifier)
			}
			sourcePath, err := ValidatePortableRelativePath(entry.Value("path"), "source path")
			if err != nil {
				return err
			}
			if _, err := ValidatePortableRelativePath(entry.Value("destination"), "destination path"); err != nil {
				return err
			}
			mode := entry.Value("mode")
			origin := any("reference")
			if entry.Has("origin") {
				origin = entry.Value("origin")
			}
			if !inSet(mode, sourceModes) {
				return fmt.Errorf("invalid install mode in %s: %s", identifier, sourcePath)
			}
			if !inSet(origin, sourceOrigins) {
				return fmt.Errorf("invalid source origin in %s: %s", identifier, sourcePath)
			}
			destination := entry.Value("destination").(string)
			if mode == "archive" && (origin != "release" || !hasZipSuffix(destination)) {
				return fmt.Errorf("archive source must build a release ZIP/PK3 in %s: %s", identifier, sourcePath)
			}
			if mode == "archive-base" && (origin != "reference" || !hasZipSuffix(sourcePath) || !hasZipSuffix(destination)) {
				return fmt.Errorf("archive base must use a reference ZIP/PK3 in %s: %s", identifier, sourcePath)
			}
			exclusions, ok := listOrDefault(entry, "exclude")
			if !ok {
				return fmt.Errorf("invalid exclusions in %s: %s", identifier, sourcePath)
			}
			for _, excluded := range exclusions.([]any) {
				item, err := ValidatePortableRelativePath(excluded, "excluded path")
				if err != nil {
					return err
				}
				if item != sourcePath && !strings.HasPrefix(item, sourcePath+"/") {
					return fmt.Errorf("exclusion is outside its source in %s: %s", identifier, sourcePath)
				}
			}
		}
		projectSources, ok := listOrDefault(component, "project_sources")
		if !ok {
			return fmt.Errorf("invalid project sources: %s", identifier)
		}
		for _, raw := range projectSources.([]any) {
			entry, ok := raw.(*Object)
			if !ok {
				return fmt.Errorf("invalid project source entry: %s", identifier)
			}
			sourcePath, err := ValidatePortableRelativePath(entry.Value("path"), "project source path")
			if err != nil {
				return err
			}
			destination, err := ValidatePortableRelativePath(entry.Value("destination"), "project destination path")
			if err != nil {
				return err
			}
			if !strings.HasPrefix(sourcePath, "dist/") {
				return fmt.Errorf("project source is outside the distribution: %s", sourcePath)
			}
			if !inSet(entry.Value("mode"), setOf("overlay", "default")) {
				return fmt.Errorf("invalid project install mode in %s: %s", identifier, sourcePath)
			}
			platform := entry.Value("platform")
			install := entry.Value("install_destination")
			if (platform == nil) != (install == nil) {
				return fmt.Errorf("incomplete project platform mapping in %s: %s", identifier, sourcePath)
			}
			if platform != nil {
				if !inSet(platform, servicePlatforms) || entry.Value("mode") != "overlay" {
					return fmt.Errorf("invalid project platform mapping in %s: %s", identifier, sourcePath)
				}
				installPath, err := ValidatePortableRelativePath(install, "project install destination")
				if err != nil {
					return err
				}
				if !strings.HasPrefix(destination, "platforms/"+identifier+"/"+platform.(string)+"/") {
					return fmt.Errorf("invalid project package destination in %s: %s", identifier, destination)
				}
				if strings.HasPrefix(installPath, "platforms/") {
					return fmt.Errorf("platform staging path cannot be installed directly: %s", installPath)
				}
			}
			folded := Casefold(destination)
			if projectDestinations[folded] {
				return fmt.Errorf("duplicate project destination: %s", destination)
			}
			projectDestinations[folded] = true
		}
		inputs, ok := listOrDefault(component, "project_inputs")
		if !ok {
			return fmt.Errorf("invalid project inputs: %s", identifier)
		}
		for _, raw := range inputs.([]any) {
			entry, ok := raw.(*Object)
			if !ok {
				return fmt.Errorf("invalid project input: %s", identifier)
			}
			sourcePath, err := ValidatePortableRelativePath(entry.Value("path"), "project input path")
			if err != nil {
				return err
			}
			if !strings.HasPrefix(sourcePath, "dist/") || projectInputs[sourcePath] {
				return fmt.Errorf("invalid or duplicate project input: %s", sourcePath)
			}
			if !pyjson.NonEmptyStr(entry.Value("purpose")) {
				return fmt.Errorf("project input has no purpose: %s", sourcePath)
			}
			projectInputs[sourcePath] = true
		}
		overrides, ok := listOrDefault(component, "project_overrides")
		if !ok {
			return fmt.Errorf("invalid project overrides: %s", identifier)
		}
		overrideTargets := map[string]bool{}
		for _, raw := range overrides.([]any) {
			override, ok := raw.(*Object)
			if !ok {
				return fmt.Errorf("invalid project override: %s", identifier)
			}
			sourcePath, err := ValidatePortableRelativePath(override.Value("path"), "project override path")
			if err != nil {
				return err
			}
			target, err := ValidatePortableRelativePath(override.Value("target"), "project override target")
			if err != nil {
				return err
			}
			if !strings.HasPrefix(sourcePath, "dist/") {
				return fmt.Errorf("project override is outside the distribution: %s", sourcePath)
			}
			if overrideTargets[Casefold(target)] {
				return fmt.Errorf("duplicate project override target: %s", target)
			}
			overrideTargets[Casefold(target)] = true
			var matches []*Object
			for _, entry := range objects(sources) {
				root := entry.Value("path").(string)
				if target == root || strings.HasPrefix(target, root+"/") {
					matches = append(matches, entry)
				}
			}
			if len(matches) != 1 || matches[0].Value("mode") == "preserve" {
				return fmt.Errorf("project override has no runtime target in %s: %s", identifier, target)
			}
		}
		archiveOverrides, ok := listOrDefault(component, "project_archive_overrides")
		if !ok {
			return fmt.Errorf("invalid project archive overrides: %s", identifier)
		}
		archiveTargets := map[[2]string]bool{}
		for _, raw := range archiveOverrides.([]any) {
			override, ok := raw.(*Object)
			if !ok {
				return fmt.Errorf("invalid project archive override: %s", identifier)
			}
			sourcePath, err := ValidatePortableRelativePath(override.Value("path"), "project archive override path")
			if err != nil {
				return err
			}
			targetArchive, err := ValidatePortableRelativePath(override.Value("target_archive"), "project archive override target")
			if err != nil {
				return err
			}
			member, err := ValidatePortableRelativePath(override.Value("member"), "project archive override member")
			if err != nil {
				return err
			}
			if !strings.HasPrefix(sourcePath, "dist/") {
				return fmt.Errorf("project archive override is outside the distribution: %s", sourcePath)
			}
			identity := [2]string{Casefold(targetArchive), Casefold(member)}
			if archiveTargets[identity] {
				return fmt.Errorf("duplicate project archive override: %s:%s", targetArchive, member)
			}
			archiveTargets[identity] = true
			found := false
			for _, entry := range objects(sources) {
				if entry.Value("destination") == targetArchive && inSet(entry.Value("mode"), setOf("archive", "archive-base")) {
					found = true
				}
			}
			if !found {
				return fmt.Errorf("project archive override has no archive target in %s: %s", identifier, targetArchive)
			}
		}
	}

	for _, identifier := range order {
		var missing []string
		for _, dependency := range dependencies[identifier] {
			if !identifiers[dependency] {
				missing = append(missing, dependency)
			}
		}
		if len(missing) > 0 || contains(dependencies[identifier], identifier) {
			if len(missing) == 0 {
				missing = []string{identifier}
			}
			sort.Strings(missing)
			return fmt.Errorf("invalid dependency for %s: %s", identifier, missing[0])
		}
	}
	if err := validateDependencyGraph(order, dependencies, "cyclic component dependency: %s"); err != nil {
		return err
	}

	compatibility, ok := catalog.Value("compatibility").(*Object)
	if !ok || !pyjson.Eq(compatibility.Value("policy"), "common-baseline") {
		return fmt.Errorf("component catalog must declare the common stable/nightly baseline")
	}
	if !pyjson.Eq(compatibility.Value("scope"), "ezquake-client-content") {
		return fmt.Errorf("component compatibility must be scoped to ezQuake client content")
	}
	clients, ok := compatibility.Value("clients").(*Object)
	valid := ok && sameKeys(clients.Keys(), "stable", "nightly")
	if valid {
		for _, key := range clients.Keys() {
			valid = valid && pyjson.NonEmptyStr(clients.Value(key))
		}
	}
	if !valid {
		return fmt.Errorf("invalid stable/nightly compatibility versions")
	}
	clientContent := map[string]bool{}
	for _, component := range objects(components) {
		if !inSet(component.Value("kind"), setOf("runtime", "service")) {
			clientContent[component.Value("id").(string)] = true
		}
	}
	covered, ok := pyjson.StrList(compatibility.Value("covered_components"))
	if !ok || !uniqueStrings(covered) || !sameSet(covered, clientContent) {
		return fmt.Errorf("stable/nightly compatibility must cover every client content component")
	}
	verified, ok := pyjson.StrList(compatibility.Value("verified"))
	if !ok || len(verified) == 0 || contains(verified, "") {
		return fmt.Errorf("invalid stable/nightly verification scope")
	}

	profiles, err := validateProfiles(catalog, identifiers, profileMessages{
		profiles: "catalog must define essential, recommended and complete profiles",
		invalid:  "invalid profile: %s",
		omits:    "profile omits a dependency: %s",
		complete: "complete profile must contain every x86QW component",
		history:  "catalog must preserve profile history",
	})
	if err != nil {
		return err
	}
	history := catalog.Value("profile_history").(*Object)
	claimed := map[string]string{}
	for _, name := range history.Keys() {
		fingerprints, ok := fingerprintList(history.Value(name))
		if !ok {
			return fmt.Errorf("invalid profile history: %s", name)
		}
		if !contains(fingerprints, ProfileFingerprint(profiles[name])) {
			return fmt.Errorf("profile history omits current definition: %s", name)
		}
		for _, fingerprint := range fingerprints {
			previous, seen := claimed[fingerprint]
			if !seen {
				claimed[fingerprint] = name
			} else if previous != name {
				return fmt.Errorf("profile history is ambiguous: %s and %s", previous, name)
			}
		}
	}
	return nil
}

type profileMessages struct{ profiles, invalid, omits, complete, history string }

// validateProfiles checks the profiles block shared by both catalog shapes
// and returns the profile selections.
func validateProfiles(catalog *Object, identifiers map[string]bool, messages profileMessages) (map[string][]string, error) {
	profiles, ok := catalog.Value("profiles").(*Object)
	if !ok || !sameKeys(profiles.Keys(), profileNames...) {
		return nil, fmt.Errorf("%s", messages.profiles)
	}
	result := map[string][]string{}
	for _, name := range profiles.Keys() {
		raw, isList := profiles.Value(name).([]any)
		unique, hashable := pyjson.UniqueHashable(raw)
		if !isList || !hashable || !unique {
			return nil, fmt.Errorf(messages.invalid, name)
		}
		selected, ok := pyjson.StrList(raw)
		if !ok {
			return nil, fmt.Errorf(messages.invalid, name)
		}
		for _, identifier := range selected {
			if !identifiers[identifier] {
				return nil, fmt.Errorf(messages.invalid, name)
			}
		}
		resolved, err := ResolveDependencies(catalog, selected)
		if err != nil {
			return nil, err
		}
		if !sameSet(resolved, setOf(selected...)) {
			return nil, fmt.Errorf(messages.omits, name)
		}
		result[name] = selected
	}
	if !sameSet(result["complete"], identifiers) {
		return nil, fmt.Errorf("%s", messages.complete)
	}
	history, ok := catalog.Value("profile_history").(*Object)
	if !ok || !sameKeys(history.Keys(), profiles.Keys()...) {
		return nil, fmt.Errorf("%s", messages.history)
	}
	return result, nil
}

var hex64Pattern = regexp.MustCompile(`^[0-9a-f]{64}$`)

func fingerprintList(value any) ([]string, bool) {
	raw, ok := value.([]any)
	if !ok || len(raw) == 0 {
		return nil, false
	}
	if unique, hashable := pyjson.UniqueHashable(raw); !unique || !hashable {
		return nil, false
	}
	values, ok := pyjson.StrList(raw)
	if !ok {
		return nil, false
	}
	for _, value := range values {
		if !hex64Pattern.MatchString(value) {
			return nil, false
		}
	}
	return values, true
}

func validNamespaces(value any) bool {
	raw, ok := value.([]any)
	if !ok || len(raw) == 0 {
		return false
	}
	if unique, hashable := pyjson.UniqueHashable(raw); !unique || !hashable {
		return false
	}
	values, ok := pyjson.StrList(raw)
	if !ok {
		return false
	}
	for _, item := range values {
		if !componentID.MatchString(item) {
			return false
		}
	}
	return true
}

func sameKeys(keys []string, expected ...string) bool {
	return sameSet(keys, setOf(expected...))
}

func sameSet(values []string, expected map[string]bool) bool {
	seen := setOf(values...)
	if len(seen) != len(expected) {
		return false
	}
	for value := range seen {
		if !expected[value] {
			return false
		}
	}
	return true
}

func uniqueStrings(values []string) bool { return len(setOf(values...)) == len(values) }

func validateDependencyGraph(order []string, dependencies map[string][]string, message string) error {
	visiting, visited := map[string]bool{}, map[string]bool{}
	var visit func(string) error
	visit = func(identifier string) error {
		if visiting[identifier] {
			return fmt.Errorf(message, identifier)
		}
		if visited[identifier] {
			return nil
		}
		visiting[identifier] = true
		for _, dependency := range dependencies[identifier] {
			if err := visit(dependency); err != nil {
				return err
			}
		}
		delete(visiting, identifier)
		visited[identifier] = true
		return nil
	}
	for _, identifier := range order {
		if err := visit(identifier); err != nil {
			return err
		}
	}
	return nil
}

// ComponentsByID mirrors components_by_id.
func ComponentsByID(catalog *Object) map[string]*Object {
	result := map[string]*Object{}
	for _, component := range objects(catalog.Value("components")) {
		if identifier, ok := component.Value("id").(string); ok {
			result[identifier] = component
		}
	}
	return result
}

// ComponentIDs returns the component ids in catalog order.
func ComponentIDs(catalog *Object) []string {
	var result []string
	for _, component := range objects(catalog.Value("components")) {
		if identifier, ok := component.Value("id").(string); ok {
			result = append(result, identifier)
		}
	}
	return result
}

// ResolveDependencies mirrors resolve_dependencies.
func ResolveDependencies(catalog *Object, selected []string) ([]string, error) {
	components := ComponentsByID(catalog)
	var resolved []string
	seen := map[string]bool{}
	var add func(string) error
	add = func(identifier string) error {
		component, ok := components[identifier]
		if !ok {
			return fmt.Errorf("unknown component: %s", identifier)
		}
		if seen[identifier] {
			return nil
		}
		requires, _ := pyjson.StrList(component.Value("requires"))
		for _, dependency := range requires {
			if err := add(dependency); err != nil {
				return err
			}
		}
		seen[identifier] = true
		resolved = append(resolved, identifier)
		return nil
	}
	for _, identifier := range selected {
		if err := add(identifier); err != nil {
			return nil, err
		}
	}
	return resolved, nil
}

func sourceOrigin(entry *Object) any {
	if entry.Has("origin") {
		return entry.Value("origin")
	}
	return "reference"
}

func excluded(path string, exclusions any) bool {
	items, _ := exclusions.([]any)
	for _, item := range items {
		text := pyjson.Repr(item)
		if path == text || strings.HasPrefix(path, text+"/") {
			return true
		}
	}
	return false
}

// SourceRoots mirrors source_roots.
func SourceRoots(catalog *Object, origin string) []string {
	roots := map[string]bool{}
	for _, component := range objects(catalog.Value("components")) {
		for _, entry := range objects(component.Value("sources")) {
			if sourceOrigin(entry) == origin {
				roots[entry.Value("path").(string)] = true
			}
		}
	}
	result := make([]string, 0, len(roots))
	for root := range roots {
		result = append(result, root)
	}
	sort.Strings(result)
	return result
}

// ComponentForSource mirrors component_for_source (origin "" = any).
func ComponentForSource(catalog *Object, path, origin string) (string, error) {
	unique := map[string]bool{}
	var first string
	for _, component := range objects(catalog.Value("components")) {
		for _, entry := range objects(component.Value("sources")) {
			if origin != "" && sourceOrigin(entry) != origin {
				continue
			}
			root := entry.Value("path").(string)
			if path != root && !strings.HasPrefix(path, root+"/") {
				continue
			}
			if excluded(path, entry.Value("exclude")) {
				continue
			}
			identifier := component.Value("id").(string)
			if len(unique) == 0 {
				first = identifier
			}
			unique[identifier] = true
		}
	}
	if len(unique) > 1 {
		return "", fmt.Errorf("source belongs to multiple components: %s", path)
	}
	return first, nil
}

// DestinationForSource mirrors destination_for_source.
func DestinationForSource(component *Object, path, origin string) (string, string, error) {
	var matches [][2]string
	for _, entry := range objects(component.Value("sources")) {
		if origin != "" && sourceOrigin(entry) != origin {
			continue
		}
		root := entry.Value("path").(string)
		if path != root && !strings.HasPrefix(path, root+"/") {
			continue
		}
		if excluded(path, entry.Value("exclude")) {
			continue
		}
		suffix := strings.TrimLeft(strings.TrimPrefix(path, root), "/")
		destination := entry.Value("destination").(string)
		mode := entry.Value("mode").(string)
		if mode == "archive" || mode == "archive-base" || suffix == "" {
			matches = append(matches, [2]string{destination, mode})
		} else {
			matches = append(matches, [2]string{destination + "/" + suffix, mode})
		}
	}
	if len(matches) != 1 {
		return "", "", fmt.Errorf("source has no unique destination: %s", path)
	}
	return matches[0][0], matches[0][1], nil
}

// ValidateTreePartition mirrors validate_tree_partition.
func ValidateTreePartition(catalog *Object, paths []string, origin string) (map[string][]string, error) {
	components := ComponentsByID(catalog)
	partition := map[string][]string{}
	var selected []string
	for _, identifier := range ComponentIDs(catalog) {
		for _, entry := range objects(components[identifier].Value("sources")) {
			if sourceOrigin(entry) == origin {
				if _, ok := partition[identifier]; !ok {
					selected = append(selected, identifier)
				}
				partition[identifier] = []string{}
			}
		}
	}
	destinations := map[string][2]string{}
	for _, path := range paths {
		identifier, err := ComponentForSource(catalog, path, origin)
		if err != nil {
			return nil, err
		}
		if identifier == "" {
			return nil, fmt.Errorf("reference source is not assigned to a component: %s", path)
		}
		destination, _, err := DestinationForSource(components[identifier], path, origin)
		if err != nil {
			return nil, err
		}
		folded := Casefold(destination)
		if previous, ok := destinations[folded]; ok && previous != [2]string{identifier, path} {
			return nil, fmt.Errorf("component destination collision: %s", destination)
		}
		destinations[folded] = [2]string{identifier, path}
		partition[identifier] = append(partition[identifier], path)
	}
	for _, identifier := range selected {
		if len(partition[identifier]) == 0 {
			return nil, fmt.Errorf("component selects no files: %s", identifier)
		}
	}
	return partition, nil
}
