package content

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
)

// Component payload resolution (maintenance/tools/component_sources.py).

// Payload is (upstream source, package member, bytes, override metadata).
type Payload struct {
	Source    string
	Member    string
	Data      []byte
	Overrides []*Object
}

// SourceContext mirrors ComponentSourceContext.
type SourceContext struct {
	Distribution string
	Catalog      *Object
	Releases     *Object
	Components   map[string]*Object
	ComponentIDs []string
	// Snapshots maps each nQuake revision to its source tree; Located maps
	// each consumed upstream path to the revision holding it.
	Snapshots     map[string]string
	Located       map[string]string
	Partition     map[string][]string
	ManifestFiles *Object
}

func sha256Hex(payload []byte) string {
	sum := sha256.Sum256(payload)
	return hex.EncodeToString(sum[:])
}

func meta(pairs ...any) *Object { return object(pairs...) }

// LoadSourceContext mirrors load_source_context. Every consumed upstream path
// lives in exactly one nQuake source tree: the revision its owner pins.
func LoadSourceContext(distribution string, catalog, releases *Object) (*SourceContext, error) {
	distribution = resolve(distribution)
	snapshots, err := DiscoverSnapshots(distribution)
	if err != nil {
		return nil, err
	}
	located := map[string]string{}
	var paths []string
	for _, revision := range sortedKeys(snapshots) {
		snapshot := snapshots[revision]
		err = filepath.WalkDir(snapshot, func(path string, entry os.DirEntry, err error) error {
			if err != nil || path == snapshot {
				return err
			}
			if !isFile(path) {
				return nil
			}
			if isSymlink(path) {
				return fmt.Errorf("canonical component source must not be a symlink: %s", path)
			}
			relative, _ := filepath.Rel(snapshot, path)
			relative = filepath.ToSlash(relative)
			if other, seen := located[relative]; seen {
				return fmt.Errorf("nQuake source is present in two revisions (%s, %s): %s", other[:12], revision[:12], relative)
			}
			located[relative] = revision
			paths = append(paths, relative)
			return nil
		})
		if err != nil {
			return nil, err
		}
	}
	sort.Strings(paths)
	partition, err := ValidateTreePartition(catalog, paths, "reference")
	if err != nil {
		return nil, err
	}
	pinned := ReferenceRevisions(releases)
	used := map[string]bool{}
	for _, identifier := range sortedKeys(partition) {
		if len(partition[identifier]) == 0 {
			continue
		}
		revision := pinned[identifier]
		if revision == "" {
			return nil, fmt.Errorf("component consumes nQuake files but pins no reference revision: %s", identifier)
		}
		for _, path := range partition[identifier] {
			if located[path] != revision {
				return nil, fmt.Errorf("nQuake source of %s is not under its pinned revision %s: %s", identifier, revision[:12], path)
			}
		}
		used[revision] = true
	}
	for revision := range snapshots {
		if !used[revision] {
			return nil, fmt.Errorf("nQuake source tree is not pinned by any component: %s", revision)
		}
	}
	context := &SourceContext{
		Distribution: distribution, Catalog: catalog, Releases: releases,
		Components: ComponentsByID(catalog), ComponentIDs: ComponentIDs(catalog),
		Snapshots: snapshots, Located: located, Partition: partition,
	}
	manifestPath := filepath.Join(distribution, "manifest.json")
	if isFile(manifestPath) && !isSymlink(manifestPath) {
		value, err := ReadJSON(manifestPath)
		if err != nil {
			return nil, fmt.Errorf("invalid distribution manifest: %s", manifestPath)
		}
		manifest, ok := value.(*Object)
		files, filesOK := pyjson.Get(value, "files").(*Object)
		if !ok || !pyjson.EqInt(manifest.Value("format"), 1) || !pyjson.Eq(manifest.Value("project"), "x86qw") || !filesOK {
			return nil, fmt.Errorf("invalid distribution manifest: %s", manifestPath)
		}
		context.ManifestFiles = files
	}
	return context, nil
}

func readRegular(path, label string) ([]byte, error) {
	if !isFile(path) || isSymlink(path) {
		return nil, fmt.Errorf("%s is missing or unsafe: %s", label, path)
	}
	return os.ReadFile(path)
}

func (c *SourceContext) projectRoot() string { return filepath.Dir(c.Distribution) }

// RevisionOf is the nQuake revision identifier consumes ("" for none).
func (c *SourceContext) RevisionOf(identifier string) string {
	return ReferenceRevisionOf(c.Releases, identifier)
}

// Revisions are the nQuake revisions present in the distribution, sorted.
func (c *SourceContext) Revisions() []string { return sortedKeys(c.Snapshots) }

func (c *SourceContext) verifiedReferencePayload(upstreamPath string) ([]byte, error) {
	revision, ok := c.Located[upstreamPath]
	if !ok {
		return nil, fmt.Errorf("canonical nQuake source is missing: %s", upstreamPath)
	}
	path := joinPosix(c.Snapshots[revision], upstreamPath)
	payload, err := readRegular(path, "canonical nQuake source")
	if err != nil {
		return nil, err
	}
	if c.ManifestFiles == nil {
		return payload, nil
	}
	relative, _ := filepath.Rel(c.Distribution, path)
	relative = filepath.ToSlash(relative)
	record, ok := c.ManifestFiles.Value(relative).(*Object)
	if !ok {
		return nil, fmt.Errorf("canonical source is absent from the distribution manifest: %s", relative)
	}
	if !pyjson.EqInt(record.Value("size"), int64(len(payload))) || !pyjson.Eq(record.Value("sha256"), sha256Hex(payload)) {
		return nil, fmt.Errorf("canonical source failed distribution integrity: %s", relative)
	}
	return payload, nil
}

func sortedKeys[V any](values map[string]V) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

