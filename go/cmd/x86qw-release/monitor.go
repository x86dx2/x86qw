package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/x86dx2/x86qw/go/internal/trust"
)

// offlineRoleWarning gives snapshot and targets (signed offline) two weeks.
const offlineRoleWarning = 14 * 24 * time.Hour

// runMonitor authenticates the public chain and fails when any role is
// inside its warning window (port of monitor_public_tuf.py).
func runMonitor(args []string) int {
	flags := flag.NewFlagSet("monitor", flag.ExitOnError)
	baseURL := flags.String("base-url", trust.DefaultBaseURL, "repositório TUF público")
	warning := flags.Int("warning-hours", 72, "janela de alerta do timestamp")
	flags.Parse(args)
	catalog, err := trust.LoadCatalog(trust.Options{BaseURL: *baseURL})
	if err != nil {
		fmt.Fprintf(os.Stderr, "[ERRO] cadeia TUF pública não autenticou: %v\n", err)
		return 1
	}
	now := time.Now().UTC()
	expires := map[string]string{}
	versions := map[string]int64{}
	for _, role := range []string{"timestamp", "snapshot", "targets"} {
		state := catalog.Roles[role]
		window := time.Duration(*warning) * time.Hour
		if role != "timestamp" && window < offlineRoleWarning {
			window = offlineRoleWarning
		}
		expires[role] = state.Expires.UTC().Format(time.RFC3339)
		versions[role] = state.Version
		if !state.Expires.After(now.Add(window)) {
			fmt.Fprintf(os.Stderr, "[ERRO] lease TUF %s expira em menos de %d horas: %s\n", role, int(window.Hours()), expires[role])
			return 1
		}
	}
	report := map[string]any{
		"format": 1, "project": "x86qw", "status": "healthy", "catalog_sha256": catalog.SHA256,
		"package_count": catalog.Packages, "metadata_versions": versions, "metadata_expires": expires,
		"warning_hours": *warning, "checked_at": now.Format(time.RFC3339),
	}
	json.NewEncoder(os.Stdout).Encode(report)
	return 0
}
