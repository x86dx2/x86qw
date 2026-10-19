package content

import (
	"crypto/sha1"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"regexp"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Upstream freshness (check_component_updates.py and manage.py check).

var hex40Strict = regexp.MustCompile(`^[0-9a-f]{40}$`)

// UpdateResult is one component freshness line.
type UpdateResult struct {
	Component, Installed, LatestSource, Status, Strategy string
}

func (r UpdateResult) object() *Object {
	return object("component", r.Component, "installed", r.Installed, "latest_source", r.LatestSource, "status", r.Status, "strategy", r.Strategy)
}

// CheckUpdates mirrors check_component_updates.check_updates.
func (p *Project) CheckUpdates(releases *Object, online bool) ([]UpdateResult, error) {
	reference := releases.Value("reference").(*Object)
	referenceRevision := pyjson.Repr(reference.Value("revision"))
	current := referenceRevision
	if online {
		revision, err := p.GitHubRefRevision(pyjson.Repr(reference.Value("repository")), "refs/heads/master")
		if err != nil {
			return nil, err
		}
		current = revision
	}
	var results []UpdateResult
	components := releases.Value("components").(*Object)
	for _, identifier := range components.Keys() {
		release := components.Value(identifier).(*Object)
		expected := pyjson.Repr(release.Value("version"))
		actual, status := expected, "current"
		strategy := pyjson.Repr(release.Value("strategy"))
		if strategy == "reference-snapshot" && current != referenceRevision {
			actual, status = current[:12], "update-available"
		}
		upstream, hasUpstream := release.Value("upstream").(*Object)
		if strategy == "reference-overlay" && hasUpstream {
			actual = pyjson.Repr(upstream.Value("release"))
			if current != referenceRevision {
				status = "update-available"
			}
		}
		if repository, ok := pyjson.Get(release, "upstream", "repository").(string); online && hasUpstream && ok {
			latest, err := p.GitHubLatestRelease(repository)
			if err != nil {
				return nil, err
			}
			actual = latest
			if !pyjson.Eq(latest, upstream.Value("release")) {
				status = "update-available"
			}
		} else if online && strategy == "upstream-package" {
			artifact := objects(release.Value("artifacts"))[0]
			size, digest := int64Of(artifact.Value("size")), pyjson.Repr(artifact.Value("sha256"))
			gotSize, gotDigest, found, err := p.RemoteFingerprint(pyjson.Repr(artifact.Value("url")), size, digest)
			var integrity *IntegrityError
			if err != nil && !errors.As(err, &integrity) {
				return nil, err
			}
			if err != nil || !found || gotSize != size || gotDigest != digest {
				if !found && err == nil {
					return nil, &HTTPError{Status: 404, URL: pyjson.Repr(artifact.Value("url"))}
				}
				actual, status = "source-artifact-changed", "update-available"
			}
		}
		results = append(results, UpdateResult{identifier, expected, actual, status, strategy})
	}
	return results, nil
}

// DeltaItem is one distribution change.
type DeltaItem struct{ Path, Status, Reason string }

// DistributionDelta mirrors manage.distribution_delta.
func DistributionDelta(assets []Asset, manifest *Object) ([]DeltaItem, error) {
	files, ok := manifest.Value("files").(*Object)
	if !ok {
		return nil, fmt.Errorf("dist/manifest.json nao possui uma colecao de arquivos valida")
	}
	desired := map[string]Asset{}
	for _, asset := range assets {
		desired[asset.Path] = asset
	}
	var changes []DeltaItem
	for path, asset := range desired {
		current, ok := files.Value(path).(*Object)
		reason := ""
		switch {
		case !ok:
			reason = "novo"
		case !pyjson.Eq(current.Value("url"), asset.URL):
			reason = "origem alterada"
		case !pyjson.EqInt(current.Value("size"), asset.ExpectedSize):
			reason = "tamanho alterado"
		case asset.ExpectedSHA256 != "" && !pyjson.Eq(current.Value("sha256"), asset.ExpectedSHA256):
			reason = "conteudo alterado"
		}
		if reason != "" {
			changes = append(changes, DeltaItem{path, "update-available", reason})
		}
	}
	for _, path := range files.Keys() {
		if _, ok := desired[path]; !ok {
			changes = append(changes, DeltaItem{path, "obsolete", "nao pertence mais ao conjunto atual"})
		}
	}
	sort.SliceStable(changes, func(i, j int) bool { return changes[i].Path < changes[j].Path })
	return changes, nil
}

func nquakeSnapshotParts(path string) ([]string, bool) {
	parts := purePosix(path).parts
	return parts, len(parts) > 3 && parts[0] == "distributions" && parts[1] == "nquake" && hex40Strict.MatchString(parts[2])
}

// SummarizeDelta mirrors manage.summarize_delta.
func SummarizeDelta(delta []DeltaItem) []string {
	type counter struct {
		order  []string
		counts map[string]int
	}
	newer, older := &counter{counts: map[string]int{}}, &counter{counts: map[string]int{}}
	var others []string
	for _, item := range delta {
		if parts, ok := nquakeSnapshotParts(item.Path); ok {
			group := newer
			if item.Status == "obsolete" {
				group = older
			}
			if group.counts[parts[2]] == 0 {
				group.order = append(group.order, parts[2])
			}
			group.counts[parts[2]]++
			continue
		}
		others = append(others, item.Path+" ("+item.Reason+")")
	}
	if len(newer.order) > 0 || len(older.order) > 0 {
		render := func(group *counter, word string) string {
			var parts []string
			for _, revision := range group.order {
				parts = append(parts, fmt.Sprintf("%s (%d %s)", revision[:12], group.counts[revision], word))
			}
			if len(parts) == 0 {
				return "nenhum"
			}
			return strings.Join(parts, ", ")
		}
		others = append([]string{"snapshot nQuake: " + render(older, "removidos") + " -> " + render(newer, "novos")}, others...)
	}
	return others
}

// ReferenceRevision mirrors manage.reference_revision.
func ReferenceRevision(assets []Asset) (string, error) {
	revisions := map[string]bool{}
	for _, asset := range assets {
		if parts := purePosix(asset.Path).parts; asset.Component == "nquake" && len(parts) > 3 {
			revisions[parts[2]] = true
		}
	}
	if len(revisions) != 1 {
		return "", fmt.Errorf("a descoberta nQuake nao produziu exatamente uma revisao")
	}
	revision := sortedKeys(revisions)[0]
	if !hex40Strict.MatchString(revision) {
		return "", fmt.Errorf("revisao nQuake invalida: %s", revision)
	}
	return revision, nil
}

// GitBlobSHA1 mirrors manage.git_blob_sha1.
func GitBlobSHA1(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return "", err
	}
	digest := sha1.New()
	fmt.Fprintf(digest, "blob %d\x00", info.Size())
	if _, err := io.Copy(digest, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(digest.Sum(nil)), nil
}

// ReferenceContentChanged mirrors manage.reference_content_changed.
func ReferenceContentChanged(assets []Asset, manifest *Object, root string) (bool, error) {
	files, ok := manifest.Value("files").(*Object)
	if !ok {
		return false, fmt.Errorf("manifesto sem arquivos")
	}
	type record struct {
		relative string
		metadata *Object
	}
	current := map[string]record{}
	for _, relative := range files.Keys() {
		metadata, isObject := files.Value(relative).(*Object)
		if parts, ok := nquakeSnapshotParts(relative); ok && isObject {
			current[strings.Join(parts[3:], "/")] = record{relative, metadata}
		}
	}
	var discovered []Asset
	for _, asset := range assets {
		if asset.Component == "nquake" {
			discovered = append(discovered, asset)
		}
	}
	if len(discovered) != len(current) {
		return true, nil
	}
	for _, asset := range discovered {
		upstream := strings.Join(purePosix(asset.Path).parts[3:], "/")
		item, ok := current[upstream]
		if !ok || asset.ExpectedGitSHA1 == "" {
			return true, nil
		}
		path := joinPosix(root, item.relative)
		info, err := os.Stat(path)
		if !isFile(path) || isSymlink(path) || err != nil || info.Size() != asset.ExpectedSize || !pyjson.EqInt(item.metadata.Value("size"), asset.ExpectedSize) {
			return true, nil
		}
		digest, err := GitBlobSHA1(path)
		if err != nil {
			return false, err
		}
		if digest != asset.ExpectedGitSHA1 {
			return true, nil
		}
	}
	return false, nil
}

// PreserveCurrentReferenceAssets mirrors manage.preserve_current_reference_assets.
func PreserveCurrentReferenceAssets(assets []Asset, manifest *Object, root string) ([]Asset, error) {
	files, ok := manifest.Value("files").(*Object)
	if !ok {
		return nil, fmt.Errorf("manifesto sem arquivos")
	}
	var preserved []Asset
	for _, asset := range assets {
		if asset.Component != "nquake" {
			preserved = append(preserved, asset)
		}
	}
	for _, relative := range files.Keys() {
		metadata, isObject := files.Value(relative).(*Object)
		if _, ok := nquakeSnapshotParts(relative); !ok || !isObject {
			continue
		}
		path := joinPosix(root, relative)
		if !isFile(path) || isSymlink(path) {
			return nil, fmt.Errorf("arquivo nQuake registrado esta ausente: %s", relative)
		}
		subcomponent := ""
		if pyjson.Truthy(metadata.Value("package")) {
			subcomponent = pyjson.Repr(metadata.Value("package"))
		}
		preserved = append(preserved, Asset{"nquake", pyjson.Repr(metadata.Value("url")), relative,
			int64Of(metadata.Value("size")), subcomponent, pyjson.Repr(metadata.Value("sha256")), ""})
	}
	sort.SliceStable(preserved, func(i, j int) bool { return preserved[i].Path < preserved[j].Path })
	return preserved, nil
}

// Check mirrors manage.py check and returns the exit code (2 = updates).
func (p *Project) Check(out, info io.Writer, offline, asJSON bool) (int, error) {
	releases, err := p.Releases()
	if err != nil {
		return 0, err
	}
	results, err := p.CheckUpdates(releases, !offline)
	if err != nil {
		return 0, err
	}
	var delta []DeltaItem
	var review []string
	var referenceNote *Object
	if !offline {
		progress := out
		if asJSON {
			progress = info
		}
		fmt.Fprintln(progress, "[INFO] Consultando clientes e arquivos incorporados...")
		assets, err := p.DiscoverAssets()
		if err != nil {
			return 0, err
		}
		manifest, err := LoadManifest(p.Path(ManifestPath))
		if err != nil {
			return 0, err
		}
		head, err := ReferenceRevision(assets)
		if err != nil {
			return 0, err
		}
		pinned := pyjson.Repr(pyjson.Get(releases, "reference", "revision"))
		changed, err := ReferenceContentChanged(assets, manifest, p.Path(DistDir))
		if err != nil {
			return 0, err
		}
		referenceNote = object("pinned", pinned, "upstream", head, "consumed_content_changed", changed)
		if head != pinned && !changed {
			if assets, err = PreserveCurrentReferenceAssets(assets, manifest, p.Path(DistDir)); err != nil {
				return 0, err
			}
			for index := range results {
				if results[index].Strategy == "reference-snapshot" || results[index].Strategy == "reference-overlay" {
					results[index].Status, results[index].LatestSource = "current", results[index].Installed
				}
			}
		}
		if assets, err = PinAssetsFromManifest(assets, manifest); err != nil {
			return 0, err
		}
		review = UnpinnedAssetPaths(assets)
		if delta, err = DistributionDelta(assets, manifest); err != nil {
			return 0, err
		}
	}
	var outdated []UpdateResult
	for _, item := range results {
		if item.Status != "current" {
			outdated = append(outdated, item)
		}
	}
	if asJSON {
		components := make([]any, len(results))
		for index, item := range results {
			components[index] = item.object()
		}
		distribution := make([]any, len(delta))
		for index, item := range delta {
			distribution[index] = object("path", item.Path, "status", item.Status, "reason", item.Reason)
		}
		reviewList := make([]any, len(review))
		for index, path := range review {
			reviewList[index] = path
		}
		var note any
		if referenceNote != nil {
			note = referenceNote
		}
		payload, _ := pyjson.Encode(object("components", components, "distribution", distribution, "reference", note, "review_required", reviewList), false)
		fmt.Fprintln(out, string(payload))
	} else {
		var referenceUpdates []UpdateResult
		for _, item := range outdated {
			if item.Strategy == "reference-snapshot" || item.Strategy == "reference-overlay" {
				referenceUpdates = append(referenceUpdates, item)
			}
		}
		isReferenceUpdate := func(item UpdateResult) bool {
			for _, other := range referenceUpdates {
				if other == item {
					return true
				}
			}
			return false
		}
		for _, item := range results {
			if isReferenceUpdate(item) {
				continue
			}
			if item.Status == "current" {
				fmt.Fprintf(out, "[OK] %s: %s\n", item.Component, item.Installed)
			} else {
				fmt.Fprintf(out, "[ATUALIZAR] %s: %s -> %s\n", item.Component, item.Installed, item.LatestSource)
			}
		}
		if len(referenceUpdates) > 0 {
			first := referenceUpdates[0]
			fmt.Fprintf(out, "[ATUALIZAR] referencia nQuake: %s -> %s (%d componentes)\n",
				prefix(first.Installed, 12), prefix(first.LatestSource, 12), len(referenceUpdates))
		} else if referenceNote != nil && referenceNote.Value("upstream") != referenceNote.Value("pinned") {
			fmt.Fprintf(out, "[OK] referencia nQuake: upstream %s alterou apenas arquivos fora do x86QW; preservado %s\n",
				prefix(pyjson.Repr(referenceNote.Value("upstream")), 12), prefix(pyjson.Repr(referenceNote.Value("pinned")), 12))
		}
		for _, summary := range SummarizeDelta(delta) {
			fmt.Fprintf(out, "[ATUALIZAR] %s\n", summary)
		}
		if len(review) > 0 {
			fmt.Fprintf(out, "[REVISAR] %d candidato(s) remoto(s) ainda não possuem SHA-256 previamente fixado; nenhum deles pode ser promovido por update.\n", len(review))
		}
		fmt.Fprintf(out, "\n%d componente(s) e %d arquivo(s) envolvidos em mudancas.\n", len(outdated), len(delta))
	}
	if len(outdated) > 0 || len(delta) > 0 || len(review) > 0 {
		return 2, nil
	}
	return 0, nil
}

func prefix(value string, length int) string {
	runes := []rune(value)
	if len(runes) > length {
		return string(runes[:length])
	}
	return value
}