// RewriteZipMembers mirrors rewrite_zip_members.
func RewriteZipMembers(payload []byte, replacements map[string][]byte, additions map[string]bool) ([]byte, error) {
	for name := range additions {
		if _, ok := replacements[name]; !ok {
			return nil, fmt.Errorf("component archive additions must have a declared payload")
		}
	}
	original, err := ReadZipMembers(payload, "base component archive")
	if err != nil {
		return nil, err
	}
	var missing, collisions []string
	for name := range replacements {
		if _, ok := original[name]; !ok && !additions[name] {
			missing = append(missing, name)
		}
	}
	for name := range additions {
		if _, ok := original[name]; ok {
			collisions = append(collisions, name)
		}
	}
	sort.Strings(missing)
	sort.Strings(collisions)
	if len(missing) > 0 {
		return nil, fmt.Errorf("component override target is missing: %s", missing[0])
	}
	if len(collisions) > 0 {
		return nil, fmt.Errorf("component archive addition already exists: %s", collisions[0])
	}
	for name, data := range replacements {
		original[name] = data
	}
	return BuildZipMembers(original)
}

func moveZipMembers(payload []byte, order []string, moves map[string]string) ([]byte, error) {
	original, err := ReadZipMembers(payload, "base component archive")
	if err != nil {
		return nil, err
	}
	var missing []string
	for old := range moves {
		if _, ok := original[old]; !ok {
			missing = append(missing, old)
		}
	}
	sort.Strings(missing)
	if len(missing) > 0 {
		return nil, fmt.Errorf("component archive move source is missing: %s", missing[0])
	}
	destinations := map[string]bool{}
	var collisions []string
	for _, destination := range moves {
		destinations[destination] = true
		if _, exists := original[destination]; exists {
			if _, moved := moves[destination]; !moved {
				collisions = append(collisions, destination)
			}
		}
	}
	if len(collisions) > 0 || len(destinations) != len(moves) {
		if len(collisions) == 0 {
			collisions = sortedKeys(destinations)
		}
		sort.Strings(collisions)
		return nil, fmt.Errorf("component archive move destination already exists: %s", collisions[0])
	}
	// original_files[new] = original_files.pop(old), in declaration order.
	for _, old := range order {
		data, ok := original[old]
		if !ok {
			return nil, fmt.Errorf("KeyError: %s", pyRepr(old))
		}
		delete(original, old)
		original[moves[old]] = data
	}
	return BuildZipMembers(original)
}

func removeZipMembers(payload []byte, removals map[string]bool) ([]byte, error) {
	original, err := ReadZipMembers(payload, "base component archive")
	if err != nil {
		return nil, err
	}
	var missing []string
	for name := range removals {
		if _, ok := original[name]; !ok {
			missing = append(missing, name)
		}
	}
	sort.Strings(missing)
	if len(missing) > 0 {
		return nil, fmt.Errorf("component archive removal target is missing: %s", missing[0])
	}
	for name := range removals {
		delete(original, name)
	}
	return BuildZipMembers(original)
}

type pakMember struct {
	name string
	data []byte
}

