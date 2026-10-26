package content

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/archive"
	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
	"github.com/x86dx2/x86qw/go/internal/fspath"
	"golang.org/x/text/unicode/norm"
)

// x86qw-content add: reviewed distribution-change definitions.

var (
	safeConsumer = regexp.MustCompile(`^[a-z][a-z0-9-]*:[a-z0-9][a-z0-9_.+-]*$`)
)

// safeRelative checks a definition's relative path, optionally under
// prefixDir.
func safeRelative(value any, prefixDir string) (string, error) {
	portable, err := ValidatePortableRelativePath(value, "path")
	if err != nil {
		return "", &ManagerError{err.Error()}
	}
	parts := strings.Split(portable, "/")
	if len(parts) < 2 {
		return "", managerError("unsafe path: %s", pyjson.Repr(value))
	}
	if parts[0] != prefixDir {
		return "", managerError("the path must stay under %s/: %s", prefixDir, pyjson.Repr(value))
	}
	return portable, nil
}

func semanticIdentity(value string) string { return Casefold(norm.NFC.String(value)) }

// validateDestinationTree refuses a destination that differs only in case or
// Unicode form from a path already in dist.
func (p *Project) validateDestinationTree(destination string) error {
	relative := strings.Split(strings.TrimPrefix(destination, "dist/"), "/")
	existing := map[string]string{}
	dist := p.Path(DistDir)
	filepath.WalkDir(dist, func(path string, entry os.DirEntry, err error) error {
		if err != nil || path == dist {
			return nil
		}
		name, _ := filepath.Rel(dist, path)
		name = filepath.ToSlash(name)
		if _, ok := existing[semanticIdentity(name)]; !ok {
			existing[semanticIdentity(name)] = name
		}
		return nil
	})
	for depth := 1; depth <= len(relative); depth++ {
		candidate := strings.Join(relative[:depth], "/")
		if known, ok := existing[semanticIdentity(candidate)]; ok && known != candidate {
			return managerError("destination collides with an existing path by case or Unicode: %s", candidate)
		}
		target := joinPosix(dist, candidate)
		if isSymlink(target) {
			return managerError("destination crosses an existing symlink: %s", candidate)
		}
		if exists(target) {
			if depth < len(relative) && !isDir(target) {
				return managerError("destination parent is not a directory: %s", candidate)
			}
			if depth == len(relative) && !isFile(target) {
				return managerError("existing destination is not a regular file: %s", candidate)
			}
		}
	}
	return nil
}

// validateLocalSourcePath resolves a local source path, which must stay
// under base.
func validateLocalSourcePath(base string, value any) (string, error) {
	portable, err := ValidatePortableRelativePath(value, "local source")
	if err != nil {
		return "", &ManagerError{err.Error()}
	}
	root, err := filepath.EvalSymlinks(base)
	if err != nil {
		return "", err
	}
	candidate := root
	for _, part := range strings.Split(portable, "/") {
		candidate = filepath.Join(candidate, part)
		if isSymlink(candidate) {
			return "", managerError("local source must be a regular file without symlinks")
		}
	}
	if !isFile(candidate) {
		return "", managerError("local source must be a regular file without symlinks")
	}
	if relative, err := filepath.Rel(root, fspath.Resolve(candidate)); err != nil || strings.HasPrefix(relative, "..") {
		return "", managerError("local source escapes the definition's directory")
	}
	return candidate, nil
}

type filePlan struct {
	entry       *Object
	destination string
	source      string
	url         string
	origin      string
	size        int64
	sha256      string
	mode        os.FileMode
}

