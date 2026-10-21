package setup

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/play"
	"github.com/x86dx2/x86qw/go/internal/privfs"
	"github.com/x86dx2/x86qw/go/internal/services"
)

// The setup keeps its own files in a private directory of the
// installation: the choices, so the management panel can show and change
// them, and the rcon password, which host presets refuse to store.
const (
	choicesDir  = ".x86qw/setup"
	choicesFile = choicesDir + "/choices.json"
	RconFile    = choicesDir + "/rcon"
	// PasswordFile and SpectatorFile hold the server's other passwords.
	PasswordFile  = choicesDir + "/password"
	SpectatorFile = choicesDir + "/spectator"
	// SetupConfig holds the client choices; x86qw-user.cfg runs it once
	// each time they are applied.
	SetupConfig = "qw/x86qw-setup.cfg"
	userConfig  = "qw/x86qw-user.cfg"
)

// SaveChoices records options in the installation, readable only by the
// user since they hold the rcon password.
func SaveChoices(target string, options Options) error {
	dir := filepath.Join(target, filepath.FromSlash(choicesDir))
	if err := privfs.MkdirAll(dir); err != nil {
		return err
	}
	payload, err := json.MarshalIndent(options, "", "  ")
	if err != nil {
		return err
	}
	if err := writePrivate(filepath.Join(target, filepath.FromSlash(choicesFile)), append(payload, '\n')); err != nil {
		return err
	}
	if !options.Intent.WantsServer() {
		return nil
	}
	return saveSecrets(target, options.Server)
}

// saveSecrets writes the passwords the host command reads from files.
func saveSecrets(target string, server Server) error {
	if err := privfs.MkdirAll(filepath.Join(target, filepath.FromSlash(choicesDir))); err != nil {
		return err
	}
	for _, secret := range []struct{ file, value string }{
		{RconFile, server.Rcon}, {PasswordFile, server.Password}, {SpectatorFile, server.Spectators},
	} {
		path := filepath.Join(target, filepath.FromSlash(secret.file))
		if secret.value == "" {
			os.Remove(path)
			continue
		}
		if err := writePrivate(path, []byte(secret.value+"\n")); err != nil {
			return err
		}
	}
	return nil
}

// bind is where the server listens: this machine only, or every
// interface.
func (s Server) bind() string {
	if s.Local {
		return "127.0.0.1"
	}
	return "0.0.0.0"
}

// slots is the player limit, the default when none was chosen.
func (s Server) slots() int {
	if s.Slots == 0 {
		return DefaultSlots
	}
	return s.Slots
}

// LoadChoices reads what SaveChoices recorded; nil when nothing was.
func LoadChoices(target string) (*Options, error) {
	payload, err := os.ReadFile(filepath.Join(target, filepath.FromSlash(choicesFile)))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var options Options
	if err := json.Unmarshal(payload, &options); err != nil {
		return nil, problem("", "err.choices", err.Error())
	}
	options.Target = target
	return &options, nil
}

func writePrivate(path string, payload []byte) error {
	if info, err := os.Lstat(path); err == nil && !info.Mode().IsRegular() {
		return fmt.Errorf("caminho privado inseguro: %s", path)
	}
	temporary := path + ".tmp"
	defer os.Remove(temporary)
	if err := os.WriteFile(temporary, payload, 0o600); err != nil {
		return err
	}
	if err := privfs.ProtectFile(temporary); err != nil {
		return err
	}
	return os.Rename(temporary, path)
}

