package install

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/archive"
	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/download"
	"github.com/x86dx2/x86qw/go/internal/macos"
	"github.com/x86dx2/x86qw/go/internal/receipt"
)

// archiveBinary is the runtime inside each platform's stable archive.
var archiveBinary = map[string]string{"macos": "ezQuake.app", "linux": "ezQuake-x86_64.AppImage", "windows": "ezquake.exe"}

var clientReceiptKeys = []string{"format", "platform", "architecture", "channel", "selection", "install_name",
	"bundle_version", "artifact_name", "artifact_url", "artifact_sha256", "binary_sha256"}

// legacyClientReceipt is the flat pre-1.0 receipt path.
func legacyClientReceipt(spec catalog.ClientSpec, channel string) string {
	return ".x86qw/ezquake-" + spec.Platform + "-" + channel + ".receipt"
}

// preparedClient is a runtime staged for publication.
type preparedClient struct {
	path    string
	receipt *receipt.Receipt
}

// prepareRelease fetches, checks and stages one catalog release of a
// client.
func (t *Transaction) prepareRelease(ctx context.Context, spec catalog.ClientSpec, channel string, release catalog.Package) (*preparedClient, error) {
	pin := download.Artifact{URLs: release.URLs, Size: release.Size, SHA256: release.SHA256}
	cacheRoot, err := download.CacheDir()
	if err != nil {
		return nil, err
	}
	if err := prepareCache(cacheRoot); err != nil {
		return nil, err
	}
	name := release.Filename
	if channel == "stable" {
		name = "stable-" + release.Version + "-" + name
	}
	cached := filepath.Join(cacheRoot, "bin", name)
	if err := t.observer().begin(ctx, "client", release.Size); err != nil {
		return nil, err
	}
	if regular(cached) {
		if !download.Cached(cached, pin) {
			return nil, fmt.Errorf("O arquivo em cache falhou na verificação: %s. Execute cleanup e tente novamente.", cached)
		}
		if report := t.observer().bytes("client"); report != nil {
			report(release.Size)
		}
	} else {
		temporary := filepath.Join(t.Stage, release.Filename+".download")
		if err := download.Fetch(ctx, pin, temporary, download.Options{Deadline: 30 * time.Minute, Progress: t.observer().bytes("client")}); err != nil {
			return nil, fmt.Errorf("download do ezQuake falhou: %w", err)
		}
		if err := os.MkdirAll(filepath.Dir(cached), 0o755); err != nil {
			return nil, err
		}
		if err := copyFile(temporary, cached, 0o644); err != nil {
			return nil, err
		}
		if !download.Cached(cached, pin) {
			return nil, errors.New("o download do ezQuake falhou na verificação após a cópia")
		}
	}
	prepared := filepath.Join(t.Stage, "prepared-runtime")
	os.RemoveAll(prepared)
	var binaryHash, bundleVersion string
	switch {
	case spec.Platform == "macos":
		plan, err := archive.Open(cached, archive.DefaultLimits)
		if err != nil {
			return nil, err
		}
		extract := filepath.Join(t.Stage, "app")
		os.RemoveAll(extract)
		err = plan.Extract(extract, "ezQuake.app/Contents/MacOS/ezQuake")
		plan.Close()
		if err != nil {
			return nil, err
		}
		app := filepath.Join(extract, "ezQuake.app")
		if bundleVersion, binaryHash, err = inspectMacApp(app); err != nil {
			return nil, err
		}
		if channel == "stable" && bundleVersion != release.Version {
			return nil, fmt.Errorf("o pacote stable traz a versão %s, esperada %s", bundleVersion, release.Version)
		}
		if channel == "nightly" {
			revision := release.Version[strings.LastIndex(release.Version, "_")+1:]
			if !strings.Contains(bundleVersion, "-g"+revision) {
				return nil, fmt.Errorf("o pacote nightly %s não confere com %s", bundleVersion, release.Version)
			}
			changed, err := macos.PrepareNightly(app)
			if err != nil {
				return nil, err
			}
			if changed {
				if bundleVersion, binaryHash, err = inspectMacApp(app); err != nil {
					return nil, err
				}
			}
		}
		if err := os.Rename(app, prepared); err != nil {
			return nil, err
		}
	default:
		source := cached
		if channel == "stable" {
			plan, err := archive.Open(cached, archive.DefaultLimits)
			if err != nil {
				return nil, err
			}
			extract := filepath.Join(t.Stage, "runtime")
			os.RemoveAll(extract)
			executables := []string{}
			if spec.Platform == "linux" {
				executables = append(executables, archiveBinary[spec.Platform])
			}
			err = plan.Extract(extract, executables...)
			plan.Close()
			if err != nil {
				return nil, err
			}
			source = filepath.Join(extract, archiveBinary[spec.Platform])
		}
		if !regular(source) {
			return nil, fmt.Errorf("o pacote não contém %s", archiveBinary[spec.Platform])
		}
		if err := copyFile(source, prepared, keepMode); err != nil {
			return nil, err
		}
		if spec.Platform == "linux" {
			if err := os.Chmod(prepared, 0o755); err != nil {
				return nil, err
			}
		}
		if binaryHash, err = inspectPortableBinary(prepared, spec.Platform); err != nil {
			return nil, err
		}
		bundleVersion = release.Version
	}
	rec := &receipt.Receipt{Keys: clientReceiptKeys, Values: map[string]string{
		"format": "1", "platform": spec.Platform, "architecture": spec.Architecture, "channel": channel,
		"selection": release.Version, "install_name": spec.Runtime(channel), "bundle_version": bundleVersion,
		"artifact_name": release.Filename, "artifact_url": release.URLs[0],
		"artifact_sha256": release.SHA256, "binary_sha256": binaryHash,
	}}
	if _, err := receipt.ParseClient(rec.Marshal(), spec.Context(channel)); err != nil {
		return nil, err
	}
	return &preparedClient{path: prepared, receipt: rec}, nil
}

