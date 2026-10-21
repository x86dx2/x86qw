// Package install inspects and maintains an x86QW installation directory.
package install

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/macos"
	"github.com/x86dx2/x86qw/go/internal/receipt"
	"github.com/x86dx2/x86qw/go/internal/state"
)

// The registered Quake PAKs. They are variables only so the offline tests
// can install small fixture PAKs instead of the real 18 MB ones.
var (
	pak0SHA256 = "eec9a020b6d8b6df73a5b911e19985f6e2539c1c6857b4a9f400553b9599677d"
	pak1SHA256 = "94e355836ec42bc464e4cbe794cfb7b5163c6efa1bcc575622bb36475bf1cf30"
)

// generatedRecords are receipts for derived files, not catalog components.
var generatedRecords = map[string]bool{"package-order": true, "play-support": true, "maps": true, "presets": true}

// qwPackagePriority is the load order of the managed PK3s in qw/pak.lst.
var qwPackagePriority = []string{
	"ktx.pk3", "models.pk3", "scoreboard_flags.pk3", "nquake.pk3", "textures.pk3",
	"qrp_maps_textures_1.pk3", "qrp_maps_textures_2.pk3", "qrp_maps_textures_3.pk3",
	"qrp_maps_textures_4.pk3", "qrp_b-models.pk3",
}

// Report summarizes a successful verification.
type Report struct {
	Clients    int
	Components []string
	Files      int
}

