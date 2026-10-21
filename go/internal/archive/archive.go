// Package archive safely extracts ZIP archives: bounded sizes and member
// counts, portable relative paths, and CRC and size checked while streaming.
package archive

import (
	"archive/zip"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"hash/crc32"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"unicode"
	"unicode/utf16"

	"golang.org/x/text/cases"
	"golang.org/x/text/unicode/norm"
)

// Limits are enforced before and while members are decompressed.
type Limits struct {
	MaxMembers          int
	MaxSourceSize       int64
	MaxMemberSize       uint64
	MaxTotalSize        uint64
	MaxDepth            int
	MaxPathUTF16Units   int
	MaxCompressionRatio float64
}

// DefaultLimits are the extraction limits unless a caller sets its own.
var DefaultLimits = Limits{
	MaxMembers:          4096,
	MaxSourceSize:       512 << 20,
	MaxMemberSize:       128 << 20,
	MaxTotalSize:        512 << 20,
	MaxDepth:            16,
	MaxPathUTF16Units:   240,
	MaxCompressionRatio: 500,
}

const (
	allowedFlags            = 0x080E
	encryptedFlags          = 0x2041
	windowsDirectory        = 0x0010
	windowsDevice           = 0x0040
	windowsReparse          = 0x0400
	unixTypeMask            = 0o170000
	unixRegular             = 0o100000
	unixDirectory           = 0o040000
	extraUnixLegacy  uint16 = 0x000D
	extraUnixNew     uint16 = 0x756E
)

var (
	windowsForbidden = `<>"|?*`
	windowsReserved  = map[string]bool{
		"CON": true, "PRN": true, "AUX": true, "NUL": true, "CLOCK$": true, "CONIN$": true, "CONOUT$": true,
		"COM1": true, "COM2": true, "COM3": true, "COM4": true, "COM5": true, "COM6": true, "COM7": true, "COM8": true, "COM9": true,
		"COM¹": true, "COM²": true, "COM³": true,
		"LPT1": true, "LPT2": true, "LPT3": true, "LPT4": true, "LPT5": true, "LPT6": true, "LPT7": true, "LPT8": true, "LPT9": true,
		"LPT¹": true, "LPT²": true, "LPT³": true,
	}
	fold = cases.Fold()
)

// Error describes a rejected archive.
type Error struct{ msg string }

func (e *Error) Error() string { return e.msg }

func fail(format string, args ...any) error { return &Error{msg: fmt.Sprintf(format, args...)} }

// Member is one validated file entry.
type Member struct {
	Name       string
	Size       uint64
	Executable bool
	file       *zip.File
}

// Plan is a validated archive ready for extraction.
type Plan struct {
	Members []Member
	SHA256  string
	reader  *zip.ReadCloser
}

// Close releases the archive.
func (p *Plan) Close() error { return p.reader.Close() }

// Open validates every member's metadata without decompressing anything.
func Open(source string, limits Limits) (*Plan, error) {
	info, err := os.Lstat(source)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, fail("ZIP file is not regular: %s", source)
	}
	if info.Size() > limits.MaxSourceSize {
		return nil, fail("ZIP file exceeds %d bytes", limits.MaxSourceSize)
	}
	digest, err := fileSHA256(source)
	if err != nil {
		return nil, err
	}
	reader, err := zip.OpenReader(source)
	if err != nil {
		return nil, fail("invalid ZIP: %v", err)
	}
	plan := &Plan{SHA256: digest, reader: reader}
	if err := plan.validate(limits); err != nil {
		reader.Close()
		return nil, err
	}
	return plan, nil
}

