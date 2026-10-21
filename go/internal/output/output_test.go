package output

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

// The JSON envelope is a public contract: keys sorted, one compact line,
// no HTML escaping, errors always a list.

func TestSuccessEnvelopeIsOneSortedLine(t *testing.T) {
	var out bytes.Buffer
	data := map[string]any{"version": "2.3.2", "target": "<home>/x86qw"}
	if err := Success("version", data).Write(&out); err != nil {
		t.Fatal(err)
	}
	want := `{"command":"version","data":{"target":"<home>/x86qw","version":"2.3.2"},"dry_run":false,"errors":[],"exit_code":0,"ok":true,"schema_version":1}` + "\n"
	if out.String() != want {
		t.Fatalf("envelope:\n%s\nesperado:\n%s", out.String(), want)
	}
}

func TestFailureEnvelopeCarriesTheError(t *testing.T) {
	var out bytes.Buffer
	if err := Failure("update", ExitUsage, "usage", "opção desconhecida: --x").Write(&out); err != nil {
		t.Fatal(err)
	}
	var decoded struct {
		Command       string           `json:"command"`
		Data          map[string]any   `json:"data"`
		DryRun        bool             `json:"dry_run"`
		Errors        []map[string]any `json:"errors"`
		ExitCode      int              `json:"exit_code"`
		OK            bool             `json:"ok"`
		SchemaVersion int              `json:"schema_version"`
	}
	if err := json.Unmarshal(out.Bytes(), &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded.OK || decoded.ExitCode != ExitUsage || decoded.Command != "update" || decoded.SchemaVersion != SchemaVersion ||
		decoded.Data == nil || len(decoded.Data) != 0 || len(decoded.Errors) != 1 ||
		decoded.Errors[0]["code"] != "usage" || decoded.Errors[0]["message"] != "opção desconhecida: --x" {
		t.Fatalf("envelope: %s", out.String())
	}
	if strings.Count(out.String(), "\n") != 1 {
		t.Fatal("o envelope deve ser uma linha só")
	}
}

func TestEnvelopeKeepsTextAsIs(t *testing.T) {
	var out bytes.Buffer
	Success("doctor", map[string]any{"summary": "a < b & c > d", "nick": "jogador™"}).Write(&out)
	if !strings.Contains(out.String(), `"summary":"a < b & c > d"`) || !strings.Contains(out.String(), `"nick":"jogador™"`) {
		t.Fatalf("o texto foi escapado: %s", out.String())
	}
}

func TestDryRunAndStructData(t *testing.T) {
	type row struct {
		Action string `json:"action"`
		Item   string `json:"item"`
	}
	envelope := Success("update", []row{{"Atualizar", "KTX"}})
	envelope.DryRun = true
	var out bytes.Buffer
	envelope.Write(&out)
	if !strings.Contains(out.String(), `"data":[{"action":"Atualizar","item":"KTX"}],"dry_run":true`) {
		t.Fatalf("envelope: %s", out.String())
	}
}

func TestExitCodesAreStable(t *testing.T) {
	if ExitSuccess != 0 || ExitFailure != 1 || ExitUsage != 2 || ExitInterrupted != 130 || SchemaVersion != 1 {
		t.Fatal("os códigos de saída e a versão do esquema são contrato público")
	}
}
