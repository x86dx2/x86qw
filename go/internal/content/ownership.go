package content

import (
	"fmt"
	"regexp"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
	"golang.org/x/text/unicode/norm"
)

// Release ownership facts (maintenance/tools/release_ownership.py).

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
		return "", ownershipError("%s inválido", label)
	}
	path := purePosix(text)
	if strings.ContainsAny(text, `\:`) || hasControl(text) || path.absolute || path.hasPart("..") {
		return "", ownershipError("%s não é um caminho POSIX seguro: %s", label, pyRepr(text))
	}
	if len(path.parts) > ownershipMaxDepth || path.asPosix() != text {
		return "", ownershipError("%s excede o limite de profundidade ou não é canônico", label)
	}
	return text, nil
}

func ownershipText(value any, label string) (string, error) {
	text, ok := value.(string)
	if !ok || text == "" || len([]rune(text)) > ownershipMaxText {
		return "", ownershipError("%s inválido", label)
	}
	if hasControl(text) {
		return "", ownershipError("%s contém controle", label)
	}
	return text, nil
}

func validateOwnershipEntry(value any, depth int, seen map[string]bool) (*Object, error) {
	if depth > ownershipMaxDepth {
		return nil, ownershipError("ownership excede a profundidade máxima")
	}
	raw, ok := value.(*Object)
	if !ok {
		return nil, ownershipError("entrada de ownership inválida")
	}
	if !sameKeys(raw.Keys(), ownershipFields...) {
		return nil, ownershipError("entrada de ownership contém campos desconhecidos ou ausentes")
	}
	path, err := ownershipPath(raw.Value("path"), "path")
	if err != nil {
		return nil, err
	}
	semantic := Casefold(norm.NFC.String(path))
	if seen[semantic] {
		return nil, ownershipError("ownership contém membro duplicado: %s", path)
	}
	seen[semantic] = true
	size := raw.Value("size")
	if order, _ := pyjson.IntCmp(size, 0); !pyjson.IsExactInt(size) || order < 0 {
		return nil, ownershipError("size de %s excede o limite permitido", path)
	}
	if order, _ := pyjson.IntCmp(size, int64(ownershipMaxBytes)); order > 0 {
		return nil, ownershipError("size de %s excede o limite permitido", path)
	}
	if digest, ok := raw.Value("sha256").(string); !ok || !hex64Pattern.MatchString(digest) {
		return nil, ownershipError("sha256 de %s precisa ser SHA-256 hexadecimal minúsculo", path)
	}
	if !inSet(raw.Value("kind"), ownershipKinds) {
		return nil, ownershipError("kind inválido em %s", path)
	}
	if !inSet(raw.Value("ownership"), ownershipValues) {
		return nil, ownershipError("ownership inválido em %s", path)
	}
	if !inSet(raw.Value("ownership_basis"), ownershipBases) {
		return nil, ownershipError("ownership_basis inválido em %s", path)
	}
	if _, err := ownershipText(raw.Value("source"), "source de "+path); err != nil {
		return nil, err
	}
	if !inSet(raw.Value("license_concluded"), ownershipLicenses) {
		return nil, ownershipError("license_concluded inválido em %s", path)
	}
	copyright, err := ownershipText(raw.Value("copyright_text"), "copyright_text de "+path)
	if err != nil {
		return nil, err
	}
	members, ok := raw.Value("members").([]any)
	if !ok || len(members) > ownershipMaxEntrys {
		return nil, ownershipError("members inválido em %s", path)
	}
	var children []*Object
	childPaths := map[string]bool{}
	for _, child := range members {
		if _, ok := child.(*Object); !ok {
			return nil, ownershipError("member inválido em %s", path)
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
			return nil, ownershipError("ownership de %s não corresponde aos membros: %s != %s", path, ownership, expected)
		}
	}
	license := raw.Value("license_concluded")
	if ownership == "project" {
		if license != "MIT" || copyright != projectCopyright {
			return nil, ownershipError("ownership project inválido em %s", path)
		}
	} else if license != "NOASSERTION" || copyright != "NOASSERTION" {
		return nil, ownershipError("ownership %s precisa ser NOASSERTION em %s", ownership, path)
	}
	licenseURL := raw.Value("license_url")
	if ownership != "project" {
		if licenseURL != nil {
			return nil, ownershipError("ownership upstream/mixed não pode carregar URL de licença do projeto")
		}
	} else {
		text, ok := licenseURL.(string)
		match := immutableLicense.FindStringSubmatch(text)
		if !ok || match == nil {
			return nil, ownershipError("ownership project exige URL imutável da licença MIT")
		}
		if contains([]string{"main", "master", "head", "latest"}, Casefold(match[1])) {
			return nil, ownershipError("URL de licença não pode apontar para referência mutável")
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

// ValidateOwnership mirrors release_ownership.validate_document.
func ValidateOwnership(value any) (*Object, error) {
	raw, ok := value.(*Object)
	if !ok || !sameKeys(raw.Keys(), "format", "project", "artifacts") {
		return nil, ownershipError("ownership document contém campos desconhecidos ou ausentes")
	}
	if !pyjson.EqInt(raw.Value("format"), 1) || !pyjson.Eq(raw.Value("project"), "x86qw") {
		return nil, ownershipError("identidade do ownership document inválida")
	}
	artifacts, ok := raw.Value("artifacts").([]any)
	if !ok || len(artifacts) == 0 || len(artifacts) > ownershipMaxEntrys {
		return nil, ownershipError("ownership document não possui artifacts válidos")
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
			return nil, ownershipError("ownership document contém artifact duplicado: %s", path)
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

// MergeOwnership mirrors release_ownership.merge_documents.
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
				return nil, ownershipError("fragmentos de ownership divergem para %s", path)
			}
			if _, ok := combined[path]; !ok {
				order = append(order, path)
			}
			combined[path] = entry
		}
	}
	if len(combined) == 0 {
		return nil, ownershipError("nenhum artifact de ownership foi declarado")
	}
	artifacts := make([]any, len(order))
	for index, path := range order {
		artifacts[index] = combined[path]
	}
	return ValidateOwnership(object("format", pyjson.Number("1"), "project", "x86qw", "artifacts", artifacts))
}

// WriteOwnership mirrors release_ownership.write_document.
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
