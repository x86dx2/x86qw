//go:build windows

package services

import (
	"net/netip"
	"testing"
)

// Each family keeps its own ports: MVDSV on IPv4 every interface does not
// meet a program on IPv6 loopback, and an IPv4 address written as IPv6
// still meets its owner. Every IPv6 interface may hold IPv4 too.
func TestPortsMeetOnlyWhereTheyBind(t *testing.T) {
	owners := []portOwner{
		{netip.MustParseAddr("::1"), 27500, 1},
		{netip.MustParseAddr("127.0.0.1"), 27501, 1},
		{netip.MustParseAddr("::"), 27502, 1},
		{netip.MustParseAddr("0.0.0.0"), 27503, 1},
	}
	for _, check := range []struct {
		want string
		port int
		held bool
	}{
		{"0.0.0.0", 27500, false},
		{"::", 27500, true},
		{"::ffff:127.0.0.1", 27501, true},
		{"0.0.0.0", 27501, true},
		{"192.168.0.2", 27502, true},
		{"fe80::1%eth0", 27503, false},
		{"127.0.0.1", 27503, true},
	} {
		if got := heldWhere(owners, netip.MustParseAddr(check.want), check.port); got != check.held {
			t.Errorf("%s:%d held=%v, want %v", check.want, check.port, got, check.held)
		}
	}
}
