// Package buildinfo identifies the running binary.
package buildinfo

// Version is set at release time with -ldflags "-X .../buildinfo.Version=1.2.3".
var Version = "0.0.0-dev"