// validateDefinitionFile checks one file entry of a definition and plans
// it.
func validateDefinitionFile(raw any, base string) (*filePlan, error) {
	entry, ok := raw.(*Object)
	if !ok {
		return nil, managerError("invalid file entry")
	}
	destination, err := safeRelative(entry.Value("destination"), "dist")
	if err != nil {
		return nil, err
	}
	source, url := entry.Value("source"), entry.Value("url")
	if (source == nil) == (url == nil) {
		return nil, managerError("each definition file must have exactly one of source or url")
	}
	plan := &filePlan{entry: entry, destination: destination}
	var parsedURL splitURL
	if source != nil {
		origin, err := validateLocalSourcePath(base, source)
		if err != nil {
			return nil, err
		}
		expectedSize, expectedSHA := entry.Value("size"), entry.Value("sha256")
		if expectedSize != nil && !sizeWithin(expectedSize, true) {
			return nil, managerError("invalid local size in the definition")
		}
		if text, ok := expectedSHA.(string); expectedSHA != nil && (!ok || !sha256Pattern.MatchString(text)) {
			return nil, managerError("invalid local sha256 in the definition")
		}
		info, err := os.Stat(origin)
		if err != nil {
			return nil, err
		}
		if info.Size() <= 0 || info.Size() > MaxArtifactBytes {
			return nil, managerError("a local file must be non-empty and within the global limit")
		}
		if expectedSize != nil && !pyjson.EqInt(expectedSize, info.Size()) {
			return nil, managerError("local size does not match the definition file")
		}
		digest, err := archive.FileSHA256(origin)
		if err != nil {
			return nil, err
		}
		if expectedSHA != nil && expectedSHA != digest {
			return nil, managerError("local sha256 does not match the definition file")
		}
		plan.source, plan.origin, plan.size, plan.sha256 = origin, "validated local file", info.Size(), digest
		plan.mode = 0o644
		if info.Mode()&0o111 != 0 {
			plan.mode = 0o755
		}
	} else {
		text, ok := url.(string)
		if !ok {
			return nil, managerError("invalid file URL")
		}
		if parsedURL, err = validateHTTPSURL(text, "definition file URL"); err != nil {
			return nil, &ManagerError{err.Error()}
		}
		size, digest := entry.Value("size"), entry.Value("sha256")
		digestText, digestOK := digest.(string)
		if !sizeWithin(size, true) || !digestOK || !sha256Pattern.MatchString(digestText) {
			return nil, managerError("a remote definition file requires a valid size and sha256")
		}
		plan.url, plan.origin, plan.size, plan.sha256 = text, safeURLForLog(text), int64Of(size), digestText
	}
	managed := getOr(entry, "managed", false)
	if _, ok := managed.(bool); !ok {
		return nil, managerError("managed must be a boolean")
	}
	if managed == true {
		if source != nil {
			return nil, managerError("a managed upstream file must use a pinned HTTPS URL")
		}
		if parsedURL.Query != "" {
			return nil, managerError("a managed file's persistent URL cannot have a query")
		}
		component, componentOK := entry.Value("distribution_component").(string)
		consumer, consumerOK := entry.Value("consumer").(string)
		if !componentOK || !componentID.MatchString(component) || !consumerOK || !safeConsumer.MatchString(consumer) {
			return nil, managerError("a managed upstream file requires a safe distribution_component and consumer")
		}
		if pkg := entry.Value("package"); pkg != nil {
			if text, ok := pkg.(string); !ok || !safeSegment.MatchString(text) {
				return nil, managerError("the managed file's package must be a safe identifier")
			}
		}
	}
	return plan, nil
}

// referenceTransition moves some components to another nQuake revision;
// previous maps each of them to the revision it leaves.
type referenceTransition struct {
	revision string
	previous map[string]string
}

// applyReferenceTransition moves exactly the components the definition
// names to its nQuake revision; every other component keeps its own.
func applyReferenceTransition(releases *Object, proposed any) (referenceTransition, bool, error) {
	if proposed == nil {
		return referenceTransition{}, false, nil
	}
	transition, ok := proposed.(*Object)
	if !ok || !sameKeys(transition.Keys(), "repository", "revision", "components") {
		return referenceTransition{}, false, managerError("reference must declare only repository, revision and components")
	}
	current, ok := releases.Value("reference").(*Object)
	if !ok {
		return referenceTransition{}, false, managerError("the release inventory has no nQuake reference")
	}
	repository, repositoryOK := transition.Value("repository").(string)
	revision, revisionOK := transition.Value("revision").(string)
	moving, movingOK := transition.Value("components").(*Object)
	if !repositoryOK || !revisionOK || !hex40Pattern.MatchString(revision) || !movingOK || len(moving.Keys()) == 0 {
		return referenceTransition{}, false, managerError("the nQuake reference transition has invalid fields")
	}
	parsed, err := validateHTTPSURL(repository, "nQuake reference repository")
	if err != nil {
		return referenceTransition{}, false, managerError("invalid nQuake reference repository")
	}
	if parsed.Query != "" {
		return referenceTransition{}, false, managerError("the nQuake reference repository cannot have a query")
	}
	if !pyjson.Eq(repository, current.Value("repository")) {
		return referenceTransition{}, false, managerError("the nQuake reference transition cannot change the repository")
	}
	result := referenceTransition{revision, map[string]string{}}
	for _, identifier := range moving.Keys() {
		previous, ok := moving.Value(identifier).(string)
		if !ok || !hex40Pattern.MatchString(previous) {
			return referenceTransition{}, false, managerError("invalid previous revision for %s", identifier)
		}
		pinned := ReferenceRevisionOf(releases, identifier)
		if pinned == "" {
			return referenceTransition{}, false, managerError("%s does not consume the nQuake reference", identifier)
		}
		if previous != pinned {
			return referenceTransition{}, false, managerError("previous revision of %s differs from the current inventory (%s)", identifier, pinned[:12])
		}
		if revision == previous {
			return referenceTransition{}, false, managerError("the nQuake reference transition must change the revision of %s", identifier)
		}
		MoveReferenceRelease(pyjson.Get(releases, "components", identifier).(*Object), revision)
		result.previous[identifier] = previous
	}
	releases.Set("checked_at", utcNow())
	return result, true, nil
}

