//go:build windows

package privfs

import (
	"os"
	"unsafe"

	"golang.org/x/sys/windows"
)

// descriptor is private_sddl(user, directory=True): owner and a protected
// DACL with inheritable full access for the current user and LOCAL SYSTEM.
func descriptor() (*windows.SECURITY_DESCRIPTOR, error) {
	token := windows.GetCurrentProcessToken()
	user, err := token.GetTokenUser()
	if err != nil {
		return nil, err
	}
	sid := user.User.Sid.String()
	return windows.SecurityDescriptorFromString("O:" + sid + "D:P(A;OICI;FA;;;" + sid + ")(A;OICI;FA;;;SY)")
}

// Mkdir atomically creates one directory with the private DACL.
func Mkdir(path string) error {
	sd, err := descriptor()
	if err != nil {
		return &os.PathError{Op: "mkdir", Path: path, Err: err}
	}
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return &os.PathError{Op: "mkdir", Path: path, Err: err}
	}
	attributes := windows.SecurityAttributes{Length: uint32(unsafe.Sizeof(windows.SecurityAttributes{})), SecurityDescriptor: sd}
	if err := windows.CreateDirectory(name, &attributes); err != nil {
		return &os.PathError{Op: "mkdir", Path: path, Err: err}
	}
	return nil
}

// Protect replaces the DACL of an existing directory with the private one.
func Protect(path string) error {
	sd, err := descriptor()
	if err != nil {
		return &os.PathError{Op: "protect", Path: path, Err: err}
	}
	dacl, _, err := sd.DACL()
	if err != nil {
		return &os.PathError{Op: "protect", Path: path, Err: err}
	}
	err = windows.SetNamedSecurityInfo(path, windows.SE_FILE_OBJECT,
		windows.DACL_SECURITY_INFORMATION|windows.PROTECTED_DACL_SECURITY_INFORMATION, nil, nil, dacl, nil)
	if err != nil {
		return &os.PathError{Op: "protect", Path: path, Err: err}
	}
	return nil
}

// Ensure always reapplies the private DACL: Windows modes carry no ACL.
func Ensure(path string, info os.FileInfo) error { return Protect(path) }
