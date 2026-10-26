// Package setup holds what the installer asks and decides, independent of
// the interface (terminal or browser) that asks it.
package setup

import (
	"crypto/rand"
	"encoding/json"
	"fmt"
	"math/big"
	"slices"
	"sort"
	"strconv"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/play"
)

// Intent is what the person wants to do with x86QW.
type Intent string

const (
	Play Intent = "play"
	Host Intent = "host"
	Both Intent = "both"
)

// WantsClient reports whether the installation includes the game client.
func (i Intent) WantsClient() bool { return i != Host }

// WantsServer reports whether the installation includes a dedicated server.
func (i Intent) WantsServer() bool { return i != Play }

// Keys binds the movement actions.
type Keys struct {
	Preset                           string // wasd, esdf, arrows or custom
	Forward, Back, Left, Right, Jump string
}

// KeyPresets are the movement layouts offered by name.
var KeyPresets = map[string]Keys{
	"wasd":   {Preset: "wasd", Forward: "w", Back: "s", Left: "a", Right: "d", Jump: "space"},
	"esdf":   {Preset: "esdf", Forward: "e", Back: "d", Left: "s", Right: "f", Jump: "space"},
	"arrows": {Preset: "arrows", Forward: "uparrow", Back: "downarrow", Left: "leftarrow", Right: "rightarrow", Jump: "space"},
}

// Mouse is the aim setup.
type Mouse struct {
	Sensitivity float64
	DPI         int
	Invert      bool
}

// CentimetersPer360 is how far the mouse travels for a full turn with the
// ezQuake default m_yaw of 0.022 degrees per count.
func (m Mouse) CentimetersPer360() float64 {
	if m.Sensitivity <= 0 || m.DPI <= 0 {
		return 0
	}
	return 360 / (m.Sensitivity * 0.022 * float64(m.DPI)) * 2.54
}

// Video is the client display setup; zero width and height mean the
// detected screen.
type Video struct {
	Width, Height int
	Display       string // fullscreen, borderless or windowed
	FOV           int
}

// Instance is one KTX game server.
type Instance struct {
	Mode, Map string
	Bots      bool
}

// Server is the dedicated server setup.
type Server struct {
	Name      string
	Hostname  string // public DNS name players connect to; empty uses the IP
	BasePort  int
	Instances []Instance
	Rcon      string
	QTVPort   int // 0 disables QTV
	QWFWDPort int // 0 disables QWFWD
	// Local keeps the server on this machine; otherwise it listens on
	// every interface, for players on the network and the internet.
	Local bool
	Slots int // players per instance
	// NoRecord turns off the automatic MVD demos.
	NoRecord bool
	// Password lets players in, Spectators lets spectators in; empty is open.
	Password, Spectators string
}

// The server's defaults, and the limits the setup accepts.
const (
	DefaultBasePort  = 27500
	DefaultQTVPort   = 28000
	DefaultQWFWDPort = 30000
	DefaultSlots     = 16
	MinSlots         = 2
	MaxSlots         = 32
	MaxInstances     = 8
	// MVDSVPathRoom is the longest folder MVDSV still sees every file of:
	// it cuts paths at 128 bytes, and its deepest files sit about 40 below.
	MVDSVPathRoom = 88
)

// PathTooLong reports whether a server installed at target would not see
// its own maps and progs.
func PathTooLong(target string) bool { return len(target) > MVDSVPathRoom }

// NameRune is what a nick, a server name or a password may hold: the
// game's console reads them between quotes, so no quote, backslash or
// semicolon.
func NameRune(r rune) bool { return r >= ' ' && r != '"' && r != '\\' && r != ';' && r != 0x7f }

// PlainRune is what a nick or a server name may hold: a NameRune in ASCII.
// The game sends names as bytes of the Quake charset, which has no accents,
// so "Nélson" would reach the other players garbled.
func PlainRune(r rune) bool { return r < 0x7f && NameRune(r) }

// KeyName reports whether key can be bound in the client: one visible ASCII
// character but the quote, or a key name such as space, uparrow or mouse2.
// The console would run anything else after the bind as another command.
func KeyName(key string) bool {
	if len(key) == 1 {
		return key[0] > ' ' && key[0] < 0x7f && key[0] != '"'
	}
	return key != "" && strings.IndexFunc(key, func(r rune) bool {
		return (r < 'a' || r > 'z') && (r < 'A' || r > 'Z') && (r < '0' || r > '9') && r != '_'
	}) < 0
}

// Ports lists the UDP game ports, one per instance.
func (s Server) Ports() []int {
	ports := make([]int, len(s.Instances))
	for index := range s.Instances {
		ports[index] = s.BasePort + index
	}
	return ports
}

