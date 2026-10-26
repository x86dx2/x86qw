package services

import (
	"bytes"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/archive"
	"github.com/x86dx2/x86qw/go/internal/privfs"
)

// StagingPrefix names the folder a payload is extracted to before it is
// published, and the copies beside its members on another volume.
const StagingPrefix = ".x86qw_ktx_"

// link publishes a member; tests replace it to play another volume.
var link = os.Link

const temporaryHeader = "// x86QW: temporary configuration, removed when the session stops.\n"

// TemporaryConfig is one ephemeral server cfg.
type TemporaryConfig struct {
	Path      string
	Identity  [2]int64
	Sensitive bool
}

// WriteTemporaryConfig writes a session cfg (prefix + 24 hex digits +
// ".cfg", 0600), journaled before it is written.
func WriteTemporaryConfig(dir, prefix string, lines [][]byte, journal *Journal) (*TemporaryConfig, error) {
	if info, err := os.Lstat(dir); err != nil || !info.IsDir() {
		return nil, errorf("Configuration folder missing or unsafe: %s", dir)
	}
	var payload bytes.Buffer
	payload.WriteString(temporaryHeader)
	for _, line := range lines {
		payload.Write(line)
		payload.WriteByte('\n')
	}
	var file *os.File
	var path string
	for attempt := 0; attempt < 128; attempt++ {
		path = filepath.Join(dir, prefix+tokenHex(12)+".cfg")
		created, err := os.OpenFile(path, os.O_RDWR|os.O_CREATE|os.O_EXCL, 0o600)
		if errors.Is(err, os.ErrExist) {
			continue
		}
		if err != nil {
			return nil, errorf("Could not create the temporary configuration with private access.")
		}
		file = created
		break
	}
	if file == nil {
		return nil, errorf("Could not create the temporary configuration with private access.")
	}
	identity, err := identityOfFile(file)
	if err != nil {
		file.Close()
		os.Remove(path)
		return nil, err
	}
	config := &TemporaryConfig{Path: path, Identity: identity, Sensitive: true}
	const origin = "configuração efêmera redigida"
	if journal != nil {
		if err := journal.RecordTemporaryIntent(path, origin, true, identity, "", 0); err != nil {
			file.Close()
			os.Remove(path)
			return nil, err
		}
	}
	_, writeErr := file.Write(payload.Bytes())
	if err := errors.Join(writeErr, file.Sync(), file.Close()); err != nil {
		cleanupSensitive(path, identity)
		return nil, err
	}
	if journal != nil {
		if err := journal.RecordTemporary(path, origin, true, identity, "", 0); err != nil {
			cleanupSensitive(path, identity)
			return nil, err
		}
	}
	return config, nil
}

// Materialized is one PK3 exposed as plain files for MVDSV.
type Materialized struct {
	Files       []*MaterializedFile
	Directories []*MaterializedDirectory
}