func readPakMembers(payload []byte) ([]pakMember, error) {
	if len(payload) < 12 || string(payload[:4]) != "PACK" {
		return nil, fmt.Errorf("base component PAK has an invalid header")
	}
	offset := int(binary.LittleEndian.Uint32(payload[4:8]))
	size := int(binary.LittleEndian.Uint32(payload[8:12]))
	if size == 0 || size%64 != 0 || offset < 12 || offset+size > len(payload) {
		return nil, fmt.Errorf("base component PAK has an invalid directory")
	}
	var members []pakMember
	seen := map[string]bool{}
	for cursor := offset; cursor < offset+size; cursor += 64 {
		raw := payload[cursor : cursor+56]
		if index := bytes.IndexByte(raw, 0); index >= 0 {
			raw = raw[:index]
		}
		for _, b := range raw {
			if b >= 0x80 {
				return nil, fmt.Errorf("base component PAK has a non-ASCII member")
			}
		}
		name := string(raw)
		fileOffset := int(binary.LittleEndian.Uint32(payload[cursor+56:]))
		fileSize := int(binary.LittleEndian.Uint32(payload[cursor+60:]))
		path := purePosix(name)
		if name == "" || strings.Contains(name, `\`) || path.absolute || path.hasPart("..") || seen[name] ||
			fileOffset < 12 || fileOffset+fileSize > offset {
			return nil, fmt.Errorf("base component PAK has an unsafe member: %s", pyRepr(name))
		}
		seen[name] = true
		members = append(members, pakMember{name, payload[fileOffset : fileOffset+fileSize]})
	}
	return members, nil
}

func buildPakMembers(members []pakMember) ([]byte, error) {
	if len(members) == 0 {
		return nil, fmt.Errorf("component PAK cannot be empty")
	}
	var body bytes.Buffer
	body.Write(make([]byte, 12))
	type entry struct {
		name         string
		offset, size int
	}
	var directory []entry
	for _, member := range members {
		if len(member.name) > 56 {
			return nil, fmt.Errorf("component PAK member name is too long: %s", member.name)
		}
		directory = append(directory, entry{member.name, body.Len(), len(member.data)})
		body.Write(member.data)
	}
	directoryOffset := body.Len()
	for _, item := range directory {
		name := make([]byte, 56)
		copy(name, item.name)
		body.Write(name)
		binary.Write(&body, binary.LittleEndian, uint32(item.offset))
		binary.Write(&body, binary.LittleEndian, uint32(item.size))
	}
	payload := body.Bytes()
	copy(payload[:4], "PACK")
	binary.LittleEndian.PutUint32(payload[4:], uint32(directoryOffset))
	binary.LittleEndian.PutUint32(payload[8:], uint32(len(directory)*64))
	return payload, nil
}

func removePakMembers(payload []byte, removals map[string]bool) ([]byte, error) {
	members, err := readPakMembers(payload)
	if err != nil {
		return nil, err
	}
	names := map[string]bool{}
	for _, member := range members {
		names[member.name] = true
	}
	var missing []string
	for name := range removals {
		if !names[name] {
			missing = append(missing, name)
		}
	}
	sort.Strings(missing)
	if len(missing) > 0 {
		return nil, fmt.Errorf("component PAK removal target is missing: %s", missing[0])
	}
	var kept []pakMember
	for _, member := range members {
		if !removals[member.name] {
			kept = append(kept, member)
		}
	}
	return buildPakMembers(kept)
}

func countInt(object *Object) int {
	value, _ := getOr(object, "count", pyjson.Number("1")).(pyjson.Number)
	count, _ := value.Int()
	if b, ok := getOr(object, "count", nil).(bool); ok && b {
		count = 1
	}
	return int(count)
}

func (c *SourceContext) applyArchiveTextReplacements(payload []byte, targetArchive string, release *Object) ([]byte, []*Object, error) {
	var replacements []*Object
	for _, replacement := range objects(getOr(release, "archive_text_replacements", []any{})) {
		if replacement.Value("target_archive") == targetArchive {
			replacements = append(replacements, replacement)
		}
	}
	if len(replacements) == 0 {
		return payload, nil, nil
	}
	current, err := ReadZipMembers(payload, "archive text target "+targetArchive)
	if err != nil {
		return nil, nil, err
	}
	memberPayloads := map[string][]byte{}
	var applied []*Object
	for _, replacement := range replacements {
		member := pyjson.Repr(replacement.Value("member"))
		data, ok := memberPayloads[member]
		if !ok {
			if data, ok = current[member]; !ok {
				return nil, nil, fmt.Errorf("archive text replacement member is missing: %s", member)
			}
		}
		sourceSHA := sha256Hex(data)
		if !pyjson.Eq(sourceSHA, replacement.Value("source_sha256")) {
			return nil, nil, fmt.Errorf("archive member source failed integrity: %s", member)
		}
		before := []byte(pyjson.Repr(replacement.Value("before")))
		after := []byte(pyjson.Repr(replacement.Value("after")))
		count := countInt(replacement)
		if bytes.Count(data, before) != count {
			return nil, nil, fmt.Errorf("archive text replacement is not uniquely applicable: %s", member)
		}
		memberPayloads[member] = bytes.Replace(data, before, after, count)
		applied = append(applied, meta(
			"target_archive", targetArchive, "member", member, "source_sha256", sourceSHA,
			"replacement_sha256", sha256Hex(after), "ownership", "mixed",
		))
	}
	rewritten, err := RewriteZipMembers(payload, memberPayloads, nil)
	return rewritten, applied, err
}

func (c *SourceContext) referenceComponentPayload(upstreamPath string, release *Object) ([]byte, []*Object, error) {
	payload, err := c.verifiedReferencePayload(upstreamPath)
	if err != nil {
		return nil, nil, err
	}
	var applied []*Object
	originalSHA := sha256Hex(payload)
	for _, replacement := range objects(getOr(release, "text_replacements", []any{})) {
		if replacement.Value("target") != upstreamPath {
			continue
		}
		if !pyjson.Eq(originalSHA, replacement.Value("source_sha256")) {
			return nil, nil, fmt.Errorf("text replacement source failed integrity: %s", upstreamPath)
		}
		before := []byte(pyjson.Repr(replacement.Value("before")))
		after := []byte(pyjson.Repr(replacement.Value("after")))
		count := countInt(replacement)
		if bytes.Count(payload, before) != count {
			return nil, nil, fmt.Errorf("text replacement is not uniquely applicable: %s", upstreamPath)
		}
		payload = bytes.Replace(payload, before, after, count)
		applied = append(applied, meta(
			"target", upstreamPath, "source_sha256", pyjson.Repr(replacement.Value("source_sha256")),
			"replacement_sha256", sha256Hex(after), "ownership", "mixed",
		))
	}
	replacements := map[string][]byte{}
	additions := map[string]bool{}
	for _, artifact := range objects(getOr(release, "artifacts", []any{})) {
		artifactMembers := objects(getOr(artifact, "members", []any{}))
		if len(artifactMembers) == 0 {
			continue
		}
		members, err := c.verifiedArtifactMembers(artifact)
		if err != nil {
			return nil, nil, err
		}
		for _, member := range artifactMembers {
			if member.Value("target_archive") != upstreamPath {
				continue
			}
			memberPath := pyjson.Repr(member.Value("path"))
			target := pyjson.Repr(member.Value("target_member"))
			replacements[target] = members[memberPath]
			if getOr(member, "target_mode", "replace") == "add" {
				additions[target] = true
			}
			applied = append(applied, meta(
				"artifact", pyjson.Repr(artifact.Value("url")), "member", memberPath,
				"sha256", pyjson.Repr(member.Value("sha256")),
			))
		}
	}
	if len(replacements) > 0 {
		if payload, err = RewriteZipMembers(payload, replacements, additions); err != nil {
			return nil, nil, err
		}
	}
	payload, archiveApplied, err := c.applyArchiveTextReplacements(payload, upstreamPath, release)
	if err != nil {
		return nil, nil, err
	}
	applied = append(applied, archiveApplied...)
	moves := map[string]string{}
	var moveOrder []string
	for _, move := range objects(getOr(release, "archive_moves", []any{})) {
		if move.Value("target_archive") == upstreamPath {
			source := pyjson.Repr(move.Value("source"))
			if _, seen := moves[source]; !seen {
				moveOrder = append(moveOrder, source)
			}
			moves[source] = pyjson.Repr(move.Value("destination"))
		}
	}
	if len(moves) > 0 {
		if payload, err = moveZipMembers(payload, moveOrder, moves); err != nil {
			return nil, nil, err
		}
		for _, old := range moveOrder {
			applied = append(applied, meta("source", old, "destination", moves[old]))
		}
	}
	removals := map[string]bool{}
	for _, removal := range objects(getOr(release, "archive_removals", []any{})) {
		if removal.Value("target_archive") == upstreamPath {
			removals[pyjson.Repr(removal.Value("member"))] = true
		}
	}
	if len(removals) > 0 {
		if strings.HasSuffix(Casefold(upstreamPath), ".pak") {
			payload, err = removePakMembers(payload, removals)
		} else {
			payload, err = removeZipMembers(payload, removals)
		}
		if err != nil {
			return nil, nil, err
		}
		for _, member := range sortedKeys(removals) {
			applied = append(applied, meta("removed", member))
		}
	}
	return payload, applied, nil
}

func (c *SourceContext) artifactPath(artifact *Object) (string, error) {
	path := joinPosix(c.Distribution, pyjson.Repr(artifact.Value("distribution_path")))
	if isSymlink(path) || !isFile(path) {
		return "", fmt.Errorf("missing or invalid component source artifact: %s", path)
	}
	return path, nil
}

func (c *SourceContext) verifiedArchive(path string, artifact *Object, required []string) (map[string][]byte, error) {
	payload, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if !pyjson.EqInt(artifact.Value("size"), int64(len(payload))) || !pyjson.Eq(artifact.Value("sha256"), sha256Hex(payload)) {
		return nil, fmt.Errorf("component source artifact is unsafe: %s: component source artifact failed SHA-256: %s", path, path)
	}
	members, err := ScanZipFile(path, required)
	if err != nil {
		return nil, fmt.Errorf("component source artifact is unsafe: %s: %v", path, err)
	}
	return members, nil
}

func (c *SourceContext) verifiedArtifactMembers(artifact *Object) (map[string][]byte, error) {
	path, err := c.artifactPath(artifact)
	if err != nil {
		return nil, err
	}
	records := objects(artifact.Value("members"))
	var names []string
	for _, member := range records {
		names = append(names, pyjson.Repr(member.Value("path")))
	}
	selected, err := c.verifiedArchive(path, artifact, names)
	if err != nil {
		return nil, err
	}
	for _, member := range records {
		name := pyjson.Repr(member.Value("path"))
		data := selected[name]
		if !pyjson.EqInt(member.Value("size"), int64(len(data))) || !pyjson.Eq(member.Value("sha256"), sha256Hex(data)) {
			return nil, fmt.Errorf("component source member failed integrity: %s", name)
		}
	}
	return selected, nil
}

func (c *SourceContext) verifiedPackageFiles(artifact *Object) (map[string][]byte, error) {
	path, err := c.artifactPath(artifact)
	if err != nil {
		return nil, err
	}
	name := filepath.Base(path)
	if strings.HasSuffix(name, ".tar.gz") || strings.HasSuffix(name, ".tgz") {
		payload, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		if !pyjson.EqInt(artifact.Value("size"), int64(len(payload))) || !pyjson.Eq(artifact.Value("sha256"), sha256Hex(payload)) {
			return nil, fmt.Errorf("component source artifact failed SHA-256: %s", path)
		}
		compressed, err := gzip.NewReader(bytes.NewReader(payload))
		if err != nil {
			return nil, err
		}
		reader := tar.NewReader(compressed)
		selected := map[string][]byte{}
		for {
			header, err := reader.Next()
			if err == io.EOF {
				break
			}
			if err != nil {
				return nil, err
			}
			if header.Typeflag == tar.TypeDir || header.Typeflag == tar.TypeXGlobalHeader {
				continue
			}
			memberName, err := safePath(tarName(header), "standalone artifact member")
			if err != nil {
				return nil, err
			}
			switch header.Typeflag {
			case tar.TypeReg, '\x00', tar.TypeCont, tar.TypeGNUSparse:
			default:
				return nil, fmt.Errorf("standalone artifact contains an unsafe member: %s", memberName)
			}
			data, err := io.ReadAll(reader)
			if err != nil {
				return nil, err
			}
			selected[memberName] = data
		}
		return selected, nil
	}
	if Casefold(pathSuffix(name)) == ".zip" {
		return c.verifiedArchive(path, artifact, nil)
	}
	return nil, fmt.Errorf("unsupported standalone component archive: %s", path)
}

func releaseSelectors(component *Object, upstreamPath string) []*Object {
	var entries []*Object
	for _, entry := range objects(component.Value("sources")) {
		if sourceOrigin(entry) != "release" {
			continue
		}
		root := pyjson.Repr(entry.Value("path"))
		if upstreamPath != root && !strings.HasPrefix(upstreamPath, root+"/") {
			continue
		}
		if excluded(upstreamPath, getOr(entry, "exclude", []any{})) {
			continue
		}
		entries = append(entries, entry)
	}
	return entries
}

func memberPrefix(mode string) string {
	if mode == "default" {
		return "defaults/"
	}
	return "payload/"
}

func (c *SourceContext) standalonePayloads(identifier string, component, release *Object) ([]Payload, error) {
	var selected []Payload
	packageFiles := map[string][]byte{}
	archiveFiles := map[string]map[string][]byte{}
	archiveMetadata := map[string][]*Object{}
	layered := map[string]bool{}
	for _, layer := range objects(getOr(release, "archive_layers", []any{})) {
		layered[pyjson.Repr(layer.Value("target_archive"))] = true
	}
	matchedRoots := map[string]bool{}
	for _, artifact := range objects(getOr(release, "artifacts", []any{})) {
		files, err := c.verifiedPackageFiles(artifact)
		if err != nil {
			return nil, err
		}
		var duplicates []string
		for name := range files {
			if _, ok := packageFiles[name]; ok {
				duplicates = append(duplicates, name)
			}
		}
		if len(duplicates) > 0 {
			sort.Strings(duplicates)
			return nil, fmt.Errorf("standalone artifacts contain a duplicate member: %s", duplicates[0])
		}
		for name, data := range files {
			packageFiles[name] = data
		}
	}
	for _, upstreamPath := range sortedKeys(packageFiles) {
		payload := packageFiles[upstreamPath]
		owner, err := ComponentForSource(c.Catalog, upstreamPath, "release")
		if err != nil {
			return nil, err
		}
		if owner != identifier {
			continue
		}
		destination, mode, err := DestinationForSource(component, upstreamPath, "release")
		if err != nil {
			return nil, err
		}
		entries := releaseSelectors(component, upstreamPath)
		roots := map[string]bool{}
		for _, entry := range entries {
			roots[pyjson.Repr(entry.Value("path"))] = true
		}
		if len(roots) != 1 {
			return nil, fmt.Errorf("release source has no unique selector: %s", upstreamPath)
		}
		for root := range roots {
			matchedRoots[root] = true
		}
		if mode == "preserve" {
			continue
		}
		if mode == "archive" {
			if len(entries) != 1 {
				return nil, fmt.Errorf("archive source has no unique selector: %s", upstreamPath)
			}
			root := pyjson.Repr(entries[0].Value("path"))
			member := strings.TrimLeft(strings.TrimPrefix(upstreamPath, root), "/")
			if member == "" {
				member = posixName(upstreamPath)
			}
			if archiveFiles[destination] == nil {
				archiveFiles[destination] = map[string][]byte{}
			}
			if _, exists := archiveFiles[destination][member]; exists {
				return nil, fmt.Errorf("component archive member collision: %s", member)
			}
			archiveFiles[destination][member] = payload
			archiveMetadata[destination] = append(archiveMetadata[destination], meta("source", upstreamPath, "sha256", sha256Hex(payload)))
			continue
		}
		selected = append(selected, Payload{upstreamPath, memberPrefix(mode) + destination, payload, nil})
	}
	var missing []string
	for _, entry := range objects(component.Value("sources")) {
		if sourceOrigin(entry) == "release" && !matchedRoots[pyjson.Repr(entry.Value("path"))] {
			missing = append(missing, pyjson.Repr(entry.Value("path")))
		}
	}
	if len(missing) > 0 {
		sort.Strings(missing)
		return nil, fmt.Errorf("standalone component source selects no files: %s", missing[0])
	}
	for _, destination := range sortedKeys(archiveFiles) {
		payload, err := BuildZipMembers(archiveFiles[destination])
		if err != nil {
			return nil, err
		}
		var applied []*Object
		if !layered[destination] {
			if payload, applied, err = c.applyArchiveTextReplacements(payload, destination, release); err != nil {
				return nil, err
			}
		}
		selected = append(selected, Payload{
			"official upstream archive: " + destination, "payload/" + destination, payload,
			append(append([]*Object{}, archiveMetadata[destination]...), applied...),
		})
	}
	for _, copy := range objects(getOr(release, "package_copies", []any{})) {
		source := pyjson.Repr(copy.Value("source"))
		destinationPath := pyjson.Repr(copy.Value("destination"))
		payload, ok := packageFiles[source]
		if !ok {
			return nil, fmt.Errorf("standalone package copy source is missing: %s", source)
		}
		if !pyjson.EqInt(copy.Value("size"), int64(len(payload))) || !pyjson.Eq(copy.Value("sha256"), sha256Hex(payload)) {
			return nil, fmt.Errorf("standalone package copy source failed integrity: %s", source)
		}
		if owner, err := ComponentForSource(c.Catalog, source, "release"); err != nil || owner != identifier {
			return nil, fmt.Errorf("standalone package copy source belongs to another component: %s", source)
		}
		if owner, err := ComponentForSource(c.Catalog, destinationPath, "release"); err != nil || owner != identifier {
			return nil, fmt.Errorf("standalone package copy destination belongs to another component: %s", destinationPath)
		}
		destination, mode, err := DestinationForSource(component, destinationPath, "release")
		if err != nil {
			return nil, err
		}
		if mode == "preserve" {
			continue
		}
		selected = append(selected, Payload{source + " -> " + destinationPath, memberPrefix(mode) + destination, payload,
			[]*Object{meta("source", source, "destination", destinationPath, "sha256", pyjson.Repr(copy.Value("sha256")))}})
	}
	if len(selected) == 0 {
		return nil, fmt.Errorf("standalone component selects no files: %s", identifier)
	}
	return selected, nil
}

func (c *SourceContext) loadArchiveLayerPolicy(identifier string, release, layer *Object) (map[string]map[string]string, string, error) {
	sourceName := pyjson.Repr(layer.Value("policy"))
	declared := map[string]bool{}
	for _, entry := range objects(getOr(c.Components[identifier], "project_inputs", []any{})) {
		declared[pyjson.Repr(entry.Value("path"))] = true
	}
	if !declared[sourceName] {
		return nil, "", fmt.Errorf("archive layer policy is not a declared project input: %s", sourceName)
	}
	source := joinPosix(c.projectRoot(), sourceName)
	payload, err := readRegular(source, "archive layer policy")
	if err != nil {
		return nil, "", err
	}
	value, err := pyjson.Decode(payload, pyjson.LastWins)
	if err != nil {
		return nil, "", fmt.Errorf("invalid archive layer policy: %s", source)
	}
	upstream, ok := release.Value("upstream").(*Object)
	if !ok {
		return nil, "", fmt.Errorf("archive layer has no upstream metadata: %s", identifier)
	}
	policy, ok := value.(*Object)
	expected := [][2]any{
		{"format", pyjson.Number("1")}, {"project", "x86qw"}, {"component", identifier},
		{"target_archive", pyjson.Repr(layer.Value("target_archive"))},
		{"reference_source", pyjson.Repr(layer.Value("reference_source"))},
		{"reference_revision", c.RevisionOf(identifier)},
		{"upstream_release", pyjson.Repr(upstream.Value("release"))},
		{"upstream_revision", pyjson.Repr(upstream.Value("source_revision"))},
	}
	for _, pair := range expected {
		ok = ok && pyjson.Eq(policy.Value(pair[0].(string)), pair[1])
	}
	if !ok {
		return nil, "", fmt.Errorf("archive layer policy identity does not match its sources: %s", source)
	}
	if !nonEmptyText(policy.Value("policy")) {
		return nil, "", fmt.Errorf("archive layer policy has no rationale: %s", source)
	}
	conflicts, ok := policy.Value("conflicts").([]any)
	if !ok {
		return nil, "", fmt.Errorf("archive layer policy has invalid conflicts: %s", source)
	}
	selected := map[string]map[string]string{}
	for _, raw := range conflicts {
		conflict, ok := raw.(*Object)
		if !ok {
			return nil, "", fmt.Errorf("archive layer policy has an invalid conflict: %s", source)
		}
		member, ok := conflict.Value("member").(string)
		if !ok {
			return nil, "", fmt.Errorf("archive layer policy has an invalid member: %s", source)
		}
		relative := purePosix(member)
		if relative.absolute || relative.hasPart("..") {
			return nil, "", fmt.Errorf("archive layer policy has an unsafe member: %s", member)
		}
		if _, seen := selected[member]; seen {
			return nil, "", fmt.Errorf("archive layer policy repeats a conflict: %s", member)
		}
		reference, referenceOK := conflict.Value("reference_sha256").(string)
		upstreamSHA, upstreamOK := conflict.Value("upstream_sha256").(string)
		if !referenceOK || !upstreamOK || !hex64Pattern.MatchString(reference) || !hex64Pattern.MatchString(upstreamSHA) {
			return nil, "", fmt.Errorf("archive layer policy has an invalid conflict hash: %s", member)
		}
		resolution := conflict.Value("resolution")
		if !inSet(resolution, setOf("reference", "upstream")) {
			return nil, "", fmt.Errorf("archive layer policy has an invalid resolution: %s", member)
		}
		selected[member] = map[string]string{"reference_sha256": reference, "upstream_sha256": upstreamSHA, "resolution": resolution.(string)}
	}
	return selected, sha256Hex(payload), nil
}

func (c *SourceContext) applyArchiveLayers(identifier string, component, release *Object, payloads []Payload) ([]Payload, error) {
	layers := objects(getOr(release, "archive_layers", []any{}))
	if len(layers) == 0 {
		return payloads, nil
	}
	updated := append([]Payload(nil), payloads...)
	consumed := map[string]bool{}
	for _, layer := range layers {
		targetArchive := pyjson.Repr(layer.Value("target_archive"))
		referenceSource := pyjson.Repr(layer.Value("reference_source"))
		destination, mode, err := DestinationForSource(component, referenceSource, "reference")
		if err != nil {
			return nil, err
		}
		owner, err := ComponentForSource(c.Catalog, referenceSource, "reference")
		if err != nil {
			return nil, err
		}
		if owner != identifier || !contains(c.Partition[identifier], referenceSource) || destination != targetArchive || mode != "archive-base" {
			return nil, fmt.Errorf("archive layer reference is not the declared base of %s: %s", identifier, referenceSource)
		}
		packageMember := "payload/" + targetArchive
		var matching []int
		for index, payload := range updated {
			if payload.Member == packageMember {
				matching = append(matching, index)
			}
		}
		if len(matching) != 1 {
			return nil, fmt.Errorf("archive layer target is not produced exactly once: %s", targetArchive)
		}
		index := matching[0]
		upstream := updated[index]
		referencePayload, err := c.verifiedReferencePayload(referenceSource)
		if err != nil {
			return nil, err
		}
		referenceFiles, err := ReadZipMembers(referencePayload, "nQuake base "+referenceSource)
		if err != nil {
			return nil, err
		}
		upstreamFiles, err := ReadZipMembers(upstream.Data, "KTX upstream "+targetArchive)
		if err != nil {
			return nil, err
		}
		actual := map[string]bool{}
		for name, data := range referenceFiles {
			if other, ok := upstreamFiles[name]; ok && !bytes.Equal(data, other) {
				actual[name] = true
			}
		}
		policy, policySHA, err := c.loadArchiveLayerPolicy(identifier, release, layer)
		if err != nil {
			return nil, err
		}
		var unresolved, stale []string
		for name := range actual {
			if _, ok := policy[name]; !ok {
				unresolved = append(unresolved, name)
			}
		}
		for name := range policy {
			if !actual[name] {
				stale = append(stale, name)
			}
		}
		if len(unresolved) > 0 || len(stale) > 0 {
			sort.Strings(unresolved)
			sort.Strings(stale)
			if len(unresolved) > 0 {
				return nil, fmt.Errorf("archive layer has a unresolved conflict: %s", unresolved[0])
			}
			return nil, fmt.Errorf("archive layer has a stale conflict: %s", stale[0])
		}
		merged := map[string][]byte{}
		for name, data := range referenceFiles {
			merged[name] = data
		}
		for name, data := range upstreamFiles {
			if !actual[name] {
				merged[name] = data
				continue
			}
			decision := policy[name]
			if decision["reference_sha256"] != sha256Hex(referenceFiles[name]) || decision["upstream_sha256"] != sha256Hex(data) {
				return nil, fmt.Errorf("archive layer conflict changed since review: %s", name)
			}
			if decision["resolution"] == "upstream" {
				merged[name] = data
			}
		}
		composed, err := BuildZipMembers(merged)
		if err != nil {
			return nil, err
		}
		composed, x86qwMetadata, err := c.applyArchiveTextReplacements(composed, targetArchive, release)
		if err != nil {
			return nil, err
		}
		layerMetadata := []*Object{
			meta("layer", "nquake", "source", referenceSource, "revision", c.RevisionOf(identifier), "sha256", sha256Hex(referencePayload)),
			meta("layer", "upstream", "source", upstream.Source,
				"revision", pyjson.Repr(pyjson.Get(release, "upstream", "source_revision")),
				"members", strconv.Itoa(len(upstreamFiles))),
			meta("layer", "x86qw-policy", "source", pyjson.Repr(layer.Value("policy")), "sha256", policySHA,
				"conflicts", strconv.Itoa(len(policy)), "ownership", "mixed"),
		}
		overrides := append(append(append([]*Object{}, upstream.Overrides...), layerMetadata...), x86qwMetadata...)
		updated[index] = Payload{referenceSource + " + " + upstream.Source + " + x86QW", packageMember, composed, overrides}
		consumed[referenceSource] = true
	}
	expected := map[string]bool{}
	for _, entry := range objects(component.Value("sources")) {
		if sourceOrigin(entry) == "reference" && entry.Value("mode") == "archive-base" {
			expected[pyjson.Repr(entry.Value("path"))] = true
		}
	}
	var missing, extra []string
	for name := range expected {
		if !consumed[name] {
			missing = append(missing, name)
		}
	}
	for name := range consumed {
		if !expected[name] {
			extra = append(extra, name)
		}
	}
	if len(missing) > 0 || len(extra) > 0 {
		sort.Strings(missing)
		sort.Strings(extra)
		detail := append(missing, extra...)[0]
		return nil, fmt.Errorf("archive base has no unique layer: %s", detail)
	}
	return updated, nil
}

func (c *SourceContext) projectPayloads(component *Object) ([]Payload, error) {
	var selected []Payload
	for _, entry := range objects(getOr(component, "project_sources", []any{})) {
		sourceName := pyjson.Repr(entry.Value("path"))
		source := joinPosix(c.projectRoot(), sourceName)
		payload, err := readRegular(source, "canonical x86QW project source")
		if err != nil {
			return nil, err
		}
		if len(payload) == 0 {
			return nil, fmt.Errorf("canonical x86QW project source is empty: %s", source)
		}
		mode := pyjson.Repr(entry.Value("mode"))
		selected = append(selected, Payload{sourceName, memberPrefix(mode) + pyjson.Repr(entry.Value("destination")), payload,
			[]*Object{meta("ownership", "project", "ownership_basis", "project-source", "project_source", sourceName)}})
	}
	return selected, nil
}

func (c *SourceContext) applyProjectOverrides(component *Object, payloads []Payload) ([]Payload, error) {
	overrides := map[string]string{}
	for _, entry := range objects(getOr(component, "project_overrides", []any{})) {
		overrides[pyjson.Repr(entry.Value("target"))] = pyjson.Repr(entry.Value("path"))
	}
	if len(overrides) == 0 {
		return payloads, nil
	}
	var updated []Payload
	applied := map[string]bool{}
	for _, payload := range payloads {
		sourceName, ok := overrides[payload.Source]
		if !ok {
			updated = append(updated, payload)
			continue
		}
		source := joinPosix(c.projectRoot(), sourceName)
		replacement, err := readRegular(source, "canonical x86QW override")
		if err != nil {
			return nil, err
		}
		if len(replacement) == 0 {
			return nil, fmt.Errorf("canonical x86QW override is empty: %s", source)
		}
		metadata := append(append([]*Object{}, payload.Overrides...), meta(
			"target", payload.Source, "project_override", sourceName, "sha256", sha256Hex(replacement),
			"ownership", "project", "ownership_basis", "project-override",
		))
		updated = append(updated, Payload{sourceName, payload.Member, replacement, metadata})
		applied[payload.Source] = true
	}
	var missing []string
	for target := range overrides {
		if !applied[target] {
			missing = append(missing, target)
		}
	}
	if len(missing) > 0 {
		sort.Strings(missing)
		return nil, fmt.Errorf("project override target is not consumed: %s", missing[0])
	}
	return updated, nil
}

func (c *SourceContext) applyProjectArchiveOverrides(component *Object, payloads []Payload) ([]Payload, error) {
	declared := objects(getOr(component, "project_archive_overrides", []any{}))
	if len(declared) == 0 {
		return payloads, nil
	}
	type replacement struct {
		source string
		data   []byte
	}
	grouped := map[string]map[string]replacement{}
	var groupOrder []string
	for _, override := range declared {
		sourceName := pyjson.Repr(override.Value("path"))
		source := joinPosix(c.projectRoot(), sourceName)
		data, err := readRegular(source, "canonical x86QW archive override")
		if err != nil {
			return nil, err
		}
		if len(data) == 0 {
			return nil, fmt.Errorf("canonical x86QW archive override is empty: %s", source)
		}
		target := pyjson.Repr(override.Value("target_archive"))
		if grouped[target] == nil {
			grouped[target] = map[string]replacement{}
			groupOrder = append(groupOrder, target)
		}
		grouped[target][pyjson.Repr(override.Value("member"))] = replacement{sourceName, data}
	}
	updated := append([]Payload(nil), payloads...)
	for _, target := range groupOrder {
		replacements := grouped[target]
		packageMember := "payload/" + target
		var matching []int
		for index, payload := range updated {
			if payload.Member == packageMember {
				matching = append(matching, index)
			}
		}
		if len(matching) != 1 {
			return nil, fmt.Errorf("project archive override target is not produced exactly once: %s", target)
		}
		index := matching[0]
		current, err := ReadZipMembers(updated[index].Data, "x86QW archive target "+target)
		if err != nil {
			return nil, err
		}
		var missing []string
		for member := range replacements {
			if _, ok := current[member]; !ok {
				missing = append(missing, member)
			}
		}
		if len(missing) > 0 {
			sort.Strings(missing)
			return nil, fmt.Errorf("project archive override member is missing: %s:%s", target, missing[0])
		}
		data := map[string][]byte{}
		for member, item := range replacements {
			data[member] = item.data
		}
		rewritten, err := RewriteZipMembers(updated[index].Data, data, nil)
		if err != nil {
			return nil, err
		}
		metadata := append([]*Object{}, updated[index].Overrides...)
		for _, member := range sortedKeys(replacements) {
			item := replacements[member]
			metadata = append(metadata, meta(
				"target_archive", target, "member", member, "project_override", item.source,
				"sha256", sha256Hex(item.data), "ownership", "mixed", "archive_member_override", true,
			))
		}
		updated[index] = Payload{updated[index].Source, updated[index].Member, rewritten, metadata}
	}
	return updated, nil
}

// ResolveComponentPayloads mirrors resolve_component_payloads.
func (c *SourceContext) ResolveComponentPayloads(identifier string) (*Object, string, []Payload, error) {
	component := c.Components[identifier]
	if component == nil {
		return nil, "", nil, fmt.Errorf("unknown component source: %s", identifier)
	}
	release, ok := pyjson.Get(c.Releases, "components", identifier).(*Object)
	if !ok {
		return nil, "", nil, fmt.Errorf("unknown component release: %s", identifier)
	}
	strategy := pyjson.Repr(release.Value("strategy"))
	var sourceRevision string
	var payloads []Payload
	var err error
	if strategy == "upstream-package" || strategy == "upstream-composed" {
		artifacts := objects(release.Value("artifacts"))
		if strategy == "upstream-composed" {
			sourceRevision = pyjson.Repr(pyjson.Get(release, "upstream", "source_revision"))
		} else {
			sourceRevision = pyjson.Repr(artifacts[0].Value("sha256"))
		}
		if payloads, err = c.standalonePayloads(identifier, component, release); err != nil {
			return nil, "", nil, err
		}
		if payloads, err = c.applyArchiveLayers(identifier, component, release, payloads); err != nil {
			return nil, "", nil, err
		}
	} else {
		sourceRevision = c.RevisionOf(identifier)
		for _, upstreamPath := range c.Partition[identifier] {
			destination, mode, err := DestinationForSource(component, upstreamPath, "reference")
			if err != nil {
				return nil, "", nil, err
			}
			if mode == "preserve" {
				continue
			}
			payload, overrides, err := c.referenceComponentPayload(upstreamPath, release)
			if err != nil {
				return nil, "", nil, err
			}
			payloads = append(payloads, Payload{upstreamPath, memberPrefix(mode) + destination, payload, overrides})
		}
		declared := map[string]bool{}
		for _, replacement := range objects(getOr(release, "text_replacements", []any{})) {
			declared[pyjson.Repr(replacement.Value("target"))] = true
		}
		appliedTargets := map[string]bool{}
		for _, payload := range payloads {
			for _, override := range payload.Overrides {
				if override.Has("source_sha256") && override.Has("target") {
					appliedTargets[pyjson.Repr(override.Value("target"))] = true
				}
			}
		}
		var missing []string
		for target := range declared {
			if !appliedTargets[target] {
				missing = append(missing, target)
			}
		}
		if len(missing) > 0 {
			sort.Strings(missing)
			return nil, "", nil, fmt.Errorf("text replacement target is not consumed by %s: %s", identifier, missing[0])
		}
		for _, entry := range objects(component.Value("sources")) {
			if sourceOrigin(entry) == "release" {
				extra, err := c.standalonePayloads(identifier, component, release)
				if err != nil {
					return nil, "", nil, err
				}
				payloads = append(payloads, extra...)
				break
			}
		}
	}
	declaredArchive := map[[2]string]bool{}
	for _, replacement := range objects(getOr(release, "archive_text_replacements", []any{})) {
		declaredArchive[[2]string{pyjson.Repr(replacement.Value("target_archive")), pyjson.Repr(replacement.Value("member"))}] = true
	}
	appliedArchive := map[[2]string]bool{}
	for _, payload := range payloads {
		for _, override := range payload.Overrides {
			if override.Has("target_archive") && override.Has("member") {
				appliedArchive[[2]string{pyjson.Repr(override.Value("target_archive")), pyjson.Repr(override.Value("member"))}] = true
			}
		}
	}
	var missingArchive [][2]string
	for key := range declaredArchive {
		if !appliedArchive[key] {
			missingArchive = append(missingArchive, key)
		}
	}
	if len(missingArchive) > 0 {
		sort.Slice(missingArchive, func(i, j int) bool {
			if missingArchive[i][0] != missingArchive[j][0] {
				return missingArchive[i][0] < missingArchive[j][0]
			}
			return missingArchive[i][1] < missingArchive[j][1]
		})
		return nil, "", nil, fmt.Errorf("archive text replacement is not consumed by %s: %s:%s", identifier, missingArchive[0][0], missingArchive[0][1])
	}
	if payloads, err = c.applyProjectOverrides(component, payloads); err != nil {
		return nil, "", nil, err
	}
	if payloads, err = c.applyProjectArchiveOverrides(component, payloads); err != nil {
		return nil, "", nil, err
	}
	project, err := c.projectPayloads(component)
	if err != nil {
		return nil, "", nil, err
	}
	payloads = append(payloads, project...)
	seen := map[string]bool{}
	for _, payload := range payloads {
		if seen[payload.Member] {
			return nil, "", nil, fmt.Errorf("component sources produce a duplicate package member: %s", identifier)
		}
		seen[payload.Member] = true
	}
	return release, sourceRevision, payloads, nil
}
