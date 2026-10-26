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

// Upstream freshness: `x86qw-content check`.

// UpdateResult is one component freshness line. Error is set when the
// component's upstream could not be consulted; the others are unaffected.
type UpdateResult struct {
	Component, Installed, LatestSource, Status, Strategy, Error string
}

func (r UpdateResult) object() *Object {
	var failure any
	if r.Error != "" {
		failure = r.Error
	}
	return object("component", r.Component, "installed", r.Installed, "latest_source", r.LatestSource, "status", r.Status,
		"strategy", r.Strategy, "error", failure)
}

// IsReferenceStrategy reports the strategies pinned to the nQuake reference.
func IsReferenceStrategy(strategy string) bool {
	return strategy == "reference-snapshot" || strategy == "reference-overlay"
}

// CheckUpdates compares each release with its upstream.
func (p *Project) CheckUpdates(releases *Object, online bool) ([]UpdateResult, error) {
	// Whether a moved nQuake revision changes a component depends on the
	// files it consumes, which Check and Update compare per component; here
	// the reference only has to answer.
	reference := releases.Value("reference").(*Object)
	var referenceErr error
	if online {
		_, referenceErr = p.GitHubRefRevision(pyjson.Repr(reference.Value("repository")), "refs/heads/master")
	}
	var results []UpdateResult
	components := releases.Value("components").(*Object)
	for _, identifier := range components.Keys() {
		release := components.Value(identifier).(*Object)
		expected := pyjson.Repr(release.Value("version"))
		actual, status := expected, "current"
		strategy := pyjson.Repr(release.Value("strategy"))
		if referenceErr != nil && IsReferenceStrategy(strategy) {
			results = append(results, UpdateResult{identifier, expected, expected, "unavailable", strategy, referenceErr.Error()})
			continue
		}
		upstream, hasUpstream := release.Value("upstream").(*Object)
		if strategy == "reference-overlay" && hasUpstream {
			actual = pyjson.Repr(upstream.Value("release"))
		}
		if repository, ok := pyjson.Get(release, "upstream", "repository").(string); online && hasUpstream && ok {
			latest, err := p.GitHubLatestRelease(repository)
			if err != nil {
				results = append(results, UpdateResult{identifier, expected, expected, "unavailable", strategy, err.Error()})
				continue
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
				results = append(results, UpdateResult{identifier, expected, expected, "unavailable", strategy, err.Error()})
				continue
			}
			if err == nil && !found {
				failure := (&HTTPError{Status: 404, URL: pyjson.Repr(artifact.Value("url"))}).Error()
				results = append(results, UpdateResult{identifier, expected, expected, "unavailable", strategy, failure})
				continue
			}
			if err != nil || gotSize != size || gotDigest != digest {
				actual, status = "source-artifact-changed", "update-available"
			}
		}
		results = append(results, UpdateResult{identifier, expected, actual, status, strategy, ""})
	}
	return results, nil
}

// DeltaItem is one distribution change.
type DeltaItem struct{ Path, Status, Reason string }

// DistributionDelta lists how the assets differ from the manifest.
func DistributionDelta(assets []Asset, manifest *Object) ([]DeltaItem, error) {
	files, ok := manifest.Value("files").(*Object)
	if !ok {
		return nil, fmt.Errorf("dist/manifest.json has no valid files collection")
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
			reason = "new"
		case !pyjson.EqInt(current.Value("size"), asset.ExpectedSize):
			reason = "size changed"
		case asset.ExpectedSHA256 != "" && !pyjson.Eq(current.Value("sha256"), asset.ExpectedSHA256):
			reason = "content changed"
		case !pyjson.Eq(current.Value("url"), asset.URL):
			reason = "origin changed"
		}
		if reason != "" {
			changes = append(changes, DeltaItem{path, "update-available", reason})
		}
	}
	for _, path := range files.Keys() {
		if _, ok := desired[path]; !ok {
			changes = append(changes, DeltaItem{path, "obsolete", "no longer in the current set"})
		}
	}
	sort.SliceStable(changes, func(i, j int) bool { return changes[i].Path < changes[j].Path })
	return changes, nil
}

