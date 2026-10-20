//go:build windows

package services

import (
	"os"
	"os/exec"
)

// Windows services (Job Objects, named mutex lock) are not ported yet; every
// entry point refuses before reaching these helpers.

var errWindows = errorf("os serviços x86QW ainda não são suportados no Windows")

func identityOf(info os.FileInfo) [2]int64 { return [2]int64{} }

func identityOfFile(file *os.File) ([2]int64, error) { return [2]int64{}, errWindows }

func processGroupStatus(group int) string { return "inconclusive" }

func terminateRecordedProcess(entry map[string]any) (string, error) { return "", errWindows }

func startDetached(arguments []string, dir string, output *os.File) (*exec.Cmd, error) {
	return nil, errWindows
}

func exitCode(state *os.ProcessState) int { return state.ExitCode() }

var serviceSignals = []os.Signal{os.Interrupt}

func signalNumber(sig os.Signal) int { return 2 }

func stopGroups(commands []*exec.Cmd, exited map[int]bool) error { return errWindows }

func spawnBackgroundController(executable string, arguments []string, request []byte) (*exec.Cmd, error) {
	return nil, errWindows
}
