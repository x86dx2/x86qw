package setup

import (
	"errors"
	"os"
	"runtime"

	"github.com/x86dx2/x86qw/go/internal/cli"
)

// SupportedIntegration is what this system can do for an installation:
// the x86qw command, qw:// links and demos everywhere, and menu shortcuts
// on Windows and Linux.
func SupportedIntegration() Integration {
	switch runtime.GOOS {
	case "windows", "linux":
		return Integration{Shortcuts: true, Path: true, QWLinks: true, Demos: true}
	case "darwin":
		return Integration{Path: true, QWLinks: true, Demos: true}
	}
	return Integration{Path: true}
}

// supported keeps the choices this system can honour.
func (i Integration) supported() Integration {
	s := SupportedIntegration()
	return Integration{Shortcuts: i.Shortcuts && s.Shortcuts, Path: i.Path && s.Path, QWLinks: i.QWLinks && s.QWLinks, Demos: i.Demos && s.Demos}
}

// Integrate makes the system match the choices for target: what is on is
// created, what is off is removed when it belongs to target.
func Integrate(target string, choices Integration) error {
	choices = choices.supported()
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	var errs []error
	switch runtime.GOOS {
	case "windows":
		if choices.Path {
			errs = append(errs, cli.AddUserPath(target))
		} else {
			errs = append(errs, cli.RemoveUserPath(target))
		}
		if choices.Shortcuts {
			_, err := cli.InstallShortcuts(target)
			errs = append(errs, err)
		} else {
			errs = append(errs, cli.RemoveShortcuts(target))
		}
	default:
		if choices.Path {
			_, err := cli.LinkUserCommand(target, home)
			errs = append(errs, err)
		} else {
			errs = append(errs, cli.UnlinkUserCommand(target, home))
		}
		if runtime.GOOS == "linux" {
			if choices.Shortcuts {
				errs = append(errs, cli.InstallDesktopEntry(target, home))
			} else {
				errs = append(errs, cli.RemoveDesktopEntry(target, home))
			}
		}
	}
	errs = append(errs, cli.SetHandlers(target, choices.QWLinks, choices.Demos))
	return errors.Join(errs...)
}
