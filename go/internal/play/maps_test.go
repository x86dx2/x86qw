package play

import (
	"archive/zip"
	"bytes"
	"os"
	"path/filepath"
	"slices"
	"testing"
)

// A package that cannot be read hides only its own maps: the others still
// list.
func TestABrokenPackageHidesOnlyItsOwnMaps(t *testing.T) {
	root := t.TempDir()
	os.MkdirAll(filepath.Join(root, "id1", "maps"), 0o755)
	os.WriteFile(filepath.Join(root, "id1", "maps", "dm6.bsp"), []byte("x"), 0o644)
	var buffer bytes.Buffer
	writer := zip.NewWriter(&buffer)
	member, _ := writer.Create("maps/aerowalk.bsp")
	member.Write([]byte("x"))
	writer.Close()
	os.MkdirAll(filepath.Join(root, "qw"), 0o755)
	os.WriteFile(filepath.Join(root, "qw", "maps.pk3"), buffer.Bytes(), 0o644)
	os.WriteFile(filepath.Join(root, "qw", "broken.pk3"), []byte("not a zip"), 0o644)
	os.WriteFile(filepath.Join(root, "qw", "broken.pak"), []byte("PACK"), 0o644)
	names, err := LocalMapNames(root, "qw")
	if err != nil || !slices.Equal(names, []string{"aerowalk", "dm6"}) {
		t.Fatalf("%v %v", names, err)
	}
}

// A .ent file's keys and values may be split by any whitespace: personal
// CTF entities written with tabs still offer their map for CTF, and both
// flags are still required.
func TestCTFEntitiesTakeAnyWhitespace(t *testing.T) {
	dir := t.TempDir()
	for name, entities := range map[string]string{
		"spaces.ent":   "{\n\"classname\"  \"item_flag_team1\"\n}\n{\n\"classname\" \"item_flag_team2\"\n}\n",
		"tabs.ent":     "{\n\"classname\"\t\"item_flag_team1\"\n}\n{\n\"classname\"\t\"item_flag_team2\"\n}\n",
		"one-flag.ent": "{\n\"classname\"\t\"item_flag_team1\"\n}\n",
	} {
		path := filepath.Join(dir, name)
		os.WriteFile(path, []byte(entities), 0o644)
		if valid := validPersonalAsset(path, ".ent"); valid != (name != "one-flag.ent") {
			t.Errorf("%s: valid %v", name, valid)
		}
	}
}
