package setup

import (
	"context"
	"time"
)

// Component is one installable unit as the interface shows it.
type Component struct {
	ID, Label, Description, Kind string
	Size                         int64
	Requires                     []string
}

// Catalog is what can be installed.
type Catalog struct {
	Components []Component
	Profiles   map[string][]string
	CoreSize   int64            // the id1 game data, always installed
	ClientSize map[string]int64 // ezQuake per platform
	Modes      []Mode
}

// Mode is a KTX game mode a server instance can run.
type Mode struct {
	ID, Label, DefaultMap string
	Maps                  []string
}

// CheckState is where one preflight check stands.
type CheckState int

const (
	CheckRunning CheckState = iota
	CheckOK
	CheckWarning
	CheckFailed
)

// Check is one preflight result.
type Check struct {
	ID, Title, Detail, Value, Fix string
	State                         CheckState
}

// Lane is one component being installed.
type Lane struct {
	ID, Label   string
	Size, Done  int64
	Files, Kept int
}

// Progress is a snapshot of an installation.
type Progress struct {
	Lanes            []Lane
	Total, Done      int64
	Speed            float64 // bytes per second, smoothed
	ETA              time.Duration
	Log              []LogLine
	Finished, Paused bool
	Err              error
}

// LogLine is one finished file.
type LogLine struct {
	At   time.Duration
	Kind byte // '+' downloaded, '=' kept unchanged, 'x' failed
	Path string
	Size int64
}

// Existing describes an installation found at the target.
type Existing struct {
	Target, CLIVersion string
	Components         int
	Updates            []Update
	Options            Options
}

// Update is one component with a newer version.
type Update struct {
	ID, Label, From, To, Note string
}

// Backend is what the interfaces talk to; the demo backend fakes it for
// prototypes and tests.
type Backend interface {
	Catalog(ctx context.Context) (*Catalog, error)
	Existing(ctx context.Context, target string) (*Existing, error)
	Network(ctx context.Context) Network
	CheckHostname(ctx context.Context, hostname string, network Network) DNSCheck
	Preflight(ctx context.Context, options Options) <-chan Check
	Install(ctx context.Context, options Options, control <-chan bool) <-chan Progress
}
