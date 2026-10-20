package install

import (
	"errors"
	"os"
	"path/filepath"

	"github.com/x86dx2/x86qw/go/internal/receipt"
)

// VerifyComponentRecord checks every managed file of one component like
// manager.py verify_component. A missing record returns (nil, nil).
func VerifyComponentRecord(root, component string) (*receipt.Record, error) {
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
