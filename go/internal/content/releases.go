package content

import (
	"fmt"
	"regexp"
	"slices"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Component release inventory: maintenance/inventory/releases.json.

var (
	releaseVersion     = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$`)
	releaseStrategies  = setOf("reference-snapshot", "reference-overlay", "upstream-overlay", "upstream-package", "upstream-composed")
	releaseFreshness   = setOf("reference-payload-current", "upstream-current")
	repositoryPattern  = regexp.MustCompile(`^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`)
	externalStrategies = setOf("reference-overlay", "upstream-overlay", "upstream-package", "upstream-composed")
)

func persistentURL(value any, label string) error {
	parsed, err := validateHTTPSURL(value, label)
	if err != nil {
		return fmt.Errorf("invalid persistent HTTPS URL: %s", label)
	}
	if parsed.Query != "" {
		return fmt.Errorf("persistent HTTPS URL contains query: %s", label)
	}
	return nil
}

// getOr is object[key], or fallback when it is absent.
func getOr(object *Object, key string, fallback any) any {
	if object.Has(key) {
		return object.Value(key)
	}
	return fallback
}

// Releases loads and validates the release inventory against the
// components.
func (p *Project) Releases() (*Object, error) {
	if !p.releasesDone {
		p.releasesDone = true
		path := p.Path(ReleasesPath)
		if p.releasesFile != "" {
			path = p.releasesFile
		}
		p.releases, p.releasesErr = p.loadReleases(path)
	}
	return p.releases, p.releasesErr
}

func (p *Project) loadReleases(path string) (*Object, error) {
	value, err := ReadJSON(path)
	if err != nil {
		return nil, fmt.Errorf("cannot read component release inventory: %s", path)
	}
	catalog, err := p.Components()
	if err != nil {
		return nil, err
	}
	namespaces, _ := pyjson.StrList(catalog.Value("content_namespaces"))
	if err := ValidateReleases(value, setOf(ComponentIDs(catalog)...), setOf(namespaces...)); err != nil {
		return nil, err
	}
	return value.(*Object), nil
}

// ValidateReleases checks a release inventory document.
func ValidateReleases(value any, componentIDs, namespaces map[string]bool) error {
	releases, ok := value.(*Object)
	if !ok || !pyjson.EqInt(releases.Value("format"), 1) || !pyjson.Eq(releases.Value("project"), "x86qw") {
		return fmt.Errorf("invalid component release inventory identity")
	}
	reference, ok := releases.Value("reference").(*Object)
	if !ok || !sameKeys(reference.Keys(), "repository") {
		return fmt.Errorf("invalid nQuake reference: it declares only the repository; each component pins its own revision")
	}
	if err := persistentURL(reference.Value("repository"), "nQuake reference repository"); err != nil {
		return err
	}
	components, ok := releases.Value("components").(*Object)
	if !ok || !sameSet(components.Keys(), componentIDs) {
		return fmt.Errorf("release inventory must cover every component exactly once")
	}
	distributionPaths := map[string]bool{}
	for _, identifier := range components.Keys() {
		release, ok := components.Value(identifier).(*Object)
		if !ok {
			return fmt.Errorf("invalid release metadata: %s", identifier)
		}
		version, ok := release.Value("version").(string)
		if !ok || !releaseVersion.MatchString(version) {
			return fmt.Errorf("invalid component version: %s", identifier)
		}
		if !inSet(release.Value("strategy"), releaseStrategies) || !inSet(release.Value("freshness"), releaseFreshness) {
			return fmt.Errorf("invalid release strategy or freshness: %s", identifier)
		}
		strategy := release.Value("strategy").(string)
		referenceRevision, pinned := release.Value("reference_revision").(string)
		if release.Has("reference_revision") && !(pinned && hex40Pattern.MatchString(referenceRevision)) {
			return fmt.Errorf("invalid nQuake reference revision: %s", identifier)
		}
		if (IsReferenceStrategy(strategy) || strategy == "upstream-overlay") && !pinned {
			return fmt.Errorf("reference component has no nQuake revision: %s", identifier)
		}
		if !inSet(getOr(release, "distribution_component", "nquake"), namespaces) {
			return fmt.Errorf("invalid distribution component: %s", identifier)
		}
		if strategy == "reference-snapshot" && !(version == referenceRevision[:12] || strings.HasPrefix(version, referenceRevision[:12]+"+x86qw.")) {
			return fmt.Errorf("reference component version differs from snapshot: %s", identifier)
		}
		external := externalStrategies[strategy]
		if external || release.Has("license") || release.Has("license_url") {
			if !nonEmptyText(release.Value("license")) {
				return fmt.Errorf("invalid release license: %s", identifier)
			}
			if err := persistentURL(release.Value("license_url"), "release license URL: "+identifier); err != nil {
				return err
			}
		}
		mirrors, ok := getOr(release, "source_mirrors", []any{}).([]any)
		if !ok {
			return fmt.Errorf("invalid source mirrors: %s", identifier)
		}
		for index, url := range mirrors {
			if err := persistentURL(url, fmt.Sprintf("source mirror %d: %s", index, identifier)); err != nil {
				return err
			}
		}
		upstreamValue := release.Value("upstream")
		if external {
			upstream, ok := upstreamValue.(*Object)
			if !ok {
				return fmt.Errorf("upstream release has no upstream metadata: %s", identifier)
			}
			composed := strategy == "upstream-overlay" || strategy == "upstream-composed"
			if composed && !repositoryPattern.MatchString(pyjson.Repr(getOr(upstream, "repository", ""))) {
				return fmt.Errorf("invalid upstream repository: %s", identifier)
			}
			if !releaseVersion.MatchString(pyjson.Repr(getOr(upstream, "release", ""))) {
				return fmt.Errorf("invalid upstream release: %s", identifier)
			}
			if composed && !hex40Pattern.MatchString(pyjson.Repr(getOr(upstream, "source_revision", ""))) {
				return fmt.Errorf("invalid upstream source revision: %s", identifier)
			}
			for _, field := range []string{"release_url", "source_url"} {
				if err := persistentURL(upstream.Value(field), "upstream "+field+": "+identifier); err != nil {
					return err
				}
			}
			if !nonEmptyText(release.Value("notes")) {
				return fmt.Errorf("upstream overlay has no release notes: %s", identifier)
			}
			if !releaseVersion.MatchString(pyjson.Repr(getOr(release, "distribution_tag", ""))) {
				return fmt.Errorf("invalid distribution tag: %s", identifier)
			}
			if !componentID.MatchString(pyjson.Repr(getOr(release, "distribution_component", ""))) {
				return fmt.Errorf("invalid distribution_component: %s", identifier)
			}
			compatibility, ok := release.Value("compatibility").(*Object)
			valid := ok
			if valid {
				scope, isList := pyjson.StrList(compatibility.Value("client_scope"))
				verified, verifiedList := compatibility.Value("verified").([]any)
				valid = isList && len(scope) > 0 && !slices.Contains(scope, "") && verifiedList && len(verified) > 0 &&
					pyjson.IsStr(compatibility.Value("gamecode_runtime"))
			}
			if !valid {
				return fmt.Errorf("invalid compatibility evidence: %s", identifier)
			}
		} else if upstreamValue != nil {
			return fmt.Errorf("unexpected upstream metadata: %s", identifier)
		}
		artifacts, ok := getOr(release, "artifacts", []any{}).([]any)
		if !ok {
			return fmt.Errorf("invalid artifacts: %s", identifier)
		}
		if external && len(artifacts) == 0 {
			return fmt.Errorf("upstream release has no artifact: %s", identifier)
		}
		for _, raw := range artifacts {
			if err := validateArtifact(raw, release, strategy, identifier, distributionPaths); err != nil {
				return err
			}
		}
		if err := validateArchiveEdits(release, strategy, identifier); err != nil {
			return err
		}
	}
	return nil
}

func validSize(value any) bool { return sizeWithin(value, false) }

func validateArtifact(raw any, release *Object, strategy, identifier string, distributionPaths map[string]bool) error {
	artifact, ok := raw.(*Object)
	if !ok {
		return fmt.Errorf("invalid artifact: %s", identifier)
	}
	filename, ok := artifact.Value("filename").(string)
	if !ok || posixName(filename) != filename {
		return fmt.Errorf("invalid artifact filename: %s", identifier)
	}
	distributionPath, err := safePath(artifact.Value("distribution_path"), "artifact distribution path")
	if err != nil {
		return err
	}
	owner := pyjson.Repr(getOr(release, "distribution_component", "nquake"))
	prefixes := []string{"mods/" + owner + "/", "servers/" + owner + "/", "services/" + owner + "/"}
	if distributionPaths[distributionPath] || !hasPrefixAny(distributionPath, prefixes) {
		return fmt.Errorf("duplicate or misplaced distribution path: %s", identifier)
	}
	distributionPaths[distributionPath] = true
	if posixName(distributionPath) != filename {
		return fmt.Errorf("artifact path and filename differ: %s", identifier)
	}
	if err := persistentURL(artifact.Value("url"), "artifact URL: "+identifier); err != nil {
		return err
	}
	if !validSize(artifact.Value("size")) {
		return fmt.Errorf("invalid artifact size: %s", identifier)
	}
	if !sha256Pattern.MatchString(pyjson.Repr(getOr(artifact, "sha256", ""))) {
		return fmt.Errorf("invalid artifact hash: %s", identifier)
	}
	members, ok := getOr(artifact, "members", []any{}).([]any)
	if !ok || (strategy == "upstream-overlay" && len(members) == 0) {
		return fmt.Errorf("artifact has invalid consumed members: %s", identifier)
	}
	if (strategy == "upstream-package" || strategy == "upstream-composed") && len(members) > 0 {
		return fmt.Errorf("standalone artifact members are selected by the component catalog: %s", identifier)
	}
	for _, rawMember := range members {
		member, ok := rawMember.(*Object)
		if !ok {
			return fmt.Errorf("invalid artifact member: %s", identifier)
		}
		for _, field := range [][2]string{{"path", "artifact member"}, {"target_archive", "target archive"}, {"target_member", "target member"}} {
			if _, err := safePath(member.Value(field[0]), field[1]); err != nil {
				return err
			}
		}
		if !inSet(getOr(member, "target_mode", "replace"), setOf("replace", "add")) {
			return fmt.Errorf("invalid artifact member target mode: %s", identifier)
		}
		if !validSize(member.Value("size")) {
			return fmt.Errorf("invalid artifact member size: %s", identifier)
		}
		if !sha256Pattern.MatchString(pyjson.Repr(getOr(member, "sha256", ""))) {
			return fmt.Errorf("invalid artifact member hash: %s", identifier)
		}
	}
	return nil
}

func positiveCount(object *Object) bool {
	count := getOr(object, "count", pyjson.Number("1"))
	order, _ := pyjson.IntCmp(count, 0)
	return pyjson.IsInstanceInt(count) && order > 0
}

func validateArchiveEdits(release *Object, strategy, identifier string) error {
	moves, ok := getOr(release, "archive_moves", []any{}).([]any)
	if !ok {
		return fmt.Errorf("invalid archive moves: %s", identifier)
	}
	moveIdentities := map[[2]string]bool{}
	for _, raw := range moves {
		move, ok := raw.(*Object)
		if !ok {
			return fmt.Errorf("invalid archive move: %s", identifier)
		}
		target, err := safePath(move.Value("target_archive"), "archive move target")
		if err != nil {
			return err
		}
		source, err := safePath(move.Value("source"), "archive move source")
		if err != nil {
			return err
		}
		destination, err := safePath(move.Value("destination"), "archive move destination")
		if err != nil {
			return err
		}
		if source == destination || moveIdentities[[2]string{target, source}] {
			return fmt.Errorf("duplicate or ineffective archive move: %s", identifier)
		}
		moveIdentities[[2]string{target, source}] = true
	}
	removals, ok := getOr(release, "archive_removals", []any{}).([]any)
	if !ok {
		return fmt.Errorf("invalid archive removals: %s", identifier)
	}
	removalIdentities := map[[2]string]bool{}
	for _, raw := range removals {
		removal, ok := raw.(*Object)
		if !ok {
			return fmt.Errorf("invalid archive removal: %s", identifier)
		}
		target, err := safePath(removal.Value("target_archive"), "archive removal target")
		if err != nil {
			return err
		}
		member, err := safePath(removal.Value("member"), "archive removal member")
		if err != nil {
			return err
		}
		if removalIdentities[[2]string{target, member}] {
			return fmt.Errorf("duplicate archive removal: %s", identifier)
		}
		removalIdentities[[2]string{target, member}] = true
	}
	archiveReplacements, ok := getOr(release, "archive_text_replacements", []any{}).([]any)
	if !ok {
		return fmt.Errorf("invalid archive text replacements: %s", identifier)
	}
	archiveReplacementIdentities := map[[3]string]bool{}
	for _, raw := range archiveReplacements {
		replacement, ok := raw.(*Object)
		if !ok {
			return fmt.Errorf("invalid archive text replacement: %s", identifier)
		}
		target, err := safePath(replacement.Value("target_archive"), "archive text replacement target")
		if err != nil {
			return err
		}
		member, err := safePath(replacement.Value("member"), "archive text replacement member")
		if err != nil {
			return err
		}
		before, beforeOK := replacement.Value("before").(string)
		after, afterOK := replacement.Value("after").(string)
		if !beforeOK || before == "" || !afterOK || before == after {
			return fmt.Errorf("ineffective archive text replacement: %s", identifier)
		}
		if !sha256Pattern.MatchString(pyjson.Repr(getOr(replacement, "source_sha256", ""))) {
			return fmt.Errorf("invalid archive member source hash: %s", identifier)
		}
		identity := [3]string{target, member, before}
		if archiveReplacementIdentities[identity] {
			return fmt.Errorf("duplicate archive text replacement: %s", identifier)
		}
		archiveReplacementIdentities[identity] = true
		if !positiveCount(replacement) {
			return fmt.Errorf("invalid archive text replacement count: %s", identifier)
		}
	}
	layers, ok := getOr(release, "archive_layers", []any{}).([]any)
	if !ok {
		return fmt.Errorf("invalid archive layers: %s", identifier)
	}
	if len(layers) > 0 && strategy != "upstream-composed" {
		return fmt.Errorf("archive layers require an upstream-composed release: %s", identifier)
	}
	layerTargets, layerReferences := map[string]bool{}, map[string]bool{}
	for _, raw := range layers {
		layer, ok := raw.(*Object)
		if !ok {
			return fmt.Errorf("invalid archive layer: %s", identifier)
		}
		target, err := safePath(layer.Value("target_archive"), "archive layer target")
		if err != nil {
			return err
		}
		reference, err := safePath(layer.Value("reference_source"), "archive layer reference source")
		if err != nil {
			return err
		}
		policy, err := safePath(layer.Value("policy"), "archive layer policy")
		if err != nil {
			return err
		}
		if !hasZipSuffix(target) {
			return fmt.Errorf("archive layer target is not a ZIP/PK3: %s", identifier)
		}
		if !hasZipSuffix(reference) {
			return fmt.Errorf("archive layer reference is not a ZIP/PK3: %s", identifier)
		}
		if !strings.HasPrefix(policy, "dist/") || !strings.HasSuffix(Casefold(policy), ".json") {
			return fmt.Errorf("archive layer policy is outside the distribution: %s", identifier)
		}
		if layerTargets[target] || layerReferences[reference] {
			return fmt.Errorf("duplicate archive layer: %s", identifier)
		}
		layerTargets[target], layerReferences[reference] = true, true
	}
	copies, ok := getOr(release, "package_copies", []any{}).([]any)
	if !ok {
		return fmt.Errorf("invalid package copies: %s", identifier)
	}
	if len(copies) > 0 && strategy != "upstream-package" {
		return fmt.Errorf("package copies require an upstream package: %s", identifier)
	}
	copyDestinations := map[string]bool{}
	for _, raw := range copies {
		copy, ok := raw.(*Object)
		if !ok {
			return fmt.Errorf("invalid package copy: %s", identifier)
		}
		source, err := safePath(copy.Value("source"), "package copy source")
		if err != nil {
			return err
		}
		destination, err := safePath(copy.Value("destination"), "package copy destination")
		if err != nil {
			return err
		}
		if source == destination || copyDestinations[destination] {
			return fmt.Errorf("duplicate or ineffective package copy: %s", identifier)
		}
		if !validSize(copy.Value("size")) {
			return fmt.Errorf("invalid package copy size: %s", identifier)
		}
		if !sha256Pattern.MatchString(pyjson.Repr(getOr(copy, "sha256", ""))) {
			return fmt.Errorf("invalid package copy hash: %s", identifier)
		}
		copyDestinations[destination] = true
	}
	replacements, ok := getOr(release, "text_replacements", []any{}).([]any)
	if !ok {
		return fmt.Errorf("invalid text replacements: %s", identifier)
	}
	if len(replacements) > 0 && strategy == "upstream-package" {
		return fmt.Errorf("text replacements require reference files: %s", identifier)
	}
	replacementIdentities := map[[2]string]bool{}
	for _, raw := range replacements {
		replacement, ok := raw.(*Object)
		if !ok {
			return fmt.Errorf("invalid text replacement: %s", identifier)
		}
		target, err := safePath(replacement.Value("target"), "text replacement target")
		if err != nil {
			return err
		}
		before, beforeOK := replacement.Value("before").(string)
		after, afterOK := replacement.Value("after").(string)
		if !beforeOK || before == "" || !afterOK || before == after {
			return fmt.Errorf("ineffective text replacement: %s", identifier)
		}
		if !sha256Pattern.MatchString(pyjson.Repr(getOr(replacement, "source_sha256", ""))) {
			return fmt.Errorf("invalid text replacement source hash: %s", identifier)
		}
		identity := [2]string{target, before}
		if replacementIdentities[identity] {
			return fmt.Errorf("duplicate text replacement: %s", identifier)
		}
		replacementIdentities[identity] = true
		if !positiveCount(replacement) {
			return fmt.Errorf("invalid text replacement count: %s", identifier)
		}
	}
	return nil
}

// ComponentForArtifactPath is the component whose release holds an
// artifact path.
func ComponentForArtifactPath(releases *Object, path string) (string, error) {
	components, _ := releases.Value("components").(*Object)
	var matches []string
	for _, identifier := range components.Keys() {
		release, ok := components.Value(identifier).(*Object)
		if !ok {
			continue
		}
		for _, artifact := range objects(release.Value("artifacts")) {
			if artifact.Value("distribution_path") == path {
				matches = append(matches, identifier)
			}
		}
	}
	if len(matches) > 1 {
		return "", fmt.Errorf("component release artifact is assigned more than once: %s", path)
	}
	if len(matches) == 0 {
		return "", nil
	}
	return matches[0], nil
}

// ReferenceRevisionOf is the nQuake revision a component consumes, or "" for
// a component that consumes nothing from the nQuake reference.
func ReferenceRevisionOf(releases *Object, identifier string) string {
	revision, _ := pyjson.Get(releases, "components", identifier, "reference_revision").(string)
	return revision
}

// ReferenceRevisions maps every nQuake consumer to the revision it pins.
func ReferenceRevisions(releases *Object) map[string]string {
	revisions := map[string]string{}
	if components, ok := releases.Value("components").(*Object); ok {
		for _, identifier := range components.Keys() {
			if revision := ReferenceRevisionOf(releases, identifier); revision != "" {
				revisions[identifier] = revision
			}
		}
	}
	return revisions
}
