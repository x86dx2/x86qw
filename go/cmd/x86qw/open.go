package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/output"
	"github.com/x86dx2/x86qw/go/internal/play"
)

// A qw:// address reaches the ezQuake console, so only a host, a port and
// one of the paths ezQuake's qwurl knows get through.
var qwURL = regexp.MustCompile(`^qw://[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?(/(join|observe|spectate|qtvplay)?)?$`)

var demoExtensions = []string{".mvd", ".qwd"}

// openDemoAge is how long a demo copied for viewing is kept.
const openDemoAge = 7 * 24 * time.Hour

// openArguments turns a link or a demo into what ezQuake runs after the
// ruleset: +qwurl for a server, +playdemo for a demo copied into
// qw/demos under a name made from its content, so nothing the file was
// called reaches the console.
func openArguments(target, item string) ([]string, error) {
	if strings.HasPrefix(strings.ToLower(item), "qw://") {
		address := "qw://" + item[len("qw://"):]
		if !qwURL.MatchString(address) {
			return nil, fmt.Errorf("endereço QuakeWorld inválido: %q", item)
		}
		if _, port, found := strings.Cut(strings.SplitN(address[len("qw://"):], "/", 2)[0], ":"); found && len(port) > 0 {
			var number int
			fmt.Sscanf(port, "%d", &number)
			if number < 1 || number > 65535 {
				return nil, fmt.Errorf("porta inválida: %q", item)
			}
		}
		return []string{"+qwurl", address}, nil
	}
	path := item
	if strings.HasPrefix(strings.ToLower(item), "file://") {
		parsed, err := url.Parse(item)
		if err != nil || (parsed.Host != "" && parsed.Host != "localhost") {
			return nil, fmt.Errorf("arquivo inválido: %q", item)
		}
		path = filepath.FromSlash(parsed.Path)
	}
	extension := strings.ToLower(filepath.Ext(path))
	known := false
	for _, candidate := range demoExtensions {
		known = known || extension == candidate
	}
	if !known {
		return nil, fmt.Errorf("x86qw open abre endereços qw:// e demos .mvd ou .qwd, não %q", item)
	}
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() {
		return nil, fmt.Errorf("demo não encontrada: %s", path)
	}
	if info.Size() > 512<<20 {
		return nil, fmt.Errorf("demo grande demais: %s", path)
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

func runOpen(args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("open", flag.ContinueOnError)
	flags.SetOutput(stderr)
	target := flags.String("target", "", "diretório da instalação")
	dryRun := flags.Bool("dry-run", false, "mostra o comando do ezQuake sem abrir")
	if err := flags.Parse(args); err != nil {
		return output.ExitUsage
	}
	if flags.NArg() != 1 {
		fmt.Fprintln(stderr, "uso: x86qw open [--target DESTINO] qw://servidor:porta | demo.mvd")
		return output.ExitUsage
	}
	if *target == "" {
		*target = defaultTarget()
	}
	root, _ := filepath.Abs(*target)
	extra, err := openArguments(root, flags.Arg(0))
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitUsage
	}
	ruleset, err := readRuleset(root)
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	if ruleset == "" {
		ruleset = "x86qw"
	}
	arguments := append(play.RulesetArguments(ruleset), extra...)
	if *dryRun {
		fmt.Fprintln(stdout, strings.Join(append([]string{"-nohome", "-basedir", root}, arguments...), " "))
		return output.ExitSuccess
	}
	runtimes, err := install.HostRuntimes(root)
	if err == nil && len(runtimes) == 0 {
		err = errors.New("nenhum ezQuake instalado; execute x86qw setup")
	}
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	return launchPlay(root, runtimes[0].Path, runtimes[0].BinarySHA, &play.Launch{Arguments: arguments}, stdout, stderr)
}

func init() {
	register("open", 72, "open ENDEREÇO|DEMO    abre um servidor qw:// ou uma demo .mvd/.qwd no ezQuake", noName(runOpen))
}
