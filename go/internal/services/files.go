package services

import (
	"crypto/sha256"
	"encoding/hex"
	"io"
	"os"
)

// MaterializedFile is a file exposed for one session.
type MaterializedFile struct {
	Path             string
	ExpectedHash     string
	Origin           string
	CreatedBySession bool
	Existed          bool
	Identity         [2]int64
	Size             int64
}

// MaterializedDirectory is a directory created for one session.
type MaterializedDirectory struct {
	Path     string
	Identity [2]int64
}

func fileSHA256(path string) (string, int64, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", 0, err
	}
	defer file.Close()
	hash := sha256.New()
	size, err := io.Copy(hash, file)
	if err != nil {
		return "", 0, err
	}
	return hex.EncodeToString(hash.Sum(nil)), size, nil
}

// cleanupSensitive removes path only if it is still the recorded inode.
func cleanupSensitive(path string, identity [2]int64) bool {
	info, err := os.Lstat(path)
	if os.IsNotExist(err) {
		return true
	}
	if err != nil || !info.Mode().IsRegular() || identityAt(path, info) != identity {
		return false
	}
	if os.Remove(path) != nil {
		return false
	}
	_, err = os.Lstat(path)
	return os.IsNotExist(err)
}

// cleanupMaterializedFile removes an unchanged session file (same inode,
// size and hash); a modified one is preserved.
func cleanupMaterializedFile(file *MaterializedFile) bool {
	if !file.CreatedBySession {
		return true
	}
	info, err := os.Lstat(file.Path)
	if os.IsNotExist(err) {
		return true
	}
	if err != nil || !info.Mode().IsRegular() || identityAt(file.Path, info) != file.Identity || info.Size() != file.Size {
		return false
	}
	digest, _, err := fileSHA256(file.Path)
	if err != nil || digest != file.ExpectedHash {
		return false
	}
	return os.Remove(file.Path) == nil
}

// cleanupDirectory removes an empty directory that is still the recorded one.
func cleanupDirectory(path string, identity [2]int64) bool {
	info, err := os.Lstat(path)
	if os.IsNotExist(err) {
		return true
	}
	if err != nil || !info.IsDir() || identityAt(path, info) != identity {
		return false
	}
	return os.Remove(path) == nil
}
