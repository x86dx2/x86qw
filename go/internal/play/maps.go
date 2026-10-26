package play

import (
	"fmt"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/archive"
	"github.com/x86dx2/x86qw/go/internal/install"
)

var (
	mapName   = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`)
	assetStem = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.#-]{0,63}$`)
)

func mapFromMember(member string) string {
	parts := strings.Split(member, "/")
	if len(parts) != 2 || !strings.EqualFold(parts[0], "maps") || !strings.EqualFold(path.Ext(parts[1]), ".bsp") {
		return ""
	}
	name := strings.TrimSuffix(parts[1], path.Ext(parts[1]))
	if !mapName.MatchString(name) {
		return ""
	}
	return name
}

func regularFile(name string) bool {
	info, err := os.Lstat(name)
	return err == nil && info.Mode().IsRegular()
}

func directory(name string) bool {
	info, err := os.Lstat(name)
	return err == nil && info.IsDir()
}

// LocalMapNames lists the .bsp files and the maps inside
// .pak/.pk3 packages of id1 and the game directory, sorted case-insensitively.
func LocalMapNames(root, gamedir string) ([]string, error) {
	found := map[string]bool{}
	roots := []string{filepath.Join(root, "id1")}
	if gamedir != "id1" {
		roots = append(roots, filepath.Join(root, gamedir))
	}
	for _, base := range roots {
		maps := filepath.Join(base, "maps")
		if directory(maps) {
			entries, _ := os.ReadDir(maps)
			for _, entry := range entries {
				if entry.Type().IsRegular() {
					if name := mapFromMember("maps/" + entry.Name()); name != "" {
						found[name] = true
					}
				}
			}
		}
		if !directory(base) {
			continue
		}
		entries, _ := os.ReadDir(base)
		for _, entry := range entries {
			if !entry.Type().IsRegular() {
				continue
			}
			package_ := filepath.Join(base, entry.Name())
			switch strings.ToLower(filepath.Ext(entry.Name())) {
			case ".pk3":
				plan, err := archive.Open(package_, archive.DefaultLimits)
				if err != nil {
					return nil, err
				}
				for _, member := range plan.Members {
					if name := mapFromMember(member.Name); name != "" {
						found[name] = true
					}
				}
				plan.Close()
			case ".pak":
				names, err := install.PakNames(package_)
				if err != nil {
					return nil, err
				}
				for _, member := range names {
					if name := mapFromMember(member); name != "" {
						found[name] = true
					}
				}
			}
		}
	}
	names := make([]string, 0, len(found))
	for name := range found {
		names = append(names, name)
	}
	sort.Slice(names, func(a, b int) bool {
		left, right := strings.ToLower(names[a]), strings.ToLower(names[b])
		if left != right {
			return left < right
		}
		return names[a] < names[b]
	})
	return names, nil
}

// KtxAssets lists qw/ktx.pk3 members plus validated personal map assets,
// all case-folded (gameplay.ktx_archive_members).
func KtxAssets(root string) (map[string]bool, error) {
	plan, err := archive.Open(filepath.Join(root, "qw", "ktx.pk3"), archive.DefaultLimits)
	if err != nil {
		return nil, err
	}
	defer plan.Close()
	assets := map[string]bool{}
	for _, member := range plan.Members {
		assets[strings.ToLower(member.Name)] = true
	}
	for _, location := range []struct{ dir, virtual, suffix string }{
		{"qw/bots/maps", "bots/maps", ".bot"},
		{"qw/race/routes", "race/routes", ".route"},
		{"id1/maps/ctf", "id1/maps/ctf", ".ent"},
	} {
		dir := filepath.Join(root, filepath.FromSlash(location.dir))
		if !directory(dir) {
			continue
		}
		entries, _ := os.ReadDir(dir)
		for _, entry := range entries {
			name := entry.Name()
			suffix := filepath.Ext(name)
			if !entry.Type().IsRegular() || !strings.EqualFold(suffix, location.suffix) || !assetStem.MatchString(strings.TrimSuffix(name, suffix)) {
				continue
			}
			if validPersonalAsset(filepath.Join(dir, name), location.suffix) {
				assets[strings.ToLower(location.virtual+"/"+name)] = true
			}
		}
	}
	return assets, nil
}

