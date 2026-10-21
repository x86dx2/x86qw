package tufsign

import (
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"

	"time"

	"github.com/theupdateframework/go-tuf/v2/metadata"
)

// Generation is the chain one timestamp.json points to in a trust
// projection (metadata/ and targets/ under one folder). Signatures are
// not checked here; trust.LoadCatalog does that.
type Generation struct {
	Timestamp, Snapshot, Targets int64
	// Catalog is the sha256 of the catalog target.
	Catalog string
	// Roots are the root files, oldest first.
	Roots []string
	// Expires is the earliest expiry of the newest root, timestamp,
	// snapshot and targets.
	Expires time.Time
}

// Files are the paths, relative to the projection, the generation needs.
func (g *Generation) Files() []string {
	files := []string{"metadata/timestamp.json", fmt.Sprintf("metadata/%d.snapshot.json", g.Snapshot),
		fmt.Sprintf("metadata/%d.targets.json", g.Targets), "targets/catalog/" + g.Catalog + ".catalog.json"}
	for _, root := range g.Roots {
		files = append(files, "metadata/"+root)
	}
	return files
}

// ReadGeneration reads the chain the projection under dir serves.
func ReadGeneration(dir string) (*Generation, error) {
	read := func(name string) ([]byte, error) { return os.ReadFile(filepath.Join(dir, "metadata", name)) }
	g := &Generation{}
	earliest := func(expires time.Time) {
		if g.Expires.IsZero() || expires.Before(g.Expires) {
			g.Expires = expires
		}
	}
	raw, err := read("timestamp.json")
	if err != nil {
		return nil, err
	}
	timestamp, err := metadata.Timestamp().FromBytes(raw)
	if err != nil {
		return nil, fmt.Errorf("timestamp.json: %w", err)
	}
	g.Timestamp = timestamp.Signed.Version
	earliest(timestamp.Signed.Expires)
	meta := timestamp.Signed.Meta["snapshot.json"]
	if meta == nil {
		return nil, errors.New("timestamp.json does not point at a snapshot")
	}
	g.Snapshot = meta.Version
	if raw, err = read(fmt.Sprintf("%d.snapshot.json", g.Snapshot)); err != nil {
		return nil, err
	}
	snapshot, err := metadata.Snapshot().FromBytes(raw)
	if err != nil {
		return nil, fmt.Errorf("%d.snapshot.json: %w", g.Snapshot, err)
	}
	earliest(snapshot.Signed.Expires)
	if meta = snapshot.Signed.Meta["targets.json"]; meta == nil {
		return nil, errors.New("snapshot does not point at a targets")
	}
	g.Targets = meta.Version
	if raw, err = read(fmt.Sprintf("%d.targets.json", g.Targets)); err != nil {
		return nil, err
	}
	targets, err := metadata.Targets().FromBytes(raw)
	if err != nil {
		return nil, fmt.Errorf("%d.targets.json: %w", g.Targets, err)
	}
	earliest(targets.Signed.Expires)
	target := targets.Signed.Targets[CatalogTarget]
	if target == nil || len(target.Hashes["sha256"]) != 32 {
		return nil, errors.New("targets without the catalog hash")
	}
	g.Catalog = hex.EncodeToString(target.Hashes["sha256"])
	roots, _ := filepath.Glob(filepath.Join(dir, "metadata", "*.root.json"))
	var newest *metadata.Metadata[metadata.RootType]
	for _, path := range roots {
		raw, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		root, err := metadata.Root().FromBytes(raw)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", filepath.Base(path), err)
		}
		if newest == nil || root.Signed.Version > newest.Signed.Version {
			newest = root
		}
		g.Roots = append(g.Roots, filepath.Base(path))
	}
	if newest == nil {
		return nil, errors.New("TUF projection without a root")
	}
	earliest(newest.Signed.Expires)
	return g, nil
}

// Prune removes the files Stale lists.
func Prune(dir string) ([]string, error) {
	stale, err := Stale(dir)
	for _, file := range stale {
		if err == nil {
			err = os.Remove(filepath.Join(dir, filepath.FromSlash(file)))
		}
	}
	return stale, err
}

// Stale lists the metadata and catalog targets under dir that the current
// generation does not use.
func Stale(dir string) ([]string, error) {
	g, err := ReadGeneration(dir)
	if err != nil {
		return nil, err
	}
	keep := map[string]bool{}
	for _, file := range g.Files() {
		keep[file] = true
	}
	var stale []string
	for _, pattern := range []string{"metadata/*.json", "targets/catalog/*.json"} {
		matches, _ := filepath.Glob(filepath.Join(dir, filepath.FromSlash(pattern)))
		for _, path := range matches {
			relative, _ := filepath.Rel(dir, path)
			if relative = filepath.ToSlash(relative); !keep[relative] {
				stale = append(stale, relative)
			}
		}
	}
	return stale, nil
}
