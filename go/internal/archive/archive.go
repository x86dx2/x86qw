// Package archive safely extracts ZIP archives with the same limits and path
// rules as x86qw_runtime/io/archive.py.
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

// DefaultLimits mirrors ArchiveLimits().
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
		return nil, fail("arquivo ZIP não é regular: %s", source)
	}
	if info.Size() > limits.MaxSourceSize {
		return nil, fail("arquivo ZIP excede %d bytes", limits.MaxSourceSize)
	}
	digest, err := fileSHA256(source)
	if err != nil {
		return nil, err
	}
	reader, err := zip.OpenReader(source)
	if err != nil {
		return nil, fail("ZIP inválido: %v", err)
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
		return fail("ZIP excede %d membros", limits.MaxMembers)
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
			return fail("membro ZIP com cabeçalho local duplicado ou inválido: %q", name)
		}
		offsets[offset] = true
		if err := checkExtra(file.Extra, name); err != nil {
			return err
		}
		unixMode := file.ExternalAttrs >> 16
		fileType := unixMode & unixTypeMask
		if fileType != 0 && fileType != unixRegular && fileType != unixDirectory {
			return fail("ZIP contém membro especial: %q", name)
		}
		dos := file.ExternalAttrs & 0xFFFF
		if dos&(windowsDevice|windowsReparse) != 0 {
			return fail("ZIP contém membro especial do Windows: %q", name)
		}
		isDir := strings.HasSuffix(name, "/")
		if dos&windowsDirectory != 0 && !isDir {
			return fail("tipo do membro ZIP conflita com os atributos DOS: %q", name)
		}
		if (isDir && fileType != 0 && fileType != unixDirectory) || (!isDir && fileType == unixDirectory) {
			return fail("tipo do membro ZIP conflita com o nome: %q", name)
		}
		canonical, parts, key, err := validateName(name, isDir, limits)
		if err != nil {
			return err
		}
		if exact[canonical] {
			return fail("ZIP contém colisão exata de caminho: %q", canonical)
		}
		exact[canonical] = true
		if previous, ok := semantic[key]; ok {
			return fail("ZIP contém colisão casefold/NFC: %q, %q", previous, canonical)
		}
		semantic[key] = canonical
		for index := 1; index <= len(parts); index++ {
			spelling := strings.Join(parts[:index], "/")
			prefixKey := semanticKey(parts[:index])
			if prior, ok := spellings[prefixKey]; ok && prior != spelling {
				return fail("ZIP contém colisão casefold/NFC de prefixo: %q, %q", prior, spelling)
			}
			spellings[prefixKey] = spelling
			wanted := "file"
			if index < len(parts) || isDir {
				wanted = "directory"
			}
			if prior, ok := types[prefixKey]; ok && prior != wanted {
				return fail("ZIP contém conflito arquivo/diretório: %q", canonical)
			}
			types[prefixKey] = wanted
		}
		if file.Flags&encryptedFlags != 0 {
			return fail("ZIP contém membro criptografado: %q", canonical)
		}
		if file.Flags&^allowedFlags != 0 {
			return fail("membro ZIP usa flags não suportadas: %q", canonical)
		}
		if file.Method != zip.Store && file.Method != zip.Deflate {
			return fail("ZIP usa compressão não suportada: %q", canonical)
		}
		if file.UncompressedSize64 > limits.MaxMemberSize {
			return fail("membro ZIP excede o limite de tamanho: %q", canonical)
		}
		if isDir {
			if file.UncompressedSize64 != 0 {
				return fail("diretório ZIP com conteúdo: %q", canonical)
			}
			continue
		}
		total += file.UncompressedSize64
		if total > limits.MaxTotalSize {
			return fail("ZIP excede o limite total descompactado")
		}
		compressed := file.CompressedSize64
		if compressed == 0 {
			compressed = 1
		}
		if file.UncompressedSize64 > 0 && float64(file.UncompressedSize64)/float64(compressed) > limits.MaxCompressionRatio {
			return fail("membro ZIP excede a razão de compressão: %q", canonical)
		}
		p.Members = append(p.Members, Member{
			Name: canonical, Size: file.UncompressedSize64, Executable: unixMode&0o111 != 0, file: file,
		})
	}
	return nil
}

func checkExtra(extra []byte, name string) error {
	for offset := 0; offset < len(extra); {
		if len(extra)-offset < 4 {
			return fail("membro ZIP com campo extra truncado: %q", name)
		}
		id := binary.LittleEndian.Uint16(extra[offset:])
		size := int(binary.LittleEndian.Uint16(extra[offset+2:]))
		offset += 4
		if offset+size > len(extra) {
			return fail("membro ZIP com campo extra truncado: %q", name)
		}
		if id == extraUnixLegacy || id == extraUnixNew {
			return fail("membro ZIP com campo extra Unix capaz de link: %q", name)
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
		return "", nil, "", fail("membro ZIP sem nome")
	}
	if utf16Units(name) > limits.MaxPathUTF16Units {
		return "", nil, "", fail("caminho ZIP excede %d unidades UTF-16: %q", limits.MaxPathUTF16Units, name)
	}
	if strings.HasPrefix(name, "/") || strings.ContainsAny(name, `\:`) {
		return "", nil, "", fail("caminho ZIP não é POSIX portável: %q", name)
	}
	for _, r := range name {
		if unicode.Is(unicode.C, r) {
			return "", nil, "", fail("caminho ZIP com caractere de controle: %q", name)
		}
	}
	canonical := name
	if isDir {
		canonical = strings.TrimSuffix(name, "/")
	}
	parts := strings.Split(canonical, "/")
	if canonical == "" || len(parts) > limits.MaxDepth {
		return "", nil, "", fail("caminho ZIP inseguro: %q", name)
	}
	for _, part := range parts {
		if part == "" || part == "." || part == ".." {
			return "", nil, "", fail("caminho ZIP inseguro: %q", name)
		}
		if strings.ContainsAny(part, windowsForbidden) {
			return "", nil, "", fail("caminho ZIP usa caractere proibido no Windows: %q", name)
		}
		if strings.HasSuffix(part, ".") || strings.HasSuffix(part, " ") {
			return "", nil, "", fail("caminho ZIP termina com ponto ou espaço: %q", name)
		}
		stem := strings.ToUpper(strings.TrimRight(strings.SplitN(part, ".", 2)[0], ". "))
		if windowsReserved[stem] {
			return "", nil, "", fail("caminho ZIP usa nome reservado do Windows: %q", name)
		}
	}
	return canonical, parts, semanticKey(parts), nil
}

// Extract writes every member below a new directory created atomically at
// destination (which must not exist), verifying CRC and size while streaming.
func (p *Plan) Extract(destination string) error {
	if _, err := os.Lstat(destination); err == nil {
		return fail("destino de extração já existe: %s", destination)
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
	target := filepath.Join(root, filepath.FromSlash(member.Name))
	if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
		return err
	}
	source, err := member.file.Open()
	if err != nil {
		return fail("membro ZIP ilegível %q: %v", member.Name, err)
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
		return fail("membro ZIP corrompido %q: %v", member.Name, copyErr)
	}
	if closeErr != nil {
		return closeErr
	}
	if uint64(written) != member.Size || checksum.Sum32() != member.file.CRC32 {
		return fail("membro ZIP diverge do tamanho ou CRC declarado: %q", member.Name)
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
			return nil, fail("membro ZIP diverge do tamanho ou CRC declarado: %q", name)
		}
		return payload, nil
	}
	return nil, fail("membro ausente do ZIP: %q", name)
}
