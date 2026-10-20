package setup

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"sort"
	"strings"
	"time"
)

// Address is one IP the server can be reached on.
type Address struct {
	IP        netip.Addr
	Interface string // empty for public addresses
}

// Network is what players can connect to.
type Network struct {
	Private     []Address
	PublicV4    netip.Addr // invalid when undetected
	PublicV6    netip.Addr
	PublicV4Err error
	PublicV6Err error
}

// traceURL answers with the caller's address ("ip=..."); it is the x86QW
// site, so detection talks to no third party.
const traceURL = "https://qw.x86.com.br/cdn-cgi/trace"

// PrivateAddresses lists the LAN addresses of the interfaces that are up,
// skipping loopback and link-local ones.
func PrivateAddresses() []Address {
	var found []Address
	interfaces, err := net.Interfaces()
	if err != nil {
		return nil
	}
	for _, iface := range interfaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		addresses, err := iface.Addrs()
		if err != nil {
			continue
		}
		for _, address := range addresses {
			prefix, err := netip.ParsePrefix(address.String())
			if err != nil {
				continue
			}
			ip := prefix.Addr()
			if ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsMulticast() {
				continue
			}
			if ip.IsPrivate() {
				found = append(found, Address{IP: ip, Interface: iface.Name})
			}
		}
	}
	sort.SliceStable(found, func(i, j int) bool { return found[i].IP.Is4() && !found[j].IP.Is4() })
	return found
}

// PublicAddress asks the trace endpoint over one address family ("tcp4" or
// "tcp6").
func PublicAddress(ctx context.Context, family string) (netip.Addr, error) {
	dialer := &net.Dialer{Timeout: 5 * time.Second}
	client := &http.Client{
		Timeout: 8 * time.Second,
		Transport: &http.Transport{DialContext: func(ctx context.Context, _, address string) (net.Conn, error) {
			return dialer.DialContext(ctx, family, address)
		}},
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, traceURL, nil)
	if err != nil {
		return netip.Addr{}, err
	}
	response, err := client.Do(request)
	if err != nil {
		return netip.Addr{}, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return netip.Addr{}, fmt.Errorf("HTTP %d", response.StatusCode)
	}
	scanner := bufio.NewScanner(io.LimitReader(response.Body, 4096))
	for scanner.Scan() {
		if value, ok := strings.CutPrefix(scanner.Text(), "ip="); ok {
			return netip.ParseAddr(strings.TrimSpace(value))
		}
	}
	return netip.Addr{}, fmt.Errorf("resposta sem endereço")
}

// DetectNetwork gathers the private addresses and both public ones.
func DetectNetwork(ctx context.Context) Network {
	network := Network{Private: PrivateAddresses()}
	type result struct {
		ip  netip.Addr
		err error
	}
	v4, v6 := make(chan result, 1), make(chan result, 1)
	go func() { ip, err := PublicAddress(ctx, "tcp4"); v4 <- result{ip, err} }()
	go func() { ip, err := PublicAddress(ctx, "tcp6"); v6 <- result{ip, err} }()
	r4, r6 := <-v4, <-v6
	network.PublicV4, network.PublicV4Err = r4.ip, r4.err
	network.PublicV6, network.PublicV6Err = r6.ip, r6.err
	return network
}

// DNSCheck is whether a hostname points at this machine.
type DNSCheck struct {
	Hostname string
	Resolved []netip.Addr
	Matches  bool // some resolved address is a detected public address
	Err      error
}

// CheckHostname resolves hostname and compares it with the public
// addresses.
func CheckHostname(ctx context.Context, hostname string, network Network) DNSCheck {
	check := DNSCheck{Hostname: hostname}
	hostname = strings.TrimSuffix(strings.TrimSpace(hostname), ".")
	if hostname == "" {
		check.Err = fmt.Errorf("hostname vazio")
		return check
	}
	addresses, err := net.DefaultResolver.LookupNetIP(ctx, "ip", hostname)
	if err != nil {
		check.Err = err
		return check
	}
	for _, address := range addresses {
		address = address.Unmap()
		check.Resolved = append(check.Resolved, address)
		if address == network.PublicV4 || (network.PublicV6.IsValid() && address == network.PublicV6) {
			check.Matches = true
		}
	}
	return check
}
