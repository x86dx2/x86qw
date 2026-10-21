//go:build windows

package atomicfile

import (
	"errors"
	"os"
	"time"

	"golang.org/x/sys/windows"
)

// rename retries for a moment while another process (an antivirus, the
// reader of a journal) has the destination open.
func rename(from, to string) error {
	deadline := time.Now().Add(2 * time.Second)
	for {
		err := os.Rename(from, to)
		if err == nil || time.Now().After(deadline) ||
			!(errors.Is(err, windows.ERROR_ACCESS_DENIED) || errors.Is(err, windows.ERROR_SHARING_VIOLATION)) {
			return err
		}
		time.Sleep(20 * time.Millisecond)
	}
}
