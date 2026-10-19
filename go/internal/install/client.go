package install

import (
	"bytes"
	"context"
	"encoding/binary"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/archive"
	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/download"
	"github.com/x86dx2/x86qw/go/internal/receipt"
)

// archiveBinary is the runtime inside each platform's stable archive.
var archiveBinary = map[string]string{"macos": "ezQuake.app", "linux": "ezQuake-x86_64.AppImage", "windows": "ezquake.exe"}

// InstallClient installs the newest ezQuake of one channel, like
// manager.py ensure_archive + prepare_runtime + commit_runtime.
func (t *Transaction) InstallClient(ctx context.Context, cat *catalog.Catalog, spec catalog.ClientSpec, channel string) (string, error) {
	return t.InstallClientVersion(ctx, cat, spec, channel, "")
}

// InstallClientVersion installs one exact catalog release ("" = newest).
func (t *Transaction) InstallClientVersion(ctx context.Context, cat *catalog.Catalog, spec catalog.ClientSpec, channel, selection string) (string, error) {
	if channel != "stable" {
		return "", fmt.Errorf("o canal %s do ezQuake ainda não foi portado; use a CLI Python", channel)
	}
	releases, err := cat.ClientReleases(spec, channel)
	if err != nil {
		return "", err
	}
	release := releases[0]
	if selection != "" {
		found := false
		for _, candidate := range releases {
			if candidate.Version == selection {
				release, found = candidate, true
			}
		}
		if !found {
			return "", fmt.Errorf("A versão registrada do ezQuake %s não está disponível para reparo.", channel)
		}
	}
	pin := download.Artifact{URLs: release.URLs, Size: release.Size, SHA256: release.SHA256}
	cacheRoot, err := download.CacheDir()
	if err != nil {
		return "", err
	}
	if err := prepareCache(cacheRoot); err != nil {
		return "", err
	}
	cached := filepath.Join(cacheRoot, "bin", "stable-"+release.Version+"-"+release.Filename)
	url := release.URLs[0]
	if regular(cached) {
		if !download.Cached(cached, pin) {
			return "", fmt.Errorf("o arquivo em cache falhou na verificação: %s; execute cleanup e tente novamente", cached)
		}
	} else {
		temporary := filepath.Join(t.Stage, release.Filename+".download")
		if err := download.Fetch(ctx, pin, temporary, download.Options{Deadline: 30 * time.Minute}); err != nil {
			return "", fmt.Errorf("download do ezQuake falhou: %w", err)
		}
		if err := os.MkdirAll(filepath.Dir(cached), 0o755); err != nil {
			return "", err
		}
		if err := copyFile(temporary, cached, 0o644); err != nil {
			return "", err
		}
	}
	runtimePath := spec.Runtime(channel)
	prepared := filepath.Join(t.Stage, "prepared-runtime")
	plan, err := archive.Open(cached, archive.DefaultLimits)
	if err != nil {
		return "", err
	}
	defer plan.Close()
	extract := filepath.Join(t.Stage, "runtime")
	var binaryHash, bundleVersion string
	switch spec.Platform {
	case "macos":
		if err := plan.Extract(extract, "ezQuake.app/Contents/MacOS/ezQuake"); err != nil {
			return "", err
		}
		app := filepath.Join(extract, "ezQuake.app")
		bundleVersion, binaryHash, err = inspectMacApp(app)
		if err != nil {
			return "", err
		}
		if bundleVersion != release.Version {
			return "", fmt.Errorf("o bundle stable tem versão %s, esperada %s", bundleVersion, release.Version)
		}
		if err := os.Rename(app, prepared); err != nil {
			return "", err
		}
	default:
		member := archiveBinary[spec.Platform]
		executables := []string{}
		if spec.Platform == "linux" {
			executables = append(executables, member)
		}
		if err := plan.Extract(extract, executables...); err != nil {
			return "", err
		}
		if err := copyFile(filepath.Join(extract, member), prepared, keepMode); err != nil {
			return "", err
		}
		if spec.Platform == "linux" {
			os.Chmod(prepared, 0o755)
		}
		if binaryHash, err = inspectPortableBinary(prepared, spec.Platform); err != nil {
			return "", err
		}
		bundleVersion = release.Version
	}
	destination := t.path(runtimePath)
	if _, err := os.Lstat(destination); err == nil {
		return "", fmt.Errorf("o runtime já existe e não é gerenciado: %s", destination)
	}
	if err := os.Rename(prepared, destination); err != nil {
		return "", err
	}
	t.undo = append(t.undo, func() error { return os.RemoveAll(destination) })
	rec := &receipt.Receipt{
		Keys: []string{"format", "platform", "architecture", "channel", "selection", "install_name",
			"bundle_version", "artifact_name", "artifact_url", "artifact_sha256", "binary_sha256"},
		Values: map[string]string{
			"format": "1", "platform": spec.Platform, "architecture": spec.Architecture, "channel": channel,
			"selection": release.Version, "install_name": runtimePath, "bundle_version": bundleVersion,
			"artifact_name": release.Filename, "artifact_url": url,
			"artifact_sha256": release.SHA256, "binary_sha256": binaryHash,
		},
	}
	if _, err := receipt.ParseClient(rec.Marshal(), spec.Context(channel)); err != nil {
		return "", err
	}
	receiptPath := t.path(spec.Receipt(channel))
	undo, err := t.replaceFiles(filepath.Dir(receiptPath), map[string][]byte{filepath.Base(receiptPath): rec.Marshal()})
	if err != nil {
		return "", err
	}
	t.undo = append(t.undo, undo)
	return release.Version, nil
}

// inspectMacApp mirrors macos.inspect_ezquake_bundle.
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
	if err != nil || (binary.BigEndian.Uint32(header) != 0xCAFEBABE && binary.BigEndian.Uint32(header) != 0xBEBAFECA) {
		return "", "", fmt.Errorf("executável ezQuake não é Mach-O universal: %s", binaryPath)
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

// inspectPortableBinary mirrors host.inspect_portable_binary's header checks.
func inspectPortableBinary(path, platform string) (string, error) {
	header, err := readHeader(path, 64)
	if err != nil {
		return "", fmt.Errorf("binário ezQuake ilegível: %s", path)
	}
	switch platform {
	case "linux":
		if !bytes.HasPrefix(header, []byte("\x7fELF\x02")) || binary.LittleEndian.Uint16(header[18:20]) != 62 {
			return "", fmt.Errorf("formato inesperado de binário Linux: %s", path)
		}
	case "windows":
		if !bytes.HasPrefix(header, []byte("MZ")) {
			return "", fmt.Errorf("formato inesperado de binário Windows: %s", path)
		}
	}
	return fileSHA256(path)
}