func (p *Plan) validate(limits Limits) error {
	files := p.reader.File
	if len(files) > limits.MaxMembers {
		return fail("ZIP exceeds %d members", limits.MaxMembers)
	}
	exact := map[string]bool{}
	semantic := map[string]string{}
	spellings := map[string]string{}
	types := map[string]string{}
	offsets := map[int64]bool{}
	var total uint64
	for _, file := range files {
		name := file.Name
		offset, err := file.DataOffset()
		if err != nil || offsets[offset] {
			return fail("ZIP member with a duplicate or invalid local header: %q", name)
		}
		offsets[offset] = true
		if err := checkExtra(file.Extra, name); err != nil {
			return err
		}
		unixMode := file.ExternalAttrs >> 16
		fileType := unixMode & unixTypeMask
		if fileType != 0 && fileType != unixRegular && fileType != unixDirectory {
			return fail("ZIP contains a special member: %q", name)
		}
		dos := file.ExternalAttrs & 0xFFFF
		if dos&(windowsDevice|windowsReparse) != 0 {
			return fail("ZIP contains a special Windows member: %q", name)
		}
		isDir := strings.HasSuffix(name, "/")
		if dos&windowsDirectory != 0 && !isDir {
			return fail("ZIP member type conflicts with its DOS attributes: %q", name)
		}
		if (isDir && fileType != 0 && fileType != unixDirectory) || (!isDir && fileType == unixDirectory) {
			return fail("ZIP member type conflicts with its name: %q", name)
		}
		canonical, parts, key, err := validateName(name, isDir, limits)
		if err != nil {
			return err
		}
		if exact[canonical] {
			return fail("ZIP contains an exact path collision: %q", canonical)
		}
		exact[canonical] = true
		if previous, ok := semantic[key]; ok {
			return fail("ZIP contains a casefold/NFC collision: %q, %q", previous, canonical)
		}
		semantic[key] = canonical
		for index := 1; index <= len(parts); index++ {
			spelling := strings.Join(parts[:index], "/")
			prefixKey := semanticKey(parts[:index])
			if prior, ok := spellings[prefixKey]; ok && prior != spelling {
				return fail("ZIP contains a casefold/NFC prefix collision: %q, %q", prior, spelling)
			}
			spellings[prefixKey] = spelling
			wanted := "file"
			if index < len(parts) || isDir {
				wanted = "directory"
			}
			if prior, ok := types[prefixKey]; ok && prior != wanted {
				return fail("ZIP contains a file/folder conflict: %q", canonical)
			}
			types[prefixKey] = wanted
		}
		if file.Flags&encryptedFlags != 0 {
			return fail("ZIP contains an encrypted member: %q", canonical)
		}
		if file.Flags&^allowedFlags != 0 {
			return fail("ZIP member uses unsupported flags: %q", canonical)
		}
		if file.Method != zip.Store && file.Method != zip.Deflate {
			return fail("ZIP uses unsupported compression: %q", canonical)
		}
		if file.UncompressedSize64 > limits.MaxMemberSize {
			return fail("ZIP member exceeds the size limit: %q", canonical)
		}
		if isDir {
			if file.UncompressedSize64 != 0 {
				return fail("ZIP folder with contents: %q", canonical)
			}
			continue
		}
		total += file.UncompressedSize64
		if total > limits.MaxTotalSize {
			return fail("ZIP exceeds the total uncompressed limit")
		}
		compressed := file.CompressedSize64
		if compressed == 0 {
			compressed = 1
		}
		if file.UncompressedSize64 > 0 && float64(file.UncompressedSize64)/float64(compressed) > limits.MaxCompressionRatio {
			return fail("ZIP member exceeds the compression ratio: %q", canonical)
		}
		p.Members = append(p.Members, Member{
			Name: canonical, Size: file.UncompressedSize64, file: file,
		})
	}
	return nil
}

func checkExtra(extra []byte, name string) error {
	for offset := 0; offset < len(extra); {
		if len(extra)-offset < 4 {
			return fail("ZIP member with a truncated extra field: %q", name)
		}
		id := binary.LittleEndian.Uint16(extra[offset:])
		size := int(binary.LittleEndian.Uint16(extra[offset+2:]))
		offset += 4
		if offset+size > len(extra) {
			return fail("ZIP member with a truncated extra field: %q", name)
		}
		if id == extraUnixLegacy || id == extraUnixNew {
			return fail("ZIP member with a link-capable Unix extra field: %q", name)
		}
		offset += size
	}
	return nil
}

func utf16Units(value string) int { return len(utf16.Encode([]rune(value))) }

func semanticKey(parts []string) string {
	keys := make([]string, len(parts))
	for index, part := range parts {
		keys[index] = fold.String(norm.NFC.String(part))
	}
	return strings.Join(keys, "/")
}

