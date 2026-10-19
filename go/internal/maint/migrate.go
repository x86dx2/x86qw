package maint

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"

	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/state"
)

// Port of `migrate` for canonical (1.0) layouts: the only migration such an
// installation can need is rewriting state.json to the 1.0 contract
// (legacy ids, format 2, one installation_version marker). Pre-1.0 layouts
// are refused instead of migrated.

const migrationTarget = "1.0.0"

// MigrationOp is one planned migration step.
type MigrationOp struct {
	Phase, Source, Destination string
}

// PlanMigration returns the planned operations and the new state bytes.
func PlanMigration(target string) ([]MigrationOp, []byte, error) {
	if lexists(filepath.Join(target, filepath.FromSlash(nquakeReceipt))) || legacyLayout(target) {
		return nil, nil, errors.New("A migração deste layout pré-1.0 não está disponível nesta CLI; reexecute o bootstrap install.sh no mesmo destino.")
	}
	path := filepath.Join(target, filepath.FromSlash(stateRelative))
	current, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil, nil
	}
	if err != nil {
		return nil, nil, err
	}
	parsed, err := state.Parse(current)
	if err != nil {
		return nil, nil, errors.New("A migração foi bloqueada por conflitos preservados: .x86qw/state.json: " + err.Error())
	}
	migrated, err := state.MarshalDocument(parsed.Migrate(migrationTarget))
	if err != nil {
		return nil, nil, err
	}
	if bytes.Equal(migrated, current) {
		return nil, nil, nil
	}
	return []MigrationOp{{"commit", ".x86qw/state.json", ".x86qw/state.json"}}, migrated, nil
}

// Migrate applies the plan under the installation lock.
func Migrate(target string) ([]MigrationOp, error) {
	held, err := lock.Acquire(target, "migrate")
	if err != nil {
		return nil, err
	}
	defer held.Release()
	ops, payload, err := PlanMigration(target)
	if err != nil || len(ops) == 0 {
		return ops, err
	}
	journal := filepath.Join(target, metadataDirectory, "migrations", "1.0")
	for _, dir := range []string{filepath.Dir(journal), journal} {
		if err := os.Mkdir(dir, 0o700); err != nil && !errors.Is(err, os.ErrExist) {
			return nil, err
		}
	}
	path := filepath.Join(target, filepath.FromSlash(stateRelative))
	temporary := path + ".migrate-tmp"
	if err := os.WriteFile(temporary, payload, 0o644); err != nil {
		return nil, err
	}
	if err := os.Chmod(temporary, 0o644); err != nil {
		os.Remove(temporary)
		return nil, err
	}
	if err := os.Rename(temporary, path); err != nil {
		os.Remove(temporary)
		return nil, err
	}
	if _, err := state.Read(target); err != nil {
		return nil, err
	}
	return ops, nil
}
