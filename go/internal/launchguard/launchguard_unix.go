//go:build !windows

package launchguard

import "os"

// Hold verifies path against expected (when given) and returns the release
// function to call once the process has started.
func Hold(path, expected string) (func(), error) {
	if expected == "" {
		return func() {}, nil
	}
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	actual, err := digest(file)
	if err != nil {
		return nil, err
	}
	if actual != expected {
		return nil, mismatch(path)
	}
	return func() {}, nil
}
