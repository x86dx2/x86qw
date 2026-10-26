package content

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"unicode"

	"github.com/x86dx2/x86qw/go/internal/archive"
	"golang.org/x/text/cases"
	"golang.org/x/text/language"
	"golang.org/x/text/unicode/norm"
)

// MaxArtifactBytes is the largest artifact a download accepts.
const MaxArtifactBytes = 512 * 1024 * 1024

var (
	folder = cases.Fold()
	upper  = cases.Upper(language.Und)
)

// Casefold applies Unicode full case folding.
func Casefold(value string) string { return folder.String(value) }

// posixPath is a parsed POSIX path: empty and "." components collapse.
type posixPath struct {
	absolute bool
	parts    []string
}

func purePosix(value string) posixPath {
	path := posixPath{absolute: strings.HasPrefix(value, "/")}
	for _, part := range strings.Split(value, "/") {
		if part != "" && part != "." {
			path.parts = append(path.parts, part)
		}
	}
	return path
}

func (p posixPath) name() string {
	if len(p.parts) == 0 {
		return ""
	}
	return p.parts[len(p.parts)-1]
}

func (p posixPath) hasPart(part string) bool {
	for _, existing := range p.parts {
		if existing == part {
			return true
		}
	}
	return false
}

func (p posixPath) asPosix() string {
	joined := strings.Join(p.parts, "/")
	if p.absolute {
		return "/" + joined
	}
	if joined == "" {
		return "."
	}
	return joined
}

// pathSuffix is the name's last extension, "" when the dot leads or ends
// the name.
func pathSuffix(name string) string {
	index := strings.LastIndex(name, ".")
	if index > 0 && index < len(name)-1 {
		return name[index:]
	}
	return ""
}

// pathStem is the name without its last extension.
func pathStem(name string) string {
	if suffix := pathSuffix(name); suffix != "" {
		return name[:len(name)-len(suffix)]
	}
	return name
}

func posixName(value string) string { return purePosix(value).name() }

// safePath checks a relative path of the runtime, upstream and release
// inventories: after the backslash/colon screen, only absolute paths and
// ".." components are rejected.
func safePath(value any, label string) (string, error) {
	text, ok := value.(string)
	if !ok || text == "" || strings.ContainsAny(text, `\:`) {
		return "", fmt.Errorf("invalid %s: %s", label, pyRepr(value))
	}
	path := purePosix(text)
	if path.absolute || path.hasPart("..") {
		return "", fmt.Errorf("unsafe %s: %s", label, text)
	}
	return text, nil
}

var windowsReserved = func() map[string]bool {
	names := map[string]bool{"CON": true, "PRN": true, "AUX": true, "NUL": true, "CONIN$": true, "CONOUT$": true}
	for _, prefix := range []string{"COM", "LPT"} {
		for number := 1; number <= 9; number++ {
			names[prefix+strconv.Itoa(number)] = true
		}
		for _, superscript := range []string{"¹", "²", "³"} {
			names[prefix+superscript] = true
		}
	}
	return names
}()

// otherCategory reports unicodedata.category(r).startswith("C").
func otherCategory(r rune) bool {
	if unicode.Is(unicode.Cc, r) || unicode.Is(unicode.Cf, r) || unicode.Is(unicode.Co, r) || unicode.Is(unicode.Cs, r) {
		return true
	}
	return !unicode.In(r, unicode.L, unicode.M, unicode.N, unicode.P, unicode.S, unicode.Z)
}

// ValidatePortableRelativePath refuses a relative path that is unsafe or
// would not survive on Windows, macOS and Linux.
func ValidatePortableRelativePath(value any, label string) (string, error) {
	text, ok := value.(string)
	invalid := !ok || text == ""
	if !invalid {
		for _, r := range text {
			if otherCategory(r) || strings.ContainsRune(`<>:"\|?*`, r) {
				invalid = true
				break
			}
		}
	}
	if invalid {
		return "", fmt.Errorf("invalid %s: %s", label, pyRepr(value))
	}
	rawParts := strings.Split(text, "/")
	for _, part := range rawParts {
		if part == "" || part == "." || part == ".." {
			return "", fmt.Errorf("unsafe %s: %s", label, text)
		}
	}
	for _, part := range rawParts {
		base, _, _ := strings.Cut(part, ".")
		if norm.NFC.String(part) != part || strings.HasSuffix(part, ".") || strings.HasSuffix(part, " ") ||
			windowsReserved[upper.String(base)] || archive.UTF16Units(part) > 255 {
			return "", fmt.Errorf("non-portable %s component: %s", label, pyRepr(part))
		}
	}
	if archive.UTF16Units(text) > 240 {
		return "", fmt.Errorf("%s exceeds 240 UTF-16 code units", label)
	}
	return text, nil
}

// pyRepr renders repr() for error messages.
func pyRepr(value any) string {
	switch typed := value.(type) {
	case string:
		quote := "'"
		if strings.Contains(typed, "'") && !strings.Contains(typed, `"`) {
			quote = `"`
		}
		var builder strings.Builder
		builder.WriteString(quote)
		for _, r := range typed {
			switch {
			case r == '\\':
				builder.WriteString(`\\`)
			case string(r) == quote:
				builder.WriteString(`\` + quote)
			case r == '\n':
				builder.WriteString(`\n`)
			case r == '\r':
				builder.WriteString(`\r`)
			case r == '\t':
				builder.WriteString(`\t`)
			case r < 0x20 || r == 0x7f:
				fmt.Fprintf(&builder, `\x%02x`, r)
			default:
				builder.WriteRune(r)
			}
		}
		builder.WriteString(quote)
		return builder.String()
	case nil:
		return "None"
	}
	return reprScalar(value)
}

// isFile reports whether path is a regular file (following symlinks).
func isFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}

// isDir reports whether path is a directory (following symlinks).
func isDir(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

// isSymlink reports whether path is a symlink.
func isSymlink(path string) bool {
	info, err := os.Lstat(path)
	return err == nil && info.Mode()&os.ModeSymlink != 0
}

// exists reports whether path exists (following symlinks).
func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// rglobFiles lists every file under root, sorted, as POSIX paths relative to
// root. Symlinked directories are not descended, but symlinks to files
// count.
func rglobFiles(root string) ([]string, error) {
	var files []string
	err := filepath.WalkDir(root, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			if errors.Is(err, os.ErrNotExist) && path == root {
				return filepath.SkipAll
			}
			return err
		}
		if path == root {
			return nil
		}
		if isFile(path) {
			relative, _ := filepath.Rel(root, path)
			files = append(files, filepath.ToSlash(relative))
		}
		return nil
	})
	sort.Strings(files)
	return files, err
}

func fileSize(path string) (int64, error) {
	info, err := os.Stat(path)
	if err != nil {
		return 0, err
	}
	return info.Size(), nil
}

func readFile(path string) ([]byte, error) { return os.ReadFile(path) }

// joinPosix joins a POSIX relative path below base.
func joinPosix(base, relative string) string {
	return filepath.Join(base, filepath.FromSlash(relative))
}
