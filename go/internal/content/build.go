package content

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Deterministic package builds: the component packages, the core package
// and their registration in the catalog.

const (
	gitlabProjectID  = "84813414"
	coreVersion      = "0.1.1"
	corePackage      = "x86qw-core-id1"
	coreReleaseTag   = "x86qw-content-core-" + coreVersion
	maxCorePakBytes  = 128 * 1024 * 1024
	projectCopyright = "Copyright (c) 2026 x86dx2"
	componentJSON    = "_x86qw/component.json"
)

var projectRefPattern = regexp.MustCompile(`^[0-9a-f]{40}$`)

// GitHubReleaseURL is the primary mirror URL of a release asset.
func GitHubReleaseURL(tag, filename string) string {
	return "https://github.com/" + PrimaryRepository + "/releases/download/" + tag + "/" + filename
}

// GitLabPackageURL is the GitLab package registry URL of an artifact.
func GitLabPackageURL(packageName, version, filename string) string {
	return "https://gitlab.com/api/v4/projects/" + gitlabProjectID + "/packages/generic/" + packageName + "/" + version + "/" + filename
}

func projectLicenseURL(ref string) string {
	return "https://github.com/x86dx2/x86qw/blob/" + ref + "/LICENSE"
}

func ownershipEntry(path string, payload []byte, kind, ownership, basis, source string, licenseURL any, copyright string, members []any) *Object {
	license := "NOASSERTION"
	if ownership == "project" {
		license = "MIT"
	}
	if members == nil {
		members = []any{}
	}
	return object(
		"path", path, "size", intNumber(len(payload)), "sha256", sha256Hex(payload), "kind", kind,
		"ownership", ownership, "ownership_basis", basis, "source", source,
		"license_concluded", license, "license_url", licenseURL, "copyright_text", copyright,
		"members", members,
	)
}

func intNumber(value int) pyjson.Number { return pyjson.Number(fmt.Sprint(value)) }

func memberOwnership(overrides []*Object, commit string) (string, string, any, string) {
	mixed, project := false, false
	for _, item := range overrides {
		switch item.Value("ownership") {
		case "mixed":
			mixed = true
		case "project":
			project = true
		}
		if item.Value("archive_member_override") == true {
			mixed = true
		}
	}
	if mixed {
		return "mixed", "composed-archive", nil, "NOASSERTION"
	}
	if project {
		return "project", "project-override", projectLicenseURL(commit), projectCopyright
	}
	return "upstream", "upstream-release", nil, "NOASSERTION"
}

// componentPackageMetadata is the internal manifest of a component package.
func componentPackageMetadata(identifier, version, strategy, sourceRevision string, members []any, referenceRevision string) *Object {
	metadata := object("format", pyjson.Number("1"), "project", "x86qw", "package", identifier, "members", orEmpty(members))
	if strategy == "upstream-package" || strategy == "upstream-composed" {
		metadata.Set("source_revision", sourceRevision)
	} else {
		metadata.Set("source_commit", sourceRevision)
	}
	if referenceRevision != "" {
		metadata.Set("source_commit", referenceRevision)
	}
	if version != sourceRevision[:min(12, len(sourceRevision))] {
		metadata.Set("version", version)
	}
	return metadata
}

// writeArtifact publishes payload at target without replacing different
// bytes (publish_verified_file's conflict rule) and validates ZIP output.
func writeArtifact(target string, payload []byte, required []string, conflict string) error {
	if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
		return err
	}
	if existing, err := os.ReadFile(target); err == nil {
		if !bytes.Equal(existing, payload) {
			return fmt.Errorf("%s", conflict)
		}
		return nil
	}
	if required != nil {
		if _, err := ReadZipMembers(payload, "package"); err != nil {
			return fmt.Errorf("component package failed canonical archive validation: %s: %v", target, err)
		}
	}
	file, err := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
	if err != nil {
		return err
	}
	_, writeErr := file.Write(payload)
	if err := file.Sync(); writeErr == nil {
		writeErr = err
	}
	if err := file.Close(); writeErr == nil {
		writeErr = err
	}
	return writeErr
}

// BuildOptions configures BuildPackages.
type BuildOptions struct {
	ProjectRef      string // required with OwnershipOutput
	OwnershipOutput string
}

