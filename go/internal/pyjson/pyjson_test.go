package pyjson

import "testing"

// The expected strings are what Python's json.dumps(..., ensure_ascii=False,
// sort_keys=True[, indent=2]) printed for the same value.
func TestMatchesPython(t *testing.T) {
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