// The nQuake files the distribution consumes live in one tree,
// dist/distributions/nquake/, laid out like the nQuake repository. Each
// component pins its own revision, recorded in the URL of each of its
// files.
const nquakeDir = "distributions/nquake"

// nquakePath is the nQuake repository path of a distribution path.
func nquakePath(path string) (string, bool) {
	rest, ok := strings.CutPrefix(path, nquakeDir+"/")
	return rest, ok && rest != ""
}

var nquakeRawURL = regexp.MustCompile(`^https://raw\.githubusercontent\.com/nQuake/distfiles/([0-9a-f]{40})/`)

// nquakeURLRevision is the nQuake revision a raw file URL points at.
func nquakeURLRevision(url string) string {
	if match := nquakeRawURL.FindStringSubmatch(url); match != nil {
		return match[1]
	}
	return ""
}

// SummarizeDelta summarizes a delta; the nQuake files are
// counted by kind of change.
func SummarizeDelta(delta []DeltaItem) []string {
	var others []string
	reasons := map[string]int{}
	for _, item := range delta {
		if _, ok := nquakePath(item.Path); ok {
			reasons[item.Reason]++
			continue
		}
		others = append(others, item.Path+" ("+item.Reason+")")
	}
	if len(reasons) > 0 {
		var parts []string
		for _, reason := range sortedKeys(reasons) {
			parts = append(parts, fmt.Sprintf("%d %s", reasons[reason], reason))
		}
		others = append([]string{"nQuake: " + strings.Join(parts, ", ")}, others...)
	}
	return others
}

// ReferenceRevision is the one nQuake revision the assets come from.
func ReferenceRevision(assets []Asset) (string, error) {
	revisions := map[string]bool{}
	for _, asset := range assets {
		if asset.Component == "nquake" {
			revisions[nquakeURLRevision(asset.URL)] = true
		}
	}
	if len(revisions) != 1 {
		return "", fmt.Errorf("nQuake discovery did not produce exactly one revision")
	}
	revision := sortedKeys(revisions)[0]
	if !hex40Pattern.MatchString(revision) {
		return "", fmt.Errorf("invalid nQuake revision: %s", revision)
	}
	return revision, nil
}

// GitBlobSHA1 is the Git blob hash of the file at path.
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

// ReferenceChanges lists the components whose consumed nQuake files differ
// between the discovered upstream revision and the files they pin: a file
// added, removed or changed. A component missing from the result keeps its
// revision even when the upstream moved.
func ReferenceChanges(assets []Asset, manifest *Object, root string) (map[string]bool, error) {
	files, ok := manifest.Value("files").(*Object)
	if !ok {
		return nil, fmt.Errorf("manifest has no files")
	}
	type record struct {
		relative, owner string
		metadata        *Object
	}
	current := map[string]record{}
	for _, relative := range files.Keys() {
		metadata, isObject := files.Value(relative).(*Object)
		if upstream, ok := nquakePath(relative); ok && isObject {
			owner, _ := metadata.Value("package").(string)
			current[upstream] = record{relative, owner, metadata}
		}
	}
	changed := map[string]bool{}
	discovered := map[string]bool{}
	for _, asset := range assets {
		if asset.Component != "nquake" {
			continue
		}
		upstream, _ := nquakePath(asset.Path)
		discovered[upstream] = true
		item, ok := current[upstream]
		if !ok || item.owner != asset.Subcomponent || asset.ExpectedGitSHA1 == "" {
			changed[asset.Subcomponent] = true
			if ok {
				changed[item.owner] = true
			}
			continue
		}
		path := joinPosix(root, item.relative)
		info, err := os.Stat(path)
		if !isFile(path) || isSymlink(path) || err != nil || info.Size() != asset.ExpectedSize || !pyjson.EqInt(item.metadata.Value("size"), asset.ExpectedSize) {
			changed[item.owner] = true
			continue
		}
		digest, err := GitBlobSHA1(path)
		if err != nil {
			return nil, err
		}
		if digest != asset.ExpectedGitSHA1 {
			changed[item.owner] = true
		}
	}
	for upstream, item := range current {
		if !discovered[upstream] {
			changed[item.owner] = true
		}
	}
	delete(changed, "")
	return changed, nil
}