// ClientConfig is the ezQuake script the client choices become. stamp
// marks this application of them: x86qw-user.cfg runs the script while
// the client has not seen the stamp, so it applies once, and changes made
// in the game afterwards are kept.
func ClientConfig(o Options, stamp int64) string {
	var b strings.Builder
	line := func(name, value string) { fmt.Fprintf(&b, "%-24s %s\n", name, cfgQuote(value)) }
	b.WriteString("// x86QW setup: the installer's choices, applied once each time they change.\n")
	b.WriteString("// Written by x86qw setup; changes made in the game keep working.\n\n")
	line("name", o.Nick)
	line("cl_fakename", FakeName(o.Nick))
	b.WriteString("\n")
	for _, bind := range []struct{ key, action string }{
		{o.Keys.Forward, "+forward"}, {o.Keys.Back, "+back"}, {o.Keys.Left, "+moveleft"},
		{o.Keys.Right, "+moveright"}, {o.Keys.Jump, "+jump"},
	} {
		if bind.key != "" && bind.key != "—" {
			fmt.Fprintf(&b, "bind %-19s %s\n", bind.key, cfgQuote(bind.action))
		}
	}
	b.WriteString("\n")
	line("sensitivity", strconv.FormatFloat(o.Mouse.Sensitivity, 'f', -1, 64))
	pitch := "0.022"
	if o.Mouse.Invert {
		pitch = "-0.022"
	}
	line("m_pitch", pitch)
	b.WriteString("\n")
	if o.Video.Width > 0 && o.Video.Height > 0 {
		line("vid_usedesktopres", "0")
		line("vid_width", strconv.Itoa(o.Video.Width))
		line("vid_height", strconv.Itoa(o.Video.Height))
	} else {
		line("vid_usedesktopres", "1")
	}
	fullscreen, borderless := "1", "0"
	switch o.Video.Display {
	case "borderless":
		fullscreen, borderless = "0", "1"
	case "windowed":
		fullscreen = "0"
	}
	line("vid_fullscreen", fullscreen)
	line("vid_win_borderless", borderless)
	line("default_fov", strconv.Itoa(o.Video.FOV))
	line("fov", strconv.Itoa(o.Video.FOV))
	line("volume", strconv.FormatFloat(float64(o.Volume)/100, 'f', -1, 64))
	b.WriteString("\n")
	// ezQuake saves user-created variables in config.cfg, so the stamp
	// outlives the session and the script does not run again.
	fmt.Fprintf(&b, "set _x86qw_setup %s\n", cfgQuote(strconv.FormatInt(stamp, 10)))
	// The renderer started before this ran; restart it so the video
	// choices hold from the first match.
	b.WriteString("vid_restart\n")
	return b.String()
}

// cfgQuote quotes a value for the ezQuake console, which reads what is
// between quotes as it is: no escapes, so what would end the quote or the
// command is dropped (Validate already refuses it).
func cfgQuote(value string) string {
	return `"` + strings.Map(func(r rune) rune {
		if !NameRune(r) {
			return -1
		}
		return r
	}, value) + `"`
}

var userBlock = regexp.MustCompile(`(?s)// x86qw setup: begin\n.*?// x86qw setup: end\n`)

// WriteClientConfig places the client choices in target: the setup script,
// the line in x86qw-user.cfg that runs it once, and the ruleset `play` uses.
func WriteClientConfig(target string, o Options, stamp int64) error {
	if err := install.SavePersonalFile(target, SetupConfig, []byte(ClientConfig(o, stamp))); err != nil {
		return err
	}
	user, err := os.ReadFile(filepath.Join(target, filepath.FromSlash(userConfig)))
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	// Unquoted, like nQuake's own guard: QuakeWorld expands $ only outside
	// quotes, and leaves an unknown variable as written, so the first run
	// compares "$_x86qw_setup" and executes the script.
	block := fmt.Sprintf("// x86qw setup: begin\nif $_x86qw_setup != %d \"exec x86qw-setup.cfg\"\n// x86qw setup: end\n", stamp)
	text := string(user)
	if userBlock.MatchString(text) {
		text = userBlock.ReplaceAllLiteralString(text, block)
	} else {
		if text != "" && !strings.HasSuffix(text, "\n") {
			text += "\n"
		}
		text += "\n" + block
	}
	if err := install.SavePersonalFile(target, userConfig, []byte(text)); err != nil {
		return err
	}
	if play.Rulesets[o.Experience] == "" {
		return problem("experience", "err.ruleset", o.Experience)
	}
	ruleset, _ := json.MarshalIndent(map[string]any{"format": 1, "project": "x86qw", "ruleset": o.Experience}, "", "  ")
	return install.SavePersonalFile(target, ".x86qw/launcher/client-ruleset.json", append(ruleset, '\n'))
}

