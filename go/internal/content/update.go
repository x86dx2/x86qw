package content

import (
	"crypto/sha1"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// manage.py update and its workspace machinery.

// ManagerError mirrors ManagerError (printed as "[ERRO] ...").
type ManagerError struct{ msg string }

func (e *ManagerError) Error() string { return e.msg }

func managerError(format string, args ...any) error {
	return &ManagerError{fmt.Sprintf(format, args...)}
}

var expectedStableMembers = map[string][]string{
	"linux":   {"ezQuake-x86_64.AppImage"},
	"macos":   {"ezQuake.app/Contents/MacOS/ezQuake"},
	"windows": {"ezquake.exe"},
}

func utcNow() string { return time.Now().UTC().Format("2006-01-02T15:04:05Z") }

// WriteManifest mirrors sync_distribution.write_manifest.
func WriteManifest(path string, manifest *Object) error {
	manifest.Set("captured_at", utcNow())
	payload, err := pyjson.EncodeFile(manifest, true)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".manifest-*.json")
	if err != nil {
		return err
	}
	defer os.Remove(file.Name())
	_, writeErr := file.Write(payload)
	if err := errors.Join(writeErr, file.Close()); err != nil {
		return err
	}
	return os.Rename(file.Name(), path)
}

// EZQuakeRuntimeCoordinates mirrors ezquake_runtime_coordinates.
func EZQuakeRuntimeCoordinates(asset Asset) (channel, version, variant string, ok bool) {
	if asset.Component != "ezquake" {
		return
	}
	parts := purePosix(asset.Path).parts
	if len(parts) < 6 || parts[0] != "clients" || parts[1] != "ezquake" {
		return
	}
	channel, version, variant = parts[2], parts[3], parts[4]
	if (channel != "stable" && channel != "nightly") ||
		!(strings.HasPrefix(variant, "macos-") || strings.HasPrefix(variant, "linux-") || strings.HasPrefix(variant, "windows-")) {
		return "", "", "", false
	}
	return channel, version, variant, true
}

// UpdateInventoryLines mirrors update_inventory_lines.
func (p *Project) UpdateInventoryLines(results []UpdateResult, assets []Asset, releases, catalog *Object) ([]string, error) {
	var lines []string
	packages, ok := catalog.Value("packages").([]any)
	if !ok {
		return nil, managerError("catalogo sem pacotes")
	}
	for _, channel := range []string{"stable", "nightly"} {
		current, upstream := map[string]bool{}, map[string]bool{}
		for _, pkg := range objects(packages) {
			if pkg.Value("component") == "ezquake" && pkg.Value("channel") == channel {
				current[pyjson.Repr(pkg.Value("version"))] = true
			}
		}
		count := 0
		for _, asset := range assets {
			if assetChannel, version, _, ok := EZQuakeRuntimeCoordinates(asset); ok && assetChannel == channel {
				upstream[version] = true
				count++
			}
		}
		if len(current) != 1 || len(upstream) != 1 {
			return nil, managerError("catalogo ezQuake %s precisa representar exatamente uma versao", channel)
		}
		currentVersion, upstreamVersion := sortedKeys(current)[0], sortedKeys(upstream)[0]
		if currentVersion == upstreamVersion {
			lines = append(lines, fmt.Sprintf("[OK] ezQuake %s: %s (%d plataformas)", channel, currentVersion, count))
		} else {
			lines = append(lines, fmt.Sprintf("[ATUALIZAR] ezQuake %s: %s -> %s (%d plataformas)", channel, currentVersion, upstreamVersion, count))
		}
	}
	releaseComponents, ok := releases.Value("components").(*Object)
	if !ok {
		return nil, managerError("inventario sem releases de componentes")
	}
	components, err := p.Components()
	if err != nil {
		return nil, err
	}
	labels := map[string]string{}
	for _, component := range objects(components.Value("components")) {
		labels[pyjson.Repr(component.Value("id"))] = pyjson.Repr(component.Value("label"))
	}
	label := func(identifier string) string {
		if value, ok := labels[identifier]; ok {
			return value
		}
		return identifier
	}
	var referenceResults []UpdateResult
	for _, item := range results {
		if item.Strategy == "reference-snapshot" {
			referenceResults = append(referenceResults, item)
		}
	}
	revisions := map[string]bool{}
	for _, item := range referenceResults {
		revisions[prefix(ReferenceRevisionOf(releases, item.Component), 12)] = true
	}
	lines = append(lines, fmt.Sprintf("[OK] Conteudo baseado no nQuake: %s (%d componentes)", strings.Join(sortedKeys(revisions), ", "), len(referenceResults)))
	for _, item := range referenceResults {
		release, ok := releaseComponents.Value(item.Component).(*Object)
		if !ok {
			return nil, managerError("release ausente: %s", item.Component)
		}
		lines = append(lines, fmt.Sprintf("     - %s: %s", label(item.Component), pyjson.Repr(release.Value("version"))))
	}
	for _, item := range results {
		if item.Strategy == "reference-snapshot" {
			continue
		}
		release, ok := releaseComponents.Value(item.Component).(*Object)
		if !ok {
			return nil, managerError("release ausente: %s", item.Component)
		}
		marker := "OK"
		if item.Status != "current" {
			marker = "ATUALIZAR"
		}
		upstreamVersion := item.LatestSource
		if upstream, ok := release.Value("upstream").(*Object); ok {
			upstreamVersion = pyjson.Repr(upstream.Value("release"))
		}
		if marker != "OK" {
			upstreamVersion = item.LatestSource
		}
		detail := item.Installed
		if upstreamVersion != detail {
			detail = "upstream " + upstreamVersion + "; pacote x86QW " + detail
		}
		var locations []string
		for _, artifact := range objects(getOr(release, "artifacts", []any{})) {
			if path, ok := artifact.Value("distribution_path").(string); ok {
				locations = append(locations, "dist/"+path)
			}
		}
		location := ""
		if len(locations) > 0 {
			location = "; fonte " + strings.Join(locations, ", ")
		}
		lines = append(lines, fmt.Sprintf("[%s] %s: %s%s", marker, label(item.Component), detail, location))
	}
	return lines, nil
}

