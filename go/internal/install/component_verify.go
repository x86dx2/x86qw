package install

import (
	"errors"
	"os"
	"path/filepath"

	"github.com/x86dx2/x86qw/go/internal/receipt"
)

// VerifyComponent checks every managed file of one component like
// manager.py verify_component. A missing record returns (nil, nil).
func VerifyComponent(root, component string) (*receipt.Record, error) {
	record, err := receipt.Load(filepath.Join(root, receipt.ComponentsDir, component))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if _, err := verifyRecord(root, record); err != nil {
		return nil, err
	}
	return record, nil
}

// CheckPaks verifies the original id1 PAKs.
func CheckPaks(root string) error {
	for _, pak := range []struct{ path, sha string }{{"id1/pak0.pak", pak0SHA256}, {"id1/pak1.pak", pak1SHA256}} {
		if err := checkPak(filepath.Join(root, pak.path), pak.sha); err != nil {
			return err
		}
	}
	return nil
}