// Integration is how x86QW plugs into the operating system.
type Integration struct {
	Shortcuts, Path, QWLinks, Demos bool
}

// Options is everything the installer asks.
type Options struct {
	Intent      Intent
	Nick        string
	Content     string   // essential, recommended, complete or custom
	Components  []string // the custom selection
	Keys        Keys
	Mouse       Mouse
	Experience  string // the client ruleset, one of RulesetKeys
	Video       Video
	Volume      int
	Server      Server
	Integration Integration
	Target      string
	// Upgrade, applied to an existing installation, also brings every
	// component to its newest version; otherwise only what was added is
	// installed. It is never recorded.
	Upgrade bool `json:"-"`
}

// Defaults is a fresh installation for intent.
func Defaults(intent Intent, target string) Options {
	return Options{
		Intent: intent, Content: "recommended", Keys: KeyPresets["wasd"],
		Mouse: Mouse{Sensitivity: 2.6, DPI: 800}, Experience: "x86qw",
		Video: Video{Display: "fullscreen", FOV: 110}, Volume: 70,
		Server: Server{
			Name: "x86QW KTX", BasePort: 27500, Rcon: Password(14),
			Instances: []Instance{{Mode: "duel", Map: "dm6"}, {Mode: "4on4", Map: "dm3", Bots: true}},
			QTVPort:   28000, QWFWDPort: 30000, Slots: 16,
		},
		Integration: SupportedIntegration(),
		Target:      target,
	}
}

// Password draws n characters without look-alikes (0/O, 1/l/I).
func Password(n int) string {
	const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
	var out strings.Builder
	for range n {
		index, err := rand.Int(rand.Reader, big.NewInt(int64(len(alphabet))))
		if err != nil {
			panic(err)
		}
		out.WriteByte(alphabet[index.Int64()])
	}
	return out.String()
}

// FakeName is the cl_fakename ezQuake prefixes team messages with: the
// first three characters of the nick.
func FakeName(nick string) string {
	runes := []rune(strings.TrimSpace(nick))
	if len(runes) > 3 {
		runes = runes[:3]
	}
	if len(runes) == 0 {
		return "pla"
	}
	return string(runes)
}

// Validate reports the first problem that stops the installation, as a
// Problem naming the choice it is about.
func (o Options) Validate() error {
	switch o.Intent {
	case Play, Host, Both:
	default:
		return problem("intent", "v.intent", o.Intent)
	}
	if o.Intent.WantsClient() {
		nick := strings.TrimSpace(o.Nick)
		switch {
		case nick == "":
			return problem("nick", "v.nick")
		case strings.IndexFunc(nick, func(r rune) bool { return !PlainRune(r) }) >= 0:
			return problem("nick", "v.nick.chars")
		case !slices.Contains(RulesetKeys, o.Experience):
			return problem("experience", "v.ruleset", o.Experience)
		}
		for _, key := range []string{o.Keys.Forward, o.Keys.Back, o.Keys.Left, o.Keys.Right, o.Keys.Jump} {
			if key != "" && key != "—" && !KeyName(key) {
				return problem("keys", "v.key", key)
			}
		}
	}
	if o.Intent.WantsServer() {
		return o.Server.Validate()
	}
	return nil
}

// Validate reports the first problem that stops the server from starting.
func (s Server) Validate() error {
	if strings.TrimSpace(s.Name) == "" {
		return problem("server.name", "v.name")
	}
	if strings.IndexFunc(s.Name, func(r rune) bool { return !PlainRune(r) }) >= 0 {
		return problem("server.name", "v.name.chars")
	}
	if len(s.Instances) == 0 || len(s.Instances) > MaxInstances {
		return problem("server.instances", "v.instances")
	}
	modes, err := play.LoadModes()
	if err != nil {
		return err
	}
	for _, instance := range s.Instances {
		mode, err := play.FindMode(modes, instance.Mode)
		if err != nil {
			return problem("server.instances", "v.mode", instance.Mode)
		}
		if instance.Bots && !mode.Bots {
			return problem("server.instances", "v.bots", mode.Label)
		}
	}
	if s.Slots != 0 && (s.Slots < MinSlots || s.Slots > MaxSlots) {
		return problem("server.slots", "v.slots", s.Slots)
	}
	for _, secret := range []struct{ field, value string }{{"server.password", s.Password}, {"server.password", s.Spectators}, {"server.rcon", s.Rcon}} {
		if strings.IndexFunc(secret.value, func(r rune) bool { return !NameRune(r) }) >= 0 {
			return problem(secret.field, "v.secrets")
		}
	}
	used := map[string]Text{}
	claim := func(field, proto string, port int, what Text) error {
		if port < 1025 || port > 65535 {
			return problem(field, "v.port.range", what, port)
		}
		key := proto + strconv.Itoa(port)
		if other, ok := used[key]; ok {
			return problem(field, "v.port.clash", other, what, strings.ToUpper(proto), port)
		}
		used[key] = what
		return nil
	}
	for index, port := range s.Ports() {
		if err := claim("server.ports", "udp", port, Say("instance", index+1)); err != nil {
			return err
		}
	}
	if s.QTVPort != 0 {
		if err := claim("server.qtv", "tcp", s.QTVPort, Raw("QTV")); err != nil {
			return err
		}
	}
	if s.QWFWDPort != 0 {
		if err := claim("server.qwfwd", "udp", s.QWFWDPort, Raw("QWFWD")); err != nil {
			return err
		}
	}
	return nil
}

