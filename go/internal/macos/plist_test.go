package macos

import (
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// TestPlistRoundTripMatchesPlistlib compares with Python's plistlib when the
// nightly Info.plist sample is available.
func TestPlistRoundTripMatchesPlistlib(t *testing.T) {
	sample := filepath.Join("testdata", "nightly-Info.plist")
	payload, err := os.ReadFile(sample)
	if err != nil {
		t.Skip("sem amostra de Info.plist")
	}
	root, err := ParsePlist(payload)
	if err != nil {
		t.Fatal(err)
	}
	if string(MarshalPlist(root)) != string(payload) {
		t.Fatal("round-trip difere do original")
	}
	root.Set(safeAreaKey, &Value{Kind: "false"})
	python, err := exec.Command("python3", "-c", `
import plistlib,sys
d=plistlib.loads(open(sys.argv[1],'rb').read()); d['NSPrefersDisplaySafeAreaCompatibilityMode']=False
sys.stdout.buffer.write(plistlib.dumps(d,fmt=plistlib.FMT_XML,sort_keys=False))`, sample).Output()
	if err != nil {
		t.Skip("sem python3")
	}
	if string(MarshalPlist(root)) != string(python) {
		t.Fatalf("difere do plistlib:\n%s", MarshalPlist(root))
	}
}

func TestPlistEscapingAndEmpty(t *testing.T) {
	root, err := ParsePlist([]byte(`<?xml version="1.0"?><plist version="1.0"><dict><key>a&amp;b</key><string>x &lt; y</string><key>e</key><array/><key>d</key><dict/><key>n</key><integer>3</integer></dict></plist>`))
	if err != nil {
		t.Fatal(err)
	}
	want := "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n<plist version=\"1.0\">\n<dict>\n\t<key>a&amp;b</key>\n\t<string>x &lt; y</string>\n\t<key>e</key>\n\t<array/>\n\t<key>d</key>\n\t<dict/>\n\t<key>n</key>\n\t<integer>3</integer>\n</dict>\n</plist>\n"
	if got := string(MarshalPlist(root)); got != want {
		t.Fatalf("%q", got)
	}
}
