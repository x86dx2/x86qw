package setup

import (
	"os/exec"
	"runtime"
)

// OpenInBrowser opens a local page, such as the panel x86qw ui writes, in
// the system's browser.
func OpenInBrowser(path string) error {
	var command *exec.Cmd
	switch runtime.GOOS {
	case "darwin":
		command = exec.Command("open", path)
	case "windows":
		command = exec.Command("rundll32", "url.dll,FileProtocolHandler", path)
	default:
		command = exec.Command("xdg-open", path)
	}
	if err := command.Start(); err != nil {
		return err
	}
	go command.Wait()
	return nil
}
