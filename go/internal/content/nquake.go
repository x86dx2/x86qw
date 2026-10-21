package content

import (
	"fmt"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// nQuake is the base the distribution is built from. Every file of the last
// reviewed nQuake revision is recorded in maintenance/inventory/nquake.json
// and is either consumed by a component (a reference source in
// components.json) or declared unused with a reason. Any upstream change is
// therefore classified: a consumed file moves only its owner, an unused one
// is reported, and a file nobody classified must be classified.

const (
	NQuakeInventoryPath = "maintenance/inventory/nquake.json"
	nquakeManifestBytes = 8 * 1024 * 1024
)

// NQuakeFile is one file of an nQuake revision.
type NQuakeFile struct {
	Size    int64
	SHA256  string
	GitSHA1 string
}

// NQuakeUpstream is a whole nQuake revision: its Git tree checked against
// the manifest.json nQuake publishes with the SHA-256 of every file.
type NQuakeUpstream struct {
	Revision string
	Files    map[string]NQuakeFile
}

// NQuakeRule declares a path (file or directory) of nQuake as unused.
type NQuakeRule struct{ Path, Reason string }

func (r NQuakeRule) matches(path string) bool {
	return path == r.Path || strings.HasPrefix(path, r.Path+"/")
}

// NQuakeInventory is maintenance/inventory/nquake.json.
type NQuakeInventory struct {
	Repository, Revision string
	Unused               []NQuakeRule
	Files                map[string]NQuakeFile
}

// LoadNQuakeInventory reads and validates the inventory structure.
func LoadNQuakeInventory(path string) (*NQuakeInventory, error) {
	document, err := LoadJSONObject(path)
	if err != nil {
		return nil, err
	}
	invalid := func(detail string) (*NQuakeInventory, error) {
		return nil, fmt.Errorf("invalid nQuake inventory (%s): %s", detail, path)
	}
	if !sameKeys(document.Keys(), "format", "project", "repository", "revision", "unused", "files") ||
		!pyjson.EqInt(document.Value("format"), 1) || !pyjson.Eq(document.Value("project"), "x86qw") {
		return invalid("identity")
	}
	inventory := &NQuakeInventory{Files: map[string]NQuakeFile{}}
	inventory.Repository, _ = document.Value("repository").(string)
	inventory.Revision, _ = document.Value("revision").(string)
	if err := persistentURL(inventory.Repository, "nQuake repository"); err != nil || !hex40Strict.MatchString(inventory.Revision) {
		return invalid("repository or revision")
	}
	seen := map[string]bool{}
	for _, rule := range objects(document.Value("unused")) {
		path, pathOK := rule.Value("path").(string)
		reason, reasonOK := rule.Value("reason").(string)
		if !sameKeys(rule.Keys(), "path", "reason") || !pathOK || !reasonOK || strings.TrimSpace(reason) == "" || seen[path] {
			return invalid("unused rule")
		}
		if _, err := ValidatePortableRelativePath(path, "nQuake unused path"); err != nil {
			return invalid("unused rule " + path)
		}
		seen[path] = true
		inventory.Unused = append(inventory.Unused, NQuakeRule{path, reason})
	}
	files, ok := document.Value("files").(*Object)
	if !ok {
		return invalid("files")
	}
	for _, path := range files.Keys() {
		entry, ok := files.Value(path).(*Object)
		if !ok || !sameKeys(entry.Keys(), "size", "sha256", "git_sha1") {
			return invalid("file " + path)
		}
		digest, _ := entry.Value("sha256").(string)
		blob, _ := entry.Value("git_sha1").(string)
		if !pyjson.IsExactInt(entry.Value("size")) || int64Of(entry.Value("size")) < 0 ||
			!sha256Pattern.MatchString(digest) || !hex40Strict.MatchString(blob) {
			return invalid("file " + path)
		}
		inventory.Files[path] = NQuakeFile{int64Of(entry.Value("size")), digest, blob}
	}
	return inventory, nil
}

// Classify returns the component consuming path or the rule declaring it
// unused; both empty means the path is not classified.
func (inventory *NQuakeInventory) Classify(catalog *Object, path string) (string, *NQuakeRule, error) {
	owner, err := ComponentForSource(catalog, path, "reference")
	if err != nil {
		return "", nil, err
	}
	for index := range inventory.Unused {
		if inventory.Unused[index].matches(path) {
			return owner, &inventory.Unused[index], nil
		}
	}
	return owner, nil, nil
}

// Unclassified lists the files of upstream that no component consumes and
// no rule declares unused.
func (inventory *NQuakeInventory) Unclassified(catalog *Object, files map[string]NQuakeFile) ([]string, error) {
	var paths []string
	for _, path := range sortedKeys(files) {
		owner, rule, err := inventory.Classify(catalog, path)
		if err != nil {
			return nil, err
		}
		if owner == "" && rule == nil {
			paths = append(paths, path)
		}
	}
	return paths, nil
}

// ValidateNQuakeInventory requires every recorded file to be classified
// exactly once and every unused rule to cover at least one file.
func ValidateNQuakeInventory(inventory *NQuakeInventory, catalog *Object) error {
	used := map[string]bool{}
	for _, path := range sortedKeys(inventory.Files) {
		owner, rule, err := inventory.Classify(catalog, path)
		if err != nil {
			return err
		}
		switch {
		case owner != "" && rule != nil:
			return fmt.Errorf("nQuake file consumed by %s and declared unused: %s", owner, path)
		case owner == "" && rule == nil:
			return fmt.Errorf("nQuake file with neither a component nor a reason in %s: %s", NQuakeInventoryPath, path)
		case rule != nil:
			used[rule.Path] = true
		}
	}
	for _, rule := range inventory.Unused {
		if !used[rule.Path] {
			return fmt.Errorf("unused nQuake rule matches no file: %s", rule.Path)
		}
	}
	return nil
}

// UnusedChanges compares the unused files of upstream with the inventory,
// grouped by rule: new, changed and removed paths.
func (inventory *NQuakeInventory) UnusedChanges(catalog *Object, files map[string]NQuakeFile) (map[string][3][]string, error) {
	changes := map[string][3][]string{}
	add := func(rule string, kind int, path string) {
		group := changes[rule]
		group[kind] = append(group[kind], path)
		changes[rule] = group
	}
	for _, path := range sortedKeys(files) {
		owner, rule, err := inventory.Classify(catalog, path)
		if err != nil {
			return nil, err
		}
		if owner != "" || rule == nil {
			continue
		}
		previous, known := inventory.Files[path]
		switch {
		case !known:
			add(rule.Path, 0, path)
		case previous.SHA256 != files[path].SHA256 || previous.Size != files[path].Size:
			add(rule.Path, 1, path)
		}
	}
	for _, path := range sortedKeys(inventory.Files) {
		if _, present := files[path]; present {
			continue
		}
		if owner, rule, err := inventory.Classify(catalog, path); err != nil {
			return nil, err
		} else if owner == "" && rule != nil {
			add(rule.Path, 2, path)
		}
	}
	return changes, nil
}

// Document renders the inventory for upstream, keeping the unused rules.
func (inventory *NQuakeInventory) Document(upstream *NQuakeUpstream) *Object {
	rules := make([]any, len(inventory.Unused))
	for index, rule := range inventory.Unused {
		rules[index] = object("path", rule.Path, "reason", rule.Reason)
	}
	files := pyjson.NewObject()
	for _, path := range sortedKeys(upstream.Files) {
		file := upstream.Files[path]
		files.Set(path, object("size", pyjson.Number(fmt.Sprint(file.Size)), "sha256", file.SHA256, "git_sha1", file.GitSHA1))
	}
	return object("format", pyjson.Number("1"), "project", "x86qw", "repository", inventory.Repository,
		"revision", upstream.Revision, "unused", rules, "files", files)
}

// Same reports whether upstream is exactly the recorded revision content.
func (inventory *NQuakeInventory) Same(upstream *NQuakeUpstream) bool {
	if len(inventory.Files) != len(upstream.Files) {
		return false
	}
	for path, file := range upstream.Files {
		if inventory.Files[path] != file {
			return false
		}
	}
	return true
}

// nquakeRepositoryMetadata reports tree entries that are not distribution
// files: top-level entries outside every package and dotfiles.
func nquakeRepositoryMetadata(path string, packages map[string]bool) bool {
	parts := strings.Split(path, "/")
	if !packages[parts[0]] {
		return true
	}
	for _, part := range parts {
		if strings.HasPrefix(part, ".") {
			return true
		}
	}
	return false
}

// fetchNQuakeUpstream reads the manifest.json of revision and checks it
// against the Git tree of the same revision: every distribution file must
// appear in both with the same size.
func (p *Project) fetchNQuakeUpstream(revision string, tree []GitTreeEntry) (*NQuakeUpstream, error) {
	url := "https://raw.githubusercontent.com/" + nquakeRepository + "/" + revision + "/manifest.json"
	response, err := p.Metadata(http.MethodGet, url, nquakeManifestBytes, 60*time.Second, nil)
	if err != nil {
		return nil, fmt.Errorf("the nQuake manifest.json is unavailable: %v", err)
	}
	value, err := pyjson.Decode(response.Data, pyjson.RejectDuplicates)
	document, ok := value.(*Object)
	if err != nil || !ok || !pyjson.EqInt(document.Value("schema"), 1) {
		return nil, fmt.Errorf("invalid nQuake manifest.json at %s", revision[:12])
	}
	groups, ok := document.Value("packages").(*Object)
	if !ok {
		return nil, fmt.Errorf("the nQuake manifest.json has no packages at %s", revision[:12])
	}
	upstream := &NQuakeUpstream{Revision: revision, Files: map[string]NQuakeFile{}}
	packages := map[string]bool{}
	for _, name := range groups.Keys() {
		packages[name] = true
		group, ok := groups.Value(name).(*Object)
		if !ok {
			return nil, fmt.Errorf("invalid package in the nQuake manifest.json: %s", name)
		}
		for _, entry := range objects(group.Value("files")) {
			relative, _ := entry.Value("path").(string)
			path, err := ValidatePortableRelativePath(name+"/"+relative, "nQuake manifest path")
			digest, _ := entry.Value("sha256").(string)
			if err != nil || !pyjson.IsExactInt(entry.Value("size")) || int64Of(entry.Value("size")) < 0 || !sha256Pattern.MatchString(digest) {
				return nil, fmt.Errorf("invalid file in the nQuake manifest.json: %s/%s", name, relative)
			}
			if _, seen := upstream.Files[path]; seen {
				return nil, fmt.Errorf("repeated file in the nQuake manifest.json: %s", path)
			}
			upstream.Files[path] = NQuakeFile{Size: int64Of(entry.Value("size")), SHA256: digest}
		}
	}
	inTree := map[string]bool{}
	for _, entry := range tree {
		inTree[entry.Path] = true
		file, listed := upstream.Files[entry.Path]
		switch {
		case listed && file.Size != entry.Size:
			return nil, fmt.Errorf("the nQuake manifest.json differs from the Git tree: %s", entry.Path)
		case listed:
			file.GitSHA1 = entry.SHA1
			upstream.Files[entry.Path] = file
		case !nquakeRepositoryMetadata(entry.Path, packages):
			return nil, fmt.Errorf("the nQuake manifest.json does not list a file of the Git tree: %s", entry.Path)
		}
	}
	for path := range upstream.Files {
		if !inTree[path] {
			return nil, fmt.Errorf("the nQuake manifest.json lists a file missing from the Git tree: %s", path)
		}
	}
	return upstream, nil
}

// sortedRuleChanges renders UnusedChanges as report lines.
func sortedRuleChanges(changes map[string][3][]string) []string {
	var lines []string
	rules := make([]string, 0, len(changes))
	for rule := range changes {
		rules = append(rules, rule)
	}
	sort.Strings(rules)
	for _, rule := range rules {
		group := changes[rule]
		lines = append(lines, fmt.Sprintf("%s: %d new, %d changed, %d removed", rule, len(group[0]), len(group[1]), len(group[2])))
	}
	return lines
}

// NQuakeReport classifies the last discovered nQuake revision against the
// inventory: files nobody classified and changes to the unused files.
type NQuakeReport struct {
	Revision, Reviewed string
	Unclassified       []string
	Unused             map[string][3][]string
}

// Pending reports whether the inventory has to be refreshed or completed.
func (r *NQuakeReport) Pending() bool { return len(r.Unclassified) > 0 || len(r.Unused) > 0 }

func (p *Project) nquakeReport() (*NQuakeReport, *NQuakeInventory, error) {
	if p.nquake == nil {
		return nil, nil, nil
	}
	inventory, err := LoadNQuakeInventory(p.Path(NQuakeInventoryPath))
	if err != nil {
		return nil, nil, err
	}
	catalog, err := p.Components()
	if err != nil {
		return nil, nil, err
	}
	unclassified, err := inventory.Unclassified(catalog, p.nquake.Files)
	if err != nil {
		return nil, nil, err
	}
	changes, err := inventory.UnusedChanges(catalog, p.nquake.Files)
	if err != nil {
		return nil, nil, err
	}
	return &NQuakeReport{p.nquake.Revision, inventory.Revision, unclassified, changes}, inventory, nil
}

// Lines renders the report for the terminal.
func (r *NQuakeReport) Lines() []string {
	var lines []string
	for _, line := range sortedRuleChanges(r.Unused) {
		lines = append(lines, "[NQUAKE] unused by the distribution, "+line)
	}
	for _, path := range r.Unclassified {
		lines = append(lines, fmt.Sprintf("[CLASSIFY] %s: nQuake file with neither a component nor a reason in %s", path, NQuakeInventoryPath))
	}
	return lines
}

// Object renders the report for --json.
func (r *NQuakeReport) Object() *Object {
	unused := pyjson.NewObject()
	rules := make([]string, 0, len(r.Unused))
	for rule := range r.Unused {
		rules = append(rules, rule)
	}
	sort.Strings(rules)
	list := func(paths []string) []any {
		items := []any{}
		for _, path := range paths {
			items = append(items, path)
		}
		return items
	}
	for _, rule := range rules {
		group := r.Unused[rule]
		unused.Set(rule, object("new", list(group[0]), "changed", list(group[1]), "removed", list(group[2])))
	}
	return object("revision", r.Revision, "inventory_revision", r.Reviewed, "unclassified", list(r.Unclassified), "unused_changes", unused)
}