func validateName(name string, isDir bool, limits Limits) (string, []string, string, error) {
	if name == "" {
		return "", nil, "", fail("ZIP member without a name")
	}
	if utf16Units(name) > limits.MaxPathUTF16Units {
		return "", nil, "", fail("ZIP path exceeds %d UTF-16 units: %q", limits.MaxPathUTF16Units, name)
	}
	if strings.HasPrefix(name, "/") || strings.ContainsAny(name, `\:`) {
		return "", nil, "", fail("ZIP path is not portable POSIX: %q", name)
	}
	for _, r := range name {
		if unicode.Is(unicode.C, r) {
			return "", nil, "", fail("ZIP path with a control character: %q", name)
		}
	}
	canonical := name
	if isDir {
		canonical = strings.TrimSuffix(name, "/")
	}
	parts := strings.Split(canonical, "/")
	if canonical == "" || len(parts) > limits.MaxDepth {
		return "", nil, "", fail("unsafe ZIP path: %q", name)
	}
	for _, part := range parts {
		if part == "" || part == "." || part == ".." {
			return "", nil, "", fail("unsafe ZIP path: %q", name)
		}
		if strings.ContainsAny(part, windowsForbidden) {
			return "", nil, "", fail("ZIP path uses a character Windows forbids: %q", name)
		}
		if strings.HasSuffix(part, ".") || strings.HasSuffix(part, " ") {
			return "", nil, "", fail("ZIP path ends with a dot or a space: %q", name)
		}
		stem := strings.ToUpper(strings.TrimRight(strings.SplitN(part, ".", 2)[0], ". "))
		if windowsReserved[stem] {
			return "", nil, "", fail("ZIP path uses a reserved Windows name: %q", name)
		}
	}
	return canonical, parts, semanticKey(parts), nil
}

// Extract writes every member below a new directory created atomically at
// destination (which must not exist), verifying CRC and size while streaming.
// Files are 0644 except the listed executables (0755); ZIP mode bits are
// ignored.
func (p *Plan) Extract(destination string, executables ...string) error {
	wanted := map[string]bool{}
	for _, name := range executables {
		wanted[name] = true
	}
	found := 0
	for index := range p.Members {
		p.Members[index].Executable = wanted[p.Members[index].Name]
		if p.Members[index].Executable {
			found++
		}
	}
	if found != len(wanted) {
		return fail("executable member missing from the ZIP")
	}
	if _, err := os.Lstat(destination); err == nil {
		return fail("extraction target already exists: %s", destination)
	} else if !errors.Is(err, fs.ErrNotExist) {
		return err
	}
	parent := filepath.Dir(destination)
	staging, err := os.MkdirTemp(parent, "."+filepath.Base(destination)+".*.tmp")
	if err != nil {
		return err
	}
	keep := false
	defer func() {
		if !keep {
			os.RemoveAll(staging)
		}
	}()
	for _, member := range p.Members {
		if err := extractMember(staging, member); err != nil {
			return err
		}
	}
	if err := filepath.WalkDir(staging, func(path string, entry fs.DirEntry, err error) error {
		if err == nil && entry.IsDir() {
			return os.Chmod(path, 0o755)
		}
		return err
	}); err != nil {
		return err
	}
	if err := os.Rename(staging, destination); err != nil {
		return err
	}
	keep = true
	return nil
}

func extractMember(root string, member Member) error {
	// validate already refused these names; this keeps the write local even
	// if a future change skips it.
	if !filepath.IsLocal(filepath.FromSlash(member.Name)) {
		return fail("unsafe ZIP path: %q", member.Name)
	}
	target := filepath.Join(root, filepath.FromSlash(member.Name))
	if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
		return err
	}
	source, err := member.file.Open()
	if err != nil {
		return fail("unreadable ZIP member %q: %v", member.Name, err)
	}
	defer source.Close()
	out, err := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	checksum := crc32.NewIEEE()
	written, copyErr := io.Copy(io.MultiWriter(out, checksum), io.LimitReader(source, int64(member.Size)+1))
	closeErr := out.Close()
	if copyErr != nil {
		return fail("corrupt ZIP member %q: %v", member.Name, copyErr)
	}
	if closeErr != nil {
		return closeErr
	}
	if uint64(written) != member.Size || checksum.Sum32() != member.file.CRC32 {
		return fail("ZIP member differs from its declared size or CRC: %q", member.Name)
	}
	mode := os.FileMode(0o644)
	if member.Executable {
		mode = 0o755
	}
	return os.Chmod(target, mode)
}

func fileSHA256(name string) (string, error) {
	file, err := os.Open(name)
	if err != nil {
		return "", err
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(hash.Sum(nil)), nil
}

// ReadMember returns the bytes of one file member, verifying size and CRC.
func (p *Plan) ReadMember(name string) ([]byte, error) {
	for _, member := range p.Members {
		if member.Name != name {
			continue
		}
		source, err := member.file.Open()
		if err != nil {
			return nil, err
		}
		defer source.Close()
		payload, err := io.ReadAll(io.LimitReader(source, int64(member.Size)+1))
		if err != nil {
			return nil, err
		}
		if uint64(len(payload)) != member.Size || crc32.ChecksumIEEE(payload) != member.file.CRC32 {
			return nil, fail("ZIP member differs from its declared size or CRC: %q", name)
		}
		return payload, nil
	}
	return nil, fail("member missing from the ZIP: %q", name)
}
