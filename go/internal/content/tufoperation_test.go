package content

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestVerifyTUFOperationReport(t *testing.T) {
	dir := t.TempDir()
	catalog := []byte(`{"format":1,"project":"x86qw","packages":[]}`)
	os.WriteFile(filepath.Join(dir, "catalog.json"), catalog, 0o644)
	sum := sha256.Sum256(catalog)
	digest := hex.EncodeToString(sum[:])
	report := `{"format": 2, "project": "x86qw", "status": "drill-passed", "mode": "offline-renewal-expiry-recovery",
		"published": false, "target_unchanged": true, "root_unchanged": true, "expiry_failure_detected": true,
		"recovery_verified": true, "checked_at": "2026-10-01T12:00:00.123Z",
		"current_metadata_version": 7, "renewed_metadata_version": 9,
		"role_versions": {"timestamp": {"current": 7, "renewed": 9}, "snapshot": {"current": 5, "renewed": 6}, "targets": {"current": 3, "renewed": 4}},
		"operation": {"operator": "x86dx2", "custody_host": "offline-mac", "timestamp_sla_hours": 72, "key_scope": "root-and-targets-offline"},
		"target": {"path": "catalog/` + digest + `.catalog.json", "sha256": "` + digest + `", "size": 44}}`
	now := time.Date(2026, 10, 19, 0, 0, 0, 0, time.UTC)
	check := func(document, want string) {
		t.Helper()
		path := filepath.Join(dir, "report.json")
		os.WriteFile(path, []byte(document), 0o644)
		_, err := VerifyTUFOperationReport(dir, path, now)
		if want == "" && err != nil || want != "" && (err == nil || !strings.Contains(err.Error(), want)) {
			t.Errorf("want %q, got %v", want, err)
		}
	}
	check(report, "")
	check(strings.Replace(report, `"format": 2`, `"format": 2.0`, 1), "")
	check(strings.Replace(report, `"format": 2`, `"format": true`, 1), "não comprova drill fechado")
	check(strings.Replace(report, `"published": false`, `"published": 0`, 1), "não comprova drill fechado")
	check(strings.Replace(report, "2026-10-01T12", "2026-11-01T12", 1), "no futuro")
	check(strings.Replace(report, `"renewed": 4`, `"renewed": 3`, 1), "não avançaram para targets")
	check(strings.Replace(report, `"renewed_metadata_version": 9`, `"renewed_metadata_version": 10`, 1), "versões agregadas")
	check(strings.Replace(report, `"x86dx2"`, `" x86dx2"`, 1), "contexto operacional TUF inválido")
	check(strings.Replace(report, `"size": 44`, `"size": 45`, 1), "não corresponde ao catálogo")
	check(`{"format": 2, "format": 2}`, "chave duplicada: format")
}
