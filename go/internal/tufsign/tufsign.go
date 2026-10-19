// Package tufsign is the offline signing ceremony for the public TUF
// repository (port of maintenance/tools/generate_trust_metadata.py and
// tuf_timestamp_renewal.py). Keys are the existing PKCS#8 Ed25519 PEMs in
// .tuf-production/keys/<role>-<n>.pem; root never changes here.
package tufsign

import (
	"crypto"
	"crypto/ed25519"
	"crypto/sha256"
	"crypto/x509"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/sigstore/sigstore/pkg/signature"
	"github.com/theupdateframework/go-tuf/v2/metadata"
)

// CatalogTarget is the single target the repository authorizes.
const CatalogTarget = "catalog/catalog.json"

// Expiry per role in the owner-only regime (ADR 0006 amendment).
var Expiry = map[string]time.Duration{
	metadata.TARGETS:   90 * 24 * time.Hour,
	metadata.SNAPSHOT:  90 * 24 * time.Hour,
	metadata.TIMESTAMP: 30 * 24 * time.Hour,
}

// LoadRoot parses the trust anchor.
func LoadRoot(path string) (*metadata.Metadata[metadata.RootType], []byte, error) {
	payload, err := os.ReadFile(path)
	if err != nil {
		return nil, nil, err
	}
	root, err := metadata.Root().FromBytes(payload)
	return root, payload, err
}

// LoadKey reads a PKCS#8 Ed25519 PEM and returns its signer and key id.
func LoadKey(path string) (signature.Signer, string, error) {
	payload, err := os.ReadFile(path)
	if err != nil {
		return nil, "", err
	}
	block, _ := pem.Decode(payload)
	if block == nil || block.Type != "PRIVATE KEY" {
		return nil, "", fmt.Errorf("chave privada PEM inválida: %s", path)
	}
	parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return nil, "", fmt.Errorf("chave privada inválida: %s: %w", path, err)
	}
	private, ok := parsed.(ed25519.PrivateKey)
	if !ok {
		return nil, "", fmt.Errorf("chave não é Ed25519: %s", path)
	}
	key, err := metadata.KeyFromPublicKey(private.Public())
	if err != nil {
		return nil, "", err
	}
	id, err := key.ID()
	if err != nil {
		return nil, "", err
	}
	signer, err := signature.LoadSigner(private, crypto.Hash(0))
	return signer, id, err
}

// RoleSigners loads <role>-1.pem.. and returns the signers whose key ids the
// root authorizes for role, enough to meet its threshold.
func RoleSigners(keyDir string, root *metadata.RootType, role string) ([]signature.Signer, error) {
	authorized := map[string]bool{}
	for _, id := range root.Roles[role].KeyIDs {
		authorized[id] = true
	}
	var signers []signature.Signer
	for index := 1; index <= len(root.Roles[role].KeyIDs); index++ {
		path := filepath.Join(keyDir, fmt.Sprintf("%s-%d.pem", role, index))
		if _, err := os.Stat(path); err != nil {
			continue
		}
		signer, id, err := LoadKey(path)
		if err != nil {
			return nil, err
		}
		if !authorized[id] {
			return nil, fmt.Errorf("%s não é uma chave %s da root", path, role)
		}
		signers = append(signers, signer)
		if len(signers) == root.Roles[role].Threshold {
			return signers, nil
		}
	}
	return nil, fmt.Errorf("chaves %s insuficientes para o threshold %d", role, root.Roles[role].Threshold)
}

type signable interface {
	Sign(signature.Signer) (*metadata.Signature, error)
	ToBytes(bool) ([]byte, error)
}

func sign(meta signable, signers []signature.Signer) ([]byte, error) {
	for _, signer := range signers {
		if _, err := meta.Sign(signer); err != nil {
			return nil, err
		}
	}
	return meta.ToBytes(false)
}

func metaFile(version int64, data []byte) *metadata.MetaFiles {
	digest := sha256.Sum256(data)
	file := metadata.MetaFile(version)
	file.Length = int64(len(data))
	file.Hashes = metadata.Hashes{"sha256": digest[:]}
	return file
}

func writeNew(path string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
	if err != nil {
		return err
	}
	_, err = file.Write(data)
	return errors.Join(err, file.Sync(), file.Close())
}

