package content

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"hash/crc32"
	"os"
	"path/filepath"
	"sort"

	"github.com/x86dx2/x86qw/go/internal/archive"
	"github.com/x86dx2/x86qw/go/internal/content/zdeflate"
)

// Deterministic ZIP layout written exactly like CPython's zipfile for
// ZipInfo(name, (2020, 1, 1, 0, 0, 0)) with ZIP_DEFLATED and
// external_attr 0o100644 << 16, written with writestr() to a seekable
// stream: no data descriptor, no extra fields, version 2.0, created on
// Unix, DOS timestamp 2020-01-01 00:00:00.

const (
	zipDOSDate      = (2020-1980)<<9 | 1<<5 | 1
	zipDOSTime      = 0
	zipVersion      = 20
	zipCreateSystem = 3
	zipExternalAttr = 0o100644 << 16
	zip64Limit      = 1<<31 - 1
)

// ZipEntry is one member to write.
type ZipEntry struct {
	Name string
	Data []byte
}

func isASCII(value string) bool {
	for index := 0; index < len(value); index++ {
		if value[index] >= 0x80 {
			return false
		}
	}
	return true
}

// BuildZip writes the members in order and returns the archive bytes.
func BuildZip(entries []ZipEntry) ([]byte, error) {
	var out bytes.Buffer
	var central bytes.Buffer
	for _, entry := range entries {
		if float64(len(entry.Data))*1.05 > zip64Limit {
			return nil, fmt.Errorf("membro ZIP exige ZIP64, fora do layout determinístico: %s", entry.Name)
		}
		compressed := zdeflate.Compress(entry.Data)
		if len(compressed) > zip64Limit || out.Len() > zip64Limit {
			return nil, fmt.Errorf("ZIP exige ZIP64, fora do layout determinístico: %s", entry.Name)
		}
		flags := uint16(0)
		if !isASCII(entry.Name) {
			flags |= 0x800
		}
		checksum := crc32.ChecksumIEEE(entry.Data)
		offset := uint32(out.Len())
		header := []any{
			uint32(0x04034b50), uint8(zipVersion), uint8(0), flags, uint16(8),
			uint16(zipDOSTime), uint16(zipDOSDate), checksum,
			uint32(len(compressed)), uint32(len(entry.Data)),
			uint16(len(entry.Name)), uint16(0),
		}
		for _, field := range header {
			binary.Write(&out, binary.LittleEndian, field)
		}
		out.WriteString(entry.Name)
		out.Write(compressed)
		record := []any{
			uint32(0x02014b50), uint8(zipVersion), uint8(zipCreateSystem), uint8(zipVersion), uint8(0),
			flags, uint16(8), uint16(zipDOSTime), uint16(zipDOSDate), checksum,
			uint32(len(compressed)), uint32(len(entry.Data)),
			uint16(len(entry.Name)), uint16(0), uint16(0), uint16(0), uint16(0),
			uint32(zipExternalAttr), offset,
		}
		for _, field := range record {
			binary.Write(&central, binary.LittleEndian, field)
		}
		central.WriteString(entry.Name)
	}
	start := out.Len()
	out.Write(central.Bytes())
	if len(entries) > 0xffff || start > zip64Limit {
		return nil, fmt.Errorf("ZIP exige ZIP64, fora do layout determinístico")
	}
	end := []any{
		uint32(0x06054b50), uint16(0), uint16(0), uint16(len(entries)), uint16(len(entries)),
		uint32(central.Len()), uint32(start), uint16(0),
	}
	for _, field := range end {
		binary.Write(&out, binary.LittleEndian, field)
	}
	return out.Bytes(), nil
}

// BuildZipMembers mirrors component_sources.build_zip_members: members
// sorted by name, then validated as a canonical archive.
func BuildZipMembers(files map[string][]byte) ([]byte, error) {
	if len(files) == 0 {
		return nil, fmt.Errorf("component archive selects no files")
	}
	names := make([]string, 0, len(files))
	for name := range files {
		names = append(names, name)
	}
	sort.Strings(names)
	entries := make([]ZipEntry, len(names))
	for index, name := range names {
		entries[index] = ZipEntry{Name: name, Data: files[name]}
	}
	payload, err := BuildZip(entries)
	if err != nil {
		return nil, err
	}
	if _, err := ReadZipMembers(payload, "component archive"); err != nil {
		return nil, fmt.Errorf("unsafe component archive member: %v", err)
	}
	return payload, nil
}

// ReadZipMembers mirrors read_zip_members: a safe, duplicate-free member map.
func ReadZipMembers(payload []byte, label string) (map[string][]byte, error) {
	dir, err := os.MkdirTemp("", "x86qw-zip-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(dir)
	path := filepath.Join(dir, "archive.zip")
	if err := os.WriteFile(path, payload, 0o600); err != nil {
		return nil, err
	}
	members, err := readZipFile(path)
	if err != nil {
		return nil, fmt.Errorf("%s is not a safe archive: %v", label, err)
	}
	if len(members) == 0 {
		return nil, fmt.Errorf("%s contains no files", label)
	}
	return members, nil
}

func readZipFile(path string) (map[string][]byte, error) {
	plan, err := archive.Open(path, archive.DefaultLimits)
	if err != nil {
		return nil, err
	}
	defer plan.Close()
	members := map[string][]byte{}
	for _, member := range plan.Members {
		data, err := plan.ReadMember(member.Name)
		if err != nil {
			return nil, err
		}
		members[member.Name] = data
	}
	return members, nil
}

// ScanZipFile validates an archive file and the presence of required
// members, like scan_archive(path, required_members=...).
func ScanZipFile(path string, required []string) (map[string][]byte, error) {
	members, err := readZipFile(path)
	if err != nil {
		return nil, err
	}
	for _, name := range required {
		if _, ok := members[name]; !ok {
			return nil, fmt.Errorf("archive lacks required members: %s", name)
		}
	}
	return members, nil
}
