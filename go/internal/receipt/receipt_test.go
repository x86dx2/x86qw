package receipt

import (
	"bytes"
	"os"
	"path/filepath"
	"testing"
)

func TestReceiptRoundTrip(t *testing.T) {
	payload := []byte("format\t1\ncomponent\tktx\nselection\t1.47+x86qw.19\nsource\thttps://example.invalid/k.zip\ninventory_sha256\t" +
		"e8720ad5da584613ea427ba51604d057e67ef96a4ad768213caf8bf7b6a8f727\n")
	rec, err := ParseReceipt(payload)
	if err != nil {
		t.Fatal(err)
	}
	if rec.Get("selection") != "1.47+x86qw.19" || !bytes.Equal(rec.Marshal(), payload) {
		t.Fatalf("receipt: %+v", rec)
	}
}

func TestInventoryRejectsUnsafeOrUnsorted(t *testing.T) {
	digest := "\t" + string(bytes.Repeat([]byte("a"), 64)) + "\n"
	cases := map[string]string{
		"absolute":  "/etc/passwd" + digest,
		"parent":    "../x" + digest,
		"unclean":   "a/./b" + digest,
		"duplicate": "a" + digest + "a" + digest,
		"bad hash":  "a\tzz\n",
		"no eol":    "a" + digest[:len(digest)-1],
	}
	for name, payload := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := ParseInventory([]byte(payload)); err == nil {
				t.Fatal("inventário inválido aceito")
			}
		})
	}
}

func TestMarshalInventorySorts(t *testing.T) {
	digest := string(bytes.Repeat([]byte("b"), 64))
	got := MarshalInventory([]Entry{{"qw/z", digest}, {"id1/a", digest}})
	if string(got) != "id1/a\t"+digest+"\nqw/z\t"+digest+"\n" {
		t.Fatalf("%q", got)
	}
	if _, err := ParseInventory(got); err != nil {
		t.Fatal(err)
	}
}

func TestRealInstallationRecords(t *testing.T) {
	home, _ := os.UserHomeDir()
	root := filepath.Join(home, "Games", "x86qw")
	if _, err := os.Stat(filepath.Join(root, ComponentsDir)); err != nil {
		t.Skip("sem instalação local")
	}
	records, err := LoadComponents(root)
	if err != nil {
		t.Fatal(err)
	}
	if len(records) == 0 {
		t.Fatal("nenhum componente")
	}
	if _, err := Load(filepath.Join(root, PersonalDir)); err != nil {
		t.Fatal(err)
	}
}
