package services

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"net"
	"os"
	"path/filepath"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/receipt"
)

// fakeRuntime records component as installed in target with a stand-in
// for its runtime binary.
func fakeRuntime(t *testing.T, target, component string) {
	t.Helper()
	variant, err := install.HostVariant()
	if err != nil {
		t.Skip(err)
	}
	relative := runtimePaths[component][variant]
	binary := []byte(component + " stand-in")
	os.MkdirAll(filepath.Dir(filepath.Join(target, filepath.FromSlash(relative))), 0o755)
	os.WriteFile(filepath.Join(target, filepath.FromSlash(relative)), binary, 0o755)
	digest := sha256.Sum256(binary)
	inventory := receipt.MarshalInventory([]receipt.Entry{{Path: relative, SHA256: hex.EncodeToString(digest[:])}})
	inventoryDigest := sha256.Sum256(inventory)
	record := &receipt.Receipt{Keys: []string{"format", "component", "selection", "source", "inventory_sha256"},
		Values: map[string]string{"format": "1", "component": component, "selection": "1", "source": "test", "inventory_sha256": hex.EncodeToString(inventoryDigest[:])}}
	dir := filepath.Join(target, filepath.FromSlash(receipt.ComponentsDir), component)
	os.MkdirAll(dir, 0o700)
	os.WriteFile(filepath.Join(dir, "inventory"), inventory, 0o644)
	os.WriteFile(filepath.Join(dir, "receipt"), record.Marshal(), 0o644)
}

// A QWFWD bound to every interface holds its port: on Windows a loopback
// bind still succeeds beside it, so that is not the probe.
func TestQWFWDOnEveryInterfaceIsReady(t *testing.T) {
	target := t.TempDir()
	fakeRuntime(t, target, "qwfwd")
	os.WriteFile(filepath.Join(target, "qwfwd", "qwfwd.cfg"), nil, 0o644)
	held, err := net.ListenPacket("udp4", "0.0.0.0:0")
	if err != nil {
		t.Fatal(err)
	}
	defer held.Close()
	spec, err := ProxySpec(target, "0.0.0.0", held.LocalAddr().(*net.UDPAddr).Port)
	if err != nil {
		t.Fatal(err)
	}
	if err := waitUDPReadiness(context.Background(), func() bool { return false }, spec.Readiness); err != nil {
		t.Fatal(err)
	}
}
