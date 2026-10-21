package setup

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
)

// RecipeFlags are the flags Recipe writes, and --recipe for the file
// RecipeJSON writes; --intent or --recipe makes `x86qw setup` install
// without asking.
var RecipeFlags = []string{"recipe", "intent", "nick", "content", "keys", "sensitivity", "invert-mouse", "experience", "video", "volume",
	"server-name", "hostname", "servers", "base-port", "qtv", "qwfwd", "local", "slots", "no-record", "integrate"}

// AddRecipeFlags declares the recipe flags on flags and returns how to
// read them into options once parsed. target is the installation folder.
// A --recipe file starts the options; the flags given change them.
func AddRecipeFlags(flags *flag.FlagSet) func(target string) (Options, error) {
	recipe := flags.String("recipe", "", "recipe JSON copied from the installer: installs without asking")
	intent := flags.String("intent", "", "play, host or both: installs without asking")
	nick := flags.String("nick", "", "your nick")
	content := flags.String("content", "", "essential, recommended, complete or a list of components")
	keys := flags.String("keys", "", "wasd, esdf, arrows or five keys: forward,back,left,right,jump")
	sensitivity := flags.Float64("sensitivity", 0, "mouse sensitivity")
	invert := flags.Bool("invert-mouse", false, "inverts the vertical axis")
	experience := flags.String("experience", "", "client ruleset: "+strings.Join(RulesetKeys, ", "))
	video := flags.String("video", "", "RESOLUTION,MODE,fovN, for example auto,fullscreen,fov110")
	volume := flags.Int("volume", -1, "volume from 0 to 100")
	serverName := flags.String("server-name", "", "server name")
	hostname := flags.String("hostname", "", "the server's public DNS name")
	servers := flags.String("servers", "", "comma-separated MODE:MAP[+bots] instances")
	basePort := flags.Int("base-port", 0, "UDP port of the first instance")
	qtv := flags.String("qtv", "", "QTV TCP port or off")
	qwfwd := flags.String("qwfwd", "", "QWFWD UDP port or off")
	integrate := flags.String("integrate", "", "shortcuts,path,qw-links,demos or none")
	local := flags.Bool("local", false, "the server only takes players from this machine")
	slots := flags.Int("slots", 0, "slots per instance, 2 to 32")
	noRecord := flags.Bool("no-record", false, "does not record MVD demos")
	return func(target string) (Options, error) {
		given := map[string]bool{}
		flags.Visit(func(f *flag.Flag) { given[f.Name] = true })
		o := Defaults(Intent(*intent), target)
		if given["recipe"] {
			loaded, err := loadRecipe(*recipe, target)
			if err != nil {
				return o, err
			}
			o = loaded
			if given["intent"] {
				o.Intent = Intent(*intent)
			}
		}
		if given["nick"] {
			o.Nick = *nick
		}
		if given["content"] {
			switch *content {
			case "essential", "recommended", "complete":
				o.Content = *content
			default:
				o.Content, o.Components = "custom", splitList(*content)
			}
		}
		if given["keys"] {
			if preset, ok := KeyPresets[*keys]; ok {
				o.Keys = preset
			} else if parts := splitList(*keys); len(parts) == 5 {
				o.Keys = Keys{Preset: "custom", Forward: parts[0], Back: parts[1], Left: parts[2], Right: parts[3], Jump: parts[4]}
			} else {
				return o, fmt.Errorf("--keys: use a layout or five keys, not %q", *keys)
			}
		}
		if given["sensitivity"] {
			o.Mouse.Sensitivity = *sensitivity
		}
		if given["invert-mouse"] {
			o.Mouse.Invert = *invert
		}
		if given["experience"] {
			o.Experience = *experience
		}
		if given["video"] {
			if err := parseVideo(*video, &o.Video); err != nil {
				return o, err
			}
		}
		if given["volume"] {
			if *volume < 0 || *volume > 100 {
				return o, fmt.Errorf("--volume: 0 to 100, not %d", *volume)
			}
			o.Volume = *volume
		}
		if given["server-name"] {
			o.Server.Name = *serverName
		}
		if given["hostname"] {
			o.Server.Hostname = strings.ToLower(*hostname)
		}
		if given["servers"] {
			o.Server.Instances = nil
			for _, item := range splitList(*servers) {
				bots := strings.HasSuffix(item, "+bots")
				mode, mapName, ok := strings.Cut(strings.TrimSuffix(item, "+bots"), ":")
				if !ok || mode == "" || mapName == "" {
					return o, fmt.Errorf("--servers: %q is not MODE:MAP", item)
				}
				o.Server.Instances = append(o.Server.Instances, Instance{Mode: mode, Map: mapName, Bots: bots})
			}
		}
		if given["base-port"] {
			o.Server.BasePort = *basePort
		}
		if given["local"] {
			o.Server.Local = *local
		}
		if given["no-record"] {
			o.Server.NoRecord = *noRecord
		}
		if given["slots"] {
			o.Server.Slots = *slots
		}
		for _, port := range []struct {
			name, value string
			into        *int
		}{{"qtv", *qtv, &o.Server.QTVPort}, {"qwfwd", *qwfwd, &o.Server.QWFWDPort}} {
			if !given[port.name] {
				continue
			}
			if port.value == "off" {
				*port.into = 0
				continue
			}
			number, err := strconv.Atoi(port.value)
			if err != nil {
				return o, fmt.Errorf("--%s: a port or off, not %q", port.name, port.value)
			}
			*port.into = number
		}
		if given["integrate"] {
			o.Integration = Integration{}
			for _, item := range splitList(*integrate) {
				switch item {
				case "shortcuts":
					o.Integration.Shortcuts = true
				case "path":
					o.Integration.Path = true
				case "qw-links":
					o.Integration.QWLinks = true
				case "demos":
					o.Integration.Demos = true
				case "none":
				default:
					return o, fmt.Errorf("--integrate: unknown %q", item)
				}
			}
		}
		return o, o.Validate()
	}
}

