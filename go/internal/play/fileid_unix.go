//go:build !windows

package play

import (
	"os"
	"syscall"
)

// fileIdentity is the (st_dev, st_ino) pair the journal records.
func fileIdentity(path string) (uint64, uint64, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return 0, 0, err
	}
	stat := info.Sys().(*syscall.Stat_t)
	return uint64(stat.Dev), uint64(stat.Ino), nil
}
