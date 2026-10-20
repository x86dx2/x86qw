//go:build !darwin && !linux && !windows

package cli

import "errors"

func setHandlers(root string, links, demos bool) error {
	if links || demos {
		return errors.New("abrir links e demos não é suportado neste sistema")
	}
	return nil
}
