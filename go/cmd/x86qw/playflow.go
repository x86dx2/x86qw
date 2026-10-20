package main

import (
	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/play"
)

// playChoice is everything needed to launch.
type playChoice struct {
	game    play.Game
	mode    *play.Mode
	mapName string
	options play.Options
	assets  map[string]bool
	runtime install.Runtime
	ruleset string
}
