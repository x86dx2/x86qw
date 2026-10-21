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
	recipe := flags.String("recipe", "", "receita JSON copiada do instalador: instala sem perguntar")
	intent := flags.String("intent", "", "play, host ou both: instala sem perguntar")
	nick := flags.String("nick", "", "seu nick")
	content := flags.String("content", "", "essential, recommended, complete ou uma lista de componentes")
	keys := flags.String("keys", "", "wasd, esdf, arrows ou cinco teclas: frente,trás,esquerda,direita,pulo")
	sensitivity := flags.Float64("sensitivity", 0, "sensibilidade do mouse")
	invert := flags.Bool("invert-mouse", false, "inverte o eixo vertical")
	experience := flags.String("experience", "", "ruleset do cliente: "+strings.Join(RulesetKeys, ", "))
	video := flags.String("video", "", "RESOLUÇÃO,MODO,fovN, por exemplo auto,fullscreen,fov110")
	volume := flags.Int("volume", -1, "volume de 0 a 100")
	serverName := flags.String("server-name", "", "nome do servidor")
	hostname := flags.String("hostname", "", "nome DNS público do servidor")
	servers := flags.String("servers", "", "instâncias MODO:MAPA[+bots] separadas por vírgula")
	basePort := flags.Int("base-port", 0, "porta UDP da primeira instância")
	qtv := flags.String("qtv", "", "porta TCP do QTV ou off")
	qwfwd := flags.String("qwfwd", "", "porta UDP do QWFWD ou off")
	integrate := flags.String("integrate", "", "shortcuts,path,qw-links,demos ou none")
	local := flags.Bool("local", false, "o servidor só aceita jogadores desta máquina")
	slots := flags.Int("slots", 0, "vagas por instância, de 2 a 32")
	noRecord := flags.Bool("no-record", false, "não grava demos MVD")
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
				return o, fmt.Errorf("--keys: use um layout ou cinco teclas, não %q", *keys)
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
				return o, fmt.Errorf("--volume: de 0 a 100, não %d", *volume)
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
					return o, fmt.Errorf("--servers: %q não é MODO:MAPA", item)
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
				return o, fmt.Errorf("--%s: uma porta ou off, não %q", port.name, port.value)
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
					return o, fmt.Errorf("--integrate: %q desconhecido", item)
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
		return Options{}, fmt.Errorf("receita JSON inválida: %w", err)
	}
	if file.Format != 1 {
		return Options{}, fmt.Errorf("receita JSON de formato %d desconhecido", file.Format)
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
		return fmt.Errorf("--video: use RESOLUÇÃO,MODO,fovN, não %q", value)
	}
	video.Width, video.Height = 0, 0
	if parts[0] != "auto" {
		width, height, ok := strings.Cut(parts[0], "x")
		w, errW := strconv.Atoi(width)
		h, errH := strconv.Atoi(height)
		if !ok || errW != nil || errH != nil {
			return fmt.Errorf("--video: resolução %q inválida", parts[0])
		}
		video.Width, video.Height = w, h
	}
	switch parts[1] {
	case "fullscreen", "borderless", "windowed":
		video.Display = parts[1]
	default:
		return fmt.Errorf("--video: modo %q inválido", parts[1])
	}
	fov, err := strconv.Atoi(strings.TrimPrefix(parts[2], "fov"))
	if err != nil {
		return fmt.Errorf("--video: %q inválido", parts[2])
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
