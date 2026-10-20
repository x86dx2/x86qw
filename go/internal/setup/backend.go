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
	Bots                  bool // the mode can fill a server with bots
	// For the match screen: who it is for and what it asks.
	Players, Description string
	BotLimit             int    // a fixed roster's bots; 0 for an open mode
	Kind                 string // race, ctf, tot or "" for the other modes
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
	FileCount   int // files in the component, so Files can reach it
}

// Progress is a snapshot of an installation.
type Progress struct {
	Lanes            []Lane
	Total, Done      int64
	Speed            float64 // bytes per second, smoothed
	ETA              time.Duration
	Log              []LogLine
	Finished, Paused bool
	// Holding is true once a paused installation has stopped: it pauses
	// between packages, so Paused can come before Holding.
	Holding  bool
	Warnings []string // what the installation preserved or could not do
	Err      error
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
	Installed          []string // component IDs; the setup adds, never removes
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
	// Install installs options, or applies them to the installation
	// already at the target; sending on control pauses (true) or resumes.
	Install(ctx context.Context, options Options, control <-chan bool) <-chan Progress
	// Repair verifies an installation and repairs what it can.
	Repair(ctx context.Context, target string) (Report, error)
	// Uninstall removes the installation; keepPersonal keeps configs,
	// demos and screenshots.
	Uninstall(ctx context.Context, target string, keepPersonal bool) error

	// MatchCatalog lists the local games, KTX modes and clients.
	MatchCatalog(ctx context.Context, target string) (*MatchCatalog, error)
	// Maps lists the maps a match can run on with its options.
	Maps(ctx context.Context, target string, match Match) (*MapList, error)
	// Play launches the match in ezQuake and records it as the last one.
	Play(ctx context.Context, target string, match Match) error
	// Servers lists public servers, or the favorites when offline.
	Servers(ctx context.Context, target string) ([]PublicServer, error)
	// Open connects ezQuake to a qw:// address.
	Open(ctx context.Context, target, address string) error
	// Stack reports the running server session, nil when none runs.
	Stack(ctx context.Context, target string) (*Stack, error)
	// StartServer records the server choices and starts every instance.
	StartServer(ctx context.Context, target string, options Options) error
	// StartService starts QTV ("qtv") or QWFWD ("proxy") on its own.
	StartService(ctx context.Context, target, kind string, options Options) error
	// StopServer stops the running session.
	StopServer(ctx context.Context, target string) error
}

// Report is what a repair found and did.
type Report struct {
	Files, Components int
	Problem           string // what verification found; empty when sound
	Repaired          bool
}
