package macos

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const safeAreaKey = "NSPrefersDisplaySafeAreaCompatibilityMode"

// StableBinaryIdentities are, for a stable
// artifact, the upstream binary hash and the hash of the bundle that legacy
// x86QW versions re-signed locally.
var StableBinaryIdentities = map[string]struct{ Upstream, Legacy string }{
	"2ccea8f214c91e5fc92b5cb195c81ac584f75a551d11a58de56e5e7951eec7ed": {
		Upstream: "14633b5d4201e9460250ad236fde2e4ad579a6ddbaf81301830099d8cf004f33",
		Legacy:   "e24524761d8ff10c57a8ecbb2fdc7ce29d1bd78641cfaecf49644d8881e2422a",
	},
}

// IsDarwin reports whether macOS-only steps run on this host.
func IsDarwin() bool { return runtime.GOOS == "darwin" }

func plistPath(app string) string { return filepath.Join(app, "Contents", "Info.plist") }

func readPlist(app string) (*Value, []byte, error) {
	path := plistPath(app)
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() > 1<<20 {
		return nil, nil, fmt.Errorf("Info.plist inválido no bundle macOS: %s", app)
	}
	payload, err := os.ReadFile(path)
	if err != nil {
		return nil, nil, err
	}
	root, err := ParsePlist(payload)
	if err != nil || root.Kind != "dict" {
		return nil, nil, fmt.Errorf("Info.plist inválido no bundle macOS: %s", app)
	}
	return root, payload, nil
}

// UsesFullDisplay reports whether the bundle's Info.plist asks for the full
// display (no safe area).
func UsesFullDisplay(app string) (bool, error) {
	root, _, err := readPlist(app)
	if err != nil {
		return false, err
	}
	value, ok := root.Dict[safeAreaKey]
	if !ok {
		return false, nil
	}
	enabled, isBool := value.Bool()
	return isBool && !enabled, nil
}

// EnableFullDisplay sets the safe-area compatibility key to false.
func EnableFullDisplay(app string) error {
	root, _, err := readPlist(app)
	if err != nil {
		return err
	}
	root.Set(safeAreaKey, &Value{Kind: "false"})
	path := plistPath(app)
	temporary := path + ".x86qw-tmp"
	if err := os.WriteFile(temporary, MarshalPlist(root), 0o644); err != nil {
		return err
	}
	if err := os.Chmod(temporary, 0o644); err != nil {
		return err
	}
	return os.Rename(temporary, path)
}

func run(name string, args ...string) ([]byte, []byte, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	command := exec.CommandContext(ctx, name, args...)
	var stdout, stderr bytes.Buffer
	command.Stdout, command.Stderr = &stdout, &stderr
	err := command.Run()
	return stdout.Bytes(), stderr.Bytes(), err
}

func codesign(args ...string) error {
	stdout, stderr, err := run("codesign", args...)
	if err != nil {
		detail := strings.TrimSpace(string(stderr))
		if detail == "" {
			detail = strings.TrimSpace(string(stdout))
		}
		if detail != "" {
			return fmt.Errorf("O comando codesign falhou: %s", detail)
		}
		return errors.New("O comando codesign falhou")
	}
	return nil
}

// IsSandboxed reports whether the bundle is signed with the sandbox
// entitlement (a codesign probe).
func IsSandboxed(app string) (bool, error) {
	if !IsDarwin() {
		return false, nil
	}
	stdout, stderr, err := run("codesign", "-d", "--entitlements", ":-", app)
	if err != nil {
		return false, errors.New("Não foi possível ler os entitlements do ezQuake.")
	}
	output := append(stdout, stderr...)
	var payload []byte
	if index := bytes.Index(output, []byte("bplist00")); index >= 0 {
		return false, errors.New("entitlements binários não são suportados")
	} else if start := bytes.Index(output, []byte("<?xml")); start >= 0 {
		end := bytes.LastIndex(output, []byte("</plist>"))
		if end >= start {
			payload = output[start : end+len("</plist>")]
		}
	}
	if payload == nil {
		return false, nil
	}
	root, err := ParsePlist(payload)
	if err != nil || root.Kind != "dict" {
		return false, errors.New("Os entitlements do ezQuake são inválidos.")
	}
	value, ok := root.Dict["com.apple.security.app-sandbox"]
	if !ok {
		return false, nil
	}
	sandboxed, isBool := value.Bool()
	return isBool && sandboxed, nil
}

