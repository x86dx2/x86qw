//go:build windows

package lock

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"path/filepath"
	"strings"
	"unsafe"

	"golang.org/x/sys/windows"
)

const noFollow = 0

// mutexName is the per-installation named mutex.
func mutexName(target string) string {
	absolute, _ := filepath.Abs(target)
	normalized := strings.ReplaceAll(strings.ToLower(absolute), "/", `\`)
	digest := sha256.Sum256([]byte(normalized))
	return `Global\x86QW-install-` + hex.EncodeToString(digest[:])
}

// serialize holds the per-installation named mutex, restricted to the
// current user and SYSTEM.
func serialize(target, sessions string, fn func() error) error {
	user, err := currentUserSID()
	if err != nil {
		return err
	}
	descriptor, err := windows.SecurityDescriptorFromString("O:" + user + "D:P(A;;GA;;;" + user + ")(A;;GA;;;S-1-5-18)")
	if err != nil {
		return fmt.Errorf("could not protect the installation mutex: %w", err)
	}
	attributes := &windows.SecurityAttributes{Length: uint32(unsafe.Sizeof(windows.SecurityAttributes{})), SecurityDescriptor: descriptor}
	name, err := windows.UTF16PtrFromString(mutexName(target))
	if err != nil {
		return err
	}
	handle, err := windows.CreateMutex(attributes, false, name)
	if handle == 0 {
		return fmt.Errorf("could not open the installation mutex: %w", err)
	}
	defer windows.CloseHandle(handle)
	event, err := windows.WaitForSingleObject(handle, windows.INFINITE)
	if err != nil || (event != windows.WAIT_OBJECT_0 && event != windows.WAIT_ABANDONED) {
		return fmt.Errorf("could not wait for the installation mutex: %v", err)
	}
	defer windows.ReleaseMutex(handle)
	return fn()
}

func currentUserSID() (string, error) {
	user, err := windows.GetCurrentProcessToken().GetTokenUser()
	if err != nil {
		return "", err
	}
	return user.User.Sid.String(), nil
}

// Identity identifies a Windows process by PID, creation time and
// executable.
func Identity(pid int) (ProcessIdentity, string) {
	if pid <= 0 {
		return ProcessIdentity{}, "dead"
	}
	handle, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if err != nil {
		if err == windows.ERROR_INVALID_PARAMETER {
			return ProcessIdentity{}, "dead"
		}
		return ProcessIdentity{}, "inconclusive"
	}
	defer windows.CloseHandle(handle)
	var creation, exit, kernel, user windows.Filetime
	if err := windows.GetProcessTimes(handle, &creation, &exit, &kernel, &user); err != nil {
		return ProcessIdentity{}, "inconclusive"
	}
	if exit.HighDateTime != 0 || exit.LowDateTime != 0 {
		return ProcessIdentity{}, "dead"
	}
	buffer := make([]uint16, 32768)
	size := uint32(len(buffer))
	if err := windows.QueryFullProcessImageName(handle, 0, &buffer[0], &size); err != nil {
		return ProcessIdentity{}, "inconclusive"
	}
	created := uint64(creation.HighDateTime)<<32 | uint64(creation.LowDateTime)
	executable := strings.ToLower(realpath(windows.UTF16ToString(buffer[:size])))
	return ProcessIdentity{PID: pid, Token: fmt.Sprintf("windows:%d", created), Executable: executable}, "alive"
}
