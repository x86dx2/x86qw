package play

import (
	"fmt"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/launchguard"
)

// startGrace is how long Start watches ezQuake after handing it a KTX cfg:
// long enough to report a client that dies at once, short enough not to
// hold the terminal. A cfg left by a later crash is removed by the next
// launch (Recover).
const startGrace = 3 * time.Second

// Start opens ezQuake on the installation at target with launch,
// publishing its KTX cfg first when it needs one.
func Start(target, executable, binarySHA string, launch *Launch) error {
	arguments := launch.Arguments
	var config *RuntimeConfig
	if len(launch.StartupCommands) > 0 {
		payload, err := Payload(launch.BotNameSettings, launch.StartupCommands)
		if err == nil {
			config, err = Create(target, payload)
		}
		if err != nil {
			return err
		}
		replaced := make([]string, len(arguments))
		for index, argument := range arguments {
			replaced[index] = strings.ReplaceAll(argument, RuntimeConfigPlaceholder, config.Name)
		}
		arguments = replaced
	}
	argv := append([]string{"-nohome", "-basedir", target}, arguments...)
	release, err := launchguard.Hold(executable, binarySHA)
	var child *Child
	if err == nil {
		child, err = Spawn(executable, argv, target)
		release()
	}
	if err != nil {
		if config != nil {
			config.Release()
		}
		return fmt.Errorf("não foi possível abrir o ezQuake: %w", err)
	}
	if config == nil {
		return nil
	}
	if err := config.Transfer(child.Pid); err != nil {
		child.Kill()
		config.Release()
		return err
	}
	if exited, code := child.ExitsWithin(startGrace); exited {
		config.Release()
		return fmt.Errorf("o ezQuake encerrou antes de carregar a configuração KTX (código %d)", code)
	}
	return nil
}
