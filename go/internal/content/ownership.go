package content

import (
	"fmt"
	"regexp"
	"slices"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
	"golang.org/x/text/unicode/norm"
)

// Release ownership facts: which files each package owns.

var (
	ownershipLicenses  = setOf("MIT", "NOASSERTION")
	ownershipValues    = setOf("project", "upstream", "mixed")
	ownershipKinds     = setOf("file", "archive", "metadata", "registered-game-data")
	ownershipBases     = setOf("project-source", "project-override", "generated-project-metadata", "build-output", "upstream-release", "registered-game-data", "composed-archive", "unclassified-candidate-input")
	immutableLicense   = regexp.MustCompile(`^https://github\.com/x86dx2/x86qw/blob/([A-Za-z0-9][A-Za-z0-9._-]*)/LICENSE$`)
	ownershipFields    = []string{"path", "size", "sha256", "kind", "ownership", "ownership_basis", "source", "license_concluded", "license_url", "copyright_text", "members"}
	ownershipMaxDepth  = 16
	ownershipMaxText   = 1024
	ownershipMaxBytes  = 512 * 1024 * 1024
	ownershipMaxEntrys = 100000
)

func ownershipError(format string, args ...any) error { return fmt.Errorf(format, args...) }

func hasControl(value string) bool {
	for _, r := range value {
		if r < 0x20 {
			return true
		}
	}
	return false
}

func ownershipPath(value any, label string) (string, error) {
	text, ok := value.(string)
	if !ok || text == "" || len([]rune(text)) > 512 {
		return "", ownershipError("invalid %s", label)
	}
	path := purePosix(text)
	if strings.ContainsAny(text, `\:`) || hasControl(text) || path.absolute || path.hasPart("..") {
		return "", ownershipError("%s is not a safe POSIX path: %s", label, pyRepr(text))
	}
	if len(path.parts) > ownershipMaxDepth || path.asPosix() != text {
		return "", ownershipError("%s exceeds the depth limit or is not canonical", label)
	}
	return text, nil
}

func ownershipText(value any, label string) (string, error) {
	text, ok := value.(string)
	if !ok || text == "" || len([]rune(text)) > ownershipMaxText {
		return "", ownershipError("invalid %s", label)
	}
	if hasControl(text) {
		return "", ownershipError("%s contains a control character", label)
	}
	return text, nil
}

func validateOwnershipEntry(value any, depth int, seen map[string]bool) (*Object, error) {
	if depth > ownershipMaxDepth {
		return nil, ownershipError("ownership exceeds the maximum depth")
	}
	raw, ok := value.(*Object)
	if !ok {
		return nil, ownershipError("invalid ownership entry")
	}
	if !sameKeys(raw.Keys(), ownershipFields...) {
		return nil, ownershipError("ownership entry has unknown or missing fields")
	}
	path, err := ownershipPath(raw.Value("path"), "path")
	if err != nil {
		return nil, err
	}
	semantic := Casefold(norm.NFC.String(path))
	if seen[semantic] {
		return nil, ownershipError("ownership has a duplicate member: %s", path)
	}
	seen[semantic] = true
	size := raw.Value("size")
	if order, _ := pyjson.IntCmp(size, 0); !pyjson.IsExactInt(size) || order < 0 {
		return nil, ownershipError("size of %s exceeds the allowed limit", path)
	}
	if order, _ := pyjson.IntCmp(size, int64(ownershipMaxBytes)); order > 0 {
		return nil, ownershipError("size of %s exceeds the allowed limit", path)
	}
	if digest, ok := raw.Value("sha256").(string); !ok || !sha256Pattern.MatchString(digest) {
		return nil, ownershipError("sha256 of %s must be a lowercase hexadecimal SHA-256", path)
	}
	if !inSet(raw.Value("kind"), ownershipKinds) {
		return nil, ownershipError("invalid kind in %s", path)
	}
	if !inSet(raw.Value("ownership"), ownershipValues) {
		return nil, ownershipError("invalid ownership in %s", path)
	}
	if !inSet(raw.Value("ownership_basis"), ownershipBases) {
		return nil, ownershipError("invalid ownership_basis in %s", path)
	}
	if _, err := ownershipText(raw.Value("source"), "source of "+path); err != nil {
		return nil, err
	}
	if !inSet(raw.Value("license_concluded"), ownershipLicenses) {
		return nil, ownershipError("invalid license_concluded in %s", path)
	}
	copyright, err := ownershipText(raw.Value("copyright_text"), "copyright_text of "+path)
	if err != nil {
		return nil, err
	}
	members, ok := raw.Value("members").([]any)
	if !ok || len(members) > ownershipMaxEntrys {
		return nil, ownershipError("invalid members in %s", path)
	}
	var children []*Object
	childPaths := map[string]bool{}
	for _, child := range members {
		if _, ok := child.(*Object); !ok {
			return nil, ownershipError("invalid member in %s", path)
		}
		entry, err := validateOwnershipEntry(child, depth+1, childPaths)
		if err != nil {
			return nil, err
		}
		children = append(children, entry)
	}
	ownership := raw.Value("ownership").(string)
	if len(children) > 0 {
		kinds := map[string]bool{}
		for _, child := range children {
			kinds[child.Value("ownership").(string)] = true
		}
		expected := "mixed"
		if len(kinds) == 1 && kinds["project"] {
			expected = "project"
		} else if len(kinds) == 1 && kinds["upstream"] {
			expected = "upstream"
		}
		if ownership != expected {
			return nil, ownershipError("ownership of %s does not match its members: %s != %s", path, ownership, expected)
		}
	}
	license := raw.Value("license_concluded")
	if ownership == "project" {
		if license != "MIT" || copyright != projectCopyright {
			return nil, ownershipError("invalid project ownership in %s", path)
		}
	} else if license != "NOASSERTION" || copyright != "NOASSERTION" {
		return nil, ownershipError("ownership %s must be NOASSERTION in %s", ownership, path)
	}
	licenseURL := raw.Value("license_url")
	if ownership != "project" {
		if licenseURL != nil {
			return nil, ownershipError("upstream/mixed ownership cannot carry the project license URL")
		}
	} else {
		text, ok := licenseURL.(string)
		match := immutableLicense.FindStringSubmatch(text)
		if !ok || match == nil {
			return nil, ownershipError("project ownership requires an immutable MIT license URL")
		}
		if slices.Contains([]string{"main", "master", "head", "latest"}, Casefold(match[1])) {
			return nil, ownershipError("a license URL cannot point at a mutable reference")
		}
	}
	sort.SliceStable(children, func(i, j int) bool {
		return children[i].Value("path").(string) < children[j].Value("path").(string)
	})
	sortedChildren := make([]any, len(children))
	for index, child := range children {
		sortedChildren[index] = child
	}
	return object(
		"path", path, "size", size, "sha256", raw.Value("sha256"), "kind", raw.Value("kind"),
		"ownership", ownership, "ownership_basis", raw.Value("ownership_basis"), "source", raw.Value("source"),
		"license_concluded", license, "license_url", licenseURL, "copyright_text", copyright,
		"members", sortedChildren,
	), nil
}

