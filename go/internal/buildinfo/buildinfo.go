// Package buildinfo identifies the running binary.
package buildinfo

import "runtime/debug"

// Version is set at release time with -ldflags "-X .../buildinfo.Version=1.2.3".
var Version = "0.0.0-dev"

// Commit returns the VCS revision embedded by the Go toolchain, if any.
func Commit() string {
	info, ok := debug.ReadBuildInfo()
	if !ok {
		return ""
	}
	revision, modified := "", false
	for _, setting := range info.Settings {
		switch setting.Key {
		case "vcs.revision":
			revision = setting.Value
		case "vcs.modified":
			modified = setting.Value == "true"
		}
	}
	if revision != "" && modified {
		revision += "-dirty"
	}
	return revision
}