// ReadRecipe reads what RecipeJSON wrote. The folder of the machine that
// wrote it does not come along: Target is empty for the caller to set. A
// server gets an rcon password of its own; the recipe carries none. The
// options are validated once Target is set (Options.Validate).
func ReadRecipe(r io.Reader) (Options, error) {
	var file recipeFile
	decoder := json.NewDecoder(io.LimitReader(r, 1<<20))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&file); err != nil {
		return Options{}, fmt.Errorf("invalid recipe JSON: %w", err)
	}
	if file.Format != 1 {
		return Options{}, fmt.Errorf("recipe JSON of unknown format %d", file.Format)
	}
	o := file.Options
	o.Target, o.Upgrade = "", false
	if o.Server.Rcon == "" {
		o.Server.Rcon = Password(14)
	}
	return o, nil
}

// loadRecipe reads the recipe file at path for an installation at target.
func loadRecipe(path, target string) (Options, error) {
	file, err := os.Open(path)
	if err != nil {
		return Options{}, fmt.Errorf("--recipe: %w", err)
	}
	defer file.Close()
	o, err := ReadRecipe(file)
	if err != nil {
		return Options{}, fmt.Errorf("--recipe: %s: %w", path, err)
	}
	o.Target = target
	return o, nil
}

func parseVideo(value string, video *Video) error {
	parts := splitList(value)
	if len(parts) != 3 || !strings.HasPrefix(parts[2], "fov") {
		return fmt.Errorf("--video: use RESOLUTION,MODE,fovN, not %q", value)
	}
	video.Width, video.Height = 0, 0
	if parts[0] != "auto" {
		width, height, ok := strings.Cut(parts[0], "x")
		w, errW := strconv.Atoi(width)
		h, errH := strconv.Atoi(height)
		if !ok || errW != nil || errH != nil {
			return fmt.Errorf("--video: invalid resolution %q", parts[0])
		}
		video.Width, video.Height = w, h
	}
	switch parts[1] {
	case "fullscreen", "borderless", "windowed":
		video.Display = parts[1]
	default:
		return fmt.Errorf("--video: invalid mode %q", parts[1])
	}
	fov, err := strconv.Atoi(strings.TrimPrefix(parts[2], "fov"))
	if err != nil {
		return fmt.Errorf("--video: invalid %q", parts[2])
	}
	video.FOV = fov
	return nil
}

func splitList(value string) []string {
	var out []string
	for _, item := range strings.Split(value, ",") {
		if item = strings.TrimSpace(item); item != "" {
			out = append(out, item)
		}
	}
	return out
}
