package services

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"

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
// bind still succeeds beside it, so that is not the probe. A port nobody
// took is not ready.
func TestQWFWDOnEveryInterfaceIsReady(t *testing.T) {
	target := t.TempDir()
	fakeRuntime(t, target, "qwfwd")
	os.WriteFile(filepath.Join(target, "qwfwd", "qwfwd.cfg"), nil, 0o644)
	held, err := net.ListenPacket("udp4", "0.0.0.0:0")
	if err != nil {
		t.Fatal(err)
	}
	port := held.LocalAddr().(*net.UDPAddr).Port
	spec, err := ProxySpec(target, "0.0.0.0", port)
	if err != nil {
		t.Fatal(err)
	}
	alive := func() bool { return false }
	if err := waitUDPReadiness(context.Background(), alive, spec.Readiness, os.Getpid()); err != nil {
		t.Fatal(err)
	}
	held.Close()
	if err := waitUDPReadiness(context.Background(), alive, spec.Readiness, os.Getpid()); err == nil {
		t.Fatal("a port nobody holds was taken for ready")
	}
}

// The preflight sees a port another program holds on every interface, also
// when the server asks for one address only: on Windows a bind to that
// address would still succeed.
func TestPreflightSeesAPortTakenOnEveryInterface(t *testing.T) {
	udp, err := net.ListenPacket("udp4", "0.0.0.0:0")
	if err != nil {
		t.Fatal(err)
	}
	defer udp.Close()
	tcp, err := net.Listen("tcp4", "0.0.0.0:0")
	if err != nil {
		t.Fatal(err)
	}
	defer tcp.Close()
	udpPort, tcpPort := udp.LocalAddr().(*net.UDPAddr).Port, tcp.Addr().(*net.TCPAddr).Port
	for _, request := range []PortRequest{
		{"MVDSV", "127.0.0.1", udpPort, "udp"}, {"MVDSV", "0.0.0.0", udpPort, "udp"},
		{"QTV", "127.0.0.1", tcpPort, "tcp"}, {"QTV", "0.0.0.0", tcpPort, "tcp"},
	} {
		if err := PreflightPorts([]PortRequest{request}); err == nil {
			t.Errorf("%s %s:%d was taken for free", request.Kind, request.Address, request.Port)
		}
	}
	free, _ := net.ListenPacket("udp4", "127.0.0.1:0")
	freePort := free.LocalAddr().(*net.UDPAddr).Port
	free.Close()
	if err := PreflightPorts([]PortRequest{{"MVDSV", "0.0.0.0", freePort, "udp"}}); err != nil {
		t.Fatalf("a free port: %v", err)
	}
}

// QTV closes the connection of the readiness probe, which leaves the port
// in TIME_WAIT for up to a minute after it stops. QTV binds it again, so a
// restart right away finds it free.
func TestPreflightTakesAPortQTVJustLeft(t *testing.T) {
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := listener.Addr().(*net.TCPAddr).Port
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { io.WriteString(w, "ok") })}
	go server.Serve(listener)
	connection, err := net.Dial("tcp", Endpoint("127.0.0.1", port))
	if err != nil {
		t.Fatal(err)
	}
	connection.Write([]byte("GET /nowplaying/ HTTP/1.0\r\nHost: x86qw.local\r\n\r\n"))
	io.ReadAll(connection)
	connection.Close()
	time.Sleep(100 * time.Millisecond)
	server.Close()
	if err := PreflightPorts([]PortRequest{{"QTV", "127.0.0.1", port, "tcp"}}); err != nil {
		t.Fatal(err)
	}
}