// PreserveCurrentReferenceAssets replaces the discovered nQuake files of
// every component keep selects with the files it pins today.
func PreserveCurrentReferenceAssets(assets []Asset, manifest *Object, root string, keep func(owner string) bool) ([]Asset, error) {
	files, ok := manifest.Value("files").(*Object)
	if !ok {
		return nil, fmt.Errorf("manifest has no files")
	}
	var preserved []Asset
	for _, asset := range assets {
		if asset.Component != "nquake" || !keep(asset.Subcomponent) {
			preserved = append(preserved, asset)
		}
	}
	for _, relative := range files.Keys() {
		metadata, isObject := files.Value(relative).(*Object)
		if _, ok := nquakePath(relative); !ok || !isObject {
			continue
		}
		if owner, _ := metadata.Value("package").(string); !keep(owner) {
			continue
		}
		path := joinPosix(root, relative)
		if !isFile(path) || isSymlink(path) {
			return nil, fmt.Errorf("registered nQuake file is missing: %s", relative)
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

// Check reports, component by component, which upstreams moved. selected
// limits the report (empty = every component). An upstream that cannot be
// consulted is reported for its own components only. The exit code is 2
// when any update exists, 1 when nothing moved but some component could not
// be checked, and 0 otherwise.
func (p *Project) Check(out, info io.Writer, offline, asJSON bool, selected []string) (int, error) {
	releases, err := p.Releases()
	if err != nil {
		return 0, err
	}
	ids := UpdatableIDs(releases)
	if err := ValidateSelection(selected, ids); err != nil {
		return 0, err
	}
	wanted := setOf(selected...)
	inScope := func(id string) bool { return len(selected) == 0 || wanted[id] }
	results, err := p.CheckUpdates(releases, !offline)
	if err != nil {
		return 0, err
	}
	var delta []DeltaItem
	var review []string
	var referenceNote *Object
	var nquakeNote *NQuakeReport
	unavailable := Unavailable{}
	if !offline {
		progress := out
		if asJSON {
			progress = info
		}
		fmt.Fprintln(progress, "[INFO] Checking clients and incorporated files...")
		var assets []Asset
		if assets, unavailable, err = p.DiscoverAssets(); err != nil {
			return 0, err
		}
		manifest, err := LoadManifest(p.Path(ManifestPath))
		if err != nil {
			return 0, err
		}
		nquakeOwners := NQuakeOwners(manifest, ids)
		if reason := unavailable["nquake"]; reason != "" {
			for index := range results {
				if nquakeOwners[results[index].Component] && results[index].Error == "" {
					results[index].Status, results[index].LatestSource, results[index].Error = "unavailable", results[index].Installed, reason
				}
			}
		} else {
			head, err := ReferenceRevision(assets)
			if err != nil {
				return 0, err
			}
			changed, err := ReferenceChanges(assets, manifest, p.Path(DistDir))
			if err != nil {
				return 0, err
			}
			pinned := pyjson.NewObject()
			revisions := ReferenceRevisions(releases)
			for _, owner := range sortedKeys(revisions) {
				pinned.Set(owner, revisions[owner])
			}
			changedList := []any{}
			for _, owner := range sortedKeys(changed) {
				changedList = append(changedList, owner)
			}
			referenceNote = object("pinned", pinned, "upstream", head, "changed_components", changedList)
			if len(selected) == 0 {
				if nquakeNote, _, err = p.nquakeReport(); err != nil {
					return 0, err
				}
			}
			moving := func(owner string) bool { return changed[owner] && inScope(owner) }
			if assets, err = PreserveCurrentReferenceAssets(assets, manifest, p.Path(DistDir), func(owner string) bool { return !moving(owner) }); err != nil {
				return 0, err
			}
			for index := range results {
				item := &results[index]
				switch {
				case item.Error != "" || !nquakeOwners[item.Component]:
				case changed[item.Component] && (IsReferenceStrategy(item.Strategy) || item.Status == "current"):
					item.Status, item.LatestSource = "update-available", "nQuake "+head[:12]
				case !changed[item.Component] && IsReferenceStrategy(item.Strategy):
					item.Status, item.LatestSource = "current", item.Installed
				}
			}
		}
		sourceDown := func(owner string) bool {
			if owner == "ezquake" {
				return unavailable["ezquake"] != ""
			}
			return unavailable["nquake"] != "" && nquakeOwners[owner]
		}
		if assets, err = KeepCurrent(assets, manifest, ids, func(owner string) bool { return !inScope(owner) || sourceDown(owner) }); err != nil {
			return 0, err
		}
		if assets, err = PinAssetsFromManifest(assets, manifest); err != nil {
			return 0, err
		}
		review = UnpinnedAssetPaths(assets)
		if delta, err = DistributionDelta(assets, manifest); err != nil {
			return 0, err
		}
	}
	var shown, outdated, failed []UpdateResult
	for _, item := range results {
		if !inScope(item.Component) {
			continue
		}
		shown = append(shown, item)
		switch {
		case item.Error != "":
			failed = append(failed, item)
		case item.Status != "current":
			outdated = append(outdated, item)
		}
	}
	ezquakeDown := inScope("ezquake") && unavailable["ezquake"] != ""
	if asJSON {
		components := make([]any, len(shown))
		for index, item := range shown {
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
		sources := pyjson.NewObject()
		for _, source := range sortedKeys(setOfKeys(unavailable)) {
			sources.Set(source, unavailable[source])
		}
		var note any
		if referenceNote != nil {
			note = referenceNote
		}
		payload, _ := pyjson.Encode(object("components", components, "distribution", distribution, "reference", note,
			"review_required", reviewList, "unavailable_sources", sources), false)
		if nquakeNote != nil {
			decoded, _ := pyjson.Decode(payload, pyjson.LastWins)
			decoded.(*Object).Set("nquake", nquakeNote.Object())
			payload, _ = pyjson.Encode(decoded, false)
		}
		fmt.Fprintln(out, string(payload))
	} else {
		for _, item := range shown {
			switch {
			case item.Error != "":
				fmt.Fprintf(out, "[UNAVAILABLE] %s: %s\n", item.Component, item.Error)
			case item.Status == "current":
				fmt.Fprintf(out, "[OK] %s: %s\n", item.Component, item.Installed)
			default:
				fmt.Fprintf(out, "[UPDATE] %s: %s -> %s\n", item.Component, item.Installed, item.LatestSource)
			}
		}
		if ezquakeDown {
			fmt.Fprintf(out, "[UNAVAILABLE] ezquake: %s\n", unavailable["ezquake"])
		}
		if referenceNote != nil && len(referenceNote.Value("changed_components").([]any)) == 0 {
			head := pyjson.Repr(referenceNote.Value("upstream"))
			fmt.Fprintf(out, "[OK] nQuake %s changed no consumed file; each component keeps its revision.\n", head[:12])
		}
		for _, summary := range SummarizeDelta(delta) {
			fmt.Fprintf(out, "[UPDATE] %s\n", summary)
		}
		if len(review) > 0 {
			fmt.Fprintf(out, "[REVIEW] %d remote candidate(s) have no previously pinned SHA-256 yet; none of them can be promoted by update.\n", len(review))
		}
		if nquakeNote != nil {
			for _, line := range nquakeNote.Lines() {
				fmt.Fprintln(out, line)
			}
		}
		unchecked := len(failed)
		if ezquakeDown {
			unchecked++
		}
		fmt.Fprintf(out, "\n%d component(s) and %d file(s) involved in changes", len(outdated), len(delta))
		if unchecked > 0 {
			fmt.Fprintf(out, "; %d component(s) could not be checked", unchecked)
		}
		fmt.Fprintln(out, ".")
	}
	switch {
	case len(outdated) > 0 || len(delta) > 0 || len(review) > 0 || (nquakeNote != nil && nquakeNote.Pending()):
		return 2, nil
	case len(failed) > 0 || ezquakeDown:
		return 1, nil
	}
	return 0, nil
}

func setOfKeys(values Unavailable) map[string]bool {
	keys := map[string]bool{}
	for key := range values {
		keys[key] = true
	}
	return keys
}

func prefix(value string, length int) string {
	runes := []rune(value)
	if len(runes) > length {
		return string(runes[:length])
	}
	return value
}
