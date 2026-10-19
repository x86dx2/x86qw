//go:build !windows

package doctor

import "golang.org/x/sys/unix"

func freeBytes(path string) (int64, error) {
	var stat unix.Statfs_t
	if err := unix.Statfs(path, &stat); err != nil {
		return 0, err
	}
	return int64(stat.Bavail) * int64(stat.Bsize), nil
}

func accessible(path string, mode uint32) bool { return unix.Access(path, mode) == nil }