// checkRuntimeOwnership refuses a runtime destination the installation
// does not own.
func (t *Transaction) checkRuntimeOwnership(spec catalog.ClientSpec, channel string) error {
	runtimePath := t.path(spec.Runtime(channel))
	receiptPath := t.path(spec.Receipt(channel))
	info, err := os.Lstat(runtimePath)
	if err == nil {
		if (spec.Platform == "macos") != info.IsDir() || info.Mode()&fs.ModeSymlink != 0 {
			return fmt.Errorf("caminho de runtime gerenciado inválido: %s", runtimePath)
		}
		if !regular(receiptPath) {
			return fmt.Errorf("refusing to replace an unmanaged %s runtime: %s", spec.Label, runtimePath)
		}
		_, err := verifyClient(t.Root, spec, channel)
		return err
	}
	if regular(receiptPath) {
		payload, err := os.ReadFile(receiptPath)
		if err != nil {
			return err
		}
		_, err = receipt.ParseClient(payload, spec.Context(channel))
		return err
	}
	return nil
}

// commitRuntime swaps the prepared runtime and its receipt into place, with
// the previous runtime and receipts kept in the stage for rollback.
func (t *Transaction) commitRuntime(spec catalog.ClientSpec, channel string, prepared *preparedClient) error {
	destination := t.path(spec.Runtime(channel))
	backupRoot, err := os.MkdirTemp(t.Stage, ".runtime-old.")
	if err != nil {
		return err
	}
	previous := filepath.Join(backupRoot, "runtime")
	hadPrevious := false
	if _, err := os.Lstat(destination); err == nil {
		if err := os.Rename(destination, previous); err != nil {
			return err
		}
		hadPrevious = true
	}
	if err := os.Rename(prepared.path, destination); err != nil {
		if hadPrevious {
			os.Rename(previous, destination)
		}
		return err
	}
	undoRuntime := func() error {
		if err := os.RemoveAll(destination); err != nil {
			return err
		}
		if hadPrevious {
			return os.Rename(previous, destination)
		}
		return nil
	}
	// The legacy flat receipt is retired once the canonical one is written.
	var movedLegacy []string
	legacy := t.path(legacyClientReceipt(spec, channel))
	if regular(legacy) {
		backup := filepath.Join(backupRoot, "legacy.receipt")
		if err := os.Rename(legacy, backup); err != nil {
			undoRuntime()
			return err
		}
		movedLegacy = append(movedLegacy, legacy, backup)
	}
	receiptPath := t.path(spec.Receipt(channel))
	created, err := mkdirParents(filepath.Join(t.Root, ".x86qw"), filepath.Dir(receiptPath), 0o700)
	if err != nil {
		undoRuntime()
		return err
	}
	undoReceipt, err := t.replaceFiles(filepath.Dir(receiptPath), map[string][]byte{filepath.Base(receiptPath): prepared.receipt.Marshal()})
	if err != nil {
		undoRuntime()
		return err
	}
	t.undo = append(t.undo, func() error {
		errs := []error{undoReceipt()}
		for index := len(created) - 1; index >= 0; index-- {
			os.Remove(created[index])
		}
		if len(movedLegacy) == 2 {
			errs = append(errs, os.Rename(movedLegacy[1], movedLegacy[0]))
		}
		errs = append(errs, undoRuntime())
		return errors.Join(errs...)
	})
	return nil
}

