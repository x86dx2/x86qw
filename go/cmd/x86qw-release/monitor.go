package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/x86dx2/x86qw/go/internal/trust"
	"github.com/x86dx2/x86qw/go/internal/tufsign"
)

// Warning windows: snapshot and targets are signed offline and get two
// weeks; a new root needs the offline root keys and gets sixty days.
const (
	offlineRoleWarning = 14 * 24 * time.Hour
	rootWarning        = 60 * 24 * time.Hour
	// timestampLease is how long sign and renew-timestamp make a
	// timestamp last, so expires minus it is when it was signed.
	timestampLease = 30 * 24 * time.Hour
	// syncGrace is how long main and production may differ: a merge
	// deploys in minutes, a renewal records itself on main in about ten.
	syncGrace = time.Hour
)

// runMonitor authenticates the public chain and fails when any role is
// inside its warning window. With --checkout it also fails when the
// checkout's generation and production's have differed for over an hour.
func runMonitor(args []string) int {
	flags := flag.NewFlagSet("monitor", flag.ExitOnError)
	baseURL := flags.String("base-url", trust.DefaultBaseURL, "public TUF repository")
	warning := flags.Int("warning-hours", 72, "timestamp warning window")
	checkout := flags.Bool("checkout", false, "compares the checkout's generation with production's")
	since := flags.String("checkout-time", "", "RFC 3339 time of the checkout's commit (the delay counts from it)")
	flags.Parse(args)
	catalog, err := trust.LoadCatalog(trust.Options{BaseURL: *baseURL})
	if err != nil {
		fmt.Fprintf(os.Stderr, "[ERROR] the public TUF chain did not authenticate: %v\n", err)
		return 1
	}
	now := time.Now().UTC()
	expires := map[string]string{}
	versions := map[string]int64{}
	failed := false
	for _, role := range []string{"timestamp", "snapshot", "targets", "root"} {
		state := catalog.Roles[role]
		window := time.Duration(*warning) * time.Hour
		switch role {
		case "snapshot", "targets":
			window = max(window, offlineRoleWarning)
		case "root":
			window = max(window, rootWarning)
		}
		expires[role] = state.Expires.UTC().Format(time.RFC3339)
		versions[role] = state.Version
		if !state.Expires.After(now.Add(window)) {
			fmt.Fprintf(os.Stderr, "[ERROR] TUF %s lease expires in less than %d days: %s\n", role, int(window.Hours()/24), expires[role])
			failed = true
		}
	}
	report := map[string]any{
		"format": 1, "project": "x86qw", "status": "healthy", "catalog_sha256": catalog.SHA256,
		"package_count": catalog.Packages, "metadata_versions": versions, "metadata_expires": expires,
		"warning_hours": *warning, "checked_at": now.Format(time.RFC3339),
	}
	if *checkout {
		state, err := compareCheckout(catalog.Roles["timestamp"], catalog.Roles["snapshot"].Version, catalog.SHA256, *since, now)
		if err != nil {
			fmt.Fprintf(os.Stderr, "[ERROR] %v\n", err)
			failed = true
		}
		report["checkout"] = state
	}
	if failed {
		report["status"] = "attention"
	}
	json.NewEncoder(os.Stdout).Encode(report)
	if failed {
		return 1
	}
	return 0
}

// compareCheckout says whether the checkout serves what production does.
// The side that is ahead has syncGrace to bring the other along: the
// checkout from its commit time, production from its timestamp's signing.
func compareCheckout(live trust.RoleState, liveSnapshot int64, liveCatalog, since string, now time.Time) (string, error) {
	repo, err := repoRoot()
	if err != nil {
		return "", err
	}
	local, err := tufsign.ReadGeneration(filepath.Join(repo, filepath.FromSlash(trustDir)))
	if err != nil {
		return "", err
	}
	switch {
	case local.Timestamp == live.Version && local.Snapshot == liveSnapshot && local.Catalog == liveCatalog:
		return "matches", nil
	case local.Timestamp == live.Version:
		// Two signings of one generation: a renewal and a release raced.
		return "diverged", fmt.Errorf("main and production both carry TUF timestamp %d but different chains: sign main again and deploy", live.Version)
	case local.Timestamp > live.Version:
		committed, err := time.Parse(time.RFC3339, since)
		if err == nil && now.Sub(committed) < syncGrace {
			return "deploying", nil
		}
		return "ahead", fmt.Errorf("main has TUF generation %d and production still serves %d: run the Deploy site workflow", local.Timestamp, live.Version)
	default:
		// A longer lease than renewals sign would read as recording for months.
		if live.Expires.After(now.Add(timestampLease + syncGrace)) {
			return "behind", fmt.Errorf("production serves timestamp %d, main records %d, and it expires %s, past the %d-day lease renewals sign", live.Version, local.Timestamp, live.Expires.UTC().Format(time.RFC3339), int(timestampLease.Hours()/24))
		}
		if now.Sub(live.Expires.Add(-timestampLease)) < syncGrace {
			return "recording", nil
		}
		return "behind", fmt.Errorf("production serves timestamp %d and main records %d: the renewal PR has not been merged", live.Version, local.Timestamp)
	}
}