// MaterializePK3 publishes a PK3's members as files: MVDSV cannot read
// PK3s, so members are published as files for the session and removed later.
func MaterializePK3(pk3, root, label string, journal *Journal) (*Materialized, error) {
	if info, err := os.Lstat(pk3); err != nil || !info.Mode().IsRegular() {
		return nil, errorf("%s package missing or unsafe: %s", label, pk3)
	}
	if info, err := os.Lstat(root); err != nil || !info.IsDir() {
		return nil, errorf("%s folder missing or unsafe: %s", label, root)
	}
	plan, err := archive.Open(pk3, archive.DefaultLimits)
	if err != nil {
		return nil, errorf("Could not prepare the %s payload for MVDSV: %v", label, err)
	}
	defer plan.Close()
	// Extracted beside the destination, each member publishes as a hard
	// link: the same volume, and the permissions the folder gives its own
	// files. Nothing is synced per file; the journal is what must be
	// durable before anything is published.
	staging, err := os.MkdirTemp(root, StagingPrefix)
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(staging)
	extracted := filepath.Join(staging, "payload")
	if err := plan.Extract(extracted); err != nil {
		return nil, errorf("Could not prepare the %s payload for MVDSV: %v", label, err)
	}
	origin := filepath.ToSlash(pk3)
	var ready, pending []*MaterializedFile
	sources := map[*MaterializedFile]string{}
	for _, member := range plan.Members {
		source := filepath.Join(extracted, filepath.FromSlash(member.Name))
		digest, size, err := fileSHA256(source)
		if err != nil {
			return nil, err
		}
		destination := filepath.Join(root, filepath.FromSlash(member.Name))
		if info, err := os.Lstat(destination); err == nil {
			current, _, hashErr := fileSHA256(destination)
			if !info.Mode().IsRegular() || hashErr != nil || current != digest {
				return nil, errorf("A local file conflicts with the dedicated %s payload: %s", label, destination)
			}
			ready = append(ready, &MaterializedFile{Path: destination, ExpectedHash: digest, Origin: origin,
				Existed: true, Identity: identityAt(destination, info), Size: size})
			continue
		}
		info, err := os.Lstat(source)
		if err != nil {
			return nil, err
		}
		entry := &MaterializedFile{Path: destination, ExpectedHash: digest, Origin: origin,
			CreatedBySession: true, Identity: identityAt(source, info), Size: size}
		pending = append(pending, entry)
		sources[entry] = source
	}
	// Every member lands inside the installation, checked before a folder
	// is made: a payload folder may be a link out of it.
	if journal != nil {
		relative := journal.relatives()
		for _, file := range append(ready, pending...) {
			if _, err := relative(file.Path); err != nil {
				return nil, err
			}
		}
	}
	// Left by a session that died while preparing: one session runs at a
	// time, under the installation's lock.
	folders := map[string]bool{root: true}
	for _, file := range append(ready, pending...) {
		folders[filepath.Dir(file.Path)] = true
	}
	for folder := range folders {
		entries, _ := os.ReadDir(folder)
		for _, entry := range entries {
			if path := filepath.Join(folder, entry.Name()); strings.HasPrefix(entry.Name(), StagingPrefix) && path != staging {
				os.RemoveAll(path)
			}
		}
	}
	result := &Materialized{}
	// Copies beside members go first: a folder made for the payload goes
	// only when it is empty.
	var copies []string
	removeCopies := func() {
		for _, path := range copies {
			os.Remove(path)
		}
	}
	defer removeCopies()
	fail := func(err error) (*Materialized, error) {
		removeCopies()
		result.Cleanup(nil)
		return nil, err
	}
	// shortcut: a kill between these folders and the journal write leaves
	// them empty and unrecorded; MVDSV reads nothing there, so they stay.
	for _, file := range pending {
		var missing []string
		for cursor := filepath.Dir(file.Path); cursor != root; cursor = filepath.Dir(cursor) {
			if _, err := os.Lstat(cursor); err == nil {
				break
			}
			missing = append(missing, cursor)
		}
		for index := len(missing) - 1; index >= 0; index-- {
			if err := os.Mkdir(missing[index], 0o755); err != nil {
				return fail(err)
			}
			info, err := os.Lstat(missing[index])
			if err != nil {
				return fail(err)
			}
			result.Directories = append(result.Directories, &MaterializedDirectory{Path: missing[index], Identity: identityAt(missing[index], info)})
		}
	}
	// A folder on another volume, a mount inside the game folder, takes no
	// link from the staging: its members are copied there first.
	linkable := map[string]bool{}
	for _, file := range pending {
		folder := filepath.Dir(file.Path)
		ok, tried := linkable[folder]
		if !tried {
			probe := filepath.Join(folder, StagingPrefix+tokenHex(8))
			ok = link(sources[file], probe) == nil
			os.Remove(probe)
			linkable[folder] = ok
		}
		if ok {
			continue
		}
		copied := filepath.Join(folder, StagingPrefix+tokenHex(8))
		copies = append(copies, copied)
		if err := copyNew(sources[file], copied); err != nil {
			return fail(err)
		}
		info, err := os.Lstat(copied)
		if err != nil {
			return fail(err)
		}
		file.Identity, sources[file] = identityAt(copied, info), copied
	}
	result.Files = append(result.Files, ready...)
	if journal != nil {
		if err := journal.RecordPayload(result.Directories, ready, pending); err != nil {
			return fail(err)
		}
	}
	for _, file := range pending {
		// A link never replaces: a file that appeared meanwhile is kept.
		if err := link(sources[file], file.Path); errors.Is(err, os.ErrExist) {
			return fail(errorf("A file appeared during preparation and was kept: %s", file.Path))
		} else if err != nil {
			return fail(errorf("Could not publish %s for MVDSV (%v): the installation's file system must support hard links.", file.Path, err))
		}
		result.Files = append(result.Files, file)
	}
	if journal != nil {
		if err := journal.RecordPayload(nil, pending, nil); err != nil {
			return fail(err)
		}
	}
	return result, nil
}

