//go:build windows

package services

import (
	"encoding/binary"
	"io"
	"net"
	"net/netip"
	"unsafe"

	"golang.org/x/sys/windows"
)

// The ports are read from the system's tables instead of probed with a bind:
// a bind of x86qw.exe on every interface makes the firewall ask about x86QW
// itself, and on Windows a bind to one address succeeds beside a socket
// bound to every interface.

var (
	getExtendedTCPTable = windows.NewLazySystemDLL("iphlpapi.dll").NewProc("GetExtendedTcpTable")
	getExtendedUDPTable = windows.NewLazySystemDLL("iphlpapi.dll").NewProc("GetExtendedUdpTable")
)

// portOwner is one bound local endpoint and its process.
type portOwner struct {
	address netip.Addr
	port    int
	pid     int
}

// portOwners lists the TCP listeners or the UDP endpoints, IPv4 and IPv6.
func portOwners(kind string) ([]portOwner, error) {
	const tcpListeners, udpEndpoints = 3, 1 // TCP_TABLE_OWNER_PID_LISTENER, UDP_TABLE_OWNER_PID
	var owners []portOwner
	for _, family := range []uint32{windows.AF_INET, windows.AF_INET6} {
		proc, class := getExtendedUDPTable, uintptr(udpEndpoints)
		// Row size and the offsets of the address, port and pid in a row.
		size, addressAt, portAt, pidAt := 12, 0, 4, 8
		if family == windows.AF_INET6 {
			size, addressAt, portAt, pidAt = 28, 0, 20, 24
		}
		if kind == "tcp" {
			proc, class = getExtendedTCPTable, tcpListeners
			size, addressAt, portAt, pidAt = 24, 4, 8, 20
			if family == windows.AF_INET6 {
				size, addressAt, portAt, pidAt = 56, 0, 20, 52
			}
		}
		var length uint32
		var table []byte
		for {
			var buffer uintptr
			if len(table) > 0 {
				buffer = uintptr(unsafe.Pointer(&table[0]))
			}
			result, _, _ := proc.Call(buffer, uintptr(unsafe.Pointer(&length)), 0, uintptr(family), class, 0)
			if result == uintptr(windows.ERROR_INSUFFICIENT_BUFFER) {
				table = make([]byte, length)
				continue
			}
			if result != 0 {
				return nil, windows.Errno(result)
			}
			break
		}
		if len(table) < 4 {
			continue
		}
		count := int(binary.LittleEndian.Uint32(table))
		for index := range count {
			row := table[4+index*size:]
			if len(row) < size {
				break
			}
			var address netip.Addr
			if family == windows.AF_INET {
				address = netip.AddrFrom4([4]byte(row[addressAt : addressAt+4]))
			} else {
				address = netip.AddrFrom16([16]byte(row[addressAt : addressAt+16]))
			}
			owners = append(owners, portOwner{address, int(binary.BigEndian.Uint16(row[portAt:])), int(binary.LittleEndian.Uint32(row[pidAt:]))})
		}
	}
	return owners, nil
}

// portTaken reports whether a socket already holds port where address
// would bind it: the same address, or either one bound to every interface.
func portTaken(kind, address string, port int) bool {
	want, err := netip.ParseAddr(address)
	owners, tableErr := portOwners(kind)
	if err != nil || tableErr != nil {
		// Without the table, a bind of its own still answers.
		var closer io.Closer
		if kind == "tcp" {
			closer, err = net.Listen(network("tcp", address), Endpoint(address, port))
		} else {
			closer, err = net.ListenPacket(network("udp", address), Endpoint(address, port))
		}
		if err != nil {
			return true
		}
		closer.Close()
		return false
	}
	return heldWhere(owners, want, port)
}

// heldWhere reports whether an owner holds port where want would bind it:
// the same address, or either one on every interface of its family. A
// socket on every IPv6 interface may take IPv4 too (dual stack).
func heldWhere(owners []portOwner, want netip.Addr, port int) bool {
	want = want.Unmap().WithZone("")
	for _, owner := range owners {
		same := owner.address.Is4() == want.Is4()
		if owner.port == port && (same && (owner.address.IsUnspecified() || want.IsUnspecified() || owner.address == want) ||
			owner.address == netip.IPv6Unspecified()) {
			return true
		}
	}
	return false
}

// udpHeldBy reports whether process pid holds the UDP port.
func udpHeldBy(address string, port, pid int) bool {
	owners, err := portOwners("udp")
	if err != nil {
		return false
	}
	for _, owner := range owners {
		if owner.port == port && owner.pid == pid {
			return true
		}
	}
	return false
}