// BuildPackages builds every component package from the distribution.
func (p *Project) BuildPackages(distribution, output string, options BuildOptions) (*Object, error) {
	catalog, err := p.Components()
	if err != nil {
		return nil, err
	}
	releases, err := p.Releases()
	if err != nil {
		return nil, err
	}
	context, err := LoadSourceContext(distribution, catalog, releases)
	if err != nil {
		return nil, err
	}
	if options.OwnershipOutput != "" && options.ProjectRef == "" {
		return nil, fmt.Errorf("--project-ref é obrigatório quando --ownership-output é usado")
	}
	if options.ProjectRef != "" && !projectRefPattern.MatchString(options.ProjectRef) {
		return nil, fmt.Errorf("project-ref deve ser um SHA-1 hexadecimal minúsculo completo")
	}
	// One build carries every nQuake revision the components pin; each
	// package records and is released under its own.
	revisions := strings.Join(context.Revisions(), "+")
	buildID := "components-" + revisions
	releaseRoot := filepath.Join(output, buildID)
	var packages, ownershipEntries []any
	for _, identifier := range context.ComponentIDs {
		commit := context.RevisionOf(identifier)
		referenceRelease := "nquake-" + commit
		release, sourceRevision, payloads, err := context.ResolveComponentPayloads(identifier)
		if err != nil {
			return nil, err
		}
		version := pyjson.Repr(release.Value("version"))
		strategy := pyjson.Repr(release.Value("strategy"))
		filename := identifier + "-" + version + ".zip"
		artifact := filepath.Join(releaseRoot, filename)
		var members, ownershipMembers []any
		var entries []ZipEntry
		required := []string{}
		for _, payload := range payloads {
			member := object("path", payload.Member, "sha256", sha256Hex(payload.Data), "source", payload.Source)
			if len(payload.Overrides) > 0 {
				overrides := make([]any, len(payload.Overrides))
				for index, item := range payload.Overrides {
					overrides[index] = item
				}
				member.Set("overrides", overrides)
			}
			members = append(members, member)
			if options.OwnershipOutput != "" {
				ownership, basis, license, copyright := memberOwnership(payload.Overrides, options.ProjectRef)
				kind := "file"
				if folded := Casefold(payload.Member); strings.HasSuffix(folded, ".zip") || strings.HasSuffix(folded, ".pk3") {
					kind = "archive"
				}
				ownershipMembers = append(ownershipMembers, ownershipEntry(payload.Member, payload.Data, kind, ownership, basis, payload.Source, license, copyright, nil))
			}
			entries = append(entries, ZipEntry{payload.Member, payload.Data})
			required = append(required, payload.Member)
		}
		reference := ""
		if pyjson.Truthy(release.Value("archive_layers")) {
			reference = commit
		}
		metadata, err := pyjson.EncodeFile(componentPackageMetadata(identifier, version, strategy, sourceRevision, members, reference), true)
		if err != nil {
			return nil, err
		}
		if options.OwnershipOutput != "" {
			ownershipMembers = append(ownershipMembers, ownershipEntry(componentJSON, metadata, "metadata", "project",
				"generated-project-metadata", "generated:component-metadata", projectLicenseURL(options.ProjectRef), projectCopyright, nil))
		}
		entries = append(entries, ZipEntry{componentJSON, metadata})
		packageBytes, err := BuildZip(entries)
		if err != nil {
			return nil, err
		}
		if err := writeArtifact(artifact, packageBytes, append(required, componentJSON), "component package target already differs: "+artifact); err != nil {
			return nil, err
		}
		if options.OwnershipOutput != "" {
			ownership := "project"
			for _, entry := range ownershipMembers {
				if entry.(*Object).Value("ownership") != "project" {
					ownership = "mixed"
				}
			}
			basis, license, copyright := "composed-archive", any(nil), "NOASSERTION"
			if ownership == "project" {
				basis, license, copyright = "build-output", projectLicenseURL(options.ProjectRef), projectCopyright
			}
			relative, _ := filepath.Rel(output, artifact)
			ownershipEntries = append(ownershipEntries, ownershipEntry("content/"+filepath.ToSlash(relative), packageBytes,
				"archive", ownership, basis, "build-component-package", license, copyright, ownershipMembers))
		}
		distributionTag := pyjson.Repr(getOr(release, "distribution_tag", referenceRelease))
		mirror := GitHubReleaseURL(distributionTag, filename)
		distributionComponent := pyjson.Repr(getOr(release, "distribution_component", "nquake"))
		title := "x86QW Content · " + pyjson.Repr(context.Components[identifier].Value("label")) + " " + version
		if distributionTag == referenceRelease && distributionComponent == "nquake" {
			title = "x86QW Content · nQuake " + commit[:12]
		}
		var sourceURLs []any
		if (strategy != "upstream-package" && strategy != "upstream-composed") || pyjson.Truthy(release.Value("archive_layers")) {
			sourceURLs = append(sourceURLs, "https://github.com/nQuake/distfiles/tree/"+commit)
		}
		upstream, hasUpstream := release.Value("upstream").(*Object)
		if hasUpstream {
			for _, key := range []string{"source_url", "release_url"} {
				if url, ok := upstream.Value(key).(string); ok {
					sourceURLs = append(sourceURLs, url)
				}
			}
		}
		for _, url := range getOr(release, "source_mirrors", []any{}).([]any) {
			sourceURLs = append(sourceURLs, pyjson.Repr(url))
		}
		record := object(
			"component", distributionComponent, "package", identifier, "version", version,
			"channel", "content", "platform", "any", "architecture", "any", "filename", filename,
			"size", intNumber(len(packageBytes)), "sha256", sha256Hex(packageBytes), "origin_url", mirror,
			"license", pyjson.Repr(getOr(release, "license", "upstream-distfiles-terms")),
			"license_url", pyjson.Repr(getOr(release, "license_url", "https://github.com/nQuake/distfiles/tree/"+commit)),
			"source_urls", orEmpty(sourceURLs), "redistribution_reviewed", true,
			"urls", []any{mirror, GitLabPackageURL(identifier, version, filename)},
			"mirror_title", title, "mirror_notes", "Pacotes de conteúdo versionados da distribuição x86QW.",
			"mirror_latest", false,
		)
		if strategy == "upstream-package" || strategy == "upstream-composed" {
			record.Set("source_revision", sourceRevision)
			if pyjson.Truthy(release.Value("archive_layers")) {
				record.Set("source_commit", commit)
			}
		} else {
			record.Set("source_commit", commit)
		}
		if hasUpstream {
			record.Set("release_url", upstream.Value("release_url"))
			record.Set("release_notes", pyjson.Repr(getOr(release, "notes", "")))
			record.Set("upstream_version", upstream.Value("release"))
		}
		packages = append(packages, record)
	}
	manifest := object(
		"format", pyjson.Number("1"), "project", "x86qw", "release", buildID, "source_commit", revisions,
		"release_inventory", filepath.Base(ReleasesPath), "packages", orEmpty(packages),
	)
	manifestBytes, err := pyjson.EncodeFile(manifest, true)
	if err != nil {
		return nil, err
	}
	manifestPath := filepath.Join(releaseRoot, "manifest.json")
	if err := writeArtifact(manifestPath, manifestBytes, nil, "component manifest already differs: "+manifestPath); err != nil {
		return nil, err
	}
	if options.OwnershipOutput != "" {
		relative, _ := filepath.Rel(output, manifestPath)
		ownershipEntries = append(ownershipEntries, ownershipEntry("content/"+filepath.ToSlash(relative), manifestBytes, "metadata", "project",
			"generated-project-metadata", "generated:component-manifest", projectLicenseURL(options.ProjectRef), projectCopyright, nil))
		document, err := ValidateOwnership(object("format", pyjson.Number("1"), "project", "x86qw", "artifacts", ownershipEntries))
		if err != nil {
			return nil, err
		}
		if err := WriteOwnership(options.OwnershipOutput, document); err != nil {
			return nil, err
		}
	}
	return manifest, nil
}

