package content

import (
	"archive/tar"
	"compress/gzip"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/archive"
	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// VerifyArtifact checks a recipe package's built artifact against its
// catalog entry.
func VerifyArtifact(path string, pkg *Object) error {
	if isSymlink(path) || !isFile(path) {
		return fmt.Errorf("artifact must be a regular file: %s", path)
	}
	info, err := os.Stat(path)
	if err != nil {
		return err
	}
	if !pyjson.EqInt(pkg.Value("size"), info.Size()) {
		return fmt.Errorf("artifact size mismatch: expected %s, got %d", pyjson.Repr(pkg.Value("size")), info.Size())
	}
	expected, _ := pyjson.StrList(pkg.Value("expected_members"))
	format := pkg.Value("artifact_format")
	if format == "zip" {
		if _, err := ScanZipFile(path, expected); err != nil {
			return fmt.Errorf("artifact is not a safe ZIP archive: %v", err)
		}
	}
	digest, err := archive.FileSHA256(path)
	if err != nil {
		return err
	}
	if !pyjson.Eq(digest, pkg.Value("sha256")) {
		return fmt.Errorf("artifact SHA-256 mismatch: expected %s, got %s", pyjson.Repr(pkg.Value("sha256")), digest)
	}
	if format == "tar.gz" {
		return verifyTarGz(path, expected)
	}
	return nil
}

func verifyTarGz(path string, expected []string) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	invalid := fmt.Errorf("artifact is not a valid gzip-compressed TAR archive")
	compressed, err := gzip.NewReader(file)
	if err != nil {
		return invalid
	}
	reader := tar.NewReader(compressed)
	names := map[string]bool{}
	for {
		header, err := reader.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return invalid
		}
		if header.Typeflag == tar.TypeXGlobalHeader {
			continue
		}
		name := strings.TrimRight(header.Name, "/")
		if name == "" {
			continue
		}
		relative := purePosix(name)
		if relative.absolute || relative.hasPart("..") || strings.Contains(name, `\`) {
			return fmt.Errorf("archive contains an unsafe path: %s", header.Name)
		}
		switch header.Typeflag {
		case tar.TypeSymlink, tar.TypeLink, tar.TypeChar, tar.TypeBlock:
			return fmt.Errorf("archive contains an unsupported member: %s", header.Name)
		}
		names[name] = true
	}
	var missing []string
	for _, name := range expected {
		if !names[name] {
			missing = append(missing, name)
		}
	}
	if len(missing) > 0 {
		sort.Strings(missing)
		return fmt.Errorf("archive is missing expected member: %s", missing[0])
	}
	return nil
}

// RecipeArtifactPath is where `build` puts a recipe package's artifact.
func (p *Project) RecipeArtifactPath(pkg *Object) string {
	repr := func(key string) string { return pyjson.Repr(pkg.Value(key)) }
	coordinates := repr("platform") + "-" + repr("architecture")
	if repr("component") == "ezquake" {
		return filepath.Join(p.Path(DistDir), "clients", "ezquake", repr("channel"), repr("version"), coordinates, repr("filename"))
	}
	return filepath.Join(p.Path(DistDir), repr("component"), repr("version"), repr("channel"), coordinates, repr("filename"))
}

// Build is `x86qw-content build [--register] [--project-ref SHA]`.
func (p *Project) Build(out io.Writer, register bool, projectRef string, now time.Time) error {
	recipes, err := RecipePaths(p.Path(RecipesDir))
	if err != nil {
		return err
	}
	for _, path := range recipes {
		recipe, err := LoadJSONObject(path)
		if err != nil {
			return err
		}
		pkg, ok := recipe.Value("package").(*Object)
		if !ok {
			return fmt.Errorf("KeyError: 'package'")
		}
		if err := VerifyArtifact(p.RecipeArtifactPath(pkg), pkg); err != nil {
			return err
		}
	}
	builds := p.Path(BuildsDir)
	parent := filepath.Dir(builds)
	if err := os.MkdirAll(parent, 0o755); err != nil {
		return err
	}
	temporary, err := os.MkdirTemp(parent, "packages-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(temporary)
	options := BuildOptions{ProjectRef: projectRef}
	var ownershipTemporary string
	if projectRef != "" {
		if ownershipTemporary, err = os.MkdirTemp(parent, "ownership-"); err != nil {
			return err
		}
		defer os.RemoveAll(ownershipTemporary)
		options.OwnershipOutput = filepath.Join(ownershipTemporary, "components.json")
	}
	manifest, err := p.BuildPackages(p.Path(DistDir), temporary, options)
	if err != nil {
		return err
	}
	coreOwnership := ""
	if ownershipTemporary != "" {
		coreOwnership = filepath.Join(ownershipTemporary, "core.json")
	}
	core, err := p.BuildCorePackage(p.Path(DistDir), temporary, coreOwnership)
	if err != nil {
		return err
	}
	manifest.Set("packages", append(manifest.Value("packages").([]any), core))
	var ownership *Object
	if ownershipTemporary != "" {
		var fragments []*Object
		for _, name := range []string{"components.json", "core.json"} {
			fragment, err := loadOwnershipFragment(filepath.Join(ownershipTemporary, name))
			if err != nil {
				return err
			}
			fragments = append(fragments, fragment)
		}
		if ownership, err = MergeOwnership(fragments...); err != nil {
			return err
		}
	}
	if err := os.RemoveAll(builds); err != nil {
		return err
	}
	if err := os.Rename(temporary, builds); err != nil {
		return err
	}
	ownershipRoot := filepath.Join(parent, "ownership")
	if err := os.RemoveAll(ownershipRoot); err != nil {
		return err
	}
	if ownership != nil {
		if err := WriteOwnership(filepath.Join(ownershipRoot, "content.json"), ownership); err != nil {
			return err
		}
	}
	if register {
		if err := p.RegisterPackages(p.Path(CatalogPath), manifest, now); err != nil {
			return err
		}
		catalog, err := LoadJSONObject(p.Path(CatalogPath))
		if err != nil {
			return err
		}
		if err := p.NormalizeCatalogReleaseMetadata(catalog); err != nil {
			return err
		}
		if err := WriteJSON(p.Path(CatalogPath), catalog); err != nil {
			return err
		}
	}
	fmt.Fprintf(out, "[OK] %d package(s) built in %s.\n", len(manifest.Value("packages").([]any)), builds)
	return nil
}

func loadOwnershipFragment(path string) (*Object, error) {
	if isSymlink(path) || !isFile(path) {
		return nil, fmt.Errorf("missing or unsafe ownership fragment: %s", path)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	value, err := pyjson.Decode(raw, pyjson.RejectDuplicates)
	if err != nil {
		return nil, fmt.Errorf("invalid ownership fragment: %s", path)
	}
	return ValidateOwnership(value)
}