// copyNew copies source to a file created at destination.
func copyNew(source, destination string) error {
	in, err := os.Open(source)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(destination, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
	if err != nil {
		return err
	}
	_, err = io.Copy(out, in)
	return errors.Join(err, out.Close())
}

// SuppressAutoexec drops gamedir/server.cfg created by the session so MVDSV
// does not autoexec it before the session cfg.
func (m *Materialized) SuppressAutoexec(gamedir string) {
	autoexec := filepath.Join(gamedir, "server.cfg")
	var kept []*MaterializedFile
	for _, entry := range m.Files {
		if entry.Path == autoexec && entry.CreatedBySession && !entry.Existed {
			if info, err := os.Lstat(autoexec); err == nil && info.Mode().IsRegular() {
				os.Remove(autoexec)
			}
			continue
		}
		kept = append(kept, entry)
	}
	m.Files = kept
}

// Cleanup removes unchanged files and created directories in reverse order.
func (m *Materialized) Cleanup(warn func(string)) {
	for index := len(m.Files) - 1; index >= 0; index-- {
		entry := m.Files[index]
		if !entry.CreatedBySession {
			continue
		}
		if !cleanupMaterializedFile(entry) && warn != nil {
			warn("A published file changed during the session and was kept: " + entry.Path)
		}
	}
	for index := len(m.Directories) - 1; index >= 0; index-- {
		directory := m.Directories[index]
		if !cleanupDirectory(directory.Path, directory.Identity) && warn != nil {
			warn("A published folder that changed or cannot be removed safely was kept: " + directory.Path)
		}
	}
}

// ReadPasswordFile reads a one-line password from a private regular file of
// at most 4096 bytes.
func ReadPasswordFile(path, label string) (string, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return "", errorf("Could not read the %s file.", label)
	}
	if !info.Mode().IsRegular() {
		return "", errorf("The %s file must be a regular file, not a symlink.", label)
	}
	if info.Size() > 4096 {
		return "", errorf("The %s file is over the 4096-byte limit.", label)
	}
	if !privfs.IsPrivateFile(path, info) {
		return "", errorf("Unsafe permissions on the %s file; %s.", label, privfs.Guidance())
	}
	payload, err := os.ReadFile(path)
	if err != nil {
		return "", errorf("Could not read the %s file.", label)
	}
	value := string(payload)
	if strings.HasSuffix(value, "\r\n") {
		value = value[:len(value)-2]
	} else if strings.HasSuffix(value, "\n") {
		value = value[:len(value)-1]
	}
	if strings.ContainsAny(value, "\r\n") {
		return "", errorf("The %s file must hold a single line.", label)
	}
	if value != "" {
		if _, err := SafeText(value, label, 4096); err != nil {
			return "", err
		}
	}
	return value, nil
}
