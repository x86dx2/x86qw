package play

import (
	"fmt"
	"path/filepath"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/launchguard"
	"github.com/x86dx2/x86qw/go/internal/lock"
)

// startGrace is how long Start watches ezQuake after handing it a KTX cfg:
// long enough to report a client that dies at once, short enough not to
// hold the terminal. A cfg left by a later crash is removed by the next
// launch (Recover).
const startGrace = 3 * time.Second

// CheckBasedir refuses an installation folder ezQuake and MVDSV would
// misread: on their command line, " +" starts a command, folder name or not.
func CheckBasedir(target string) error {
	if strings.Contains(target, " +") {
		return fmt.Errorf(`the folder name holds " +", which ezQuake and MVDSV run as a command; move the installation: %s`, target)
	}
	return nil
}

// Start opens ezQuake on the installation at target with launch,
// publishing its KTX cfg first when it needs one.
func Start(target, executable, binarySHA string, launch *Launch) error {
	if err := CheckBasedir(target); err != nil {
		return err
	}
	// An update, repair or uninstall replaces or removes the files the
	// client would run from; a hosted server holds the lock too, and stays.
	if owner, err := lock.ReadOwner(filepath.Join(target, ".x86qw", "sessions", "active.lock")); err == nil && owner.OperationKind == "maintenance" &&
		lock.Probe(owner.ControllerPID, owner.ControllerStartToken, owner.ControllerExecutable) == "alive" {
		return fmt.Errorf("x86QW is running %s in this installation; wait for it to finish, then start ezQuake again", owner.Command)
	}
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
		return fmt.Errorf("could not open ezQuake: %w", err)
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
		return fmt.Errorf("ezQuake quit before loading the KTX configuration (code %d)", code)
	}
	return nil
}
