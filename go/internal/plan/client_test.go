package plan

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/receipt"
)

func clientRows(t *testing.T, action string, age bool) []Row {
	t.Helper()
	input := fixture(t)
	path := filepath.Join(input.Root, ".x86qw/clients/ezquake/macos/stable.receipt")
	if age {
		payload, _ := os.ReadFile(path)
		// An older stable: selection and bundle_version move together.
		os.WriteFile(path, []byte(strings.ReplaceAll(string(payload), "3.6.9", "3.6.8")), 0o644)
	}
	input.ClientAction = func(spec catalog.ClientSpec, channel string, rec *receipt.Receipt) (string, error) {
		return action, nil
	}
	result, err := Update(input)
	if err != nil {
		t.Fatal(err)
	}
	var rows []Row
	for _, row := range result.Operations {
		if row.Kind == "Cliente" {
			rows = append(rows, row)
		}
	}
	return rows
}

func TestClientRowsFollowUpdateRuntimeOrder(t *testing.T) {
	if rows := clientRows(t, "", false); len(rows) != 0 {
		t.Fatalf("cliente atualizado sem ação: %+v", rows)
	}
	rows := clientRows(t, ActionRestoreUpstream, false)
	if len(rows) != 1 || rows[0].Action != "Restaurar" || rows[0].Installed != "bundle re-assinado localmente" ||
		rows[0].Available != "bundle upstream integral" || rows[0].Size == nil {
		t.Fatalf("restore-upstream: %+v", rows)
	}
	rows = clientRows(t, "", true)
	if len(rows) != 1 || rows[0].Action != "Atualizar" || rows[0].Installed != "3.6.8" || rows[0].Item != "ezQuake macOS stable" {
		t.Fatalf("stable antigo: %+v", rows)
	}
	// prepare-nightly with the same version is only meaningful for nightly;
	// with a newer release available the update supersedes the repair.
	rows = clientRows(t, ActionPrepareNightly, true)
	if len(rows) != 1 || rows[0].Action != "Atualizar" {
		t.Fatalf("prepare + versão nova: %+v", rows)
	}
	rows = clientRows(t, ActionPrepareNightly, false)
	if len(rows) != 1 || rows[0].Action != "Reparar" || rows[0].Installed != "área segura" || rows[0].Size != nil {
		t.Fatalf("prepare-nightly: %+v", rows)
	}
}