// MoveReferenceRelease pins release to another nQuake revision and carries
// the revision into its version and distribution tag.
func MoveReferenceRelease(release *Object, revision string) {
	old := pyjson.Repr(getOr(release, "reference_revision", ""))
	short, oldShort := prefix(revision, 12), prefix(old, 12)
	release.Set("reference_revision", revision)
	version := pyjson.Repr(getOr(release, "version", ""))
	switch release.Value("strategy") {
	case "reference-snapshot":
		suffix := ""
		if strings.HasPrefix(version, oldShort) {
			suffix = version[len(oldShort):]
		}
		release.Set("version", short+suffix)
	case "upstream-overlay", "reference-overlay":
		release.Set("version", strings.ReplaceAll(version, "nquake."+oldShort, "nquake."+short))
		if tag, ok := release.Value("distribution_tag").(string); ok {
			release.Set("distribution_tag", strings.ReplaceAll(tag, "nquake."+oldShort, "nquake."+short))
		}
	}
}

// copyTree mirrors manage.copy_tree: hard links when possible.
func copyTree(source, destination string) error {
	return filepath.WalkDir(source, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		relative, _ := filepath.Rel(source, path)
		if entry.Name() == "__pycache__" && entry.IsDir() {
			return filepath.SkipDir
		}
		if strings.HasSuffix(entry.Name(), ".pyc") {
			return nil
		}
		target := filepath.Join(destination, relative)
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		switch {
		case info.Mode()&os.ModeSymlink != 0:
			link, err := os.Readlink(path)
			if err != nil {
				return err
			}
			return os.Symlink(link, target)
		case info.IsDir():
			return os.MkdirAll(target, info.Mode().Perm())
		}
		if os.Link(path, target) == nil {
			return nil
		}
		payload, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(target, payload, info.Mode().Perm())
	})
}

func copyFile(source, destination string) error {
	payload, err := os.ReadFile(source)
	if err != nil {
		return err
	}
	info, err := os.Stat(source)
	if err != nil {
		return err
	}
	return os.WriteFile(destination, payload, info.Mode().Perm())
}

// prepareWorkspace mirrors manage.prepare_workspace.
func (p *Project) prepareWorkspace() (string, error) {
	parent := p.Path("maintenance/build")
	if err := os.MkdirAll(parent, 0o755); err != nil {
		return "", err
	}
	work, err := os.MkdirTemp(parent, "update-")
	if err != nil {
		return "", err
	}
	steps := []func() error{
		func() error { return copyTree(p.Path(DistDir), filepath.Join(work, "dist")) },
		func() error { return copyTree(p.Path(RecipesDir), filepath.Join(work, "recipes")) },
		func() error { return copyFile(p.Path(ComponentsPath), filepath.Join(work, "components.json")) },
		func() error { return copyFile(p.Path(ReleasesPath), filepath.Join(work, "component-releases.json")) },
		func() error { return copyFile(p.Path(CatalogPath), filepath.Join(work, "catalog.json")) },
		func() error { return copyFile(p.Path(NQuakeInventoryPath), filepath.Join(work, "nquake.json")) },
	}
	for _, step := range steps {
		if err := step(); err != nil {
			os.RemoveAll(work)
			return "", err
		}
	}
	return work, nil
}

