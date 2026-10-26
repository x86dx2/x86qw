//go:build !windows

package services

import (
	"context"
	"io"
	"net"
	"syscall"
)

// portTaken probes with a bind of its own, without SO_REUSEADDR: with it,
// macOS lets a listener share a port another holds on every interface.
func portTaken(kind, address string, port int) bool {
	config := net.ListenConfig{Control: func(_, _ string, raw syscall.RawConn) error {
		var err error
		raw.Control(func(fd uintptr) { err = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, syscall.SO_REUSEADDR, 0) })
		return err
	}}
	var closer io.Closer
	var err error
	if kind == "tcp" {
		closer, err = config.Listen(context.Background(), network("tcp", address), Endpoint(address, port))
	} else {
		closer, err = config.ListenPacket(context.Background(), network("udp", address), Endpoint(address, port))
	}
	if err != nil {
		return true
	}
	closer.Close()
	return false
}

// udpHeldBy reports whether the port is taken. A bind cannot tell by whom;
// on loopback it still sees a socket bound to every interface, and asks no
// firewall about x86QW.
func udpHeldBy(address string, port, pid int) bool {
	return portTaken("udp", LocalServiceAddress(address), port)
}