// InstallClient installs (or reinstalls) the newest release of a channel.
func (t *Transaction) InstallClient(ctx context.Context, cat *catalog.Catalog, spec catalog.ClientSpec, channel string) (string, error) {
	return t.InstallClientRelease(ctx, cat, spec, channel, "")
}

// InstallClientRelease installs one release of a channel; "" or "latest"
// is the newest (choose_release).
func (t *Transaction) InstallClientRelease(ctx context.Context, cat *catalog.Catalog, spec catalog.ClientSpec, channel, version string) (string, error) {
	return t.installClient(ctx, cat, spec, channel, version, fmt.Sprintf("A versão %s não está disponível no canal %s.", version, channel))
}

// InstallClientVersion reinstalls the receipt's exact release for repair.
func (t *Transaction) InstallClientVersion(ctx context.Context, cat *catalog.Catalog, spec catalog.ClientSpec, channel, selection string) (string, error) {
	return t.installClient(ctx, cat, spec, channel, selection, fmt.Sprintf("A versão registrada do ezQuake %s não está disponível para reparo.", channel))
}

func (t *Transaction) installClient(ctx context.Context, cat *catalog.Catalog, spec catalog.ClientSpec, channel, version, missing string) (string, error) {
	if channel != "stable" && channel != "nightly" {
		return "", fmt.Errorf("canal ezQuake inválido: %s", channel)
	}
	if spec.Platform == "macos" {
		if err := macos.EnsureProcessAbsent("ezQuake"); err != nil {
			return "", err
		}
	}
	if err := t.checkRuntimeOwnership(spec, channel); err != nil {
		return "", err
	}
	releases, err := cat.ClientReleases(spec, channel)
	if err != nil {
		return "", err
	}
	release := releases[0]
	if version != "" && version != "latest" {
		found := false
		for _, candidate := range releases {
			if candidate.Version == version {
				release, found = candidate, true
			}
		}
		if !found {
			return "", errors.New(missing)
		}
	}
	prepared, err := t.prepareRelease(ctx, spec, channel, release)
	if err != nil {
		return "", err
	}
	if err := t.commitRuntime(spec, channel, prepared); err != nil {
		return "", err
	}
	return release.Version, nil
}

// RepairNightly re-prepares an installed macOS nightly in place (the
// `Reparar` row), like repair_installed_macos_runtime.
func (t *Transaction) RepairNightly(spec catalog.ClientSpec) error {
	if spec.Platform != "macos" {
		return nil
	}
	runtimePath := t.path(spec.Runtime("nightly"))
	needs, err := macos.NeedsPreparation(runtimePath)
	if err != nil || !needs {
		return err
	}
	if err := macos.EnsureProcessAbsent("ezQuake"); err != nil {
		return err
	}
	payload, err := os.ReadFile(t.path(spec.Receipt("nightly")))
	if err != nil {
		return err
	}
	rec, err := receipt.ParseClient(payload, spec.Context("nightly"))
	if err != nil {
		return err
	}
	prepared := filepath.Join(t.Stage, "prepared-runtime")
	os.RemoveAll(prepared)
	if err := copyTree(runtimePath, prepared); err != nil {
		return err
	}
	if _, err := macos.PrepareNightly(prepared); err != nil {
		return err
	}
	_, binaryHash, err := inspectMacApp(prepared)
	if err != nil {
		return err
	}
	updated := &receipt.Receipt{Keys: clientReceiptKeys, Values: map[string]string{}}
	for _, key := range clientReceiptKeys {
		updated.Values[key] = rec.Get(key)
	}
	updated.Values["binary_sha256"] = binaryHash
	if _, err := receipt.ParseClient(updated.Marshal(), spec.Context("nightly")); err != nil {
		return err
	}
	return t.commitRuntime(spec, "nightly", &preparedClient{path: prepared, receipt: updated})
}

