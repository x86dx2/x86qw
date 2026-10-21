// Package doctor diagnoses an installation without changing it: read-only
// checks with a closed report shape, and an optional bundle to share.
package doctor

import (
	"archive/zip"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/fspath"
)

const (
	Audience     = "owner-only"
	lowDisk      = 512 << 20
	criticalDisk = 64 << 20
	trustWarning = 6 * time.Hour
)

// BundleDestination is where a bundle named raw goes: never inside the
// installation. A bare file name that would land there goes to the home
// folder instead; any other path inside is refused.
func BundleDestination(raw, target string) (string, error) {
	requested := raw
	if requested == "~" || strings.HasPrefix(requested, "~/") || strings.HasPrefix(requested, `~\`) {
		home, _ := os.UserHomeDir()
		requested = filepath.Join(home, requested[1:])
	}
	destination, err := filepath.Abs(requested)
	if err != nil {
		return "", err
	}
	destination = filepath.Join(fspath.Resolve(filepath.Dir(destination)), filepath.Base(destination))
	root := fspath.Resolve(target)
	if !fspath.Inside(destination, root) {
		return destination, nil
	}
	if filepath.Dir(raw) != "." {
		return "", errors.New("o bundle não pode ser gravado dentro da instalação")
	}
	home, _ := os.UserHomeDir()
	fallback := filepath.Join(fspath.Resolve(home), filepath.Base(raw))
	if !fspath.Inside(fallback, root) {
		return fallback, nil
	}
	return "", errors.New("o bundle não pode ser gravado dentro da instalação")
}

// Check is one diagnostic.
type Check struct {
	ID      string `json:"id"`
	Status  string `json:"status"`
	Summary string `json:"summary"`
}

// Report is the closed doctor data.
type Report struct {
	Audience string  `json:"audience"`
	Checks   []Check `json:"checks"`
	Healthy  bool    `json:"healthy"`
	Target   string  `json:"target"`
}

// releasedPlatforms are the GOOS/GOARCH pairs the CLI is published for.
var releasedPlatforms = map[string]bool{
	"darwin/arm64": true, "darwin/amd64": true, "linux/amd64": true, "linux/arm64": true, "windows/amd64": true,
}

// Diagnose runs every check against target. trustTimestamps are candidate
// cached timestamp.json paths; the first readable one is used.
func Diagnose(target string, trustTimestamps []string, now time.Time) Report {
	report := Report{Audience: Audience, Target: target, Healthy: true}
	add := func(id, status, summary string) {
		report.Checks = append(report.Checks, Check{ID: id, Status: status, Summary: summary})
		if status == "fail" {
			report.Healthy = false
		}
	}
	status, summary := installation(target)
	add("installation", status, summary)
	status, summary = trust(trustTimestamps, now)
	add("trust", status, summary)
	platform := runtime.GOOS + "/" + runtime.GOARCH
	if releasedPlatforms[platform] {
		add("runtime", "ok", "plataforma publicada: "+platform)
	} else {
		add("runtime", "warn", "plataforma sem build publicado: "+platform)
	}
	add("network", "skip", "rede não consultada")
	status, summary = disk(target)
	add("disk", status, summary)
	status, summary = permissions(target)
	add("permissions", status, summary)
	return report
}

func installation(target string) (string, string) {
	if info, err := os.Lstat(target); err == nil && info.Mode()&os.ModeSymlink != 0 {
		return "fail", "destino é um symlink"
	}
	if _, err := os.Stat(target); err != nil {
		return "fail", "instalação ausente"
	}
	state := filepath.Join(target, ".x86qw", "state.json")
	if info, err := os.Lstat(state); err != nil || !info.Mode().IsRegular() {
		return "warn", "instalação sem state.json"
	}
	return "ok", "instalação presente"
}

func trust(paths []string, now time.Time) (string, string) {
	for _, path := range paths {
		info, err := os.Lstat(path)
		if err != nil || !info.Mode().IsRegular() {
			continue
		}
		payload, err := os.ReadFile(path)
		if err != nil || len(payload) > 64*1024 {
			return "fail", "metadata TUF local ilegível"
		}
		var document struct {
			Signed struct {
				Expires string `json:"expires"`
				Version *int   `json:"version"`
			} `json:"signed"`
		}
		if err := json.Unmarshal(payload, &document); err != nil {
			return "fail", "metadata TUF local ilegível"
		}
		if document.Signed.Expires == "" {
			return "fail", "timestamp TUF sem validade"
		}
		expiry, err := time.Parse(time.RFC3339, strings.Replace(document.Signed.Expires, "Z", "+00:00", 1))
		if err != nil {
			return "fail", "timestamp TUF com validade inválida"
		}
		if !now.Before(expiry) {
			if document.Signed.Version != nil {
				return "warn", fmt.Sprintf("cache TUF local v%d expirado", *document.Signed.Version)
			}
			return "warn", "cache TUF local expirado"
		}
		if expiry.Sub(now) <= trustWarning {
			return "warn", "timestamp TUF perto de expirar"
		}
		return "ok", "timestamp TUF válido"
	}
	return "skip", "sem metadata TUF local"
}

func disk(target string) (string, string) {
	probe := target
	if _, err := os.Stat(probe); err != nil {
		probe = filepath.Dir(target)
	}
	free, err := freeBytes(probe)
	if err != nil {
		return "skip", "espaço em disco indisponível"
	}
	switch {
	case free < criticalDisk:
		return "fail", "espaço livre insuficiente"
	case free < lowDisk:
		return "warn", "espaço livre baixo"
	}
	return "ok", "espaço em disco suficiente"
}

func permissions(target string) (string, string) {
	exists := true
	probe := target
	if _, err := os.Stat(probe); err != nil {
		exists = false
		probe = filepath.Dir(target)
	}
	if !accessible(probe, 4) {
		return "fail", "sem permissão de leitura"
	}
	if exists && !accessible(target, 2) {
		return "warn", "sem permissão de escrita"
	}
	return "ok", "permissões de leitura ok"
}

// Render is the human report.
func Render(report Report) string {
	lines := []string{"x86QW doctor", "Destino: " + report.Target, ""}
	for _, check := range report.Checks {
		lines = append(lines, fmt.Sprintf("[%s] %s: %s", check.Status, check.ID, check.Summary))
	}
	lines = append(lines, "")
	if report.Healthy {
		lines = append(lines, "Nenhum problema encontrado.")
	} else {
		lines = append(lines, "Problemas encontrados.")
	}
	return strings.Join(lines, "\n") + "\n"
}

// WriteBundle writes a reviewable zip (NOTICE.txt + doctor.json) outside the
// installation, with the home directory replaced by "~".
func WriteBundle(envelope []byte, destination, target string) (string, error) {
	absolute, err := BundleDestination(destination, target)
	if err != nil {
		return "", err
	}
	if _, err := os.Lstat(absolute); err == nil {
		return "", fmt.Errorf("já existe um arquivo em %s", absolute)
	}
	if home, err := os.UserHomeDir(); err == nil && home != "" {
		envelope = []byte(strings.ReplaceAll(string(envelope), home, "~"))
	}
	staging := absolute + ".tmp"
	file, err := os.Create(staging)
	if err != nil {
		return "", err
	}
	writer := zip.NewWriter(file)
	for _, entry := range []struct{ name, body string }{
		{"NOTICE.txt", "Diagnóstico do x86QW. Nada é enviado automaticamente; revise o conteúdo antes de compartilhar.\n"},
		{"doctor.json", string(envelope)},
	} {
		out, err := writer.Create(entry.name)
		if err == nil {
			_, err = out.Write([]byte(entry.body))
		}
		if err != nil {
			writer.Close()
			file.Close()
			os.Remove(staging)
			return "", err
		}
	}
	if err := errors.Join(writer.Close(), file.Close()); err != nil {
		os.Remove(staging)
		return "", err
	}
	return absolute, os.Rename(staging, absolute)
}

// FreeBytes is the space left for the user on the volume holding path.
func FreeBytes(path string) (int64, error) { return freeBytes(path) }