var (
	botCommands   = map[string]bool{"CreateMarker": true, "SetGoal": true, "SetMarkerFlag": true, "SetMarkerPath": true, "SetMarkerPathAngleHint": true, "SetMarkerPathFlags": true, "SetMarkerViewOfs": true, "SetRocketJumpPathFields": true, "SetZone": true}
	routeCommands = map[string]bool{"race_add_route_node": true, "race_route_add_end": true, "race_route_add_start": true, "race_set_node_size": true, "race_set_route_falsestart_mode": true, "race_set_route_name": true, "race_set_route_timeout": true, "race_set_route_weapon_mode": true, "race_set_teleport_flags_by_name": true}
)

// validPersonalAsset reports whether name is a usable personal KTX asset: a
// regular text file of at most 1 MiB with suffix.
func validPersonalAsset(name, suffix string) bool {
	info, err := os.Lstat(name)
	if err != nil || !info.Mode().IsRegular() || info.Size() <= 0 || info.Size() > 1<<20 {
		return false
	}
	payload, err := os.ReadFile(name)
	if err != nil {
		return false
	}
	for _, b := range payload {
		if b == 0 || (b < 32 && b != 9 && b != 10 && b != 13) {
			return false
		}
	}
	// latin-1 decode: every byte is one rune.
	runes := make([]rune, len(payload))
	for index, b := range payload {
		runes[index] = rune(b)
	}
	var lines []string
	for _, line := range splitLines(string(runes)) {
		trimmed := strings.TrimLeft(line, " \t\v\f\r\n\u0085 ")
		if strings.TrimSpace(line) != "" && !strings.HasPrefix(trimmed, "//") {
			lines = append(lines, trimmed)
		}
	}
	if suffix == ".ent" {
		text := strings.Join(lines, "\n")
		return strings.HasPrefix(text, "{") && strings.HasSuffix(text, "}") &&
			strings.Count(text, `"classname" "item_flag_team1"`) == 1 &&
			strings.Count(text, `"classname" "item_flag_team2"`) == 1 && !strings.Contains(text, ";")
	}
	var commands []string
	for _, line := range lines {
		if strings.Contains(line, ";") {
			return false
		}
		commands = append(commands, strings.Fields(line)[0])
	}
	if len(commands) == 0 {
		return false
	}
	switch suffix {
	case ".bot":
		create := false
		for _, command := range commands {
			if !botCommands[command] {
				return false
			}
			create = create || command == "CreateMarker"
		}
		return create
	case ".route":
		node, starts, ends := false, 0, 0
		for _, command := range commands {
			if !routeCommands[command] {
				return false
			}
			node = node || command == "race_add_route_node"
			if command == "race_route_add_start" {
				starts++
			}
			if command == "race_route_add_end" {
				ends++
			}
		}
		return node && starts == ends
	}
	return false
}

// splitLines splits at the line separators that can appear after the
// control-character filter (\n, \r, \r\n, \x85).
func splitLines(text string) []string {
	text = strings.ReplaceAll(text, "\r\n", "\n")
	text = strings.ReplaceAll(text, "\r", "\n")
	text = strings.ReplaceAll(text, "\u0085", "\n")
	return strings.Split(text, "\n")
}

// CompatibleMaps are the installed maps that carry every asset requirements
// asks for.
func CompatibleMaps(root string, game Game, requirements []Requirement, assets map[string]bool, label string) ([]string, error) {
	maps, err := LocalMapNames(root, game.Gamedir)
	if err != nil {
		return nil, err
	}
	if len(requirements) == 0 {
		return maps, nil
	}
	var filtered []string
	for _, name := range maps {
		ok := true
		for _, requirement := range requirements {
			ok = ok && assets[strings.ToLower(strings.ReplaceAll(requirement.Asset, "{map}", strings.ToLower(name)))]
		}
		if ok {
			filtered = append(filtered, name)
		}
	}
	if len(filtered) == 0 {
		keys := make([]string, len(requirements))
		for index, requirement := range requirements {
			keys[index] = requirement.Asset
		}
		return nil, fmt.Errorf("No installed map has the asset %s needs: %s.", label, strings.Join(keys, ", "))
	}
	return filtered, nil
}

// ResolveMap is the compatible installed map matching requested,
// case-insensitively.
func ResolveMap(root string, game Game, requested string, requirements []Requirement, assets map[string]bool, label string) (string, error) {
	maps, err := CompatibleMaps(root, game, requirements, assets, label)
	if err != nil {
		return "", err
	}
	for _, name := range maps {
		if strings.EqualFold(name, requested) {
			return name, nil
		}
	}
	return "", fmt.Errorf("Map %s is not available or not compatible with %s.", requested, label)
}