// copyTree copies a directory tree preserving modes (shutil.copytree).
func copyTree(source, destination string) error {
	return filepath.WalkDir(source, func(path string, entry fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		relative, _ := filepath.Rel(source, path)
		target := filepath.Join(destination, relative)
		info, err := entry.Info()
		if err != nil {
			return err
		}
		switch {
		case entry.IsDir():
			if err := os.Mkdir(target, info.Mode().Perm()); err != nil {
				return err
			}
			return os.Chmod(target, info.Mode().Perm())
		case entry.Type()&fs.ModeSymlink != 0:
			return fmt.Errorf("symlink inesperado no bundle: %s", path)
		default:
			return copyFile(path, target, info.Mode().Perm())
		}
	})
}

// ClientAction verifies an installed client and returns its macOS action.
func ClientAction(root string) func(spec catalog.ClientSpec, channel string, rec *receipt.Receipt) (string, error) {
	return func(spec catalog.ClientSpec, channel string, rec *receipt.Receipt) (string, error) {
		if _, err := verifyClient(root, spec, channel); err != nil {
			return "", err
		}
		if spec.Platform != "macos" {
			return "", nil
		}
		return macos.RuntimeAction(filepath.Join(root, spec.Runtime(channel)), channel, rec.Get("artifact_sha256"), rec.Get("binary_sha256"))
	}
}

// clientRowTarget maps an `ezQuake <label> <channel>` row back to its spec.
func clientRowTarget(item string) (catalog.ClientSpec, string, bool) {
	for _, spec := range catalog.ClientSpecs {
		for _, channel := range catalog.Channels {
			if item == "ezQuake "+spec.Label+" "+channel {
				return spec, channel, true
			}
		}
	}
	return catalog.ClientSpec{}, "", false
}

// inspectMacApp checks an ezQuake app bundle (its files, the universal
// binary and, on macOS, the code signature) and returns its version and the
// binary's digest.
func inspectMacApp(app string) (string, string, error) {
	binaryPath := filepath.Join(app, "Contents", "MacOS", "ezQuake")
	if !regular(binaryPath) || !regular(filepath.Join(app, "Contents", "_CodeSignature", "CodeResources")) {
		return "", "", fmt.Errorf("bundle ezQuake incompleto: %s", app)
	}
	version, err := bundleVersion(filepath.Join(app, "Contents", "Info.plist"))
	if err != nil {
		return "", "", err
	}
	header, err := readHeader(binaryPath, 4)
	if err != nil || (binary.BigEndian.Uint32(header) != 0xCAFEBABE && binary.BigEndian.Uint32(header) != 0xCAFEBABF) {
		return "", "", fmt.Errorf("Executável Mach-O universal esperado: %s", binaryPath)
	}
	if runtime.GOOS == "darwin" {
		command := exec.Command("codesign", "--verify", "--deep", "--strict", app)
		var stderr bytes.Buffer
		command.Stderr = &stderr
		if err := command.Run(); err != nil {
			return "", "", fmt.Errorf("assinatura do ezQuake inválida: %s", strings.TrimSpace(stderr.String()))
		}
	}
	digest, err := fileSHA256(binaryPath)
	return version, digest, err
}

// inspectPortableBinary checks that a client binary's header fits the
// platform.
func inspectPortableBinary(path, platform string) (string, error) {
	header, err := readHeader(path, 64)
	if err != nil {
		return "", fmt.Errorf("binário ezQuake ilegível: %s", path)
	}
	switch platform {
	case "linux":
		if !bytes.HasPrefix(header, []byte("\x7fELF\x02")) || binary.LittleEndian.Uint16(header[18:20]) != 62 {
			return "", fmt.Errorf("unexpected Linux binary format: %s", path)
		}
	case "windows":
		if !bytes.HasPrefix(header, []byte("MZ")) {
			return "", fmt.Errorf("unexpected Windows binary format: %s", path)
		}
	}
	return fileSHA256(path)
}