// ValidateOwnership checks an ownership document.
func ValidateOwnership(value any) (*Object, error) {
	raw, ok := value.(*Object)
	if !ok || !sameKeys(raw.Keys(), "format", "project", "artifacts") {
		return nil, ownershipError("ownership document has unknown or missing fields")
	}
	if !pyjson.EqInt(raw.Value("format"), 1) || !pyjson.Eq(raw.Value("project"), "x86qw") {
		return nil, ownershipError("invalid ownership document identity")
	}
	artifacts, ok := raw.Value("artifacts").([]any)
	if !ok || len(artifacts) == 0 || len(artifacts) > ownershipMaxEntrys {
		return nil, ownershipError("ownership document has no valid artifacts")
	}
	var entries []*Object
	paths, semantic := map[string]bool{}, map[string]bool{}
	for _, artifact := range artifacts {
		entry, err := validateOwnershipEntry(artifact, 0, map[string]bool{})
		if err != nil {
			return nil, err
		}
		path := entry.Value("path").(string)
		key := Casefold(norm.NFC.String(path))
		if paths[path] || semantic[key] {
			return nil, ownershipError("ownership document has a duplicate artifact: %s", path)
		}
		paths[path], semantic[key] = true, true
		entries = append(entries, entry)
	}
	sort.SliceStable(entries, func(i, j int) bool {
		return entries[i].Value("path").(string) < entries[j].Value("path").(string)
	})
	sorted := make([]any, len(entries))
	for index, entry := range entries {
		sorted[index] = entry
	}
	return object("format", pyjson.Number("1"), "project", "x86qw", "artifacts", sorted), nil
}

// MergeOwnership merges ownership documents, refusing a path two packages
// claim.
func MergeOwnership(documents ...*Object) (*Object, error) {
	combined := map[string]*Object{}
	var order []string
	for _, document := range documents {
		normalized, err := ValidateOwnership(document)
		if err != nil {
			return nil, err
		}
		for _, entry := range objects(normalized.Value("artifacts")) {
			path := entry.Value("path").(string)
			if previous, ok := combined[path]; ok && !pyjson.Eq(previous, entry) {
				return nil, ownershipError("ownership fragments differ for %s", path)
			}
			if _, ok := combined[path]; !ok {
				order = append(order, path)
			}
			combined[path] = entry
		}
	}
	if len(combined) == 0 {
		return nil, ownershipError("no ownership artifact was declared")
	}
	artifacts := make([]any, len(order))
	for index, path := range order {
		artifacts[index] = combined[path]
	}
	return ValidateOwnership(object("format", pyjson.Number("1"), "project", "x86qw", "artifacts", artifacts))
}

// WriteOwnership writes an ownership document.
func WriteOwnership(path string, document *Object) error {
	normalized, err := ValidateOwnership(document)
	if err != nil {
		return err
	}
	payload, err := pyjson.EncodeFile(normalized, true)
	if err != nil {
		return err
	}
	return writeAtomic(path, payload, 0o644)
}