// BuildCorePackage builds the core package and its ownership record.
func (p *Project) BuildCorePackage(distribution, output, ownershipOutput string) (*Object, error) {
	type source struct {
		name    string
		payload []byte
		digest  string
	}
	var sources []source
	for _, name := range []string{"pak0.pak", "pak1.pak"} {
		path := filepath.Join(distribution, "game-data", "id1", name)
		info, err := os.Lstat(path)
		if err != nil || !info.Mode().IsRegular() {
			return nil, fmt.Errorf("build input must be a regular non-symlink file: %s", path)
		}
		if info.Size() > maxCorePakBytes {
			return nil, fmt.Errorf("build input exceeds the %d-byte limit: %s", maxCorePakBytes, path)
		}
		payload, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		if !bytes.HasPrefix(payload, []byte("PACK")) {
			return nil, fmt.Errorf("core game data is not a PAK: %s", path)
		}
		sources = append(sources, source{name, payload, sha256Hex(payload)})
	}
	var lines strings.Builder
	for _, item := range sources {
		lines.WriteString(item.name + "\t" + item.digest + "\n")
	}
	revision := sha256Hex([]byte(lines.String()))
	filename := corePackage + "-" + coreVersion + ".zip"
	artifact := filepath.Join(output, "core-"+coreVersion, filename)
	var members, ownershipMembers []any
	var entries []ZipEntry
	for _, item := range sources {
		member := "payload/id1/" + item.name
		origin := "dist/game-data/id1/" + item.name
		members = append(members, object(
			"path", member, "sha256", item.digest, "source", origin, "ownership", "upstream",
			"ownership_basis", "registered-game-data", "license_concluded", "NOASSERTION",
			"license_url", nil, "copyright_text", "NOASSERTION",
		))
		ownershipMembers = append(ownershipMembers, ownershipEntry(member, item.payload, "registered-game-data", "upstream",
			"registered-game-data", origin, nil, "NOASSERTION", nil))
		entries = append(entries, ZipEntry{member, item.payload})
	}
	metadata, err := pyjson.EncodeFile(object(
		"format", pyjson.Number("1"), "project", "x86qw", "package", corePackage, "version", coreVersion,
		"source_revision", revision, "members", members,
	), true)
	if err != nil {
		return nil, err
	}
	entries = append(entries, ZipEntry{componentJSON, metadata})
	packageBytes, err := BuildZip(entries)
	if err != nil {
		return nil, err
	}
	if err := writeArtifact(artifact, packageBytes, []string{componentJSON}, "core package target already differs: "+artifact); err != nil {
		return nil, err
	}
	relative, _ := filepath.Rel(output, artifact)
	license := "https://github.com/x86dx2/x86qw/blob/" + coreReleaseTag + "/LICENSE"
	ownershipMembers = append(ownershipMembers, ownershipEntry(componentJSON, metadata, "metadata", "project",
		"generated-project-metadata", "generated:component-metadata", license, projectCopyright, nil))
	document, err := ValidateOwnership(object("format", pyjson.Number("1"), "project", "x86qw", "artifacts", []any{
		ownershipEntry("content/"+filepath.ToSlash(relative), packageBytes, "archive", "mixed", "composed-archive",
			"build-core-package", nil, "NOASSERTION", ownershipMembers),
	}))
	if err != nil {
		return nil, err
	}
	if ownershipOutput != "" {
		if err := WriteOwnership(ownershipOutput, document); err != nil {
			return nil, err
		}
	}
	mirror := GitHubReleaseURL(coreReleaseTag, filename)
	return object(
		"component", "core", "package", corePackage, "version", coreVersion, "channel", "content",
		"platform", "any", "architecture", "any", "filename", filename,
		"size", intNumber(len(packageBytes)), "sha256", sha256Hex(packageBytes), "origin_url", mirror,
		"license", "id-software-registered-game-data", "license_url", license,
		"source_urls", []any{"https://github.com/x86dx2/x86qw/tree/main/dist/game-data/id1"},
		"redistribution_reviewed", true, "urls", []any{mirror, GitLabPackageURL(corePackage, coreVersion, filename)},
		"source_revision", revision, "mirror_title", "x86QW Content · Dados base "+coreVersion,
		"mirror_notes", "Dados base obrigatórios usados pelo instalador x86QW.", "mirror_latest", false,
	), nil
}