// PresetName names the host preset of server instance index (from 0).
func PresetName(index int) string { return fmt.Sprintf("x86qw-%d", index+1) }

// HostOptions is how `x86qw host` runs instance index of the server: open
// to the network, the first instance carrying QTV and QWFWD.
func HostOptions(o Options, index int) *services.HostOptions {
	instance := o.Server.Instances[index]
	host := &services.HostOptions{
		Game: "ktx", Selection: "ktx", Mode: instance.Mode, Map: instance.Map, ModeGiven: true,
		Bind: o.Server.bind(), Hostname: o.Server.Name, Port: o.Server.BasePort + index, Maxclients: o.Server.slots(), NoMVD: o.Server.NoRecord,
		// Unused QTV and proxy keep the host command's own defaults.
		QtvBind: "127.0.0.1", QtvPort: DefaultQTVPort, ProxyBind: "127.0.0.1", ProxyPort: DefaultQWFWDPort,
	}
	if index == 0 && o.Server.QTVPort != 0 {
		host.WithQTV, host.QtvBind, host.QtvPort = true, o.Server.bind(), o.Server.QTVPort
	}
	if index == 0 && o.Server.QWFWDPort != 0 {
		host.WithProxy, host.ProxyBind, host.ProxyPort = true, o.Server.bind(), o.Server.QWFWDPort
	}
	return host
}

// SaveServerPresets stores one host preset per instance, so each starts
// with `x86qw host --preset x86qw-N`.
func SaveServerPresets(target string, o Options) error {
	for index := range o.Server.Instances {
		if _, err := services.SavePreset(target, PresetName(index), HostOptions(o, index)); err != nil {
			return err
		}
	}
	return nil
}

// HostCommand is the command line that starts every instance of the
// server in one background session, with the rcon password the setup
// stored: the first from its preset, the others as --instance.
func HostCommand(target string, o Options) []string {
	args := []string{"host", "--target", target, "--preset", PresetName(0), "--background",
		"--rcon-password-file", filepath.Join(target, filepath.FromSlash(RconFile))}
	if o.Server.Instances[0].Bots {
		args = append(args, "--fill-bots")
	}
	if o.Server.Password != "" {
		args = append(args, "--password-file", filepath.Join(target, filepath.FromSlash(PasswordFile)))
	}
	if o.Server.Spectators != "" {
		args = append(args, "--spectator-password-file", filepath.Join(target, filepath.FromSlash(SpectatorFile)))
	}
	for index, instance := range o.Server.Instances[1:] {
		args = append(args, "--instance", services.Instance{Mode: instance.Mode, Map: instance.Map, Port: o.Server.BasePort + index + 1, Bots: instance.Bots}.String())
	}
	return args
}

// ServiceCommand starts QTV ("qtv") or QWFWD ("proxy") on its own, in
// the background, where the server's choices put it.
func ServiceCommand(target string, o Options, kind string) []string {
	s := o.Server
	if kind == "qtv" {
		port := s.QTVPort
		if port == 0 {
			port = DefaultQTVPort
		}
		return []string{"qtv", "--target", target, "--bind", s.bind(), "--port", strconv.Itoa(port), "--hostname", s.Name + " QTV", "--background"}
	}
	port := s.QWFWDPort
	if port == 0 {
		port = DefaultQWFWDPort
	}
	return []string{"proxy", "--target", target, "--bind", s.bind(), "--port", strconv.Itoa(port), "--background"}
}
