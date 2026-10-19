package main

import (
	"bytes"
	"encoding/json"
	"testing"
)

func TestVersionJSONMatchesPythonEnvelope(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if code := run([]string{"version", "--json"}, &stdout, &stderr); code != 0 {
		t.Fatalf("exit %d: %s", code, stderr.String())
	}
	want := `{"command":"version","data":{"project":"x86qw","version":"0.0.0-dev"},"dry_run":false,"errors":[],"exit_code":0,"ok":true,"schema_version":1}` + "\n"
	if stdout.String() != want {
		t.Fatalf("envelope:\n got %s\nwant %s", stdout.String(), want)
	}
}

func TestCatalogFailureIsJSONEnvelope(t *testing.T) {
	var stdout, stderr bytes.Buffer
	code := run([]string{"catalog", "--json", "--cache-dir", "", "--base-url", "http://insecure.invalid/"}, &stdout, &stderr)
	if code != 1 {
		t.Fatalf("exit %d", code)
	}
	var envelope map[string]any
	if err := json.Unmarshal(stdout.Bytes(), &envelope); err != nil {
		t.Fatal(err)
	}
	if envelope["ok"] != false || envelope["exit_code"] != float64(1) {
		t.Fatalf("envelope inesperado: %s", stdout.String())
	}
}

func TestUnknownCommandIsUsageError(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if code := run([]string{"nope"}, &stdout, &stderr); code != 2 {
		t.Fatalf("exit %d", code)
	}
}
