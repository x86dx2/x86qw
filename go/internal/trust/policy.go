// Package trust authenticates the public x86QW catalog through TUF.
//
// The embedded root must match the
// approved Ed25519 role policy before any network access, and the catalog is
// only returned after the full root → timestamp → snapshot → targets chain
// verifies.
package trust

import (
	_ "embed"
	"fmt"

	"github.com/theupdateframework/go-tuf/v2/metadata"
)

// RootJSON is the trust anchor shipped with the binary. It must stay
// byte-identical to maintenance/trust/root.json (enforced by a test).
//
//go:embed root.json
var RootJSON []byte

// rolePolicy is the approved (key count, threshold) per top-level role.
var rolePolicy = map[string]struct{ keys, threshold int }{
	metadata.ROOT:      {3, 2},
	metadata.TARGETS:   {3, 2},
	metadata.SNAPSHOT:  {2, 1},
	metadata.TIMESTAMP: {2, 1},
}

// Error is returned for every failure to establish catalog trust.
type Error struct{ msg string }

func (e *Error) Error() string { return e.msg }

func errorf(format string, args ...any) error {
	return &Error{msg: fmt.Sprintf(format, args...)}
}

// ValidateRootPolicy rejects a root that diverges from the approved policy:
// consistent snapshots, exactly the four top-level roles with their key
// counts and thresholds, Ed25519 only, no key shared between roles, no
// unused keys, and a root that satisfies its own threshold.
func ValidateRootPolicy(rootBytes []byte) error {
	if len(rootBytes) == 0 {
		return errorf("root TUF incorporada está ausente")
	}
	root, err := metadata.Root().FromBytes(rootBytes)
	if err != nil {
		return errorf("root TUF incorporada é inválida: %v", err)
	}
	signed := root.Signed
	if !signed.ConsistentSnapshot {
		return errorf("root TUF deve habilitar consistent snapshots")
	}
	if len(signed.Roles) != len(rolePolicy) {
		return errorf("root TUF declara roles fora da política aprovada")
	}
	usedKeyIDs := map[string]bool{}
	usedMaterial := map[string]bool{}
	for name, policy := range rolePolicy {
		role, ok := signed.Roles[name]
		if !ok {
			return errorf("root TUF declara roles fora da política aprovada")
		}
		if len(role.KeyIDs) != policy.keys || role.Threshold != policy.threshold {
			return errorf("role %s diverge da política %d-de-%d", name, policy.threshold, policy.keys)
		}
		seen := map[string]bool{}
		for _, keyID := range role.KeyIDs {
			if seen[keyID] {
				return errorf("role %s repete key IDs", name)
			}
			seen[keyID] = true
			if usedKeyIDs[keyID] {
				return errorf("uma chave TUF não pode pertencer a mais de uma role")
			}
			usedKeyIDs[keyID] = true
			key, ok := signed.Keys[keyID]
			if !ok {
				return errorf("role %s referencia chave ausente", name)
			}
			if key.Type != metadata.KeyTypeEd25519 || key.Scheme != metadata.KeySchemeEd25519 {
				return errorf("todas as chaves TUF devem usar Ed25519")
			}
			material := key.Value.PublicKey
			if usedMaterial[material] {
				return errorf("material público de chave TUF duplicado")
			}
			usedMaterial[material] = true
		}
	}
	if len(signed.Keys) != len(usedKeyIDs) {
		return errorf("root TUF contém chaves sem role aprovada")
	}
	if err := root.VerifyDelegate(metadata.ROOT, root); err != nil {
		return errorf("root TUF não satisfaz seu próprio threshold: %v", err)
	}
	return nil
}