// referenceAssetURL is the raw URL of a file of the reference repository
// at revision.
func referenceAssetURL(repository, revision, upstreamPath string) (string, error) {
	parsed, err := validateHTTPSURL(repository, "nQuake reference repository")
	if err != nil {
		return "", managerError("invalid nQuake reference repository")
	}
	parts := strings.Split(strings.Trim(parsed.Path, "/"), "/")
	_, _, _, port, hasPort := parsed.hostinfo()
	if Casefold(parsed.Hostname()) != "github.com" || (hasPort && port != "443") || parsed.Query != "" ||
		len(parts) != 2 || parts[0] == "" || parts[1] == "" {
		return "", managerError("the nQuake reference repository is not a canonical GitHub repository")
	}
	return "https://raw.githubusercontent.com/" + parts[0] + "/" + parts[1] + "/" + revision + "/" + quotePath(upstreamPath), nil
}

// upsertComponent adds a component, or replaces it when replace is set,
// keeping the list sorted by id.
func upsertComponent(catalog *Object, component *Object, replace bool) error {
	entries, ok := catalog.Value("components").([]any)
	identifier, idOK := component.Value("id").(string)
	if !ok || !idOK {
		return managerError("invalid definition component")
	}
	index := -1
	for position, item := range objects(entries) {
		if item.Value("id") == identifier && index < 0 {
			index = position
		}
	}
	if index >= 0 && !replace {
		return managerError("component %s already exists; declare replace: true to update it", identifier)
	}
	if index >= 0 {
		for position, raw := range entries {
			if item, ok := raw.(*Object); ok && item.Value("id") == identifier {
				entries[position] = component
				break
			}
		}
	} else {
		entries = append(entries, component)
	}
	sort.SliceStable(entries, func(i, j int) bool {
		return pyjson.Repr(getOr(entries[i].(*Object), "id", "")) < pyjson.Repr(getOr(entries[j].(*Object), "id", ""))
	})
	catalog.Set("components", entries)
	return nil
}

// preserveProfileFingerprints appends each profile's current fingerprint
// to its history when it is not there yet.
func preserveProfileFingerprints(catalog *Object) error {
	profiles, profilesOK := catalog.Value("profiles").(*Object)
	history, historyOK := catalog.Value("profile_history").(*Object)
	if !profilesOK || !historyOK || !sameKeys(profiles.Keys(), history.Keys()...) {
		return managerError("invalid component profile history")
	}
	for _, name := range profiles.Keys() {
		selected, selectedOK := pyjson.StrList(profiles.Value(name))
		fingerprints, fingerprintsOK := history.Value(name).([]any)
		if !selectedOK || !fingerprintsOK {
			return managerError("invalid history for profile %s", name)
		}
		fingerprint := ProfileFingerprint(selected)
		if !listContains(fingerprints, fingerprint) {
			history.Set(name, append(fingerprints, fingerprint))
		}
	}
	return nil
}

func addToProfiles(staged *Object, identifier string, profiles []string) error {
	profileMap, ok := staged.Value("profiles").(*Object)
	if !ok {
		return managerError("invalid component profiles")
	}
	for _, profile := range profiles {
		members, ok := profileMap.Value(profile).([]any)
		if !ok {
			return managerError("unknown profile: %s", profile)
		}
		if !listContains(members, identifier) {
			profileMap.Set(profile, append(members, identifier))
		}
	}
	return nil
}

