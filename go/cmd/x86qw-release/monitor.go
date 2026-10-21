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
	baseURL := flags.String("base-url", trust.DefaultBaseURL, "repositório TUF público")
	warning := flags.Int("warning-hours", 72, "janela de alerta do timestamp")
	checkout := flags.Bool("checkout", false, "compara a geração do checkout com a de produção")
	since := flags.String("checkout-time", "", "hora RFC 3339 do commit do checkout (o atraso conta a partir dela)")
	flags.Parse(args)
	catalog, err := trust.LoadCatalog(trust.Options{BaseURL: *baseURL})
	if err != nil {
		fmt.Fprintf(os.Stderr, "[ERRO] cadeia TUF pública não autenticou: %v\n", err)
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
			fmt.Fprintf(os.Stderr, "[ERRO] lease TUF %s expira em menos de %d dias: %s\n", role, int(window.Hours()/24), expires[role])
			failed = true
		}
	}
	report := map[string]any{
		"format": 1, "project": "x86qw", "status": "healthy", "catalog_sha256": catalog.SHA256,
		"package_count": catalog.Packages, "metadata_versions": versions, "metadata_expires": expires,
		"warning_hours": *warning, "checked_at": now.Format(time.RFC3339),
	}
	if *checkout {
		state, err := compareCheckout(catalog.Roles["timestamp"], *since, now)
		if err != nil {
			fmt.Fprintf(os.Stderr, "[ERRO] %v\n", err)
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
func compareCheckout(live trust.RoleState, since string, now time.Time) (string, error) {
	repo, err := repoRoot()
	if err != nil {
		return "", err
	}
	local, err := tufsign.ReadGeneration(filepath.Join(repo, filepath.FromSlash(trustDir)))
	if err != nil {
		return "", err
	}
	switch {
	case local.Timestamp == live.Version:
		return "matches", nil
	case local.Timestamp > live.Version:
		committed, err := time.Parse(time.RFC3339, since)
		if err == nil && now.Sub(committed) < syncGrace {
			return "deploying", nil
		}
		return "ahead", fmt.Errorf("main tem a geração TUF %d e produção ainda serve a %d: rode o workflow Deploy site", local.Timestamp, live.Version)
	default:
		if now.Sub(live.Expires.Add(-timestampLease)) < syncGrace {
			return "recording", nil
		}
		return "behind", fmt.Errorf("produção serve o timestamp %d e main registra o %d: o PR da renovação não entrou", live.Version, local.Timestamp)
	}
}
