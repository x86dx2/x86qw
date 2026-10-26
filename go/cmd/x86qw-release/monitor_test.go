package main

import (
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/x86dx2/x86qw/go/internal/trust"
	"github.com/x86dx2/x86qw/go/internal/tufsign"
)

// Two signings of one generation carry the same timestamp version: only the
// same catalog under it matches.
func TestCheckoutMatchesOnlyTheSameCatalog(t *testing.T) {
	repo, err := repoRoot()
	if err != nil {
		t.Fatal(err)
	}
	local, err := tufsign.ReadGeneration(filepath.Join(repo, filepath.FromSlash(trustDir)))
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	live := trust.RoleState{Version: local.Timestamp, Expires: now.Add(30 * 24 * time.Hour)}
	if state, err := compareCheckout(live, local.Snapshot, local.Catalog, "", now); state != "matches" || err != nil {
		t.Fatalf("the same chain: %s %v", state, err)
	}
	if state, err := compareCheckout(live, local.Snapshot, strings.Repeat("0", 64), "", now); err == nil {
		t.Fatalf("another catalog under the same timestamp is %s", state)
	}
	if state, err := compareCheckout(live, local.Snapshot+1, local.Catalog, "", now); err == nil {
		t.Fatalf("another snapshot under the same timestamp is %s", state)
	}
}

// Production ahead of main is "recording" only in the hour after a renewal
// signed it with the usual lease; a longer lease must not stretch that hour.
func TestProductionAheadWithALongLeaseFails(t *testing.T) {
	repo, err := repoRoot()
	if err != nil {
		t.Fatal(err)
	}
	local, err := tufsign.ReadGeneration(filepath.Join(repo, filepath.FromSlash(trustDir)))
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	ahead := func(lease time.Duration) (string, error) {
		live := trust.RoleState{Version: local.Timestamp + 1, Expires: now.Add(lease)}
		return compareCheckout(live, local.Snapshot, local.Catalog, "", now)
	}
	if state, err := ahead(timestampLease - 10*time.Minute); state != "recording" || err != nil {
		t.Fatalf("a renewal signed ten minutes ago: %s %v", state, err)
	}
	if state, err := ahead(timestampLease - 2*time.Hour); state != "behind" || err == nil {
		t.Fatalf("a renewal signed two hours ago: %s %v", state, err)
	}
	if state, err := ahead(365 * 24 * time.Hour); err == nil {
		t.Fatalf("a renewal with a one-year lease is %s", state)
	}
}