// ShellJoin quotes arguments for a POSIX shell when they need it.
func ShellJoin(args []string) string {
	out := make([]string, len(args))
	for index, arg := range args {
		if arg == "" || strings.ContainsAny(arg, " \t\"'$`\\!*?;&|<>()[]{}~#") {
			out[index] = "'" + strings.ReplaceAll(arg, "'", `'\''`) + "'"
		} else {
			out[index] = arg
		}
	}
	return strings.Join(out, " ")
}

// Recipe is the command that repeats this installation without questions.
func (o Options) Recipe() []string {
	args := []string{"x86qw", "setup", "--intent", string(o.Intent)}
	if o.Intent.WantsClient() {
		content := o.Content
		if content == "custom" {
			selection := append([]string(nil), o.Components...)
			sort.Strings(selection)
			content = strings.Join(selection, ",")
		}
		keys := o.Keys.Preset
		if keys == "custom" || keys == "" {
			keys = strings.Join([]string{o.Keys.Forward, o.Keys.Back, o.Keys.Left, o.Keys.Right, o.Keys.Jump}, ",")
		}
		args = append(args, "--nick", strings.TrimSpace(o.Nick), "--content", content, "--keys", keys,
			"--sensitivity", strconv.FormatFloat(o.Mouse.Sensitivity, 'f', -1, 64))
		if o.Mouse.Invert {
			args = append(args, "--invert-mouse")
		}
		video := "auto"
		if o.Video.Width > 0 && o.Video.Height > 0 {
			video = fmt.Sprintf("%dx%d", o.Video.Width, o.Video.Height)
		}
		args = append(args, "--experience", o.Experience,
			"--video", fmt.Sprintf("%s,%s,fov%d", video, o.Video.Display, o.Video.FOV), "--volume", strconv.Itoa(o.Volume))
	}
	if o.Intent.WantsServer() {
		var instances []string
		for _, instance := range o.Server.Instances {
			value := instance.Mode + ":" + instance.Map
			if instance.Bots {
				value += "+bots"
			}
			instances = append(instances, value)
		}
		args = append(args, "--server-name", o.Server.Name)
		if o.Server.Hostname != "" {
			args = append(args, "--hostname", o.Server.Hostname)
		}
		args = append(args, "--servers", strings.Join(instances, ","), "--base-port", strconv.Itoa(o.Server.BasePort),
			"--qtv", portFlag(o.Server.QTVPort), "--qwfwd", portFlag(o.Server.QWFWDPort))
		if o.Server.Local {
			args = append(args, "--local")
		}
		if o.Server.Slots != 0 && o.Server.Slots != 16 {
			args = append(args, "--slots", strconv.Itoa(o.Server.Slots))
		}
		if o.Server.NoRecord {
			args = append(args, "--no-record")
		}
	}
	var integrate []string
	for _, item := range []struct {
		on   bool
		name string
	}{{o.Integration.Shortcuts, "shortcuts"}, {o.Integration.Path, "path"}, {o.Integration.QWLinks, "qw-links"}, {o.Integration.Demos, "demos"}} {
		if item.on {
			integrate = append(integrate, item.name)
		}
	}
	if len(integrate) == 0 {
		integrate = []string{"none"}
	}
	return append(args, "--integrate", strings.Join(integrate, ","), "--target", o.Target, "--yes")
}

func portFlag(port int) string {
	if port == 0 {
		return "off"
	}
	return strconv.Itoa(port)
}

// RecipeJSON is the same recipe as a file for `x86qw setup --recipe`; like
// the command, it carries no password.
func (o Options) RecipeJSON() ([]byte, error) {
	copy := o
	copy.Server.Rcon, copy.Server.Password, copy.Server.Spectators = "", "", ""
	return json.MarshalIndent(recipeFile{1, copy}, "", "  ")
}

// recipeFile is a recipe as RecipeJSON writes it.
type recipeFile struct {
	Format int `json:"format"`
	Options
}

// RulesetKeys are the client rulesets `x86qw play` knows, the recommended
// one first; play owns the list.
var RulesetKeys = play.RulesetKeys
