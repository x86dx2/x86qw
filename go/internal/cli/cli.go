// Package cli installs this binary as the command-line layer of an
// installation:
//
//	.x86qw/cli/x86qw[.exe]  the native CLI (0755)
//	.x86qw/cli/receipt      {"format":1,...,"version":V}
//	x86qw.sh / x86qw.cmd    a shim that execs the native CLI
//	~/.local/bin/x86qw      symlink to x86qw.sh (POSIX)
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

// Shim is the POSIX launcher: resolve symlinks to find the installation,
// then exec its native CLI with the arguments unchanged.
const Shim = `#!/bin/sh
# x86QW launcher: runs the native CLI of this installation.
set -eu
path=$0
while [ -L "$path" ]; do
  dir=$(CDPATH='' cd -- "$(dirname -- "$path")" && pwd)
  link=$(readlink "$path")
  case "$link" in /*) path=$link ;; *) path=$dir/$link ;; esac
done
root=$(CDPATH='' cd -- "$(dirname -- "$path")" && pwd)
exec "$root/.x86qw/cli/x86qw" "$@"
`

// CmdShim is the Windows launcher.
const CmdShim = "@echo off\r\n\"%~dp0.x86qw\\cli\\x86qw.exe\" %*\r\nexit /b %ERRORLEVEL%\r\n"

// BinaryName is the CLI file name for this OS.
func BinaryName() string {
	if runtime.GOOS == "windows" {
		return "x86qw.exe"
	}
	return "x86qw"
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
	if runtime.GOOS == "windows" && existed {
		// A running exe cannot be replaced, only renamed: move it aside.
		// What a previous update moved aside goes first; TidyAside removes
		// it once that process has ended.
		os.Remove(path + asideSuffix)
		if err := os.Rename(path, path+asideSuffix); err != nil {
			return fmt.Errorf("a CLI em uso não pôde ser substituída: %w", err)
		}
	}
	if err := os.Rename(temporary.Name(), path); err != nil {
		return err
	}
	l.changes = append(l.changes, change{path: path, previous: previous, mode: oldMode, existed: existed})
	return nil
}

func (l *Layer) remove(path string) error {
	previous, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	info, _ := os.Lstat(path)
	if err := os.Remove(path); err != nil {
		return err
	}
	l.changes = append(l.changes, change{path: path, previous: previous, mode: info.Mode().Perm(), existed: true})
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

// Install publishes source (normally os.Executable()) as the CLI of root.
func Install(root, source, version string) (*Layer, error) {
	payload, err := readExecutable(source)
	if err != nil {
		return nil, err
	}
	layer := &Layer{}
	fail := func(err error) (*Layer, error) {
		if undo := layer.Rollback(); undo != nil {
			return nil, fmt.Errorf("%w; rollback incompleto: %v", err, undo)
		}
		return nil, err
	}
	dir := filepath.Join(root, filepath.FromSlash(cliDir))
	if err := layer.write(filepath.Join(dir, BinaryName()), payload, 0o755); err != nil {
		return fail(err)
	}
	if err := layer.write(filepath.Join(dir, "receipt"), Receipt(version), 0o644); err != nil {
		return fail(err)
	}
	if runtime.GOOS == "windows" {
		if err := layer.write(filepath.Join(root, "x86qw.cmd"), []byte(CmdShim), 0o644); err != nil {
			return fail(err)
		}
		if err := layer.remove(filepath.Join(root, "x86qw.sh")); err != nil {
			return fail(err)
		}
	} else {
		if err := layer.write(filepath.Join(root, "x86qw.sh"), []byte(Shim), 0o755); err != nil {
			return fail(err)
		}
		if err := layer.remove(filepath.Join(root, "x86qw.cmd")); err != nil {
			return fail(err)
		}
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

// LinkUserCommand points ~/.local/bin/x86qw at root/x86qw.sh. An existing
// link to another target or a regular file is refused.
// On Windows it creates the Start Menu and Desktop shortcuts instead.
func LinkUserCommand(root, home string) (string, error) {
	if runtime.GOOS == "windows" {
		return InstallShortcuts(root)
	}
	command := filepath.Join(home, ".local", "bin", "x86qw")
	launcher := filepath.Join(root, "x86qw.sh")
	if info, err := os.Lstat(command); err == nil {
		if info.Mode()&os.ModeSymlink == 0 {
			return "", fmt.Errorf("Um arquivo não gerenciado já ocupa %s.", command)
		}
		target, _ := os.Readlink(command)
		if !filepath.IsAbs(target) {
			target = filepath.Join(filepath.Dir(command), target)
		}
		resolved, _ := filepath.EvalSymlinks(target)
		expected, _ := filepath.EvalSymlinks(launcher)
		if resolved == expected && expected != "" {
			return command, nil
		}
		if _, err := os.Stat(target); err == nil {
			return "", fmt.Errorf("Um link não gerenciado já ocupa %s.", command)
		}
		// Dangling link from a removed installation: replace it.
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
