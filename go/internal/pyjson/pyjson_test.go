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
		t.Fatalf("one line: %s", got)
	}
	want := "{\n  \"a\": null,\n  \"b\": [\n    1,\n    \"á\\n\"\n  ],\n  \"c\": {},\n  \"d\": true\n}"
	if got, _ := Dumps(value, 2); got != want {
		t.Fatalf("indented: %q", got)
	}
	decoded, err := Decode([]byte(`{"pid": 12345678901234, "ratio": 0.5}`))
	if err != nil {
		t.Fatal(err)
	}
	if got, _ := Dumps(decoded, -1); got != `{"pid": 12345678901234, "ratio": 0.5}` {
		t.Fatalf("numbers: %s", got)
	}
}

// Floats come out as Python's json.dumps writes them, so files the Python
// tools wrote and these match byte for byte.
func TestFloatsAreWrittenLikePython(t *testing.T) {
	for value, want := range map[float64]string{1234567.5: "1234567.5", 2: "2.0", 0.5: "0.5", 1e16: "1e+16", 1.5e-07: "1.5e-07", 123456789012.25: "123456789012.25"} {
		if got, err := Dumps(value, -1); err != nil || got != want {
			t.Errorf("%v: %q %v, want %q", value, got, err, want)
		}
	}
}