// applyWorkspace mirrors manage.apply_workspace (all-or-nothing rename).
func (p *Project) applyWorkspace(work string, includeComponents bool) error {
	targets := [][2]string{{filepath.Join(work, "dist"), p.Path(DistDir)}, {filepath.Join(work, "recipes"), p.Path(RecipesDir)}}
	if includeComponents {
		targets = append(targets, [2]string{filepath.Join(work, "components.json"), p.Path(ComponentsPath)},
			[2]string{filepath.Join(work, "component-releases.json"), p.Path(ReleasesPath)})
	}
	targets = append(targets, [2]string{filepath.Join(work, "catalog.json"), p.Path(CatalogPath)},
		[2]string{filepath.Join(work, "nquake.json"), p.Path(NQuakeInventoryPath)})
	if exists(filepath.Join(work, "packages")) {
		targets = append(targets, [2]string{filepath.Join(work, "packages"), p.Path(BuildsDir)})
	}
	type applied struct{ target, backup string }
	var done []applied
	for _, item := range targets {
		source, target := item[0], item[1]
		os.MkdirAll(filepath.Dir(target), 0o755)
		backup := filepath.Join(work, fmt.Sprintf("backup-%d", len(done)))
		record := applied{target: target}
		if exists(target) {
			if err := os.Rename(target, backup); err != nil {
				return err
			}
			record.backup = backup
		}
		done = append(done, record)
		if err := os.Rename(source, target); err != nil {
			for index := len(done) - 1; index >= 0; index-- {
				os.RemoveAll(done[index].target)
				if done[index].backup != "" {
					os.Rename(done[index].backup, done[index].target)
				}
			}
			return err
		}
	}
	for _, record := range done {
		if record.backup != "" {
			os.RemoveAll(record.backup)
		}
	}
	return nil
}

func (p *Project) requireCleanWorktree() error {
	output, err := p.command(nil, "git", "status", "--porcelain")
	if err != nil {
		return err
	}
	if strings.TrimSpace(string(output)) != "" {
		return managerError("o Git possui alteracoes locais. Conclua ou descarte essas alteracoes antes de modificar a distribuicao")
	}
	return nil
}

// Confirm asks the operator unless --yes (manage.confirm).
type Confirm func(message string) error

// downloadAsset mirrors sync_distribution.download_asset.
func (p *Project) downloadAsset(root string, asset Asset, known *Object, reuse string) (*Object, bool, error) {
	if asset.ExpectedSize <= 0 || asset.ExpectedSize > MaxArtifactBytes || !sha256Pattern.MatchString(asset.ExpectedSHA256) {
		return nil, false, fmt.Errorf("distribution asset requires reviewed size and SHA-256 before download: %s", asset.Path)
	}
	target := joinPosix(root, asset.Path)
	if isSymlink(target) {
		return nil, false, fmt.Errorf("distribution target must not be a symlink: %s", target)
	}
	if known != nil && isFile(target) {
		info, _ := os.Stat(target)
		digest, _ := fileSHA256(target)
		if pyjson.EqInt(known.Value("size"), info.Size()) && pyjson.Eq(known.Value("sha256"), digest) &&
			pyjson.Eq(known.Value("url"), asset.URL) && pyjson.EqInt(known.Value("size"), asset.ExpectedSize) &&
			pyjson.Eq(known.Value("sha256"), asset.ExpectedSHA256) {
			metadata := known.Clone()
			if asset.Subcomponent != "" {
				metadata.Set("package", asset.Subcomponent)
			}
			return metadata, true, nil
		}
	}
	staging := target + ".x86qw-download"
	defer os.Remove(staging)
	reused := false
	if reuse != "" && reuse != target && isFile(reuse) && !isSymlink(reuse) {
		if os.Link(reuse, staging) != nil {
			copyFile(reuse, staging)
		}
		size, _ := fileSize(staging)
		digest, _ := fileSHA256(staging)
		reused = size == asset.ExpectedSize && digest == asset.ExpectedSHA256
	}
	if !reused {
		os.Remove(staging)
		if err := p.Pinned(asset.URL, staging, asset.ExpectedSize, asset.ExpectedSHA256, 3); err != nil {
			return nil, false, err
		}
	}
	if asset.ExpectedGitSHA1 != "" {
		file, err := os.Open(staging)
		if err != nil {
			return nil, false, err
		}
		digest := sha1.New()
		fmt.Fprintf(digest, "blob %d\x00", asset.ExpectedSize)
		io.Copy(digest, file)
		file.Close()
		if hex.EncodeToString(digest.Sum(nil)) != asset.ExpectedGitSHA1 {
			return nil, false, fmt.Errorf("download Git blob identity mismatch for %s", safeURLForLog(asset.URL))
		}
	}
	if err := os.Rename(staging, target); err != nil {
		return nil, false, err
	}
	consumer, err := p.FirstConsumer(asset.Component)
	if err != nil {
		return nil, false, err
	}
	metadata := object("component", asset.Component, "consumer", consumer, "url", asset.URL,
		"size", pyjson.Number(fmt.Sprint(asset.ExpectedSize)), "sha256", asset.ExpectedSHA256)
	if asset.Subcomponent != "" {
		metadata.Set("package", asset.Subcomponent)
	}
	return metadata, reused, nil
}

func removeEmptyDirectories(root string) {
	var dirs []string
	filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if err == nil && entry.IsDir() && path != root {
			dirs = append(dirs, path)
		}
		return nil
	})
	sort.Sort(sort.Reverse(sort.StringSlice(dirs)))
	for _, dir := range dirs {
		os.Remove(dir)
	}
}

