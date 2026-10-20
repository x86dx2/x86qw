package services

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/archive"
)

const temporaryHeader = "// x86QW: configuração efêmera removida ao encerrar.\n"

// TemporaryConfig is one ephemeral server cfg.
type TemporaryConfig struct {
	Path      string
	Identity  [2]int64
	Sensitive bool
}

// WriteTemporaryConfig mirrors services.temporary_config (prefix +
// token_hex(12) + ".cfg", 0600, journaled before writing).
func WriteTemporaryConfig(dir, prefix string, lines [][]byte, journal *Journal) (*TemporaryConfig, error) {
	if info, err := os.Lstat(dir); err != nil || !info.IsDir() {
		return nil, errorf("Diretório de configuração ausente ou inseguro: %s", dir)
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
			return nil, errorf("Não foi possível criar a configuração efêmera com acesso privado.")
		}
		file = created
		break
	}
	if file == nil {
		return nil, errorf("Não foi possível criar a configuração efêmera com acesso privado.")
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

// MaterializePK3 mirrors managed_files.materialize_archive: MVDSV cannot read
// PK3s, so members are published as files for the session and removed later.
func MaterializePK3(pk3, root, label string, journal *Journal) (*Materialized, error) {
	if info, err := os.Lstat(pk3); err != nil || !info.Mode().IsRegular() {
		return nil, errorf("Pacote %s ausente ou inseguro: %s", label, pk3)
	}
	if info, err := os.Lstat(root); err != nil || !info.IsDir() {
		return nil, errorf("Diretório de %s ausente ou inseguro: %s", label, root)
	}
	plan, err := archive.Open(pk3, archive.DefaultLimits)
	if err != nil {
		return nil, errorf("Não foi possível preparar a carga de %s para o MVDSV: %v", label, err)
	}
	defer plan.Close()
	scratch, err := os.MkdirTemp("", "x86qw-pk3-")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(scratch)
	extracted := filepath.Join(scratch, "payload")
	if err := plan.Extract(extracted); err != nil {
		return nil, errorf("Não foi possível preparar a carga de %s para o MVDSV: %v", label, err)
	}
	result := &Materialized{}
	fail := func(err error) (*Materialized, error) {
		result.Cleanup(nil)
		return nil, err
	}
	type prepared struct {
		name, source, destination, digest string
		size                              int64
		existed                           bool
	}
	var members []prepared
	for _, member := range plan.Members {
		source := filepath.Join(extracted, filepath.FromSlash(member.Name))
		digest, size, err := fileSHA256(source)
		if err != nil {
			return nil, err
		}
		destination := filepath.Join(root, filepath.FromSlash(member.Name))
		existed := false
		if info, err := os.Lstat(destination); err == nil {
			current, _, hashErr := fileSHA256(destination)
			if !info.Mode().IsRegular() || hashErr != nil || current != digest {
				return nil, errorf("Arquivo local conflita com a carga dedicada de %s: %s", label, destination)
			}
			existed = true
		}
		members = append(members, prepared{member.Name, source, destination, digest, size, existed})
	}
	origin := filepath.ToSlash(pk3)
	for _, member := range members {
		parent := filepath.Dir(member.destination)
		var missing []string
		for cursor := parent; cursor != root; cursor = filepath.Dir(cursor) {
			if _, err := os.Lstat(cursor); err == nil {
				break
			}
			missing = append(missing, cursor)
		}
		for index := len(missing) - 1; index >= 0; index-- {
			if err := os.Mkdir(missing[index], 0o755); err != nil && !errors.Is(err, os.ErrExist) {
				return fail(err)
			}
			info, err := os.Lstat(missing[index])
			if err != nil {
				return fail(err)
			}
			directory := &MaterializedDirectory{Path: missing[index], Identity: identityAt(missing[index], info)}
			result.Directories = append(result.Directories, directory)
			if journal != nil {
				if err := journal.RecordDirectory(directory); err != nil {
					return fail(err)
				}
			}
		}
		if member.existed {
			info, err := os.Lstat(member.destination)
			if err != nil {
				return fail(err)
			}
			entry := &MaterializedFile{Path: member.destination, ExpectedHash: member.digest, Origin: origin,
				Existed: true, Identity: identityAt(member.destination, info), Size: member.size}
			if journal != nil {
				if err := journal.RecordMaterialized(entry); err != nil {
					return fail(err)
				}
			}
			result.Files = append(result.Files, entry)
			continue
		}
		temporary := filepath.Join(parent, ".x86qw_ktx_"+tokenHex(12))
		identity, err := copyPrivate(member.source, temporary, member.digest, member.size)
		if err != nil {
			os.Remove(temporary)
			return fail(errorf("Conteúdo divergente no pacote %s: %s", label, member.name))
		}
		entry := &MaterializedFile{Path: member.destination, ExpectedHash: member.digest, Origin: origin,
			CreatedBySession: true, Identity: identity, Size: member.size}
		if journal != nil {
			if err := journal.RecordMaterializedIntent(entry); err != nil {
				os.Remove(temporary)
				return fail(err)
			}
		}
		if err := os.Link(temporary, member.destination); err != nil {
			os.Remove(temporary)
			return fail(errorf("Arquivo surgiu durante a preparação e foi preservado: %s", member.destination))
		}
		result.Files = append(result.Files, entry)
		os.Remove(temporary)
		if journal != nil {
			if err := journal.RecordMaterialized(entry); err != nil {
				return fail(err)
			}
		}
	}
	return result, nil
}

func copyPrivate(source, destination, digest string, size int64) ([2]int64, error) {
	in, err := os.Open(source)
	if err != nil {
		return [2]int64{}, err
	}
	defer in.Close()
	out, err := os.OpenFile(destination, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return [2]int64{}, err
	}
	hash := sha256.New()
	copied, copyErr := io.Copy(io.MultiWriter(out, hash), io.LimitReader(in, size+1))
	syncErr := out.Sync()
	chmodErr := out.Chmod(0o644)
	identity, identityErr := identityOfFile(out)
	closeErr := out.Close()
	if err := errors.Join(copyErr, syncErr, chmodErr, identityErr, closeErr); err != nil {
		return [2]int64{}, err
	}
	if copied != size || hex.EncodeToString(hash.Sum(nil)) != digest {
		return [2]int64{}, errors.New("conteúdo divergente")
	}
	return identity, nil
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
			warn("Arquivo materializado alterado durante a sessão foi preservado: " + entry.Path)
		}
	}
	for index := len(m.Directories) - 1; index >= 0; index-- {
		directory := m.Directories[index]
		if !cleanupDirectory(directory.Path, directory.Identity) && warn != nil {
			warn("Diretório materializado alterado ou não removível com segurança foi preservado: " + directory.Path)
		}
	}
}

