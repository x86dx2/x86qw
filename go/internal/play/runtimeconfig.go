//go:build !windows

package play

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"syscall"

	"github.com/x86dx2/x86qw/go/internal/lock"
)

// Ephemeral KTX launch configuration, journaled like
// x86qw_runtime/gameplay/runtime_configs.py so either CLI can recover it.

const journalDir = ".x86qw/gameplay/ktx-runtime-configs"

var journalName = regexp.MustCompile(`^x86qw-ktx-session-[0-9a-f]{24}\.json$`)

// RuntimeConfig is a published cfg and its journal.
type RuntimeConfig struct {
	Name    string // qw/<Name>
	config  string
	journal string
	digest  string
	size    int
	device  uint64
	inode   uint64
	staging string
}

type controller struct {
	CreationToken string `json:"creation_token"`
	Executable    string `json:"executable"`
	PID           int    `json:"pid"`
}

type configRecord struct {
	Device      *uint64 `json:"device"`
	Inode       *uint64 `json:"inode"`
	Path        string  `json:"path"`
	SHA256      string  `json:"sha256"`
	Size        int     `json:"size"`
	StagingPath string  `json:"staging_path"`
}

type journalDocument struct {
	Config     configRecord `json:"config"`
	Controller controller   `json:"controller"`
	Format     int          `json:"format"`
	Project    string       `json:"project"`
	State      string       `json:"state"`
	Type       string       `json:"type"`
}

func writeJournal(path string, document journalDocument, create bool) error {
	payload, err := json.Marshal(document)
	if err != nil {
		return err
	}
	payload = append(payload, '\n')
	if create {
		file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
		if err != nil {
			return err
		}
		_, err = file.Write(payload)
		return errors.Join(err, file.Sync(), file.Close())
	}
	temporary := path + ".tmp"
	if err := os.WriteFile(temporary, payload, 0o600); err != nil {
		return err
	}
	return os.Rename(temporary, path)
}

func currentController() (controller, error) {
	identity, status := lock.Identity(os.Getpid())
	if status != "alive" {
		return controller{}, errors.New("Não foi possível registrar o controlador da configuração KTX.")
	}
	return controller{PID: identity.PID, CreationToken: identity.Token, Executable: identity.Executable}, nil
}

// Recover removes configs whose controller is gone, like recover_runtime_configs.
func Recover(target string) error {
	dir := filepath.Join(target, filepath.FromSlash(journalDir))
	entries, err := os.ReadDir(dir)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if !journalName.MatchString(entry.Name()) {
			continue
		}
		path := filepath.Join(dir, entry.Name())
		payload, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		var document journalDocument
		if json.Unmarshal(payload, &document) != nil || document.Type != "ktx-runtime-config" {
			return fmt.Errorf("Journal KTX inválido preservado para inspeção: %s", path)
		}
		switch lock.Probe(document.Controller.PID, document.Controller.CreationToken, document.Controller.Executable) {
		case "alive":
			continue
		case "inconclusive":
			return fmt.Errorf("Controlador da configuração KTX não pôde ser confirmado; preservado: %s", document.Config.Path)
		}
		for _, relative := range []string{document.Config.Path, document.Config.StagingPath} {
			candidate := filepath.Join(target, filepath.FromSlash(relative))
			data, err := os.ReadFile(candidate)
			if errors.Is(err, os.ErrNotExist) {
				continue
			}
			sum := sha256.Sum256(data)
			if err != nil || hex.EncodeToString(sum[:]) != document.Config.SHA256 {
				return fmt.Errorf("Objetos KTX divergentes e seu journal foram preservados para inspeção: %s", candidate)
			}
			if err := os.Remove(candidate); err != nil {
				return err
			}
		}
		if err := os.Remove(path); err != nil {
			return fmt.Errorf("Journal KTX não pôde ser removido: %s", path)
		}
	}
	return nil
}

// Create journals and publishes qw/x86qw-ktx-session-<token>.cfg.
func Create(target string, payload []byte) (*RuntimeConfig, error) {
	if info, err := os.Lstat(filepath.Join(target, "qw")); err != nil || !info.IsDir() {
		return nil, fmt.Errorf("Diretório de configuração do ezQuake inválido: %s", filepath.Join(target, "qw"))
	}
	if err := Recover(target); err != nil {
		return nil, err
	}
	dir := filepath.Join(target, filepath.FromSlash(journalDir))
	for _, path := range []string{filepath.Join(target, ".x86qw", "gameplay"), dir} {
		if err := os.Mkdir(path, 0o700); err != nil && !errors.Is(err, os.ErrExist) {
			return nil, errors.New("Não foi possível criar o journal privado do gameplay.")
		}
	}
	raw := make([]byte, 12)
	rand.Read(raw)
	token := hex.EncodeToString(raw)
	sum := sha256.Sum256(payload)
	config := &RuntimeConfig{
		Name:    "x86qw-ktx-session-" + token + ".cfg",
		digest:  hex.EncodeToString(sum[:]),
		size:    len(payload),
		journal: filepath.Join(dir, "x86qw-ktx-session-"+token+".json"),
		staging: filepath.Join(dir, ".x86qw-ktx-session-"+token+".stage"),
	}
	config.config = filepath.Join(target, "qw", config.Name)
	owner, err := currentController()
	if err != nil {
		return nil, err
	}
	document := journalDocument{
		Format: 1, Project: "x86qw", Type: "ktx-runtime-config", State: "intent", Controller: owner,
		Config: configRecord{
			Path: "qw/" + config.Name, StagingPath: journalDir + "/.x86qw-ktx-session-" + token + ".stage",
			SHA256: config.digest, Size: config.size,
		},
	}
	if err := writeJournal(config.journal, document, true); err != nil {
		return nil, errors.New("Não foi possível registrar a intenção KTX.")
	}
	fail := func(err error) (*RuntimeConfig, error) {
		os.Remove(config.config)
		os.Remove(config.staging)
		os.Remove(config.journal)
		return nil, fmt.Errorf("Não foi possível publicar a configuração KTX: %w", err)
	}
	file, err := os.OpenFile(config.staging, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return fail(err)
	}
	_, writeErr := file.Write(payload)
	if err := errors.Join(writeErr, file.Sync(), file.Close()); err != nil {
		return fail(err)
	}
	info, err := os.Lstat(config.staging)
	if err != nil {
		return fail(err)
	}
	stat := info.Sys().(*syscall.Stat_t)
	config.device, config.inode = uint64(stat.Dev), uint64(stat.Ino)
	document.State = "ready"
	document.Config.Device, document.Config.Inode = &config.device, &config.inode
	if err := writeJournal(config.journal, document, false); err != nil {
		return fail(err)
	}
	if err := os.Link(config.staging, config.config); err != nil {
		return fail(err)
	}
	os.Remove(config.staging)
	return config, nil
}

// Transfer binds the journal to the launched client process.
func (c *RuntimeConfig) Transfer(pid int) error {
	identity, status := lock.Identity(pid)
	if status != "alive" {
		return errors.New("o cliente encerrou antes de receber a configuração KTX")
	}
	payload, err := os.ReadFile(c.journal)
	if err != nil {
		return err
	}
	var document journalDocument
	if err := json.Unmarshal(payload, &document); err != nil {
		return err
	}
	document.Controller = controller{PID: identity.PID, CreationToken: identity.Token, Executable: identity.Executable}
	return writeJournal(c.journal, document, false)
}

// Release removes the cfg and its journal (failure paths only).
func (c *RuntimeConfig) Release() {
	os.Remove(c.config)
	os.Remove(c.staging)
	os.Remove(c.journal)
}
