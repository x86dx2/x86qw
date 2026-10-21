package play

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
)

// A qw:// address reaches the ezQuake console, so only a host, a port and
// one of the paths ezQuake's qwurl knows get through.
var qwURL = regexp.MustCompile(`^qw://[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?(/(join|observe|spectate|qtvplay)?)?$`)

// DemoExtensions are the demos x86qw open plays.
var DemoExtensions = []string{".mvd", ".qwd"}

// openDemoAge is how long a demo copied for viewing is kept.
const openDemoAge = 7 * 24 * time.Hour

// OpenArguments turns a link or a demo into what ezQuake runs after the
// ruleset: +qwurl for a server, +playdemo for a demo copied into qw/demos
// under a name made from its content, so nothing the file was called
// reaches the console. An item that is neither is a cliargs.UsageError.
func OpenArguments(target, item string) ([]string, error) {
	if strings.HasPrefix(strings.ToLower(item), "qw://") {
		address := "qw://" + item[len("qw://"):]
		if !qwURL.MatchString(address) {
			return nil, cliargs.Usagef("endereço QuakeWorld inválido: %q", item)
		}
		if _, port, found := strings.Cut(strings.SplitN(address[len("qw://"):], "/", 2)[0], ":"); found && len(port) > 0 {
			var number int
			fmt.Sscanf(port, "%d", &number)
			if number < 1 || number > 65535 {
				return nil, cliargs.Usagef("porta inválida: %q", item)
			}
		}
		return []string{"+qwurl", address}, nil
	}
	path := item
	if strings.HasPrefix(strings.ToLower(item), "file://") {
		parsed, err := url.Parse(item)
		if err != nil || (parsed.Host != "" && parsed.Host != "localhost") {
			return nil, cliargs.Usagef("arquivo inválido: %q", item)
		}
		path = parsed.Path
		if len(path) > 2 && path[0] == '/' && path[2] == ':' {
			path = path[1:] // file:///C:/demos/x.mvd on Windows
		}
		path = filepath.FromSlash(path)
	}
	extension := strings.ToLower(filepath.Ext(path))
	known := false
	for _, candidate := range DemoExtensions {
		known = known || extension == candidate
	}
	if !known {
		return nil, cliargs.Usagef("x86qw open abre endereços qw:// e demos .mvd ou .qwd, não %q", item)
	}
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() {
		return nil, fmt.Errorf("demo não encontrada: %s", path)
	}
	if info.Size() > 512<<20 {
		return nil, fmt.Errorf("demo grande demais (mais de 512 MiB): %s", path)
	}
	payload, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	digest := sha256.Sum256(payload)
	name := "x86qw-open-" + hex.EncodeToString(digest[:6]) + extension
	demos := filepath.Join(target, "qw", "demos")
	if err := os.MkdirAll(demos, 0o755); err != nil {
		return nil, err
	}
	pruneOpenedDemos(demos)
	if err := os.WriteFile(filepath.Join(demos, name), payload, 0o644); err != nil {
		return nil, err
	}
	return []string{"+playdemo", "demos/" + name}, nil
}

// pruneOpenedDemos removes the copies made for viewing that are old.
func pruneOpenedDemos(dir string) {
	entries, _ := os.ReadDir(dir)
	for _, entry := range entries {
		if !strings.HasPrefix(entry.Name(), "x86qw-open-") {
			continue
		}
		if info, err := entry.Info(); err == nil && time.Since(info.ModTime()) > openDemoAge {
			os.Remove(filepath.Join(dir, entry.Name()))
		}
	}
}
