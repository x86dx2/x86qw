package services

import (
	"os"
	"path/filepath"
)

// writeOracleCapture is the parity hook used by go/scripts/services-oracle:
// with X86QW_SERVICES_ORACLE=<file> the operation dumps what it would start
// (specs, ephemeral cfg contents, materialized files and the journal) and
// then runs its normal cleanup without ever spawning a runtime.
func writeOracleCapture(path, action string, specs []*Spec, journal *Journal, session *Session) error {
	var captured []any
	for _, spec := range specs {
		item := map[string]any{
			"label": spec.Label, "arguments": toAny(spec.Arguments), "cwd": spec.Cwd,
			"expected_sha256": spec.ExpectedSHA256, "startup_rcon": nil, "readiness": nil,
		}
		var parameters []any
		for _, pair := range spec.Parameters {
			parameters = append(parameters, []any{pair[0], pair[1]})
		}
		item["parameters"] = parameters
		if rcon := spec.StartupRcon; rcon != nil {
			item["startup_rcon"] = map[string]any{
				"address": rcon.Address, "port": rcon.Port, "password": rcon.Password,
				"config_name": rcon.ConfigName, "expected_map": rcon.ExpectedMap, "expected_gamedir": rcon.ExpectedGamedir,
			}
		}
		if readiness := spec.Readiness; readiness != nil {
			var upstream any
			if readiness.Upstream != "" {
				upstream = readiness.Upstream
			}
			item["readiness"] = map[string]any{
				"kind": readiness.Kind, "address": readiness.Address, "port": readiness.Port, "upstream": upstream,
			}
		}
		captured = append(captured, item)
	}
	configs := map[string]any{}
	for _, config := range session.Temporary {
		payload, err := os.ReadFile(config.Path)
		if err != nil {
			return err
		}
		configs[filepath.Base(config.Path)] = latin1(payload)
	}
	var materialized []any
	var data any
	if journal != nil {
		data = journal.Data
		for _, item := range journal.list("materialized_files") {
			if entry, ok := item.(map[string]any); ok {
				if file, ok := JournalPath(journal.Target, entry["path"]); ok {
					materialized = append(materialized, file)
				}
			}
		}
	}
	document := map[string]any{
		"action": action, "specs": captured, "configs": configs, "materialized": materialized, "journal": data,
	}
	return os.WriteFile(path, []byte(pyDumps(document, true)+"\n"), 0o600)
}
