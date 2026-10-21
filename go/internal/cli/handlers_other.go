//go:build !darwin && !linux && !windows

package cli

import "errors"

func setHandlers(root string, links, demos bool) error {
	if links || demos {
		return errors.New("opening links and demos is not supported on this system")
	}
	return nil
}