var publicationFields = []string{"component", "origin_url", "release_url", "urls", "mirror_title", "mirror_notes", "mirror_latest"}

func packageKey(item *Object) any {
	if item.Has("package") {
		return item.Value("package")
	}
	return item.Value("component")
}

// RegisterPackages writes the built packages into the catalog; now is the
// generated_at clock.
func (p *Project) RegisterPackages(catalogPath string, manifest *Object, now time.Time) error {
	value, err := ReadJSON(catalogPath)
	if err != nil {
		return err
	}
	catalog, _ := value.(*Object)
	if catalog == nil {
		return fmt.Errorf("catalog packages must be a list")
	}
	packages, ok := catalog.Value("packages").([]any)
	if !ok {
		return fmt.Errorf("catalog packages must be a list")
	}
	manifestPackages := objects(manifest.Value("packages"))
	current := map[string]bool{}
	for _, item := range manifestPackages {
		current[pyjson.Repr(item.Value("package"))] = true
	}
	var kept []any
	for _, raw := range packages {
		item, ok := raw.(*Object)
		key := ""
		if ok {
			key = pyjson.Repr(packageKey(item))
		}
		if ok && pyjson.Eq(item.Value("channel"), "content") && !(pyjson.IsStr(packageKey(item)) && current[key]) {
			continue
		}
		kept = append(kept, raw)
	}
	packages = kept
	for _, pkg := range manifestPackages {
		identity := []any{pkg.Value("package"), pkg.Value("version")}
		var existing []*Object
		for _, raw := range packages {
			if item, ok := raw.(*Object); ok && pyjson.Eq([]any{packageKey(item), item.Value("version")}, identity) {
				existing = append(existing, item)
			}
		}
		if len(existing) > 0 {
			candidate := existing[0]
			same := true
			for _, key := range pkg.Keys() {
				if !contains(publicationFields, key) && !pyjson.Eq(candidate.Value(key), pkg.Value(key)) {
					same = false
				}
			}
			if len(existing) != 1 || !same {
				return fmt.Errorf("published package identity changed: (%s, %s)", pyRepr(identity[0]), pyRepr(identity[1]))
			}
			candidate.Set("component", pkg.Value("component"))
			for _, field := range publicationFields {
				if pkg.Has(field) {
					candidate.Set(field, pkg.Value(field))
				}
			}
			candidate.Delete("distribution_path")
			continue
		}
		var filtered []any
		for _, raw := range packages {
			item, ok := raw.(*Object)
			if ok && pyjson.Eq([]any{packageKey(item), item.Value("channel"), item.Value("platform"), item.Value("architecture")},
				[]any{pkg.Value("package"), pkg.Value("channel"), pkg.Value("platform"), pkg.Value("architecture")}) {
				continue
			}
			filtered = append(filtered, raw)
		}
		packages = append(filtered, pkg)
	}
	key := func(raw any) [4]string {
		item := raw.(*Object)
		return [4]string{
			pyjson.Repr(getOr(item, "component", "")), pyjson.Repr(getOr(item, "package", getOr(item, "component", ""))),
			pyjson.Repr(getOr(item, "version", "")), pyjson.Repr(getOr(item, "platform", "")),
		}
	}
	// The CLI binaries stay last, in the order x86qw-release prepare
	// writes them, so both tools produce the same catalog bytes.
	var content, cli []any
	for _, raw := range packages {
		if item, ok := raw.(*Object); ok && IsCLIPackage(item) {
			cli = append(cli, raw)
		} else {
			content = append(content, raw)
		}
	}
	sort.SliceStable(content, func(i, j int) bool {
		a, b := key(content[i]), key(content[j])
		for index := range a {
			if a[index] != b[index] {
				return a[index] < b[index]
			}
		}
		return false
	})
	packages = append(content, cli...)
	catalog.Set("packages", orEmpty(packages))
	catalog.Set("generated_at", now.UTC().Format("2006-01-02T15:04:05Z"))
	if _, err := p.ValidateCatalog(catalog); err != nil {
		return err
	}
	return WriteJSON(catalogPath, catalog)
}

// NormalizeCatalogReleaseMetadata brings the catalog's release metadata to
// its canonical form.
func (p *Project) NormalizeCatalogReleaseMetadata(catalog *Object) error {
	packages, ok := catalog.Value("packages").([]any)
	if !ok {
		return fmt.Errorf("catalogo invalido ao normalizar metadados de release")
	}
	setDefault := func(item *Object, key string, value any) {
		if !item.Has(key) {
			item.Set(key, value)
		}
	}
	for _, raw := range packages {
		item, ok := raw.(*Object)
		if !ok {
			continue
		}
		component := pyjson.Repr(getOr(item, "component", ""))
		version := pyjson.Repr(getOr(item, "version", ""))
		switch component {
		case "ezquake":
			channel := pyjson.Repr(getOr(item, "channel", ""))
			setDefault(item, "mirror_title", "x86QW Content · ezQuake "+channel+" "+version)
			setDefault(item, "mirror_notes", "Cliente ezQuake "+channel+" "+version+" preservado pela distribuição x86QW.")
			item.Set("mirror_latest", false)
		default:
			item.Set("mirror_latest", false)
		}
	}
	_, err := p.ValidateCatalog(catalog)
	return err
}