// validateDistributionChange checks a whole definition and plans its files.
func (p *Project) validateDistributionChange(definition *Object, definitionPath string) ([]*filePlan, error) {
	if !pyjson.EqInt(definition.Value("format"), 1) || !pyjson.Eq(definition.Value("project"), "x86qw") || !pyjson.Eq(definition.Value("kind"), "distribution-change") {
		return nil, managerError("the definition must be x86qw/distribution-change format 1")
	}
	files, ok := getOr(definition, "files", []any{}).([]any)
	if !ok || len(files) == 0 {
		return nil, managerError("the definition must incorporate at least one directly used file")
	}
	var plans []*filePlan
	for _, raw := range files {
		plan, err := validateDefinitionFile(raw, filepath.Dir(definitionPath))
		if err != nil {
			return nil, err
		}
		plans = append(plans, plan)
	}
	identities := map[string]bool{}
	for _, plan := range plans {
		identities[semanticIdentity(plan.destination)] = true
	}
	if len(identities) != len(plans) {
		return nil, managerError("the definition has duplicate or colliding destinations")
	}
	for _, plan := range plans {
		if err := p.validateDestinationTree(plan.destination); err != nil {
			return nil, err
		}
	}
	policy, _, err := p.Policy()
	if err != nil {
		return nil, managerError("invalid distribution inventory: %v", err)
	}
	manifest, err := LoadManifest(p.Path(ManifestPath))
	if err != nil {
		return nil, managerError("invalid distribution inventory: %v", err)
	}
	registry, err := p.Upstreams()
	if err != nil {
		return nil, managerError("invalid distribution inventory: %v", err)
	}
	currentFiles := manifest.Value("files").(*Object)
	currentCatalog, err := LoadJSONObject(p.Path(CatalogPath))
	if err != nil {
		return nil, err
	}
	if _, err := p.ValidateCatalog(currentCatalog); err != nil {
		return nil, managerError("invalid current public catalog: %v", err)
	}
	pkgValue := definition.Value("package")
	pkg, _ := pkgValue.(*Object)
	if pkgValue != nil {
		if _, err := p.ValidatePackage(pkgValue, "definition.package", true); err != nil {
			return nil, &ManagerError{err.Error()}
		}
		if IsCLIPackage(pkg) {
			return nil, managerError("x86qw CLI packages can only be prepared by the CLI release flow")
		}
	}
	stagedComponents, err := LoadJSONObject(p.Path(ComponentsPath))
	if err != nil {
		return nil, err
	}
	stagedReleases, err := LoadJSONObject(p.Path(ReleasesPath))
	if err != nil {
		return nil, err
	}
	transition, hasTransition, err := applyReferenceTransition(stagedReleases, definition.Value("reference"))
	if err != nil {
		return nil, err
	}
	replace := getOr(definition, "replace", false)
	if _, ok := replace.(bool); !ok {
		return nil, managerError("replace must be a boolean")
	}
	if componentValue := definition.Value("component"); componentValue != nil {
		component, ok := componentValue.(*Object)
		if !ok {
			return nil, managerError("component must be an object")
		}
		if identifier, ok := component.Value("id").(string); !ok || !componentID.MatchString(identifier) {
			return nil, managerError("component.id must be a safe identifier")
		}
		profiles, ok := pyjson.StrList(getOr(definition, "profiles", []any{}))
		for _, profile := range profiles {
			ok = ok && componentID.MatchString(profile)
		}
		if !ok {
			return nil, managerError("profiles must be a list of safe identifiers")
		}
		proposed, ok := definition.Value("release").(*Object)
		if !ok {
			return nil, managerError("every new or replaced component must declare release")
		}
		entries, ok := stagedReleases.Value("components").(*Object)
		if !ok {
			return nil, managerError("the release inventory has no valid components")
		}
		identifier := component.Value("id").(string)
		if current, ok := entries.Value(identifier).(*Object); replace == true && ok {
			currentNamespace := getOr(current, "distribution_component", "nquake")
			if !pyjson.Eq(getOr(proposed, "distribution_component", "nquake"), currentNamespace) {
				return nil, managerError("a replacement release must keep distribution_component %s: %s", pyjson.Repr(currentNamespace), identifier)
			}
		}
		if err := preserveProfileFingerprints(stagedComponents); err != nil {
			return nil, err
		}
		if err := upsertComponent(stagedComponents, component, replace == true); err != nil {
			return nil, err
		}
		if err := addToProfiles(stagedComponents, identifier, profiles); err != nil {
			return nil, err
		}
		if err := preserveProfileFingerprints(stagedComponents); err != nil {
			return nil, err
		}
		entries.Set(identifier, proposed)
	} else if definition.Has("profiles") || definition.Has("release") {
		return nil, managerError("profiles and release require component")
	}
	if err := ValidateComponentCatalog(stagedComponents); err != nil {
		return nil, managerError("invalid proposed inventories: %v", err)
	}
	namespaces, _ := pyjson.StrList(stagedComponents.Value("content_namespaces"))
	if err := ValidateReleases(stagedReleases, setOf(ComponentIDs(stagedComponents)...), setOf(namespaces...)); err != nil {
		return nil, managerError("invalid proposed inventories: %v", err)
	}
	projectConsumers := func(path string) int {
		consumers := map[string]bool{}
		for _, candidate := range objects(stagedComponents.Value("components")) {
			identifier, ok := candidate.Value("id").(string)
			if !ok {
				continue
			}
			for _, field := range []string{"project_sources", "project_inputs", "project_overrides", "project_archive_overrides"} {
				for _, item := range objects(getOr(candidate, field, []any{})) {
					if item.Value("path") == path {
						consumers[identifier] = true
					}
				}
			}
		}
		return len(consumers)
	}
	type authority struct {
		releaseID, owner string
		artifact         *Object
	}
	stagedAuthority := func(path string) (*authority, error) {
		entries, ok := stagedReleases.Value("components").(*Object)
		if !ok {
			return nil, nil
		}
		var matches []authority
		for _, releaseID := range entries.Keys() {
			release, ok := entries.Value(releaseID).(*Object)
			if !ok {
				continue
			}
			for _, artifact := range objects(getOr(release, "artifacts", []any{})) {
				if artifact.Value("distribution_path") == path {
					matches = append(matches, authority{releaseID, pyjson.Repr(getOr(release, "distribution_component", "nquake")), artifact})
				}
			}
		}
		if len(matches) > 1 {
			return nil, managerError("destination appears in more than one proposed release: %s", path)
		}
		if len(matches) == 0 {
			return nil, nil
		}
		return &matches[0], nil
	}
	requireIdentity := func(plan *filePlan, record *Object, label, urlField string) error {
		if !pyjson.Eq(plan.entry.Value("url"), record.Value(urlField)) || !pyjson.EqInt(record.Value("size"), plan.size) || !pyjson.Eq(record.Value("sha256"), plan.sha256) {
			return managerError("URL, size or SHA-256 differs from the %s: %s", label, plan.destination)
		}
		return nil
	}
	preservedSource := func(path string) (*Object, error) {
		var matches []*Object
		for _, upstream := range objects(registry.Value("upstreams")) {
			if source, ok := upstream.Value("source").(*Object); ok && source.Value("distribution_path") == path {
				matches = append(matches, source)
			}
		}
		if len(matches) > 1 {
			return nil, managerError("duplicate preserved source in the inventory: %s", path)
		}
		if len(matches) == 0 {
			return nil, nil
		}
		return matches[0], nil
	}
	stagedProject := p.Staged(p.Path(ComponentsPath), p.Path(ReleasesPath))
	for _, plan := range plans {
		entry := plan.entry
		relative := strings.TrimPrefix(plan.destination, "dist/")
		if plan.source != "" {
			if currentFiles.Has(relative) {
				return nil, managerError("a local source cannot replace a managed upstream file: %s", relative)
			}
			if projectConsumers(plan.destination) != 1 {
				return nil, managerError("a local source needs an exact consumer in the proposed BOM: %s", plan.destination)
			}
			continue
		}
		if entry.Value("managed") != true {
			return nil, managerError("every persistent remote file must declare managed: true")
		}
		componentName := entry.Value("distribution_component").(string)
		consumer := entry.Value("consumer").(string)
		if err := p.RequireComponent(componentName, &relative); err != nil {
			return nil, &ManagerError{err.Error()}
		}
		owner, err := p.ComponentForDistributionPath(relative)
		if err != nil {
			return nil, &ManagerError{err.Error()}
		}
		if owner != componentName {
			return nil, managerError("the declared component does not own the destination: %s != %s", componentName, ownerRepr(owner))
		}
		existing, _ := currentFiles.Value(relative).(*Object)
		sourceComponent := SourceOwner(registry, relative)
		allowed := setOf(objectsOrStrings(policy[componentName].Value("consumers"))...)
		if sourceComponent != "" {
			allowed["development:"+sourceComponent] = true
		}
		if existing != nil && existing.Value("component") == componentName {
			if text, ok := existing.Value("consumer").(string); ok {
				allowed[text] = true
			}
		}
		if !allowed[consumer] {
			return nil, managerError("undeclared consumer for %s: %s", componentName, consumer)
		}
		referencePackage := ""
		if componentName == "nquake" {
			parts := purePosix(relative).parts
			if len(parts) >= 4 && parts[0] == "distributions" && parts[1] == "nquake" {
				reference, ok := stagedReleases.Value("reference").(*Object)
				if !ok {
					return nil, managerError("the release inventory has no nQuake reference")
				}
				upstreamPath, _ := nquakePath(relative)
				if referencePackage, err = ComponentForSource(stagedComponents, upstreamPath, "reference"); err != nil {
					return nil, err
				}
				revision := ReferenceRevisionOf(stagedReleases, referencePackage)
				if revision == "" {
					return nil, managerError("%s does not pin an nQuake revision: %s", ownerRepr(referencePackage), relative)
				}
				repository, ok := reference.Value("repository").(string)
				if !ok {
					return nil, managerError("the release inventory has no nQuake repository")
				}
				expected, err := referenceAssetURL(repository, revision, upstreamPath)
				if err != nil {
					return nil, err
				}
				if !pyjson.Eq(entry.Value("url"), expected) {
					return nil, managerError("the nQuake snapshot URL differs from the pinned reference: %s", relative)
				}
			}
		}
		artifactAuthority, err := stagedAuthority(relative)
		if err != nil {
			return nil, err
		}
		if artifactAuthority != nil && artifactAuthority.owner != componentName {
			return nil, managerError("the release distribution_component differs from the managed plan: %s != %s: %s", artifactAuthority.owner, componentName, relative)
		}
		var expectedPackage any
		switch {
		case referencePackage != "":
			expectedPackage = referencePackage
		case artifactAuthority != nil:
			expectedPackage = artifactAuthority.releaseID
		case sourceComponent != "":
			expectedPackage = sourceComponent
		case existing != nil:
			current := existing.Value("package")
			if _, ok := current.(string); current != nil && !ok {
				return nil, managerError("invalid current package in the manifest: %s", relative)
			}
			expectedPackage = current
		}
		currentOwner, err := stagedProject.ConsumedComponent(relative)
		if err != nil {
			return nil, managerError("invalid managed destination: %v", err)
		}
		stagedOwner := ""
		if artifactAuthority != nil {
			stagedOwner = artifactAuthority.owner
		}
		if currentOwner != componentName && stagedOwner != componentName &&
			!(componentName == "nquake" && referencePackage != "" && sourceComponent == "") {
			return nil, managerError("destination has no declared operational consumer: %s", relative)
		}
		provided := entry.Value("package")
		if expectedPackage == nil && provided != nil {
			return nil, managerError("package does not belong to the managed destination: %s", relative)
		}
		if expectedPackage != nil && !pyjson.Eq(provided, expectedPackage) {
			return nil, managerError("the managed file's package must be %s: %s", pyjson.Repr(expectedPackage), relative)
		}
		// A file of a component moving to another nQuake revision is the
		// only managed path that may change in place.
		if existing != nil && !(referencePackage != "" && hasTransition && transition.previous[referencePackage] != "") {
			if err := requireIdentity(plan, existing, "current immutable manifest", "url"); err != nil {
				return nil, err
			}
			for _, field := range [][2]string{{"component", "distribution_component"}, {"consumer", "consumer"}, {"package", "package"}} {
				if !pyjson.Eq(entry.Value(field[1]), existing.Value(field[0])) {
					return nil, managerError("metadata %s differs from the current immutable manifest: %s", field[0], relative)
				}
			}
			continue
		}
		authorities := 0
		if artifactAuthority != nil {
			if !pyjson.Eq(entry.Value("package"), artifactAuthority.releaseID) {
				return nil, managerError("the managed file's package differs from the proposed release: %s", relative)
			}
			if err := requireIdentity(plan, artifactAuthority.artifact, "proposed release", "url"); err != nil {
				return nil, err
			}
			authorities++
		}
		record, err := preservedSource(relative)
		if err != nil {
			return nil, err
		}
		if record != nil {
			if err := requireIdentity(plan, record, "upstream record", "url"); err != nil {
				return nil, err
			}
			authorities++
		}
		if pkg != nil && pkg.Value("distribution_path") == relative {
			if err := requireIdentity(plan, pkg, "proposed public package", "origin_url"); err != nil {
				return nil, err
			}
			authorities++
		}
		if componentName == "nquake" && referencePackage != "" {
			authorities++
		}
		if authorities == 0 {
			return nil, managerError("remote file is not bound to a proposed release, upstream or package: %s", relative)
		}
	}
	if hasTransition {
		for _, identifier := range sortedKeys(transition.previous) {
			found := false
			for _, plan := range plans {
				_, nquake := nquakePath(strings.TrimPrefix(plan.destination, "dist/"))
				found = found || (nquake && pyjson.Eq(plan.entry.Value("package"), identifier) &&
					nquakeURLRevision(pyjson.Repr(plan.entry.Value("url"))) == transition.revision)
			}
			if !found {
				return nil, managerError("the nQuake reference transition must incorporate the files of %s at %s", identifier, transition.revision[:12])
			}
		}
	}
	if pkg != nil {
		distributionPath, isText := pkg.Value("distribution_path").(string)
		if isText && currentFiles.Has(distributionPath) {
			return nil, managerError("a proposed package cannot reuse a path already registered in the manifest")
		}
		var matching *filePlan
		for _, plan := range plans {
			if isText && plan.destination == "dist/"+distributionPath {
				matching = plan
				break
			}
		}
		if matching == nil {
			return nil, managerError("the declared package must match a definition file")
		}
		if !pyjson.EqInt(pkg.Value("size"), matching.size) || !pyjson.Eq(pkg.Value("sha256"), matching.sha256) {
			return nil, managerError("the declared package differs from the definition file's pins")
		}
		if !pyjson.Eq(matching.entry.Value("url"), pkg.Value("origin_url")) {
			return nil, managerError("the package origin differs from the definition file URL")
		}
		if matching.entry.Value("managed") != true {
			return nil, managerError("the declared package must match a managed file")
		}
		if !pyjson.Eq(matching.entry.Value("distribution_component"), pkg.Value("component")) {
			return nil, managerError("the package component differs from the managed file's owner")
		}
		expectedPublic := matching.entry.Value("package")
		if !pyjson.Truthy(expectedPublic) {
			expectedPublic = matching.entry.Value("distribution_component")
		}
		public := getOr(pkg, "package", pkg.Value("component"))
		if !pyjson.Eq(public, expectedPublic) {
			return nil, managerError("the public package's logical identity differs from the managed file: %s != %s", pyjson.Repr(public), pyjson.Repr(expectedPublic))
		}
		entries, ok := currentCatalog.Value("packages").([]any)
		if !ok {
			return nil, managerError("the current public catalog has no valid packages")
		}
		if packageIdentityExists(entries, pkg) {
			return nil, managerError("the package identity already exists: %s", identityRepr(pkg))
		}
		proposed := currentCatalog.Clone()
		proposed.Set("packages", append(append([]any{}, entries...), pkg))
		if _, err := p.ValidateCatalog(proposed); err != nil {
			return nil, managerError("invalid proposed public catalog: %v", err)
		}
	}
	return plans, nil
}

