// Package fspath answers where a path really is: with its links followed
// and whether it lies inside another.
package fspath

import (
	"path/filepath"
	"strings"
)

// Resolve makes name absolute and follows the links in as much of it as
// exists. A path that does not exist yet must still compare equal to the
// same place reached through a link (on macOS, /var is /private/var).
func Resolve(name string) string {
	absolute, _ := filepath.Abs(name)
	if resolved, err := filepath.EvalSymlinks(absolute); err == nil {
		return resolved
	}
	parent := filepath.Dir(absolute)
	if parent == absolute {
		return absolute
	}
	return filepath.Join(Resolve(parent), filepath.Base(absolute))
}

// Inside reports whether name is root or below it. Rel speaks the OS
// separator, so on Windows a path outside starts with ..\ (and one on
// another drive is an error).
func Inside(name, root string) bool {
	relative, err := filepath.Rel(root, name)
	return err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator))
}
