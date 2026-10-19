// Package doctor ports x86qw_runtime/doctor.py: seven read-only checks with
// a closed report shape.
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
)

const (
	Audience          = "owner-only"
	OwnerOnlyFirstRun = "Modo owner-only: um usuário, Apple M3. Instalação limpa permitida. Windows e Linux continuam preview."
	lowDisk           = 512 << 20
	criticalDisk      = 64 << 20
	trustWarning      = 6 * time.Hour
)

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

// Diagnose runs every check against target. trustTimestamps are candidate
// cached timestamp.json paths; the first readable one is used.
func Diagnose(target string, commands []string, trustTimestamps []string, now time.Time) Report {
	report := Report{Audience: Audience, Target: target, Healthy: true}
	add := func(id, status, summary string) {
		report.Checks = append(report.Checks, Check{ID: id, Status: status, Summary: summary})
		if status == "fail" {
			report.Healthy = false
		}
	}
	status, summary := installation(target)
	add("installation", status, summary)
	if len(commands) == 0 {
		add("catalog", "fail", "catálogo sem comandos públicos")
	} else {
		add("catalog", "ok", "catálogo de capacidades legível")
	}
	status, summary = trust(trustTimestamps, now)
	add("trust", status, summary)
	if runtime.GOOS == "darwin" && runtime.GOARCH == "arm64" {
		add("runtime", "ok", "runtime macOS arm64")
	} else {
		add("runtime", "warn", "plataforma sem evidência nativa M3")
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
	lines := []string{"x86QW doctor — " + report.Audience, OwnerOnlyFirstRun, "Destino: " + report.Target, ""}
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
	absolute, err := filepath.Abs(destination)
	if err != nil {
		return "", err
	}
	root, _ := filepath.Abs(target)
	if rel, err := filepath.Rel(root, absolute); err == nil && !strings.HasPrefix(rel, "..") {
		if filepath.Dir(destination) != "." {
			return "", errors.New("o bundle não pode ser gravado dentro da instalação")
		}
		home, _ := os.UserHomeDir()
		absolute = filepath.Join(home, filepath.Base(destination))
	}
	if _, err := os.Lstat(absolute); err == nil {
		return "", errors.New("bundle destination already exists")
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
		{"NOTICE.txt", OwnerOnlyFirstRun + "\nSem upload automático. Revise o conteúdo antes de partilhar.\n"},
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