func ownerRepr(owner string) string {
	if owner == "" {
		return "None"
	}
	return owner
}

var identityFields = []string{"component", "version", "channel", "platform", "architecture"}

func packageIdentityExists(entries []any, pkg *Object) bool {
	for _, item := range objects(entries) {
		same := true
		for _, field := range identityFields {
			same = same && pyjson.Eq(item.Value(field), pkg.Value(field))
		}
		if same {
			return true
		}
	}
	return false
}

func identityRepr(pkg *Object) string {
	var parts []string
	for _, field := range identityFields {
		parts = append(parts, pyRepr(pkg.Value(field)))
	}
	return "(" + strings.Join(parts, ", ") + ")"
}

// fetchDefinitionFile downloads or copies one planned file and returns its
// size and digest.
func (p *Project) fetchDefinitionFile(plan *filePlan, destination string) (int64, string, error) {
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return 0, "", err
	}
	if plan.source != "" {
		payload, err := os.ReadFile(plan.source)
		if err != nil {
			return 0, "", managerError("could not copy the local source: %v", err)
		}
		if int64(len(payload)) != plan.size || sha256Hex(payload) != plan.sha256 {
			return 0, "", managerError("local source changed after validation")
		}
		os.Remove(destination) // never write through a workspace hard link
		if err := writeAtomic(destination, payload, plan.mode); err != nil {
			return 0, "", err
		}
	} else if err := p.Pinned(plan.url, destination, plan.size, plan.sha256, 1); err != nil {
		return 0, "", managerError("failed to download the definition file: %v", err)
	}
	size, err := fileSize(destination)
	if err != nil {
		return 0, "", err
	}
	digest, err := archive.FileSHA256(destination)
	if err != nil {
		return 0, "", err
	}
	label := pyjson.Repr(plan.entry.Value("destination"))
	if size != plan.size {
		return 0, "", managerError("unexpected size for %s", label)
	}
	if digest != plan.sha256 {
		return 0, "", managerError("unexpected SHA-256 for %s", label)
	}
	return size, digest, nil
}

