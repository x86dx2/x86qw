package content

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// A component follows a new nQuake revision on its own when its consumed
// files changed. Its x86QW customizations are pinned to the SHA-256 of the
// file they edit; a customization whose text still applies to the new file
// is pinned to it again, and one that no longer applies stops the move for
// review.

// referenceRepin re-pins one customization to the new upstream file.
type referenceRepin struct {
	field  string // text_replacements or archive_text_replacements
	index  int
	sha256 string
}

// referenceMove is a component that follows the discovered revision.
type referenceMove struct {
	owner   string
	changed []string // consumed upstream paths that changed
	repins  []referenceRepin
}

// changedReferenceFiles lists the upstream paths owner consumes whose
// content differs from the files it pins (added, changed or removed).
func changedReferenceFiles(owner string, upstream *NQuakeUpstream, assets []Asset, manifest *Object) []string {
	pinned := map[string]string{}
	files, _ := manifest.Value("files").(*Object)
	for _, relative := range files.Keys() {
		upstream, ok := nquakePath(relative)
		if ok && pyjson.Eq(pyjson.Get(files, relative, "package"), owner) {
			pinned[upstream] = pyjson.Repr(pyjson.Get(files, relative, "sha256"))
		}
	}
	discovered := map[string]bool{}
	var changed []string
	for _, asset := range assets {
		if asset.Component != "nquake" || asset.Subcomponent != owner {
			continue
		}
		path, _ := nquakePath(asset.Path)
		discovered[path] = true
		if pinned[path] != upstream.Files[path].SHA256 {
			changed = append(changed, path)
		}
	}
	for path := range pinned {
		if !discovered[path] {
			changed = append(changed, path)
		}
	}
	return sortedStrings(changed)
}

func sortedStrings(values []string) []string {
	set := map[string]bool{}
	for _, value := range values {
		set[value] = true
	}
	return sortedKeys(set)
}

// planReferenceMove decides whether owner can follow upstream on its own.
// It returns the reason it cannot, or the re-pins its customizations need.
func (p *Project) planReferenceMove(owner string, release *Object, upstream *NQuakeUpstream, assets []Asset, manifest *Object, scratch string) (referenceMove, string, error) {
	move := referenceMove{owner: owner, changed: changedReferenceFiles(owner, upstream, assets, manifest)}
	fetched := map[string][]byte{}
	fetch := func(path string) ([]byte, bool, error) {
		if data, ok := fetched[path]; ok {
			return data, true, nil
		}
		file, ok := upstream.Files[path]
		if !ok {
			return nil, false, nil
		}
		target := filepath.Join(scratch, fmt.Sprintf("nquake-%d", len(fetched)))
		url := "https://raw.githubusercontent.com/" + nquakeRepository + "/" + upstream.Revision + "/" + quotePath(path)
		if err := p.Pinned(url, target, file.Size, file.SHA256, 3); err != nil {
			return nil, false, err
		}
		data, err := os.ReadFile(target)
		if err != nil {
			return nil, false, err
		}
		fetched[path] = data
		return data, true, nil
	}
	for _, path := range move.changed {
		for _, layer := range objects(getOr(release, "archive_layers", []any{})) {
			if layer.Value("reference_source") == path {
				return move, fmt.Sprintf("%s changed and the x86QW layer over it needs its composition policy reviewed (%s)", path, pyjson.Repr(layer.Value("policy"))), nil
			}
		}
		for index, replacement := range objects(getOr(release, "text_replacements", []any{})) {
			if replacement.Value("target") != path {
				continue
			}
			data, present, err := fetch(path)
			if err != nil {
				return move, "", err
			}
			if !present || bytes.Count(data, []byte(pyjson.Repr(replacement.Value("before")))) != countInt(replacement) {
				return move, fmt.Sprintf("the x86QW replacement in %s no longer applies to the new file; review text_replacements", path), nil
			}
			move.repins = append(move.repins, referenceRepin{"text_replacements", index, sha256Hex(data)})
		}
		for index, replacement := range objects(getOr(release, "archive_text_replacements", []any{})) {
			if replacement.Value("target_archive") != path {
				continue
			}
			data, present, err := fetch(path)
			if err != nil {
				return move, "", err
			}
			if !present {
				return move, fmt.Sprintf("%s left nQuake and has x86QW replacements; review archive_text_replacements", path), nil
			}
			members, err := ReadZipMembers(data, "nQuake "+path)
			if err != nil {
				return move, "", err
			}
			member, ok := members[pyjson.Repr(replacement.Value("member"))]
			switch {
			case ok && pyjson.Eq(replacement.Value("source_sha256"), sha256Hex(member)):
			case ok && bytes.Count(member, []byte(pyjson.Repr(replacement.Value("before")))) == countInt(replacement):
				move.repins = append(move.repins, referenceRepin{"archive_text_replacements", index, sha256Hex(member)})
			default:
				return move, fmt.Sprintf("the x86QW replacement in %s:%s no longer applies; review archive_text_replacements", path, pyjson.Repr(replacement.Value("member"))), nil
			}
		}
	}
	return move, "", nil
}

var x86qwRevision = regexp.MustCompile(`([+-]x86qw\.)([0-9]+)$`)

// applyReferenceMove pins release to revision, carries the revision into
// its version, re-pins its customizations and, when the version does not
// name the nQuake revision, bumps its x86QW revision so the rebuilt package
// gets a new identity.
func applyReferenceMove(release *Object, revision string, move referenceMove) {
	before := pyjson.Repr(release.Value("version"))
	MoveReferenceRelease(release, revision)
	if pyjson.Eq(release.Value("version"), before) {
		bump := func(value string) string {
			match := x86qwRevision.FindStringSubmatchIndex(value)
			if match == nil {
				return value + "+x86qw.1"
			}
			number, _ := strconv.Atoi(value[match[4]:match[5]])
			return value[:match[4]] + strconv.Itoa(number+1)
		}
		release.Set("version", bump(before))
		if tag, ok := release.Value("distribution_tag").(string); ok && x86qwRevision.MatchString(tag) {
			release.Set("distribution_tag", bump(tag))
		}
	}
	for _, repin := range move.repins {
		objects(release.Value(repin.field))[repin.index].Set("source_sha256", repin.sha256)
	}
}
