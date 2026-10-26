package maint

import (
	"errors"
	"html"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/doctor"
	"github.com/x86dx2/x86qw/go/internal/fspath"
)

// The local UI is a read-only HTML snapshot of the installation.

func htmlEscape(value string) string {
	// Quotes are written as &quot; and &#x27;, the entities the page always used.
	escaped := strings.ReplaceAll(html.EscapeString(value), "&#39;", "&#x27;")
	return strings.ReplaceAll(escaped, "&#34;", "&quot;")
}

func uiEntries(entries []LibraryEntry) string {
	if len(entries) == 0 {
		return "<li>(none)</li>"
	}
	var builder strings.Builder
	for _, item := range entries {
		builder.WriteString("<li><code>" + htmlEscape(item.Address) + "</code> " + htmlEscape(item.Title) + " (" + htmlEscape(item.Origin) + ")</li>")
	}
	return builder.String()
}

// RenderLocalUI renders the snapshot (doctor without catalog or trust).
func RenderLocalUI(target string) (string, error) {
	report := doctor.Diagnose(target, nil, time.Now().UTC())
	library, err := LoadLibrary(target)
	if err != nil {
		return "", err
	}
	var checks strings.Builder
	for _, item := range report.Checks {
		checks.WriteString("<li><code>" + htmlEscape(item.ID) + "</code> " + htmlEscape(item.Status) + ": " + htmlEscape(item.Summary) + "</li>")
	}
	healthy := "with failures"
	if report.Healthy {
		healthy = "healthy"
	}
	return "<!DOCTYPE html>\n" +
		`<html lang="en"><head><meta charset="utf-8">` +
		"<title>x86QW</title></head><body>" +
		"<p>x86QW " + htmlEscape(report.Audience) + " — " + htmlEscape(healthy) + "</p>" +
		"<p>Target: <code>" + htmlEscape(report.Target) + "</code></p>" +
		"<h1>doctor</h1><ul>" + checks.String() + "</ul>" +
		"<h1>favorites</h1><ul>" + uiEntries(library.Favorites) + "</ul>" +
		"<h1>recents</h1><ul>" + uiEntries(library.Recents) + "</ul>" +
		"</body></html>\n", nil
}

// WriteLocalUI writes the snapshot outside the installation.
func WriteLocalUI(target, destination string) (string, error) {
	target = fspath.Resolve(target)
	output, err := filepath.Abs(destination)
	if err != nil {
		return "", err
	}
	output = fspath.Resolve(output)
	if output == target || fspath.Inside(output, target) {
		return "", errors.New("the panel cannot be written inside the installation")
	}
	if _, err := os.Lstat(output); err == nil {
		return "", errors.New("a file already exists where the panel would go")
	}
	page, err := RenderLocalUI(target)
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(filepath.Dir(output), 0o755); err != nil {
		return "", err
	}
	return output, os.WriteFile(output, []byte(page), 0o644)
}