// NeedsPreparation reports whether a bundle is sandboxed or keeps the safe
// area, on macOS only.
func NeedsPreparation(app string) (bool, error) {
	if !IsDarwin() {
		return false, nil
	}
	sandboxed, err := IsSandboxed(app)
	if err != nil || sandboxed {
		return sandboxed, err
	}
	full, err := UsesFullDisplay(app)
	return !full, err
}

// PrepareNightly enables the full display,
// ad-hoc re-sign without the sandbox and verify. Only on macOS.
func PrepareNightly(app string) (bool, error) {
	if !IsDarwin() {
		return false, nil
	}
	if _, err := IsSandboxed(app); err != nil {
		return false, err
	}
	full, err := UsesFullDisplay(app)
	if err != nil {
		return false, err
	}
	if !full {
		if err := EnableFullDisplay(app); err != nil {
			return false, fmt.Errorf("Info.plist não pôde ser atualizado: %s", app)
		}
	}
	if err := codesign("--force", "--deep", "--sign", "-", app); err != nil {
		return false, err
	}
	if err := codesign("--verify", "--deep", "--strict", app); err != nil {
		return false, err
	}
	if sandboxed, err := IsSandboxed(app); err != nil || sandboxed {
		return false, fmt.Errorf("Não foi possível remover o sandbox incompatível de %s.", app)
	}
	if full, err := UsesFullDisplay(app); err != nil || !full {
		return false, fmt.Errorf("Não foi possível habilitar o fullscreen integral em %s.", app)
	}
	return true, nil
}

// StableHasLegacyRewrite reports whether a stable bundle is the one legacy
// x86QW versions rewrote: the known legacy binary, the full display and no
// sandbox.
func StableHasLegacyRewrite(app, artifactSHA, binarySHA string) (bool, error) {
	identities, ok := StableBinaryIdentities[artifactSHA]
	if !ok || binarySHA != identities.Legacy {
		return false, nil
	}
	full, err := UsesFullDisplay(app)
	if err != nil || !full {
		return false, err
	}
	if !IsDarwin() {
		return true, nil
	}
	sandboxed, err := IsSandboxed(app)
	return !sandboxed, err
}

// RuntimeAction is "restore-upstream" for the
// legacy re-signed stable bundle, "prepare-nightly" for an unprepared
// nightly, "" otherwise.
func RuntimeAction(app, channel, artifactSHA, binarySHA string) (string, error) {
	if channel == "stable" {
		legacy, err := StableHasLegacyRewrite(app, artifactSHA, binarySHA)
		if legacy {
			return "restore-upstream", err
		}
		return "", err
	}
	needs, err := NeedsPreparation(app)
	if needs {
		return "prepare-nightly", err
	}
	return "", err
}

// EnsureProcessAbsent refuses while an exact-named process runs (pgrep -x).
func EnsureProcessAbsent(name string) error {
	if !IsDarwin() {
		return nil
	}
	stdout, stderr, err := run("pgrep", "-x", name)
	if err == nil {
		return fmt.Errorf("Feche o %s antes de continuar. O macOS mantém a autorização do diretório do jogo enquanto o aplicativo está aberto.", name)
	}
	var exit *exec.ExitError
	if errors.As(err, &exit) && exit.ExitCode() == 1 {
		return nil
	}
	detail := strings.TrimSpace(string(stderr) + string(stdout))
	return fmt.Errorf("Não foi possível verificar se o %s está aberto: %s", name, detail)
}