// AddOptions are the flags of `x86qw-content add`.
type AddOptions struct {
	DryRun  bool
	Yes     bool
	Confirm Confirm
}

// Add is `x86qw-content add`.
func (p *Project) Add(out io.Writer, definitionPath string, options AddOptions) error {
	definitionPath, err := filepath.Abs(definitionPath)
	if err != nil {
		return err
	}
	definition, err := LoadJSONObject(definitionPath)
	if err != nil {
		return err
	}
	plans, err := p.validateDistributionChange(definition, definitionPath)
	if err != nil {
		return err
	}
	files := definition.Value("files").([]any)
	if options.DryRun {
		fmt.Fprintf(out, "[PLAN] Valid definition: %d file(s).\n", len(plans))
		for _, plan := range plans {
			fmt.Fprintf(out, "  %s | %s\n", plan.destination, plan.origin)
		}
		fmt.Fprintln(out, "[OK] Dry run finished; no remote byte was downloaded and no file was changed.")
		return nil
	}
	if err := p.requireCleanWorktree(); err != nil {
		return err
	}
	if !options.Yes {
		if err := options.Confirm(fmt.Sprintf("Incorporate %d file(s) and update the inventories?", len(files))); err != nil {
			return err
		}
	}
	work, err := p.prepareWorkspace()
	if err != nil {
		return err
	}
	defer os.RemoveAll(work)
	stagedComponents, err := LoadJSONObject(filepath.Join(work, "components.json"))
	if err != nil {
		return err
	}
	stagedReleases, err := LoadJSONObject(filepath.Join(work, "component-releases.json"))
	if err != nil {
		return err
	}
	stagedCatalog, err := LoadJSONObject(filepath.Join(work, "catalog.json"))
	if err != nil {
		return err
	}
	transition, hasTransition, err := applyReferenceTransition(stagedReleases, definition.Value("reference"))
	if err != nil {
		return err
	}
	if componentValue := definition.Value("component"); componentValue != nil {
		component, ok := componentValue.(*Object)
		if !ok {
			return managerError("component must be an object")
		}
		if err := preserveProfileFingerprints(stagedComponents); err != nil {
			return err
		}
		if err := upsertComponent(stagedComponents, component, pyjson.Truthy(definition.Value("replace"))); err != nil {
			return err
		}
		identifier := pyjson.Repr(component.Value("id"))
		profiles, ok := pyjson.StrList(getOr(definition, "profiles", []any{}))
		if !ok {
			return managerError("profiles must be a list")
		}
		if err := addToProfiles(stagedComponents, identifier, profiles); err != nil {
			return err
		}
		if err := preserveProfileFingerprints(stagedComponents); err != nil {
			return err
		}
		release, releaseOK := definition.Value("release").(*Object)
		entries, entriesOK := stagedReleases.Value("components").(*Object)
		if !releaseOK || !entriesOK {
			return managerError("every new or replaced component must declare release")
		}
		entries.Set(identifier, release)
	}
	manifestPath := filepath.Join(work, "dist", "manifest.json")
	manifest, err := LoadManifest(manifestPath)
	if err != nil {
		return err
	}
	manifestFiles := manifest.Value("files").(*Object)
	if hasTransition {
		// The moving components' files are replaced by the definition; the
		// ones the new revision no longer has go away.
		for _, relative := range manifestFiles.Keys() {
			_, snapshot := nquakePath(relative)
			owner, _ := pyjson.Get(manifestFiles, relative, "package").(string)
			if !snapshot || transition.previous[owner] == "" {
				continue
			}
			path := joinPosix(filepath.Join(work, "dist"), relative)
			if isSymlink(path) {
				return managerError("unsafe previous nQuake file in the workspace: %s", relative)
			}
			if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
				return err
			}
			manifestFiles.Delete(relative)
		}
		removeEmptyDirectories(filepath.Join(work, "dist", filepath.FromSlash(nquakeDir)))
	}
	for index, raw := range files {
		entry, ok := raw.(*Object)
		if !ok {
			return managerError("invalid file entry")
		}
		destination, err := safeRelative(entry.Value("destination"), "dist")
		if err != nil {
			return err
		}
		relative := strings.TrimPrefix(destination, "dist/")
		size, digest, err := p.fetchDefinitionFile(plans[index], joinPosix(filepath.Join(work, "dist"), relative))
		if err != nil {
			return err
		}
		if entry.Value("managed") == true {
			component, consumer, url := entry.Value("distribution_component"), entry.Value("consumer"), entry.Value("url")
			if !pyjson.NonEmptyStr(component) || !pyjson.NonEmptyStr(consumer) || !pyjson.NonEmptyStr(url) {
				return managerError("a managed upstream file requires distribution_component, consumer and url")
			}
			metadata := object("component", component, "consumer", consumer, "url", url,
				"size", pyjson.Number(fmt.Sprint(size)), "sha256", digest)
			if text, ok := entry.Value("package").(string); ok {
				metadata.Set("package", text)
			}
			manifestFiles.Set(relative, metadata)
		}
	}
	if err := WriteManifest(manifestPath, manifest); err != nil {
		return err
	}
	if pkgValue := definition.Value("package"); pkgValue != nil {
		if _, err := p.ValidatePackage(pkgValue, "definition.package", true); err != nil {
			return err
		}
		pkg := pkgValue.(*Object)
		artifact := joinPosix(filepath.Join(work, "dist"), pyjson.Repr(getOr(pkg, "distribution_path", "")))
		size, _ := fileSize(artifact)
		digest, _ := archive.FileSHA256(artifact)
		if !isFile(artifact) || !pyjson.EqInt(pkg.Value("size"), size) || !pyjson.Eq(pkg.Value("sha256"), digest) {
			return managerError("the declared package does not match the incorporated file")
		}
		entries, ok := stagedCatalog.Value("packages").([]any)
		if !ok {
			return managerError("invalid package catalog")
		}
		if packageIdentityExists(entries, pkg) {
			return managerError("the package identity already exists: %s", identityRepr(pkg))
		}
		stagedCatalog.Set("packages", append(entries, pkg))
		stagedCatalog.Set("generated_at", utcNow())
	}
	for name, document := range map[string]*Object{"components.json": stagedComponents, "component-releases.json": stagedReleases, "catalog.json": stagedCatalog} {
		if err := WriteJSON(filepath.Join(work, name), document); err != nil {
			return err
		}
	}
	staged, err := p.validateStaged(work)
	if err != nil {
		return err
	}
	if err := staged.buildIntoWorkspace(work); err != nil {
		return err
	}
	if err := p.applyWorkspace(work, true); err != nil {
		return err
	}
	fmt.Fprintln(out, "[OK] Files incorporated, inventories validated and packages rebuilt.")
	fmt.Fprintln(out, "[NEXT] Run 'x86qw-content verify' and review 'git diff'.")
	return nil
}
