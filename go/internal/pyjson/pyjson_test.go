package pyjson

import (
	"os/exec"
	"testing"
)

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
	if _, err := exec.LookPath("python3"); err == nil {
		out, err := exec.Command("python3", "-c", `import json;print(json.dumps({"b":[1,"ç\"\n\x01",{},[]],"a":{"z":True,"y":None},"É":"x","Z":2.5},ensure_ascii=False,sort_keys=True),end="")`).Output()
		if err == nil && string(out) != cases[-1] {
			t.Fatalf("python: %q", out)
		}
	}
}