// syncCandidate mirrors manage.sync_candidate.
func (p *Project) syncCandidate(out io.Writer, candidate string, assets []Asset, workers int) (*Object, error) {
	manifestPath := filepath.Join(candidate, "manifest.json")
	manifest, err := LoadManifest(manifestPath)
	if err != nil {
		return nil, err
	}
	files := manifest.Value("files").(*Object)
	// A component that moves to another nQuake revision keeps most of its
	// files byte for byte; reuse the local copy instead of downloading it.
	local := map[string]string{}
	for _, relative := range files.Keys() {
		if digest, ok := pyjson.Get(files, relative, "sha256").(string); ok {
			local[digest] = joinPosix(candidate, relative)
		}
	}
	desired := map[string]bool{}
	for _, asset := range assets {
		desired[asset.Path] = true
		os.MkdirAll(filepath.Dir(joinPosix(candidate, asset.Path)), 0o755)
	}
	type outcome struct {
		asset    Asset
		metadata *Object
		cached   bool
		err      error
	}
	jobs := make(chan Asset)
	results := make(chan outcome)
	var group sync.WaitGroup
	for range workers {
		group.Add(1)
		go func() {
			defer group.Done()
			for asset := range jobs {
				known, _ := files.Value(asset.Path).(*Object)
				metadata, cached, err := p.downloadAsset(candidate, asset, known, local[asset.ExpectedSHA256])
				results <- outcome{asset, metadata, cached, err}
			}
		}()
	}
	go func() {
		for _, asset := range assets {
			jobs <- asset
		}
		close(jobs)
		group.Wait()
		close(results)
	}()
	var failures []string
	completed := 0
	for result := range results {
		if result.err != nil {
			failures = append(failures, result.asset.Path+": "+result.err.Error())
			continue
		}
		files.Set(result.asset.Path, result.metadata)
		completed++
		action := "baixado"
		if result.cached {
			action = "cache"
		}
		fmt.Fprintf(out, "[%3d/%d] %s: %s\n", completed, len(assets), action, result.asset.Path)
	}
	if len(failures) > 0 {
		return nil, managerError("%d download(s) falharam; primeiro erro: %s", len(failures), failures[0])
	}
	for _, relative := range sortedKeys(objectMap(files)) {
		if desired[relative] {
			continue
		}
		target := joinPosix(candidate, relative)
		if isFile(target) || isSymlink(target) {
			os.Remove(target)
		}
		files.Delete(relative)
	}
	removeEmptyDirectories(candidate)
	manifest.Set("repositories", pyjson.NewObject())
	manifest.Set("layout", "distribution-v1")
	if err := WriteManifest(manifestPath, manifest); err != nil {
		return nil, err
	}
	if _, err := p.VerifyDistribution(candidate, manifest); err != nil {
		return nil, err
	}
	return manifest, nil
}

func objectMap(object *Object) map[string]bool {
	result := map[string]bool{}
	for _, key := range object.Keys() {
		result[key] = true
	}
	return result
}

// validateStaged mirrors manage.validate_staged.
func (p *Project) validateStaged(work string) (*Project, error) {
	staged := p.Staged(filepath.Join(work, "components.json"), filepath.Join(work, "component-releases.json"))
	components, err := staged.Components()
	if err != nil {
		return nil, err
	}
	releases, err := staged.Releases()
	if err != nil {
		return nil, err
	}
	catalog, err := LoadJSONObject(filepath.Join(work, "catalog.json"))
	if err != nil {
		return nil, err
	}
	if _, err := p.ValidateCatalog(catalog); err != nil {
		return nil, err
	}
	recipes, err := RecipePaths(filepath.Join(work, "recipes"))
	if err != nil {
		return nil, err
	}
	for _, path := range recipes {
		recipe, err := LoadJSONObject(path)
		if err != nil {
			return nil, err
		}
		if _, err := p.ValidateRecipe(recipe, path); err != nil {
			return nil, err
		}
	}
	candidate := filepath.Join(work, "dist")
	if _, err := LoadSourceContext(candidate, components, releases); err != nil {
		return nil, err
	}
	for _, component := range objects(components.Value("components")) {
		for _, source := range objects(getOr(component, "project_sources", []any{})) {
			parts := purePosix(pyjson.Repr(source.Value("path"))).parts
			path := filepath.Join(append([]string{candidate}, parts[1:]...)...)
			info, err := os.Stat(path)
			if !isFile(path) || isSymlink(path) || err != nil || info.Size() == 0 {
				return nil, managerError("fonte x86QW ausente: %s", pyjson.Repr(source.Value("path")))
			}
		}
	}
	manifest, err := LoadManifest(filepath.Join(candidate, "manifest.json"))
	if err != nil {
		return nil, err
	}
	if _, err := staged.VerifyDistribution(candidate, manifest); err != nil {
		return nil, err
	}
	if _, ok := releases.Value("components").(*Object); !ok {
		return nil, managerError("inventario de releases vazio")
	}
	return staged, nil
}