// Verify checks an installation the way `x86qw verify` reports it:
// original PAKs, managed ezQuake clients, state/component agreement,
// every managed file hash and the qw/pak.lst load order.
//
// Not checked: PK3 structure (hash only), macOS code signatures and legacy
// nQuake/metadata layouts, which this version neither installs nor
// verifies.
func Verify(root string) (*Report, error) {
	report := &Report{}
	for _, pak := range []struct{ path, sha string }{{"id1/pak0.pak", pak0SHA256}, {"id1/pak1.pak", pak1SHA256}} {
		if err := checkPak(filepath.Join(root, pak.path), pak.sha); err != nil {
			return nil, err
		}
	}
	for _, spec := range catalog.ClientSpecs {
		for _, channel := range catalog.Channels {
			ok, err := verifyClient(root, spec, channel)
			if err != nil {
				return nil, err
			}
			if ok {
				report.Clients++
			}
		}
	}
	if report.Clients == 0 {
		return nil, fmt.Errorf("no managed ezQuake was found in %s; run install first", root)
	}

	records, err := receipt.LoadComponents(root)
	if err != nil {
		return nil, err
	}
	byID := map[string]*receipt.Record{}
	for _, record := range records {
		byID[record.ID] = record
		if !generatedRecords[record.ID] {
			report.Components = append(report.Components, record.ID)
		}
	}
	sort.Strings(report.Components)
	if current, err := state.Read(root); err == nil {
		recorded := append([]string(nil), current.Installed()...)
		sort.Strings(recorded)
		if strings.Join(recorded, "\n") != strings.Join(report.Components, "\n") {
			return nil, errors.New("the installation state differs from the recorded components; run update to reconcile")
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	for _, record := range records {
		if record.ID == "play-support" {
			continue // checked against the derived gamecode below
		}
		count, err := verifyRecord(root, record)
		if err != nil {
			return nil, err
		}
		report.Files += count
	}
	if _, err := os.Lstat(filepath.Join(root, "id1/gpl_maps.pk3")); err == nil {
		return nil, errors.New("the shareware gpl_maps.pk3 cannot be installed with the recorded PAKs")
	}
	games, err := LoadGames()
	if err != nil {
		return nil, err
	}
	issues, _, err := PlaySupportIssues(root, AvailableGames(root, games))
	if err != nil {
		return nil, err
	}
	if len(issues) > 0 {
		return nil, fmt.Errorf("incomplete run support (%s); run update, upgrade or repair before you play or host", strings.Join(issues, "; "))
	}
	if err := verifyPackageOrder(root, byID["package-order"]); err != nil {
		return nil, err
	}
	return report, nil
}

func checkPak(name, expected string) error {
	header, err := readHeader(name, 4)
	if err != nil {
		return fmt.Errorf("required PAK not found: %s", name)
	}
	if string(header) != "PACK" {
		return fmt.Errorf("the file has no valid PAK header: %s", name)
	}
	digest, err := fileSHA256(name)
	if err != nil {
		return err
	}
	if digest != expected {
		return fmt.Errorf("the PAK does not match the original recorded version: %s", name)
	}
	return nil
}

func verifyClient(root string, spec catalog.ClientSpec, channel string) (bool, error) {
	rec, err := loadVerifiedClient(root, spec, channel)
	return rec != nil, err
}

// loadVerifiedClient returns the verified receipt of an installed client,
// or nil when that channel is not installed.
func loadVerifiedClient(root string, spec catalog.ClientSpec, channel string) (*receipt.Receipt, error) {
	receiptPath := filepath.Join(root, filepath.FromSlash(spec.Receipt(channel)))
	payload, err := os.ReadFile(receiptPath)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	rec, err := receipt.ParseClient(payload, spec.Context(channel))
	if err != nil {
		return nil, fmt.Errorf("%w: %s", err, receiptPath)
	}
	runtime := filepath.Join(root, spec.Runtime(channel))
	binaryPath := runtime
	if spec.Platform == "macos" {
		binaryPath = filepath.Join(runtime, "Contents/MacOS/ezQuake")
		version, err := bundleVersion(filepath.Join(runtime, "Contents/Info.plist"))
		if err != nil {
			return nil, err
		}
		if version != rec.Get("bundle_version") {
			return nil, fmt.Errorf("unexpected version in %s: %s", runtime, version)
		}
	}
	info, err := os.Lstat(binaryPath)
	if err != nil || !info.Mode().IsRegular() {
		return nil, fmt.Errorf("ezQuake runtime missing: %s", runtime)
	}
	digest, err := fileSHA256(binaryPath)
	if err != nil {
		return nil, err
	}
	if digest != rec.Get("binary_sha256") {
		return nil, fmt.Errorf("unexpected ezQuake executable hash: %s", runtime)
	}
	return rec, nil
}

// bundleVersion reads CFBundleShortVersionString from an XML Info.plist and
// requires it to equal CFBundleVersion.
func bundleVersion(name string) (string, error) {
	payload, err := os.ReadFile(name)
	if err != nil {
		return "", fmt.Errorf("Info.plist missing: %s", name)
	}
	decoder := xml.NewDecoder(bytes.NewReader(payload))
	decoder.Strict = false
	values := map[string]string{}
	key, element := "", ""
	for {
		token, err := decoder.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return "", fmt.Errorf("invalid Info.plist: %s", name)
		}
		switch t := token.(type) {
		case xml.StartElement:
			element = t.Name.Local
		case xml.CharData:
			text := strings.TrimSpace(string(t))
			if element == "key" {
				key = text
			} else if element == "string" && key != "" && text != "" {
				values[key] = text
				key = ""
			}
		case xml.EndElement:
			element = ""
		}
	}
	short := values["CFBundleShortVersionString"]
	if short == "" || short != values["CFBundleVersion"] {
		return "", fmt.Errorf("mismatched versions in the macOS bundle: %s", name)
	}
	return short, nil
}

func verifyRecord(root string, record *receipt.Record) (int, error) {
	for _, entry := range record.Inventory.Entries {
		managed := filepath.Join(root, filepath.FromSlash(entry.Path))
		info, err := os.Lstat(managed)
		if err != nil || !info.Mode().IsRegular() {
			return 0, fmt.Errorf("managed file missing from component %s: %s", record.ID, managed)
		}
		digest, err := fileSHA256(managed)
		if err != nil {
			return 0, err
		}
		if digest != entry.SHA256 {
			return 0, fmt.Errorf("managed file changed in component %s: %s", record.ID, managed)
		}
		switch strings.ToLower(filepath.Ext(managed)) {
		case ".bsp":
			header, err := readHeader(managed, 4)
			if err != nil || binary.LittleEndian.Uint32(header) != 29 {
				return 0, fmt.Errorf("invalid managed BSP: %s", managed)
			}
		case ".pak":
			header, err := readHeader(managed, 4)
			if err != nil || string(header) != "PACK" {
				return 0, fmt.Errorf("invalid managed PAK: %s", managed)
			}
		}
	}
	return len(record.Inventory.Entries), nil
}

// ExpectedPackageOrder returns qw/*.pk3 in load order: the fixed priority
// list first, then the rest case-insensitively.
func ExpectedPackageOrder(root string) ([]string, error) {
	entries, err := os.ReadDir(filepath.Join(root, "qw"))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	available := map[string]bool{}
	for _, entry := range entries {
		if entry.Type().IsRegular() && strings.EqualFold(filepath.Ext(entry.Name()), ".pk3") {
			available[entry.Name()] = true
		}
	}
	var order []string
	for _, name := range qwPackagePriority {
		if available[name] {
			order = append(order, name)
			delete(available, name)
		}
	}
	var rest []string
	for name := range available {
		rest = append(rest, name)
	}
	sort.Slice(rest, func(a, b int) bool {
		left, right := strings.ToLower(rest[a]), strings.ToLower(rest[b])
		if left != right {
			return left < right
		}
		return rest[a] < rest[b]
	})
	return append(order, rest...), nil
}

func verifyPackageOrder(root string, record *receipt.Record) error {
	packages, err := ExpectedPackageOrder(root)
	if err != nil {
		return err
	}
	if len(packages) == 0 {
		if record != nil {
			return errors.New("managed pak.lst exists without PK3 packages in qw")
		}
		return nil
	}
	if record == nil {
		return errors.New("PK3 order not recorded; run the x86QW bootstrap again on the same target")
	}
	payload, err := os.ReadFile(filepath.Join(root, "qw/pak.lst"))
	if err != nil {
		return err
	}
	if string(payload) != strings.Join(packages, "\n")+"\n" {
		return errors.New("qw/pak.lst does not match the installed PK3s; run the x86QW bootstrap again on the same target")
	}
	return nil
}

func readHeader(name string, size int) ([]byte, error) {
	file, err := os.Open(name)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	header := make([]byte, size)
	if _, err := io.ReadFull(file, header); err != nil {
		return nil, err
	}
	return header, nil
}

func fileSHA256(name string) (string, error) {
	file, err := os.Open(name)
	if err != nil {
		return "", err
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

// CheckPaks checks the original id1 PAKs.
func CheckPaks(root string) error {
	for _, pak := range []struct{ path, sha string }{{"id1/pak0.pak", pak0SHA256}, {"id1/pak1.pak", pak1SHA256}} {
		if err := checkPak(filepath.Join(root, pak.path), pak.sha); err != nil {
			return err
		}
	}
	return nil
}

// VerifyComponent checks the managed files of one component, returning
// their count and the receipt's selection.
func VerifyComponent(root, component string) (int, string, error) {
	record, err := receipt.Load(filepath.Join(root, receipt.ComponentsDir, component))
	if err != nil {
		return 0, "", err
	}
	count, err := verifyRecord(root, record)
	return count, record.Receipt.Get("selection"), err
}

// VerifyPlaySupport checks the play-support files of the installed games.
func VerifyPlaySupport(root string, games []Game) error {
	issues, _, err := PlaySupportIssues(root, games)
	if err != nil {
		return err
	}
	if len(issues) > 0 {
		return fmt.Errorf("Incomplete run support (%s). Run update, upgrade or repair before you play or host.", strings.Join(issues, "; "))
	}
	return nil
}

// Runtime is one launchable ezQuake of the host platform.
type Runtime struct {
	Label     string // ezQuake <channel> <selection>
	Channel   string // stable or nightly
	Path      string // the executable (inside the bundle on macOS)
	BinarySHA string
}

// HostRuntimes are the verified stable and nightly clients of the host
// platform, refusing a macOS bundle that needs repair.
func HostRuntimes(root string) ([]Runtime, error) {
	var spec *catalog.ClientSpec
	for index := range catalog.ClientSpecs {
		if catalog.ClientSpecs[index].Platform == HostPlatform() {
			spec = &catalog.ClientSpecs[index]
		}
	}
	if spec == nil {
		return nil, fmt.Errorf("Automatic launch is not supported on this system: %s.", runtime.GOOS)
	}
	var found []Runtime
	for _, channel := range catalog.Channels {
		rec, err := loadVerifiedClient(root, *spec, channel)
		if err != nil {
			return nil, err
		}
		if rec == nil {
			continue
		}
		bundle := filepath.Join(root, spec.Runtime(channel))
		executable := bundle
		if spec.Platform == "macos" {
			action, err := macos.RuntimeAction(bundle, channel, rec.Get("artifact_sha256"), rec.Get("binary_sha256"))
			if err != nil {
				return nil, err
			}
			if action != "" {
				return nil, fmt.Errorf("The macOS runtime needs a repair before it runs: %s. Run update.", bundle)
			}
			executable = filepath.Join(bundle, "Contents", "MacOS", "ezQuake")
		}
		found = append(found, Runtime{Label: "ezQuake " + channel + " " + rec.Get("selection"), Channel: channel, Path: executable, BinarySHA: rec.Get("binary_sha256")})
	}
	if len(found) == 0 {
		return nil, errors.New("No managed ezQuake for this system is installed. Run install first.")
	}
	return found, nil
}
