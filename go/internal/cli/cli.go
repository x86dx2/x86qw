// Package cli installs this binary as the command-line layer of an
// installation:
//
//	x86qw[.exe]             the native CLI (0755), what everything runs
//	.x86qw/cli/receipt      {"format":1,...,"version":V}
//	~/.local/bin/x86qw      symlink to it (POSIX)
//
// Before 2.5 the binary lived in .x86qw/cli and x86qw.sh or x86qw.cmd
// launched it; Retire removes those once nothing points at them.
package cli

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"slices"

	"github.com/x86dx2/x86qw/go/internal/semver"
)

const (
	cliDir = ".x86qw/cli"
)

// moveRunningAside is whether a running CLI has to move aside before its
// replacement goes in (Windows), and renameFile is os.Rename: tests stand
// in for both, so the swap is checked everywhere.
var (
	moveRunningAside = runtime.GOOS == "windows"
	renameFile       = os.Rename
)

// BinaryName is the CLI file name for this OS.
func BinaryName() string {
	if runtime.GOOS == "windows" {
		return "x86qw.exe"
	}
	return "x86qw"
}

// Command is root's CLI: the command, the shortcuts, the menu entries and
// the qw:// and demo handlers all run it.
func Command(root string) string { return filepath.Join(root, BinaryName()) }

// legacyPaths are what the layout before 2.5 put in root: the binary in
// .x86qw/cli and its launchers.
func legacyPaths(root string) []string {
	return []string{
		filepath.Join(root, filepath.FromSlash(cliDir), BinaryName()),
		filepath.Join(root, "x86qw.sh"),
		filepath.Join(root, "x86qw.cmd"),
	}
}

// legacyLauncher is the launcher that integrations pointed at before 2.5.
func legacyLauncher(root string) string {
	if runtime.GOOS == "windows" {
		return filepath.Join(root, "x86qw.cmd")
	}
	return filepath.Join(root, "x86qw.sh")
}

// Legacy reports whether root still has any of the layout before 2.5.
func Legacy(root string) bool {
	for _, path := range legacyPaths(root) {
		if _, err := os.Lstat(path); err == nil {
			return true
		}
	}
	return false
}

// Retire removes the layout before 2.5 from root. A binary that is still
// running moves aside instead (TidyAside removes it later), and on Windows
// an x86qw.cmd that cmd.exe is running goes once that cmd.exe ends.
func Retire(root string) error {
	var errs []error
	binary := legacyPaths(root)[0]
	if err := os.Remove(binary); err != nil && !errors.Is(err, os.ErrNotExist) {
		// Windows keeps a running executable, but lets it be renamed.
		errs = append(errs, os.Rename(binary, freeAside(binary+asideSuffix)))
	}
	if err := os.Remove(filepath.Join(root, "x86qw.sh")); err != nil && !errors.Is(err, os.ErrNotExist) {
		errs = append(errs, err)
	}
	errs = append(errs, retireBatch(filepath.Join(root, "x86qw.cmd")))
	return errors.Join(errs...)
}

// Receipt renders .x86qw/cli/receipt: sorted keys, indent 2, trailing
// newline.
func Receipt(version string) []byte {
	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	encoder.SetIndent("", "  ")
	encoder.Encode(map[string]any{
		"format": 1, "min_cli_version": "0.7.0", "project": "x86qw", "receipt_version": 1, "version": version,
	})
	return buffer.Bytes()
}

type change struct {
	path     string
	previous []byte
	mode     os.FileMode
	existed  bool
}

// Layer tracks a CLI layer installation so it can be undone.
type Layer struct {
	changes []change
}

func (l *Layer) write(path string, data []byte, mode os.FileMode) error {
	return l.put(path, path+asideSuffix, data, mode)
}

// put writes path atomically. On Windows the file it replaces, which may
// be the running CLI, moves to aside first.
func (l *Layer) put(path, aside string, data []byte, mode os.FileMode) error {
	previous, err := os.ReadFile(path)
	existed := err == nil
	var oldMode os.FileMode
	if info, err := os.Lstat(path); err == nil {
		oldMode = info.Mode().Perm()
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), "."+filepath.Base(path)+".*.tmp")
	if err != nil {
		return err
	}
	defer os.Remove(temporary.Name())
	if _, err := temporary.Write(data); err != nil {
		temporary.Close()
		return err
	}
	if err := errors.Join(temporary.Sync(), temporary.Close(), os.Chmod(temporary.Name(), mode)); err != nil {
		return err
	}
	movedAside := false
	if moveRunningAside && existed {
		// A running exe cannot be replaced, only renamed: move it aside.
		// TidyAside removes it once that process has ended.
		if err := os.MkdirAll(filepath.Dir(aside), 0o755); err != nil {
			return err
		}
		aside = freeAside(aside)
		if err := renameFile(path, aside); err != nil {
			return fmt.Errorf("the CLI in use could not be replaced: %w", err)
		}
		movedAside = true
	}
	if err := renameFile(temporary.Name(), path); err != nil {
		if movedAside {
			// Without this the installation is left with no CLI at all.
			if undo := renameFile(aside, path); undo != nil {
				return fmt.Errorf("%w; the CLI is left at %s: move it back to %s (%v)", err, aside, path, undo)
			}
		}
		return err
	}
	l.changes = append(l.changes, change{path: path, previous: previous, mode: oldMode, existed: existed})
	return nil
}

