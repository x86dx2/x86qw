package cli

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// On macOS links and documents arrive as Apple events, never as
// arguments, so a small AppleScript applet inside the installation
// receives them and runs `x86qw open`. It declares the qw scheme and the
// demo type, is signed ad hoc after its Info.plist changes, registered
// with Launch Services and made the default handler.

const lsregister = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"

// HandlerApp is where the applet lives inside root.
func HandlerApp(root string) string { return filepath.Join(root, ".x86qw", "handlers", "x86QW.app") }

func appleString(value string) string {
	return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(value) + `"`
}

func handlerScript(root string) string {
	return fmt.Sprintf(`on run
end run

on open location theURL
	openItem(theURL)
end open location

on open theFiles
	repeat with theFile in theFiles
		openItem(POSIX path of theFile)
	end repeat
end open

on openItem(theItem)
	do shell script quoted form of %s & " open --target " & quoted form of %s & " " & quoted form of theItem & " >/dev/null 2>&1 &"
end openItem
`, appleString(filepath.Join(root, "x86qw.sh")), appleString(root))
}

// handlerPlist are the Info.plist keys the applet gets, nil to remove.
func handlerPlist(links, demos bool) map[string]any {
	keys := map[string]any{
		"CFBundleIdentifier": HandlerBundleID, "CFBundleName": "x86QW", "LSUIElement": true,
		"CFBundleURLTypes": nil, "CFBundleDocumentTypes": nil, "UTExportedTypeDeclarations": nil,
	}
	if links {
		keys["CFBundleURLTypes"] = []any{map[string]any{"CFBundleURLName": "QuakeWorld", "CFBundleURLSchemes": []any{"qw"}}}
	}
	if demos {
		keys["CFBundleDocumentTypes"] = []any{map[string]any{
			"CFBundleTypeName": "QuakeWorld demo", "CFBundleTypeRole": "Viewer", "LSHandlerRank": "Owner",
			"LSItemContentTypes": []any{DemoTypeID},
		}}
		keys["UTExportedTypeDeclarations"] = []any{map[string]any{
			"UTTypeIdentifier": DemoTypeID, "UTTypeDescription": "QuakeWorld demo", "UTTypeConformsTo": []any{"public.data"},
			"UTTypeTagSpecification": map[string]any{"public.filename-extension": []any{"mvd", "qwd"}},
		}}
	}
	return keys
}

func run(name string, args ...string) error {
	output, err := exec.Command(name, args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s: %s", filepath.Base(name), strings.TrimSpace(string(output)+" "+err.Error()))
	}
	return nil
}

// BuildHandlerApp compiles, configures and signs the applet at app.
func BuildHandlerApp(root, app string, links, demos bool) error {
	staging, err := os.MkdirTemp("", "x86qw-handler.")
	if err != nil {
		return err
	}
	defer os.RemoveAll(staging)
	script := filepath.Join(staging, "handler.applescript")
	if err := os.WriteFile(script, []byte(handlerScript(root)), 0o600); err != nil {
		return err
	}
	built := filepath.Join(staging, "x86QW.app")
	if err := run("osacompile", "-o", built, script); err != nil {
		return err
	}
	plist := filepath.Join(built, "Contents", "Info.plist")
	for key, value := range handlerPlist(links, demos) {
		if value == nil {
			exec.Command("plutil", "-remove", key, plist).Run()
			continue
		}
		encoded, _ := json.Marshal(value)
		if err := run("plutil", "-replace", key, "-json", string(encoded), plist); err != nil {
			return err
		}
	}
	// Editing Info.plist broke the applet's signature; Apple Silicon runs
	// nothing unsigned.
	if err := run("codesign", "--force", "--sign", "-", built); err != nil {
		return err
	}
	if err := run("codesign", "--verify", built); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(app), 0o755); err != nil {
		return err
	}
	os.RemoveAll(app)
	return os.Rename(built, app)
}

func setHandlers(root string, links, demos bool) error {
	app := HandlerApp(root)
	if !links && !demos {
		if _, err := os.Stat(app); err == nil {
			exec.Command(lsregister, "-u", app).Run()
			return os.RemoveAll(app)
		}
		return nil
	}
	if err := BuildHandlerApp(root, app, links, demos); err != nil {
		return err
	}
	if err := run(lsregister, "-f", app); err != nil {
		return err
	}
	var calls []string
	if links {
		calls = append(calls, fmt.Sprintf("$.LSSetDefaultHandlerForURLScheme($('qw'), $('%s'))", HandlerBundleID))
	}
	if demos {
		calls = append(calls, fmt.Sprintf("$.LSSetDefaultRoleHandlerForContentType($('%s'), 0xFFFFFFFF, $('%s'))", DemoTypeID, HandlerBundleID))
	}
	// The JavaScript bridge reaches Launch Services without cgo; each call
	// returns an OSStatus, 0 when it worked.
	script := "ObjC.import('CoreServices'); [" + strings.Join(calls, ", ") + "].join(' ')"
	output, err := exec.Command("osascript", "-l", "JavaScript", "-e", script).CombinedOutput()
	if err != nil {
		return fmt.Errorf("osascript: %s", strings.TrimSpace(string(output)))
	}
	for _, status := range strings.Fields(string(output)) {
		if status != "0" {
			return fmt.Errorf("Launch Services recusou o x86QW como padrão (OSStatus %s)", status)
		}
	}
	return nil
}
