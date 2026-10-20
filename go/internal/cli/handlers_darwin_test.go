package cli

import (
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// The applet is built and checked without registering it: Launch
// Services on the test machine stays as it is.
func TestHandlerAppDeclaresLinksAndDemosSigned(t *testing.T) {
	root := filepath.Join(t.TempDir(), `pasta "com" aspas`)
	app := filepath.Join(t.TempDir(), "x86QW.app")
	if err := BuildHandlerApp(root, app, true, true); err != nil {
		t.Fatal(err)
	}
	if output, err := exec.Command("codesign", "--verify", "--strict", app).CombinedOutput(); err != nil {
		t.Fatalf("assinatura: %s", output)
	}
	plist := filepath.Join(app, "Contents", "Info.plist")
	for key, want := range map[string]string{
		"CFBundleIdentifier": HandlerBundleID, "CFBundleURLTypes.0.CFBundleURLSchemes.0": "qw",
		"CFBundleDocumentTypes.0.LSItemContentTypes.0": DemoTypeID, "UTExportedTypeDeclarations.0.UTTypeTagSpecification.public\\.filename-extension.0": "mvd",
		"LSUIElement": "true",
	} {
		output, err := exec.Command("plutil", "-extract", key, "raw", plist).CombinedOutput()
		if err != nil || strings.TrimSpace(string(output)) != want {
			t.Fatalf("%s: %q %v", key, output, err)
		}
	}
	links := filepath.Join(t.TempDir(), "links.app")
	if err := BuildHandlerApp(root, links, true, false); err != nil {
		t.Fatal(err)
	}
	if err := exec.Command("plutil", "-extract", "CFBundleDocumentTypes", "raw", filepath.Join(links, "Contents", "Info.plist")).Run(); err == nil {
		t.Fatal("só links não deveria declarar demos")
	}
}

func TestHandlerScriptQuotesTheFolder(t *testing.T) {
	script := handlerScript(`/Users/a "b"\c`)
	if !strings.Contains(script, `"/Users/a \"b\"\\c"`) {
		t.Fatalf("script:\n%s", script)
	}
}
