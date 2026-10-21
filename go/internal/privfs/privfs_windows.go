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
	user, err := currentUserSID()
	if err != nil {
		return nil, err
	}
	sid := user.String()
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

// ProtectFile replaces the DACL of an existing file with a protected one
// granting access only to the current user and LOCAL SYSTEM.
func ProtectFile(path string) error {
	sid, err := currentUserSID()
	if err != nil {
		return &os.PathError{Op: "protect", Path: path, Err: err}
	}
	sd, err := windows.SecurityDescriptorFromString("D:P(A;;FA;;;" + sid.String() + ")(A;;FA;;;SY)")
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

func currentUserSID() (*windows.SID, error) {
	user, err := windows.GetCurrentProcessToken().GetTokenUser()
	if err != nil {
		return nil, err
	}
	return user.User.Sid, nil
}

// IsPrivateFile reports whether path is private: the DACL
// may grant access only to the current user and LOCAL SYSTEM, and must
// grant it to the current user.
func IsPrivateFile(path string, info os.FileInfo) bool {
	user, err := currentUserSID()
	if err != nil {
		return false
	}
	system, err := windows.CreateWellKnownSid(windows.WinLocalSystemSid)
	if err != nil {
		return false
	}
	sd, err := windows.GetNamedSecurityInfo(path, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION)
	if err != nil {
		return false
	}
	dacl, _, err := sd.DACL()
	if err != nil || dacl == nil {
		return false
	}
	userGranted := false
	for index := uint32(0); index < uint32(dacl.AceCount); index++ {
		var ace *windows.ACCESS_ALLOWED_ACE
		if windows.GetAce(dacl, index, &ace) != nil {
			return false
		}
		if ace.Header.AceType != windows.ACCESS_ALLOWED_ACE_TYPE {
			continue
		}
		sid := (*windows.SID)(unsafe.Pointer(&ace.SidStart))
		switch {
		case sid.Equals(user):
			userGranted = true
		case sid.Equals(system):
		default:
			return false
		}
	}
	return userGranted
}

// Guidance tells the user how to make a rejected secret file private.
func Guidance() string { return "protect the DACL for the current user and LOCAL SYSTEM" }
