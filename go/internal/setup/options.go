// Package setup holds what the installer asks and decides, independent of
// the interface (terminal or browser) that asks it.
package setup

import (
	"slices"
	"crypto/rand"
	"encoding/json"
	"fmt"
	"math/big"
	"sort"
	"strconv"
	"strings"
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
			QTVPort:   28000, QWFWDPort: 30000,
		},
		Integration: Integration{Shortcuts: true, Path: true, QWLinks: true, Demos: true},
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

// Validate reports the first problem that stops the installation.
func (o Options) Validate() error {
	switch o.Intent {
	case Play, Host, Both:
	default:
		return fmt.Errorf("intenção inválida: %q", o.Intent)
	}
	if o.Intent.WantsClient() && strings.TrimSpace(o.Nick) == "" {
		return fmt.Errorf("escolha um nick")
	}
	if o.Intent.WantsClient() && !slices.Contains(RulesetKeys, o.Experience) {
		return fmt.Errorf("ruleset desconhecido: %q", o.Experience)
	}
	if o.Intent.WantsServer() {
		s := o.Server
		if strings.TrimSpace(s.Name) == "" {
			return fmt.Errorf("dê um nome ao servidor")
		}
		if len(s.Instances) == 0 || len(s.Instances) > 8 {
			return fmt.Errorf("o servidor precisa de 1 a 8 instâncias")
		}
		used := map[string]string{}
		claim := func(proto string, port int, what string) error {
			if port < 1025 || port > 65535 {
				return fmt.Errorf("%s: porta %d fora de 1025–65535", what, port)
			}
			key := proto + strconv.Itoa(port)
			if other, ok := used[key]; ok {
				return fmt.Errorf("%s e %s usam a mesma porta %s %d", other, what, strings.ToUpper(proto), port)
			}
			used[key] = what
			return nil
		}
		for index, port := range s.Ports() {
			if err := claim("udp", port, fmt.Sprintf("instância #%d", index+1)); err != nil {
				return err
			}
		}
		if s.QTVPort != 0 {
			if err := claim("tcp", s.QTVPort, "QTV"); err != nil {
				return err
			}
		}
		if s.QWFWDPort != 0 {
			if err := claim("udp", s.QWFWDPort, "QWFWD"); err != nil {
				return err
			}
		}
	}
	return nil
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

// RecipeJSON is the same recipe as a file; the rcon password is left out.
func (o Options) RecipeJSON() ([]byte, error) {
	copy := o
	copy.Server.Rcon = ""
	return json.MarshalIndent(struct {
		Format int `json:"format"`
		Options
	}{1, copy}, "", "  ")
}

// RulesetKeys are the client rulesets `x86qw play` knows, the recommended
// one first.
var RulesetKeys = []string{"x86qw", "qcon", "smackdown", "thunderdome", "mtfl", "smackdrive"}
