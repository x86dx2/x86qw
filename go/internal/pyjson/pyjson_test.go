package pyjson

import "testing"

// The expected strings are the format the published files keep: what
// json.dumps(..., ensure_ascii=False, sort_keys=True[, indent=2]) printed
// for the same value.
func TestDumpsKeepsThePublishedFormat(t *testing.T) {
	value := map[string]any{
		"b": []any{1, "ç\"\n\x01", map[string]any{}, []any{}},
		"a": map[string]any{"z": true, "y": nil},
		"É": "x", "Z": 2.5,
	}
	cases := map[int]string{
		-1: `{"Z": 2.5, "a": {"y": null, "z": true}, "b": [1, "ç\"\n\u0001", {}, []], "É": "x"}`,
		2:  "{\n  \"Z\": 2.5,\n  \"a\": {\n    \"y\": null,\n    \"z\": true\n  },\n  \"b\": [\n    1,\n    \"ç\\\"\\n\\u0001\",\n    {},\n    []\n  ],\n  \"É\": \"x\"\n}",
	}
	for indent, want := range cases {
		got, err := Dumps(value, indent)
		if err != nil {
			t.Fatal(err)
		}
		if got != want {
			t.Fatalf("indent %d:\n got %q\nwant %q", indent, got, want)
		}
	}
}

// The services documents: one line or two-space indent, numbers kept as
// decoded.
func TestServicesDocuments(t *testing.T) {
	value := map[string]any{"b": []any{1, "á\n"}, "a": nil, "c": map[string]any{}, "d": true}
	if got, _ := Dumps(value, -1); got != `{"a": null, "b": [1, "á\n"], "c": {}, "d": true}` {
		t.Fatalf("uma linha: %s", got)
	}
	want := "{\n  \"a\": null,\n  \"b\": [\n    1,\n    \"á\\n\"\n  ],\n  \"c\": {},\n  \"d\": true\n}"
	if got, _ := Dumps(value, 2); got != want {
		t.Fatalf("indentado: %q", got)
	}
	decoded, err := Decode([]byte(`{"pid": 12345678901234, "ratio": 0.5}`))
	if err != nil {
		t.Fatal(err)
	}
	if got, _ := Dumps(decoded, -1); got != `{"pid": 12345678901234, "ratio": 0.5}` {
		t.Fatalf("números: %s", got)
	}
}
