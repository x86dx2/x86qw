//go:build !windows

package services

import (
	"context"
	"io"
	"net"
	"syscall"
	"time"
)

// portTaken probes with a bind of its own. UDP binds without SO_REUSEADDR:
// with it, macOS lets a socket share a port another holds on every
// interface. TCP binds as QTV does, so a port left in TIME_WAIT is free,
// and dials first, which finds a listener on every interface.
func portTaken(kind, address string, port int) bool {
	var closer io.Closer
	var err error
	if kind == "tcp" {
		if connection, err := net.DialTimeout("tcp", Endpoint(LocalServiceAddress(address), port), 200*time.Millisecond); err == nil {
			connection.Close()
			return true
		}
		closer, err = net.Listen(network("tcp", address), Endpoint(address, port))
	} else {
		config := net.ListenConfig{Control: func(_, _ string, raw syscall.RawConn) error {
			var err error
			raw.Control(func(fd uintptr) { err = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, syscall.SO_REUSEADDR, 0) })
			return err
		}}
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
