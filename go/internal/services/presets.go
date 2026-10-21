package services

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"

	"github.com/x86dx2/x86qw/go/internal/atomicfile"
	"github.com/x86dx2/x86qw/go/internal/pyjson"
)

// Host presets live in qw/x86qw-host-presets.json.

const (
	hostPresetsPath = "qw/x86qw-host-presets.json"
	maxPresets      = 20
)

var presetName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$`)

var presetFields = []string{"name", "game", "mode", "map", "bind", "port", "hostname", "maxclients",
	"no_mvd", "with_qtv", "qtv_bind", "qtv_port", "with_proxy", "proxy_bind", "proxy_port"}

// Preset is one secret-free host layout.
type Preset map[string]any

// LoadPresets reads every preset without creating the file.
func LoadPresets(target string) (map[string]Preset, error) {
	path := filepath.Join(target, filepath.FromSlash(hostPresetsPath))
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return map[string]Preset{}, nil
	}
	if err != nil {
		return nil, err
	}
	if info.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("the presets file is a symlink")
	}
	if !info.Mode().IsRegular() {
		return map[string]Preset{}, nil
	}
	payload, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	value, err := pyjson.Decode(payload)
	if err != nil {
		return nil, errors.New("the presets file is not valid JSON")
	}
	document, ok := value.(map[string]any)
	if !ok || !isNumber(document["format"], 1) || document["kind"] != "host-presets" {
		return nil, errors.New("the presets file has an invalid header")
	}
	if len(document) != 3 {
		return nil, errors.New("the presets file has too many or too few fields")
	}
	raw, ok := document["presets"].([]any)
	if !ok || len(raw) > maxPresets {
		return nil, errors.New("the host presets are invalid")
	}
	presets := map[string]Preset{}
	for _, item := range raw {
		preset, err := closedPreset(item)
		if err != nil {
			return nil, err
		}
		name := preset["name"].(string)
		if _, dup := presets[name]; dup {
			return nil, fmt.Errorf("duplicate host preset: %s", name)
		}
		presets[name] = preset
	}
	return presets, nil
}

func isNumber(value any, want int64) bool {
	number, ok := value.(json.Number)
	if !ok {
		return false
	}
	parsed, err := number.Int64()
	return err == nil && parsed == want && number.String() == fmt.Sprint(want)
}

func intField(value any) (int, bool) {
	number, ok := value.(json.Number)
	if !ok {
		return 0, false
	}
	parsed, err := number.Int64()
	if err != nil || number.String() != fmt.Sprint(parsed) {
		return 0, false
	}
	return int(parsed), true
}

func closedPreset(value any) (Preset, error) {
	raw, ok := value.(map[string]any)
	if !ok || len(raw) != len(presetFields) {
		return nil, errors.New("a host preset is invalid")
	}
	for _, field := range presetFields {
		if _, ok := raw[field]; !ok {
			return nil, errors.New("a host preset is invalid")
		}
	}
	name, ok := raw["name"].(string)
	if !ok || !presetName.MatchString(name) {
		return nil, errors.New("a preset name is invalid")
	}
	preset := Preset{}
	for _, field := range []string{"port", "qtv_port", "proxy_port"} {
		port, ok := intField(raw[field])
		if !ok || port < 1024 || port > 65535 {
			return nil, fmt.Errorf("a preset's %s field is invalid", field)
		}
		preset[field] = port
	}
	maxclients, ok := intField(raw["maxclients"])
	if !ok || maxclients < 1 || maxclients > 32 {
		return nil, errors.New("a preset's slot count is invalid")
	}
	preset["maxclients"] = maxclients
	for _, flag := range []string{"no_mvd", "with_qtv", "with_proxy"} {
		value, ok := raw[flag].(bool)
		if !ok {
			return nil, fmt.Errorf("a preset's %s field is invalid", flag)
		}
		preset[flag] = value
	}
	for _, field := range []string{"name", "game", "mode", "map", "bind", "hostname", "qtv_bind", "proxy_bind"} {
		value, ok := raw[field].(string)
		if !ok {
			return nil, fmt.Errorf("a preset's %s field is invalid", field)
		}
		preset[field] = value
	}
	if preset["game"] == "" || preset["map"] == "" {
		return nil, errors.New("host preset requires game and map")
	}
	return preset, nil
}

// SavePreset persists one layout (no secrets) and returns it.
func SavePreset(target, name string, options *HostOptions) (Preset, error) {
	if options.Password != "" || options.SpectatorPassword != "" || options.RconPassword != "" || options.QtvPassword != "" ||
		options.PromptPassword || options.PromptSpectatorPassword || options.PromptRconPassword || options.PromptQtvPassword ||
		options.PasswordFile != "" || options.SpectatorPasswordFile != "" || options.RconPasswordFile != "" || options.QtvPasswordFile != "" {
		return nil, errors.New("presets do not keep passwords or where to read them from")
	}
	if !presetName.MatchString(name) {
		return nil, fmt.Errorf("invalid preset name: '%s'", name)
	}
	if options.Game == "" || options.Map == "" {
		return nil, errors.New("host preset requires game and map")
	}
	preset := Preset{
		"name": name, "game": options.Game, "mode": options.Mode, "map": options.Map,
		"bind": options.Bind, "port": options.Port, "hostname": options.Hostname, "maxclients": options.Maxclients,
		"no_mvd": options.NoMVD, "with_qtv": options.WithQTV, "qtv_bind": options.QtvBind, "qtv_port": options.QtvPort,
		"with_proxy": options.WithProxy, "proxy_bind": options.ProxyBind, "proxy_port": options.ProxyPort,
	}
	presets, err := LoadPresets(target)
	if err != nil {
		return nil, err
	}
	presets[name] = preset
	if len(presets) > maxPresets {
		return nil, errors.New("the preset list is full")
	}
	names := make([]string, 0, len(presets))
	for key := range presets {
		names = append(names, key)
	}
	sort.Strings(names)
	list := make([]any, 0, len(names))
	for _, key := range names {
		list = append(list, map[string]any(presets[key]))
	}
	payload, err := pyjson.Dumps(map[string]any{"format": 1, "kind": "host-presets", "presets": list}, -1)
	if err != nil {
		return nil, err
	}
	payload += "\n"
	destination := filepath.Join(target, filepath.FromSlash(hostPresetsPath))
	if info, err := os.Lstat(destination); err == nil && info.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("the presets file is a symlink")
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return nil, err
	}
	return preset, atomicfile.Write(destination, []byte(payload), 0o644)
}

// ApplyPreset copies a stored layout onto host options.
func ApplyPreset(options *HostOptions, preset Preset) {
	options.Selection = preset["game"].(string)
	options.Game = preset["game"].(string)
	options.Mode = preset["mode"].(string)
	options.Map = preset["map"].(string)
	options.Bind = preset["bind"].(string)
	options.Port = preset["port"].(int)
	options.Hostname = preset["hostname"].(string)
	options.Maxclients = preset["maxclients"].(int)
	options.NoMVD = preset["no_mvd"].(bool)
	options.WithQTV = preset["with_qtv"].(bool)
	options.QtvBind = preset["qtv_bind"].(string)
	options.QtvPort = preset["qtv_port"].(int)
	options.WithProxy = preset["with_proxy"].(bool)
	options.ProxyBind = preset["proxy_bind"].(string)
	options.ProxyPort = preset["proxy_port"].(int)
}