// Rollback restores every file touched, newest first.
func (l *Layer) Rollback() error {
	var errs []error
	for index := len(l.changes) - 1; index >= 0; index-- {
		item := l.changes[index]
		if item.existed {
			errs = append(errs, os.WriteFile(item.path, item.previous, item.mode))
			errs = append(errs, os.Chmod(item.path, item.mode))
		} else {
			if err := os.Remove(item.path); err != nil && !errors.Is(err, os.ErrNotExist) {
				errs = append(errs, err)
			}
		}
	}
	l.changes = nil
	return errors.Join(errs...)
}

// Install publishes source (normally os.Executable()) as the CLI of root,
// with its receipt. It leaves the layout before 2.5 alone: Retire removes
// it once whatever pointed at it points at the new binary.
func Install(root, source, version string) (*Layer, error) {
	payload, err := readExecutable(source)
	if err != nil {
		return nil, err
	}
	layer := &Layer{}
	fail := func(err error) (*Layer, error) {
		if undo := layer.Rollback(); undo != nil {
			return nil, fmt.Errorf("%w; incomplete rollback: %v", err, undo)
		}
		return nil, err
	}
	dir := filepath.Join(root, filepath.FromSlash(cliDir))
	// The running CLI moves aside into .x86qw/cli, out of the user's way.
	if err := layer.put(Command(root), filepath.Join(dir, BinaryName()+asideSuffix), payload, 0o755); err != nil {
		return fail(err)
	}
	if err := layer.write(filepath.Join(dir, "receipt"), Receipt(version), 0o644); err != nil {
		return fail(err)
	}
	return layer, nil
}

func readExecutable(source string) ([]byte, error) {
	file, err := os.Open(source)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	return io.ReadAll(io.LimitReader(file, 256<<20))
}

// LinkUserCommand points ~/.local/bin/x86qw at root's CLI, also when it
// pointed at root's x86qw.sh. A link to anything else or a regular file is
// refused. On Windows it creates the Start Menu and Desktop shortcuts
// instead and puts root, where x86qw.exe lives, on the user PATH.
func LinkUserCommand(root, home string) (string, error) {
	if runtime.GOOS == "windows" {
		shortcut, err := InstallShortcuts(root)
		if err != nil {
			return "", err
		}
		return shortcut, AddUserPath(root)
	}
	command := filepath.Join(home, ".local", "bin", "x86qw")
	launcher := Command(root)
	if info, err := os.Lstat(command); err == nil {
		if info.Mode()&os.ModeSymlink == 0 {
			return "", fmt.Errorf("An unmanaged file is already at %s.", command)
		}
		switch target := LinkTarget(command); {
		case samePath(target, launcher):
			return command, nil
		case samePath(target, legacyLauncher(root)):
			// The link of the layout before 2.5: repoint it.
		default:
			if _, err := os.Stat(target); err == nil {
				return "", fmt.Errorf("An unmanaged link is already at %s.", command)
			}
			// Dangling link from a removed installation: replace it.
		}
	}
	if err := os.MkdirAll(filepath.Dir(command), 0o755); err != nil {
		return "", err
	}
	temporary := command + ".x86qw-tmp"
	os.Remove(temporary)
	if err := os.Symlink(launcher, temporary); err != nil {
		return "", err
	}
	if err := os.Rename(temporary, command); err != nil {
		os.Remove(temporary)
		return "", err
	}
	return command, nil
}

// InstalledVersion is the version root's CLI receipt records, "" when
// there is none or it cannot be read.
func InstalledVersion(root string) string {
	payload, err := os.ReadFile(filepath.Join(root, filepath.FromSlash(cliDir), "receipt"))
	if err != nil {
		return ""
	}
	var receipt struct {
		Version string `json:"version"`
	}
	if json.Unmarshal(payload, &receipt) != nil {
		return ""
	}
	return receipt.Version
}

// Replaces reports whether a CLI of version may replace root's: a
// development build never does, an older release never does, and the
// same version does only when sameToo is set.
func Replaces(version, root string, sameToo bool) bool {
	running, err := semver.Parse(version)
	if err != nil || slices.Contains(running.Prerelease, "dev") {
		return false
	}
	installed := InstalledVersion(root)
	if installed == "" {
		return true
	}
	current, err := semver.Parse(installed)
	if err != nil {
		return true
	}
	order := semver.Compare(running, current)
	return order > 0 || (order == 0 && sameToo)
}

// LinkTarget is where the symlink at path points, made absolute; "" when
// path is no symlink.
func LinkTarget(path string) string {
	target, err := os.Readlink(path)
	if err != nil {
		return ""
	}
	if !filepath.IsAbs(target) {
		target = filepath.Join(filepath.Dir(path), target)
	}
	return filepath.Clean(target)
}

// samePath compares two paths through their symlinks; for a file that is
// gone, through its folder's.
func samePath(a, b string) bool {
	return a != "" && b != "" && resolvePath(a) == resolvePath(b)
}

func resolvePath(path string) string {
	if resolved, err := filepath.EvalSymlinks(path); err == nil {
		return resolved
	}
	if dir, err := filepath.EvalSymlinks(filepath.Dir(path)); err == nil {
		return filepath.Join(dir, filepath.Base(path))
	}
	return filepath.Clean(path)
}

// freeAside is where a running executable can move: aside, once what an
// earlier update moved there is gone; while that one still runs (an app
// left open across two updates), aside.1, aside.2 and so on.
func freeAside(aside string) string {
	for index := 0; ; index++ {
		candidate := aside
		if index > 0 {
			candidate = fmt.Sprintf("%s.%d", aside, index)
		}
		os.Remove(candidate)
		if _, err := os.Lstat(candidate); errors.Is(err, os.ErrNotExist) {
			return candidate
		}
	}
}
