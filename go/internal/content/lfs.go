package content

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/download"
)

// LFS materialization from GitHub's media endpoint: CI may have no LFS
// quota while the
// commit's bytes stay public, so every object is fetched and verified
// against its committed pointer.

const (
	lfsPointerVersion = "version https://git-lfs.github.com/spec/v1"
	maxPointerBytes   = 4096
	maxLFSObjectBytes = 512 << 20
)

var (
	hex64        = regexp.MustCompile(`^[0-9a-f]{64}$`)
	commitID     = regexp.MustCompile(`^[0-9a-f]{40}$`)
	repositoryID = regexp.MustCompile(`^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`)
)

// LFSPointer is one pointer file under dist.
type LFSPointer struct {
	Path string // slash-separated, relative to the checkout
	OID  string
	Size int64
}

// ParseLFSPointer returns the object id and size of a canonical pointer.
func ParseLFSPointer(payload []byte) (string, int64, error) {
	if len(payload) > maxPointerBytes {
		return "", 0, errors.New("ponteiro LFS excede o limite estrutural")
	}
	for _, b := range payload {
		if b > 0x7f {
			return "", 0, errors.New("ponteiro LFS não é ASCII")
		}
	}
	lines := strings.Split(strings.TrimSuffix(string(payload), "\n"), "\n")
	if len(lines) != 3 || lines[0] != lfsPointerVersion || strings.Contains(string(payload), "\r") {
		return "", 0, errors.New("ponteiro LFS não canônico")
	}
	fields := map[string]string{}
	for _, line := range lines[1:] {
		name, value, found := strings.Cut(line, " ")
		if _, seen := fields[name]; !found || seen || value == "" {
			return "", 0, errors.New("ponteiro LFS malformado")
		}
		fields[name] = value
	}
	oid := fields["oid"]
	if !strings.HasPrefix(oid, "sha256:") || !hex64.MatchString(oid[7:]) {
		return "", 0, errors.New("ponteiro LFS sem SHA-256 canônico")
	}
	sizeText := fields["size"]
	size, err := strconv.ParseInt(sizeText, 10, 64)
	if err != nil || sizeText == "" || strings.Trim(sizeText, "0123456789") != "" || (len(sizeText) > 1 && sizeText[0] == '0') {
		return "", 0, errors.New("ponteiro LFS sem tamanho decimal canônico")
	}
	if size > maxLFSObjectBytes {
		return "", 0, errors.New("objeto LFS excede o limite de download")
	}
	if len(fields) != 2 {
		return "", 0, errors.New("ponteiro LFS contém campos inesperados")
	}
	return oid[7:], size, nil
}

// LFSPointers lists the pointer files under dist in sorted order.
func LFSPointers(root string) ([]LFSPointer, error) {
	distribution := filepath.Join(root, "dist")
	if info, err := os.Lstat(distribution); err != nil || !info.IsDir() {
		return nil, errors.New("a árvore dist não existe ou é um symlink")
	}
	var pointers []LFSPointer
	err := filepath.WalkDir(distribution, func(path string, entry os.DirEntry, err error) error {
		if err != nil || !entry.Type().IsRegular() {
			return err
		}
		file, err := os.Open(path)
		if err != nil {
			return err
		}
		defer file.Close()
		payload, err := io.ReadAll(io.LimitReader(file, maxPointerBytes+1))
		if err != nil {
			return err
		}
		if !bytes.HasPrefix(payload, GitLFSPointerPrefix) {
			return nil
		}
		oid, size, err := ParseLFSPointer(payload)
		if err != nil {
			return err
		}
		relative, _ := filepath.Rel(root, path)
		pointers = append(pointers, LFSPointer{Path: filepath.ToSlash(relative), OID: oid, Size: size})
		return nil
	})
	sort.Slice(pointers, func(i, j int) bool { return pointers[i].Path < pointers[j].Path })
	return pointers, err
}

// LFSMediaURL is the immutable GitHub media URL of one path at commit.
func LFSMediaURL(repository, commit, relative string) (string, error) {
	if !repositoryID.MatchString(repository) {
		return "", errors.New("repositório GitHub inválido")
	}
	if !commitID.MatchString(commit) {
		return "", errors.New("commit candidato inválido")
	}
	return "https://media.githubusercontent.com/media/" + repository + "/" + commit + "/" + quotePath(relative), nil
}

func verifyLFSObject(path string, size int64, oid string) error {
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() {
		return fmt.Errorf("objeto LFS ausente ou não regular: %s", path)
	}
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil {
		return err
	}
	if info.Size() != size || hex.EncodeToString(hash.Sum(nil)) != oid {
		return fmt.Errorf("objeto LFS divergente: %s", path)
	}
	return nil
}

// LFSFetch downloads one verified object to destination.
type LFSFetch func(ctx context.Context, url, destination string, size int64, oid string) error

// FetchLFSObject is the default LFSFetch.
func FetchLFSObject(ctx context.Context, url, destination string, size int64, oid string) error {
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return err
	}
	return download.Fetch(ctx, download.Artifact{URLs: []string{url}, Size: size, SHA256: oid}, destination, download.Options{Deadline: 30 * time.Minute})
}

// MaterializeLFS fills .git/lfs/objects and returns how many objects were new.
func MaterializeLFS(ctx context.Context, root, repository, commit string, fetch LFSFetch) (int, error) {
	pointers, err := LFSPointers(root)
	if err != nil {
		return 0, err
	}
	if len(pointers) == 0 {
		return 0, errors.New("nenhum ponteiro LFS foi encontrado em dist")
	}
	created := 0
	for _, pointer := range pointers {
		destination := filepath.Join(root, ".git", "lfs", "objects", pointer.OID[:2], pointer.OID[2:4], pointer.OID)
		if _, err := os.Lstat(destination); err == nil {
			if err := verifyLFSObject(destination, pointer.Size, pointer.OID); err != nil {
				return created, err
			}
			continue
		}
		address, err := LFSMediaURL(repository, commit, pointer.Path)
		if err != nil {
			return created, err
		}
		if err := fetch(ctx, address, destination, pointer.Size, pointer.OID); err != nil {
			return created, err
		}
		if err := verifyLFSObject(destination, pointer.Size, pointer.OID); err != nil {
			return created, err
		}
		created++
	}
	return created, nil
}
