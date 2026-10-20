//go:build windows

package launchguard

import (
	"os"

	"golang.org/x/sys/windows"
)

// Hold opens path sharing only reads, so it cannot be replaced, renamed or
// deleted, verifies expected through that handle and keeps it open until
// the returned release runs after process creation.
func Hold(path, expected string) (func(), error) {
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return nil, err
	}
	handle, err := windows.CreateFile(name, windows.GENERIC_READ, windows.FILE_SHARE_READ, nil,
		windows.OPEN_EXISTING, windows.FILE_ATTRIBUTE_NORMAL, 0)
	if err != nil {
		return nil, &os.PathError{Op: "open", Path: path, Err: err}
	}
	release := func() { windows.CloseHandle(handle) }
	if expected == "" {
		return release, nil
	}
	process := windows.CurrentProcess()
	var duplicate windows.Handle
	if err := windows.DuplicateHandle(process, handle, process, &duplicate, 0, false, windows.DUPLICATE_SAME_ACCESS); err != nil {
		release()
		return nil, err
	}
	file := os.NewFile(uintptr(duplicate), path)
	actual, err := digest(file)
	file.Close()
	if err != nil {
		release()
		return nil, err
	}
	if actual != expected {
		release()
		return nil, mismatch(path)
	}
	return release, nil
}
