package content

import (
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Mirror recipes (maintenance/recipes).

// ValidateRecipe checks a recipe and returns its review status.
func (p *Project) ValidateRecipe(value any, label string) (string, error) {
	recipe, ok := value.(*Object)
	if !ok {
		return "", fmt.Errorf("%s must be a JSON object", label)
	}
	if !pyjson.EqInt(recipe.Value("format"), 1) || !pyjson.Eq(recipe.Value("project"), "x86qw") {
		return "", fmt.Errorf("%s has an unsupported identity or format", label)
	}
	if !pyjson.Eq(recipe.Value("kind"), "mirror") {
		return "", fmt.Errorf("%s.kind must be mirror", label)
	}
	if _, err := p.ValidatePackage(recipe.Value("package"), label+".package", false); err != nil {
		return "", err
	}
	pkg := recipe.Value("package").(*Object)
	if !inSet(pkg.Value("artifact_format"), setOf("tar.gz", "zip")) {
		return "", fmt.Errorf("%s.package.artifact_format is invalid", label)
	}
	members, ok := pkg.Value("expected_members").([]any)
	if !ok || len(members) == 0 {
		return "", fmt.Errorf("%s.package.expected_members must not be empty", label)
	}
	names, ok := pyjson.StrList(members)
	if !ok || slices.Contains(names, "") {
		return "", fmt.Errorf("%s.package.expected_members must contain paths", label)
	}
	if !uniqueStrings(names) {
		return "", fmt.Errorf("%s.package.expected_members contains duplicates", label)
	}
	for _, member := range names {
		path := purePosix(member)
		if path.absolute || path.hasPart("..") {
			return "", fmt.Errorf("%s.package.expected_members contains an unsafe path", label)
		}
	}
	review, ok := recipe.Value("review").(*Object)
	if !ok || !inSet(review.Value("status"), setOf("blocked", "ready")) {
		return "", fmt.Errorf("%s.review.status is invalid", label)
	}
	if !nonEmptyText(review.Value("notes")) {
		return "", fmt.Errorf("%s.review.notes must not be empty", label)
	}
	status := review.Value("status").(string)
	if (status == "ready") != pkg.Value("redistribution_reviewed").(bool) {
		return "", fmt.Errorf("%s review status and redistribution flag disagree", label)
	}
	return status, nil
}

// RecipePaths lists every *.json outside a "sources" folder, sorted
// component by component.
func RecipePaths(root string) ([]string, error) {
	var paths [][]string
	err := filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			if path == root && os.IsNotExist(err) {
				return filepath.SkipAll
			}
			return err
		}
		if path == root || !strings.HasSuffix(entry.Name(), ".json") {
			return nil
		}
		relative, _ := filepath.Rel(root, path)
		parts := strings.Split(filepath.ToSlash(relative), "/")
		if slices.Contains(parts, "sources") {
			return nil
		}
		paths = append(paths, parts)
		return nil
	})
	sort.Slice(paths, func(i, j int) bool {
		a, b := paths[i], paths[j]
		for index := 0; index < len(a) && index < len(b); index++ {
			if a[index] != b[index] {
				return a[index] < b[index]
			}
		}
		return len(a) < len(b)
	})
	result := make([]string, len(paths))
	for index, parts := range paths {
		result[index] = filepath.Join(append([]string{root}, parts...)...)
	}
	return result, err
}
