// Package receipt reads the per-component receipt and inventory files kept
// under .x86qw/components/<id>/ (and .x86qw/personal/).
//
// receipt:   "key\tvalue\n" lines, format 1, with inventory_sha256 equal to
//
//	the SHA-256 of the inventory file bytes.
//
// inventory: "relative/path\tsha256\n" lines, written sorted by path.
package receipt

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
)

const (
	ComponentsDir = ".x86qw/components"
	PersonalDir   = ".x86qw/personal"
	maxFileBytes  = 16 * 1024 * 1024
)

var hex64 = regexp.MustCompile(`^[0-9a-f]{64}$`)

// Receipt is one parsed receipt file; Keys preserves the written order.
type Receipt struct {
	Keys   []string
	Values map[string]string
}

// Get returns a value or "".
func (r *Receipt) Get(key string) string { return r.Values[key] }

// Entry is one managed file.
type Entry struct {
	Path   string
	SHA256 string
}

// Inventory is the set of files a component owns.
type Inventory struct {
	Entries []Entry
	Raw     []byte
}

// SHA256 of the inventory bytes, as recorded in the receipt.
func (i *Inventory) SHA256() string {
	digest := sha256.Sum256(i.Raw)
	return hex.EncodeToString(digest[:])
}

func readBounded(name string) ([]byte, error) {
	info, err := os.Lstat(name)
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() {
		return nil, fmt.Errorf("%s is not a regular file", name)
	}
	if info.Size() > maxFileBytes {
		return nil, fmt.Errorf("%s is over %d bytes", name, maxFileBytes)
	}
	return os.ReadFile(name)
}

// ParseReceipt parses "key\tvalue" lines.
func ParseReceipt(payload []byte) (*Receipt, error) {
	receipt := &Receipt{Values: map[string]string{}}
	scanner := bufio.NewScanner(bytes.NewReader(payload))
	for scanner.Scan() {
		key, value, ok := strings.Cut(scanner.Text(), "\t")
		if !ok || key == "" || strings.Contains(value, "\t") {
			return nil, fmt.Errorf("invalid receipt line: %q", scanner.Text())
		}
		if _, dup := receipt.Values[key]; dup {
			return nil, fmt.Errorf("duplicate receipt key: %s", key)
		}
		receipt.Keys = append(receipt.Keys, key)
		receipt.Values[key] = value
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	if receipt.Get("format") != "1" {
		return nil, fmt.Errorf("unsupported receipt format: %q", receipt.Get("format"))
	}
	return receipt, nil
}

// Marshal writes the receipt in its original key order.
func (r *Receipt) Marshal() []byte {
	var buffer bytes.Buffer
	for _, key := range r.Keys {
		buffer.WriteString(key + "\t" + r.Values[key] + "\n")
	}
	return buffer.Bytes()
}

// ParseInventory parses "path\tsha256" lines and checks paths are safe and
// unique. They are written sorted, but any order is accepted.
func ParseInventory(payload []byte) (*Inventory, error) {
	inventory := &Inventory{Raw: payload}
	if len(payload) > 0 && payload[len(payload)-1] != '\n' {
		return nil, fmt.Errorf("inventory without a final line break")
	}
	seen := map[string]bool{}
	for _, line := range strings.Split(strings.TrimSuffix(string(payload), "\n"), "\n") {
		if line == "" && len(payload) == 0 {
			break
		}
		name, digest, ok := strings.Cut(line, "\t")
		if !ok || !hex64.MatchString(digest) {
			return nil, fmt.Errorf("invalid inventory line: %q", line)
		}
		if err := SafeRelative(name); err != nil {
			return nil, err
		}
		if seen[name] {
			return nil, fmt.Errorf("duplicate inventory entry: %s", name)
		}
		seen[name] = true
		inventory.Entries = append(inventory.Entries, Entry{Path: name, SHA256: digest})
	}
	return inventory, nil
}

// MarshalInventory renders entries sorted by path.
func MarshalInventory(entries []Entry) []byte {
	sorted := append([]Entry(nil), entries...)
	sort.Slice(sorted, func(a, b int) bool { return sorted[a].Path < sorted[b].Path })
	var buffer bytes.Buffer
	for _, entry := range sorted {
		buffer.WriteString(entry.Path + "\t" + entry.SHA256 + "\n")
	}
	return buffer.Bytes()
}

// SafeRelative rejects absolute, parent-escaping, empty or control-character paths.
func SafeRelative(name string) error {
	if name == "" || strings.HasPrefix(name, "/") || strings.Contains(name, "\\") ||
		path.Clean(name) != name || name == ".." || strings.HasPrefix(name, "../") {
		return fmt.Errorf("unsafe managed path: %q", name)
	}
	for _, r := range name {
		if r < 0x20 || r == 0x7f {
			return fmt.Errorf("managed path with a control character: %q", name)
		}
	}
	return nil
}

// Record is a component's receipt plus its inventory.
type Record struct {
	ID        string
	Receipt   *Receipt
	Inventory *Inventory
}

// Load reads <dir>/receipt and <dir>/inventory and checks the binding.
func Load(dir string) (*Record, error) {
	receiptBytes, err := readBounded(filepath.Join(dir, "receipt"))
	if err != nil {
		return nil, err
	}
	rec, err := ParseReceipt(receiptBytes)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", dir, err)
	}
	inventoryBytes, err := readBounded(filepath.Join(dir, "inventory"))
	if err != nil {
		return nil, err
	}
	inventory, err := ParseInventory(inventoryBytes)
	if err != nil {
		return nil, fmt.Errorf("%s: %w", dir, err)
	}
	if inventory.SHA256() != rec.Get("inventory_sha256") {
		return nil, fmt.Errorf("%s: the inventory does not match the receipt", dir)
	}
	return &Record{ID: rec.Get("component"), Receipt: rec, Inventory: inventory}, nil
}

// LoadComponents reads every component record of an installation.
func LoadComponents(root string) ([]*Record, error) {
	entries, err := os.ReadDir(filepath.Join(root, ComponentsDir))
	if err != nil {
		return nil, err
	}
	var records []*Record
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		record, err := Load(filepath.Join(root, ComponentsDir, entry.Name()))
		if err != nil {
			return nil, err
		}
		if record.ID != entry.Name() {
			return nil, fmt.Errorf("the receipt of %s declares component %q", entry.Name(), record.ID)
		}
		records = append(records, record)
	}
	return records, nil
}