// Generate signs targets, snapshot and timestamp at version for catalog into
// a new repository directory (metadata/ + targets/catalog/<sha>.catalog.json).
func Generate(keyDir, rootPath, catalogPath, output string, version int64, now time.Time) error {
	if _, err := os.Stat(output); err == nil {
		return fmt.Errorf("repositório de saída já existe: %s", output)
	}
	// TUF serializes expiries at second precision; python-tuf rejects more.
	now = now.UTC().Truncate(time.Second)
	root, rootBytes, err := LoadRoot(rootPath)
	if err != nil {
		return err
	}
	catalog, err := os.ReadFile(catalogPath)
	if err != nil {
		return err
	}
	targets := metadata.Targets(now.Add(Expiry[metadata.TARGETS]))
	targets.Signed.Version = version
	target, err := metadata.TargetFile().FromBytes(CatalogTarget, catalog, "sha256")
	if err != nil {
		return err
	}
	targets.Signed.Targets[CatalogTarget] = target
	signers, err := RoleSigners(keyDir, &root.Signed, metadata.TARGETS)
	if err != nil {
		return err
	}
	targetsBytes, err := sign(targets, signers)
	if err != nil {
		return err
	}
	snapshot := metadata.Snapshot(now.Add(Expiry[metadata.SNAPSHOT]))
	snapshot.Signed.Version = version
	snapshot.Signed.Meta["targets.json"] = metaFile(version, targetsBytes)
	if signers, err = RoleSigners(keyDir, &root.Signed, metadata.SNAPSHOT); err != nil {
		return err
	}
	snapshotBytes, err := sign(snapshot, signers)
	if err != nil {
		return err
	}
	timestamp := metadata.Timestamp(now.Add(Expiry[metadata.TIMESTAMP]))
	timestamp.Signed.Version = version
	timestamp.Signed.Meta["snapshot.json"] = metaFile(version, snapshotBytes)
	if signers, err = RoleSigners(keyDir, &root.Signed, metadata.TIMESTAMP); err != nil {
		return err
	}
	timestampBytes, err := sign(timestamp, signers)
	if err != nil {
		return err
	}
	digest := sha256.Sum256(catalog)
	staging := output + ".partial"
	os.RemoveAll(staging)
	files := map[string][]byte{
		fmt.Sprintf("metadata/%d.root.json", root.Signed.Version):            rootBytes,
		fmt.Sprintf("metadata/%d.targets.json", version):                     targetsBytes,
		fmt.Sprintf("metadata/%d.snapshot.json", version):                    snapshotBytes,
		"metadata/timestamp.json":                                            timestampBytes,
		"targets/catalog/" + hex.EncodeToString(digest[:]) + ".catalog.json": catalog,
	}
	for relative, data := range files {
		if err := writeNew(filepath.Join(staging, filepath.FromSlash(relative)), data); err != nil {
			os.RemoveAll(staging)
			return err
		}
	}
	return os.Rename(staging, output)
}

// RenewTimestamp re-signs only timestamp.json (version N+1, lease from now,
// never earlier than the current expiry) and returns the new bytes.
func RenewTimestamp(current []byte, keyPath, rootPath string, lease time.Duration, now time.Time) ([]byte, error) {
	root, _, err := LoadRoot(rootPath)
	if err != nil {
		return nil, err
	}
	signer, id, err := LoadKey(keyPath)
	if err != nil {
		return nil, err
	}
	allowed := false
	for _, keyID := range root.Signed.Roles[metadata.TIMESTAMP].KeyIDs {
		allowed = allowed || keyID == id
	}
	if !allowed {
		return nil, errors.New("a chave fornecida não pertence à role timestamp; root/targets não são aceitas")
	}
	old, err := metadata.Timestamp().FromBytes(current)
	if err != nil {
		return nil, err
	}
	expires := now.Add(lease).UTC().Truncate(time.Second)
	if !expires.After(old.Signed.Expires) {
		expires = old.Signed.Expires.UTC().Truncate(time.Second).Add(time.Second)
	}
	renewed := metadata.Timestamp(expires)
	renewed.Signed.Version = old.Signed.Version + 1
	renewed.Signed.Meta = old.Signed.Meta
	return sign(renewed, []signature.Signer{signer})
}
