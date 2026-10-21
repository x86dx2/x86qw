package content

import (
	"fmt"
	"io"
	"strings"
)

// VerifyOptions tunes the repository hygiene checks that complement the
// data checks of x86qw-content verify.
type VerifyOptions struct {
	// SkipGit disables the Git LFS and committed-diff checks. They are
	// skipped automatically when the root is not a Git work tree.
	SkipGit bool
	// DiffCommand overrides the committed whitespace check command.
	DiffCommand []string
}

// misplacedRootPaths must never reappear at the repository root.
var misplacedRootPaths = []string{
	"DESIGN.md", "PRODUCT.md", "wrangler.jsonc", "distribution",
	"installer", "inventory", "recipes", "tools", "tests",
}

// Verify runs the data checks, then the Git LFS and committed-diff checks.
// Progress lines go to out.
func (p *Project) Verify(out io.Writer, options VerifyOptions) error {
	catalog, err := LoadJSONObject(p.Path(CatalogPath))
	if err != nil {
		return err
	}
	packageCount, err := p.ValidateCatalog(catalog)
	if err != nil {
		return err
	}
	trustStatus, err := p.VerifyLocalTUFCatalog()
	if err != nil {
		return fmt.Errorf("TUF does not authenticate the local catalog: %v", err)
	}
	components, err := p.Components()
	if err != nil {
		return err
	}
	inventory, err := p.LoadInventory(components, catalog)
	if err != nil {
		return err
	}
	if err := p.CheckProduct(); err != nil {
		return err
	}
	releases, err := p.Releases()
	if err != nil {
		return err
	}
	registry, err := p.Upstreams()
	if err != nil {
		return err
	}
	recipes, err := RecipePaths(p.Path(RecipesDir))
	if err != nil {
		return err
	}
	for _, path := range recipes {
		recipe, err := LoadJSONObject(path)
		if err != nil {
			return err
		}
		if _, err := p.ValidateRecipe(recipe, path); err != nil {
			return err
		}
	}
	distribution := p.Path(DistDir)
	sources, err := LoadSourceContext(distribution, components, releases)
	if err != nil {
		return err
	}
	nquake, err := LoadNQuakeInventory(p.Path(NQuakeInventoryPath))
	if err != nil {
		return err
	}
	if err := ValidateNQuakeInventory(nquake, components); err != nil {
		return err
	}
	var revisions []string
	for _, revision := range sources.Revisions() {
		revisions = append(revisions, revision[:12])
	}
	manifest, err := LoadManifest(p.Path(ManifestPath))
	if err != nil {
		return err
	}
	upstreamCount, err := p.VerifyDistribution(distribution, manifest)
	if err != nil {
		return err
	}
	sourceCount, err := p.VerifyPreservedSources(registry, distribution)
	if err != nil {
		return err
	}
	fmt.Fprintf(out, "[OK] Catalog: %d packages; components: %d; runtimes: %d; games: %d; recipes: %d; "+
		"upstream files: %d; sources: %d; nQuake: %s; TUF: %s.\n",
		packageCount, len(objects(components.Value("components"))),
		len(inventory.Runtimes.Value("runtimes").([]any)), len(inventory.Games.Value("games").([]any)),
		len(recipes), upstreamCount, sourceCount, strings.Join(revisions, ", "), trustStatus)
	var misplaced []string
	for _, path := range misplacedRootPaths {
		if exists(p.Path(path)) {
			misplaced = append(misplaced, path)
		}
	}
	if len(misplaced) > 0 {
		return fmt.Errorf("out-of-place items at the root: %s", strings.Join(misplaced, ", "))
	}
	if !options.SkipGit && IsGitWorkTree(p.Root) {
		count, problems, err := CheckLFS(p.Root)
		if err != nil {
			return err
		}
		if len(problems) > 0 {
			return fmt.Errorf("Git LFS: %s", strings.Join(problems, "; "))
		}
		fmt.Fprintf(out, "[OK] Git LFS: %d objects materialized; no large binary committed directly.\n", count)
		command := options.DiffCommand
		if command == nil {
			if command, err = CommittedDiffFromEnvironment(); err != nil {
				return err
			}
		}
		if _, err := CheckCommittedDiff(p.Root, command); err != nil {
			return err
		}
		fmt.Fprintf(out, "[OK] Committed diff has no whitespace errors: %s\n", strings.Join(command, " "))
	}
	fmt.Fprintln(out, "[OK] Distribution and project structure validated.")
	return nil
}
