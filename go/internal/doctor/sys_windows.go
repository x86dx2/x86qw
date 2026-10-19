//go:build windows

package doctor

import (
	"os"

	"golang.org/x/sys/windows"
)

func freeBytes(path string) (int64, error) {
	var free uint64
	pointer, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return 0, err
	}
	if err := windows.GetDiskFreeSpaceEx(pointer, &free, nil, nil); err != nil {
		return 0, err
	}
	return int64(free), nil
}

func accessible(path string, mode uint32) bool {
	_, err := os.Stat(path)
	return err == nil
}