// UpdateOptions mirrors manage.py update flags.
type UpdateOptions struct {
	Workers int
	DryRun  bool
	Yes     bool
	Commit  bool
	Push    bool
	Message string
	Confirm Confirm
	// Components limits the update (empty = every component that can be
	// checked and needs no reviewed definition).
	Components []string
}

// Update brings the selected components to their upstreams. Each component
// moves on its own: one that is unavailable or needs a reviewed definition
// is skipped (or, when named explicitly, fails the update) and keeps its
// current files.
func (p *Project) Update(out io.Writer, options UpdateOptions) error {
	if options.Workers < 1 || options.Workers > 32 {
		return managerError("--workers deve estar entre 1 e 32")
	}
	releases, err := p.Releases()
	if err != nil {
		return err
	}
	ids := UpdatableIDs(releases)
	if err := ValidateSelection(options.Components, ids); err != nil {
		return err
	}
	explicit := setOf(options.Components...)
	fmt.Fprintln(out, "[INFO] Consultando os upstreams declarados...")
	results, err := p.CheckUpdates(releases, true)
	if err != nil {
		return err
	}
	assets, unavailable, err := p.DiscoverAssets()
	if err != nil {
		return err
	}
	catalog, err := LoadJSONObject(p.Path(CatalogPath))
	if err != nil {
		return err
	}
	manifest, err := LoadManifest(p.Path(ManifestPath))
	if err != nil {
		return err
	}
	nquakeOwners := NQuakeOwners(manifest, ids)
	skipped := map[string]string{}
	skip := func(id, reason string) error {
		if explicit[id] {
			return managerError("%s: %s", id, reason)
		}
		skipped[id] = reason
		return nil
	}
	for _, item := range results {
		switch {
		case item.Error != "":
			err = skip(item.Component, "upstream indisponível ("+item.Error+")")
		case item.Status != "current" && !IsReferenceStrategy(item.Strategy):
			err = skip(item.Component, "exige nova versao/revisao explicita; use 'add' com uma definicao revisada")
		}
		if err != nil {
			return err
		}
	}
	if reason := unavailable["ezquake"]; reason != "" {
		if err := skip("ezquake", "upstream indisponível ("+reason+")"); err != nil {
			return err
		}
	}
	if reason := unavailable["nquake"]; reason != "" {
		for owner := range nquakeOwners {
			if err := skip(owner, "referência nQuake indisponível ("+reason+")"); err != nil {
				return err
			}
		}
	}
	// A component whose consumed nQuake files changed follows the
	// discovered revision on its own, unless one of its x86QW
	// customizations no longer applies; every other component keeps the
	// nQuake files it pins. Without a selection the whole nQuake inventory
	// is refreshed too.
	var notices []string
	var moves []referenceMove
	var head string
	var nquakeReport *NQuakeReport
	var nquakeInventory *NQuakeInventory
	if unavailable["nquake"] == "" {
		if head, err = ReferenceRevision(assets); err != nil {
			return err
		}
		changed, err := ReferenceChanges(assets, manifest, p.Path(DistDir))
		if err != nil {
			return err
		}
		strategies := map[string]string{}
		for _, item := range results {
			strategies[item.Component] = item.Strategy
		}
		if err := os.MkdirAll(p.Path("maintenance/build"), 0o755); err != nil {
			return err
		}
		scratch, err := os.MkdirTemp(p.Path("maintenance/build"), "nquake-")
		if err != nil {
			return err
		}
		defer os.RemoveAll(scratch)
		moving := map[string]bool{}
		for _, owner := range sortedKeys(changed) {
			if _, out := skipped[owner]; out || (len(explicit) > 0 && !explicit[owner]) {
				continue
			}
			release, _ := pyjson.Get(releases, "components", owner).(*Object)
			if release == nil {
				continue
			}
			move, reason, err := p.planReferenceMove(owner, release, p.nquake, assets, manifest, scratch)
			if err != nil {
				return err
			}
			if reason != "" {
				reason = "nQuake " + head[:12] + ": " + reason
				if !IsReferenceStrategy(strategies[owner]) {
					notices = append(notices, fmt.Sprintf("[REVISAR] %s: %s", owner, reason))
				} else if err := skip(owner, reason); err != nil {
					return err
				}
				continue
			}
			moves = append(moves, move)
			moving[owner] = true
		}
		if assets, err = PreserveCurrentReferenceAssets(assets, manifest, p.Path(DistDir), func(owner string) bool { return !moving[owner] }); err != nil {
			return err
		}
		if len(explicit) == 0 {
			if nquakeReport, nquakeInventory, err = p.nquakeReport(); err != nil {
				return err
			}
		}
	}
	inScope := func(id string) bool {
		if _, out := skipped[id]; out {
			return false
		}
		return len(explicit) == 0 || explicit[id]
	}
	if assets, err = KeepCurrent(assets, manifest, ids, func(owner string) bool { return !inScope(owner) }); err != nil {
		return err
	}
	// The report shows what this update does: components left out keep
	// their current version.
	for index := range results {
		if !inScope(results[index].Component) {
			results[index].Status, results[index].LatestSource = "current", results[index].Installed
		}
	}
	fmt.Fprintln(out, "\nComponentes e clientes verificados:")
	lines, err := p.UpdateInventoryLines(results, assets, releases, catalog)
	if err != nil {
		return err
	}
	for _, line := range lines {
		fmt.Fprintln(out, line)
	}
	for _, id := range sortedKeys(setOfKeys(Unavailable(skipped))) {
		fmt.Fprintf(out, "[PULADO] %s: %s\n", id, skipped[id])
	}
	for _, notice := range notices {
		fmt.Fprintln(out, notice)
	}
	for _, move := range moves {
		fmt.Fprintf(out, "[NQUAKE] %s: %s -> %s (%d arquivo(s) do nQuake alterado(s); %d customizacao(oes) x86QW reaplicada(s))\n",
			move.owner, prefix(ReferenceRevisionOf(releases, move.owner), 12), head[:12], len(move.changed), len(move.repins))
	}
	inventoryChanged := false
	if nquakeReport != nil {
		for _, line := range nquakeReport.Lines() {
			fmt.Fprintln(out, line)
		}
		inventoryChanged = len(nquakeReport.Unclassified) == 0 && (nquakeInventory.Revision != head || !nquakeInventory.Same(p.nquake))
		if len(nquakeReport.Unclassified) > 0 {
			fmt.Fprintf(out, "[INFO] O inventario nQuake so avanca quando todo arquivo estiver classificado em %s.\n", NQuakeInventoryPath)
		}
		if inventoryChanged {
			fmt.Fprintf(out, "[INFO] Inventario nQuake: %s -> %s.\n", nquakeInventory.Revision[:12], head[:12])
		}
	}
	if assets, err = PinAssetsFromManifest(assets, manifest); err != nil {
		return err
	}
	review := UnpinnedAssetPaths(assets)
	delta, err := DistributionDelta(assets, manifest)
	if err != nil {
		return err
	}
	if len(delta) == 0 && len(review) == 0 {
		if !inventoryChanged {
			fmt.Fprintln(out, "[OK] O dist e os inventarios ja representam os upstreams atuais.")
			return nil
		}
		// Only the inventory of the unused nQuake files moves: no package
		// changes, so nothing is rebuilt.
		if options.DryRun {
			return nil
		}
		if err := p.requireCleanWorktree(); err != nil {
			return err
		}
		if !options.Yes {
			if err := options.Confirm("Atualizar o inventario nQuake?"); err != nil {
				return err
			}
		}
		if err := WriteJSON(p.Path(NQuakeInventoryPath), nquakeInventory.Document(p.nquake)); err != nil {
			return err
		}
		fmt.Fprintf(out, "[OK] Inventario nQuake atualizado para %s.\n", head[:12])
		return nil
	}
	fmt.Fprintf(out, "[INFO] %d alteracao(oes) de arquivo.\n", len(delta))
	if options.DryRun {
		for _, summary := range SummarizeDelta(delta) {
			fmt.Fprintf(out, "  - %s\n", summary)
		}
	}
	if len(review) > 0 {
		return managerError("%d candidato(s) remoto(s) exige(m) tamanho e SHA-256 revisados antes do update; primeiro: %s. "+
			"Use 'add' com uma definição revisada para incorporar o arquivo sem confiar na própria transferência. "+
			"Nenhum payload candidato foi baixado.", len(review), review[0])
	}
	if options.DryRun {
		return nil
	}
	if err := p.requireCleanWorktree(); err != nil {
		return err
	}
	if !options.Yes {
		if err := options.Confirm("Aplicar esta atualizacao ao Git working tree?"); err != nil {
			return err
		}
	}
	work, err := p.prepareWorkspace()
	if err != nil {
		return err
	}
	defer os.RemoveAll(work)
	distManifest, err := p.syncCandidate(out, filepath.Join(work, "dist"), assets, options.Workers)
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
	stable, nightly, err := p.updateEZQuakeCatalog(stagedCatalog, assets, distManifest, filepath.Join(work, "recipes"))
	if err != nil {
		return err
	}
	updateClientCompatibility(stagedReleases, stable, nightly)
	for _, move := range moves {
		applyReferenceMove(pyjson.Get(stagedReleases, "components", move.owner).(*Object), head, move)
	}
	if inventoryChanged {
		if err := WriteJSON(filepath.Join(work, "nquake.json"), nquakeInventory.Document(p.nquake)); err != nil {
			return err
		}
	}
	if err := WriteJSON(filepath.Join(work, "component-releases.json"), stagedReleases); err != nil {
		return err
	}
	if err := WriteJSON(filepath.Join(work, "catalog.json"), stagedCatalog); err != nil {
		return err
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
	fmt.Fprintf(out, "[OK] Working tree atualizado: ezQuake %s/%s.\n", stable, nightly)
	switch {
	case options.Commit:
		return p.Commit(out, options.Message, options.Push)
	case options.Push:
		return managerError("--push exige --commit")
	}
	fmt.Fprintln(out, "[PROXIMO] Execute 'x86qw-content verify' e revise 'git diff' antes do commit.")
	return nil
}

// buildIntoWorkspace mirrors build_packages + core + register_packages on
// a staged workspace.
func (p *Project) buildIntoWorkspace(work string) error {
	manifest, err := p.BuildPackages(filepath.Join(work, "dist"), filepath.Join(work, "packages"), BuildOptions{})
	if err != nil {
		return err
	}
	core, err := p.BuildCorePackage(filepath.Join(work, "dist"), filepath.Join(work, "packages"), "")
	if err != nil {
		return err
	}
	manifest.Set("packages", append(manifest.Value("packages").([]any), core))
	if err := p.RegisterPackages(filepath.Join(work, "catalog.json"), manifest, time.Now()); err != nil {
		return err
	}
	catalog, err := LoadJSONObject(filepath.Join(work, "catalog.json"))
	if err != nil {
		return err
	}
	_, err = p.ValidateCatalog(catalog)
	return err
}

func updateClientCompatibility(releases *Object, stable, nightly string) {
	components, ok := releases.Value("components").(*Object)
	if !ok {
		return
	}
	for _, identifier := range components.Keys() {
		release, ok := components.Value(identifier).(*Object)
		if !ok {
			continue
		}
		if compatibility, ok := release.Value("compatibility").(*Object); ok {
			if _, isList := compatibility.Value("client_scope").([]any); isList {
				compatibility.Set("client_scope", []any{"ezquake-stable:" + stable, "ezquake-nightly:" + nightly})
			}
		}
	}
}

func assetPlatform(asset Asset) (string, error) {
	_, _, variant, ok := EZQuakeRuntimeCoordinates(asset)
	if !ok {
		return "", managerError("artefato não é um runtime ezQuake: %s", asset.Path)
	}
	for _, platform := range []string{"macos", "linux", "windows"} {
		if strings.HasPrefix(variant, platform) {
			return platform, nil
		}
	}
	return "", managerError("plataforma ezQuake desconhecida: %s", asset.Path)
}

func (p *Project) ezquakeSourceRevision(asset Asset) (string, error) {
	channel, version, _, ok := EZQuakeRuntimeCoordinates(asset)
	if !ok {
		return "", managerError("artefato não é um runtime ezQuake: %s", asset.Path)
	}
	if channel == "stable" {
		return version, nil
	}
	registry, err := p.Upstreams()
	if err != nil {
		return "", err
	}
	for _, upstream := range objects(registry.Value("upstreams")) {
		if upstream.Value("id") != "ezquake-nightly" || !pyjson.Eq(upstream.Value("version"), version) {
			continue
		}
		if revision, ok := upstream.Value("revision").(string); ok && hex40Strict.MatchString(revision) {
			return revision, nil
		}
	}
	short := version[strings.LastIndex(version, "_")+1:]
	return p.GitHubCommitRevision(ezquakeReleaseRepository, short)
}

var githubReleasePattern = regexp.MustCompile(`^https://github\.com/([^/]+/[^/]+)/releases/download/([^/]+)/([^/]+)$`)

// GitHubReleaseCoordinates mirrors manage.github_release_coordinates.
func GitHubReleaseCoordinates(url, filename string) (string, string, error) {
	match := githubReleasePattern.FindStringSubmatch(url)
	if match == nil || match[3] != filename {
		return "", "", managerError("URL primaria nao representa um GitHub Release: %s", safeURLForLog(url))
	}
	return match[1], match[2], nil
}

// ArtifactURL mirrors publish_gitlab_packages.artifact_url.
func ArtifactURL(pkg *Object) string {
	name := pyjson.Repr(getOr(pkg, "package", pkg.Value("component")))
	return "https://gitlab.com/api/v4/projects/" + gitlabProjectID + "/packages/generic/" +
		quote(name, "+._-") + "/" + quote(pyjson.Repr(pkg.Value("version")), "+._-") + "/" + quote(pyjson.Repr(pkg.Value("filename")), "+._-")
}

func (p *Project) updateEZQuakeCatalog(catalog *Object, assets []Asset, manifest *Object, recipes string) (string, string, error) {
	packages, packagesOK := catalog.Value("packages").([]any)
	files, filesOK := manifest.Value("files").(*Object)
	if !packagesOK || !filesOK {
		return "", "", managerError("catalogo ou manifesto invalido")
	}
	templates := map[[2]string]*Object{}
	for _, item := range objects(packages) {
		if item.Value("component") == "ezquake" {
			templates[[2]string{pyjson.Repr(item.Value("channel")), pyjson.Repr(item.Value("platform"))}] = item
		}
	}
	var replacements []any
	var stableRecords []*Object
	stableVersion, nightlyVersion := "", ""
	for _, asset := range assets {
		channel, version, _, ok := EZQuakeRuntimeCoordinates(asset)
		if !ok {
			continue
		}
		platform, err := assetPlatform(asset)
		if err != nil {
			return "", "", err
		}
		template := templates[[2]string{channel, platform}]
		metadata, ok := files.Value(asset.Path).(*Object)
		if template == nil || !ok {
			return "", "", managerError("faltam metadados para atualizar o catalogo: %s", asset.Path)
		}
		sourceRevision, err := p.ezquakeSourceRevision(asset)
		if err != nil {
			return "", "", err
		}
		record := template.Clone()
		filename := posixName(asset.Path)
		tagsPrefix := ""
		if channel == "stable" {
			tagsPrefix = "refs/tags/"
		}
		for _, pair := range [][2]any{
			{"version", version}, {"filename", filename}, {"size", metadata.Value("size")}, {"sha256", metadata.Value("sha256")},
			{"origin_url", asset.URL}, {"license_url", "https://github.com/QW-Group/ezquake-source/blob/" + sourceRevision + "/LICENSE"},
			{"source_urls", []any{"https://github.com/QW-Group/ezquake-source/archive/" + tagsPrefix + sourceRevision + ".tar.gz"}},
			{"distribution_path", asset.Path},
		} {
			record.Set(pair[0].(string), pair[1])
		}
		tag := "ezquake-nightly-" + version
		if channel == "stable" {
			tag = "ezquake-" + version
		}
		repository := PrimaryRepository
		if pyjson.Eq(template.Value("version"), version) && pyjson.Eq(template.Value("sha256"), metadata.Value("sha256")) {
			for _, existing := range objectsOrStrings(template.Value("urls")) {
				if found, _, err := GitHubReleaseCoordinates(existing, filename); err == nil {
					repository = found
					break
				}
			}
		}
		record.Set("urls", []any{"https://github.com/" + repository + "/releases/download/" + tag + "/" + filename, ArtifactURL(record)})
		record.Set("mirror_title", "x86QW Content · ezQuake "+channel+" "+version)
		record.Set("mirror_notes", "Cliente ezQuake "+channel+" "+version+" preservado pela distribuição x86QW.")
		record.Set("mirror_latest", false)
		replacements = append(replacements, record)
		if channel == "stable" {
			stableVersion = version
			stableRecords = append(stableRecords, record)
		} else {
			nightlyVersion = version
		}
	}
	if len(replacements) != 6 || stableVersion == "" || nightlyVersion == "" {
		return "", "", managerError("a atualizacao exige tres builds stable e tres builds nightly do ezQuake")
	}
	var kept []any
	for _, raw := range packages {
		if item, ok := raw.(*Object); ok && item.Value("component") == "ezquake" {
			continue
		}
		kept = append(kept, raw)
	}
	kept = append(kept, replacements...)
	key := func(raw any) [5]string {
		item := raw.(*Object)
		return [5]string{
			pyjson.Repr(getOr(item, "component", "")), pyjson.Repr(getOr(item, "package", getOr(item, "component", ""))),
			pyjson.Repr(getOr(item, "channel", "")), pyjson.Repr(getOr(item, "platform", "")), pyjson.Repr(getOr(item, "version", "")),
		}
	}
	sort.SliceStable(kept, func(i, j int) bool {
		a, b := key(kept[i]), key(kept[j])
		for index := range a {
			if a[index] != b[index] {
				return a[index] < b[index]
			}
		}
		return false
	})
	catalog.Set("packages", kept)
	catalog.Set("generated_at", utcNow())
	ezquakeRecipes := filepath.Join(recipes, "ezquake")
	os.RemoveAll(ezquakeRecipes)
	targetRoot := filepath.Join(ezquakeRecipes, stableVersion)
	if err := os.MkdirAll(targetRoot, 0o755); err != nil {
		return "", "", err
	}
	for _, record := range stableRecords {
		platform := pyjson.Repr(record.Value("platform"))
		pkg := pyjson.NewObject()
		for _, field := range PackageFields {
			pkg.Set(field, record.Value(field))
		}
		pkg.Set("artifact_format", "zip")
		members := make([]any, 0)
		for _, member := range expectedStableMembers[platform] {
			members = append(members, member)
		}
		pkg.Set("expected_members", members)
		recipe := object("format", pyjson.Number("1"), "project", "x86qw", "kind", "mirror", "package", pkg,
			"review", object("status", "ready", "notes", "Artefato oficial preservado byte a byte no dist e validado por SHA-256."))
		variant := purePosix(pyjson.Repr(record.Value("distribution_path"))).parts[4]
		if err := WriteJSON(filepath.Join(targetRoot, variant+".json"), recipe); err != nil {
			return "", "", err
		}
	}
	if _, err := p.ValidateCatalog(catalog); err != nil {
		return "", "", err
	}
	paths, err := RecipePaths(recipes)
	if err != nil {
		return "", "", err
	}
	for _, path := range paths {
		recipe, err := LoadJSONObject(path)
		if err != nil {
			return "", "", err
		}
		if _, err := p.ValidateRecipe(recipe, path); err != nil {
			return "", "", err
		}
	}
	return stableVersion, nightlyVersion, nil
}

func objectsOrStrings(value any) []string {
	items, _ := value.([]any)
	var result []string
	for _, item := range items {
		result = append(result, pyjson.Repr(item))
	}
	return result
}