// ReadPasswordFile mirrors services.read_password_file.
func ReadPasswordFile(path, label string) (string, error) {
	info, err := os.Lstat(path)
	if err != nil {
		return "", errorf("Não foi possível ler o arquivo de %s.", label)
	}
	if !info.Mode().IsRegular() {
		return "", errorf("O arquivo de %s precisa ser regular e não pode ser symlink.", label)
	}
	if info.Size() > 4096 {
		return "", errorf("O arquivo de %s excede o limite de 4096 bytes.", label)
	}
	if info.Mode().Perm()&0o077 != 0 {
		return "", errorf("Permissões inseguras no arquivo de %s; use chmod 600.", label)
	}
	payload, err := os.ReadFile(path)
	if err != nil {
		return "", errorf("Não foi possível ler o arquivo de %s.", label)
	}
	value := string(payload)
	if strings.HasSuffix(value, "\r\n") {
		value = value[:len(value)-2]
	} else if strings.HasSuffix(value, "\n") {
		value = value[:len(value)-1]
	}
	if strings.ContainsAny(value, "\r\n") {
		return "", errorf("O arquivo de %s deve conter uma única linha.", label)
	}
	if value != "" {
		if _, err := SafeText(value, label, 4096); err != nil {
			return "", err
		}
	}
	return value, nil
}

var _ = fmt.Sprint
