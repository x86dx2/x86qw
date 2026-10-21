// Package services runs the dedicated MVDSV, QTV and QWFWD stack: option
// validation, presets, the private session journal, ephemeral server
// configs, PK3 materialization for MVDSV, process supervision and
// status/stop.
package services

import (
	"fmt"
	"net/netip"
	"strconv"
	"strings"
	"unicode"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
)

// Error is a services failure (exit 1).
type Error struct{ msg string }

func (e *Error) Error() string { return e.msg }

func errorf(format string, args ...any) error { return &Error{msg: fmt.Sprintf(format, args...)} }

// UsageError is a mistake in the command line (exit 2).
type UsageError = cliargs.UsageError

var usagef = cliargs.Usagef

// IsUsage reports whether err is a usage error.
func IsUsage(err error) bool { return cliargs.IsUsage(err) }

// BindAddress normalizes an IPv4/IPv6 literal like ipaddress.ip_address.
func BindAddress(value string) (string, error) {
	address, err := netip.ParseAddr(value)
	if err != nil {
		return "", usagef("use um endereço IPv4 ou IPv6 literal")
	}
	return address.String(), nil
}

// SafeText refuses text that is empty, longer than maximum, or carries
// quotes, backslashes, semicolons or line breaks.
func SafeText(value, label string, maximum int) (string, error) {
	invalid := value == "" || len([]rune(value)) > maximum || strings.ContainsAny(value, "\\\";\r\n")
	for _, r := range value {
		if r < 32 || r == 127 {
			invalid = true
		}
	}
	if invalid {
		return "", errorf("%s contém caracteres inválidos ou excede %d caracteres.", label, maximum)
	}
	return value, nil
}

// ParseNetworkEndpoint validates host:port or [IPv6]:port.
func ParseNetworkEndpoint(value string) (string, error) {
	bad := value == "" || len(value) > 253 || strings.ContainsAny(value, "\\\";")
	for _, r := range value {
		if unicode.IsSpace(r) || r < 32 {
			bad = true
		}
	}
	if bad {
		return "", usagef("use host:porta ou [IPv6]:porta")
	}
	var host, portText, normalized string
	if strings.HasPrefix(value, "[") {
		closing := strings.Index(value, "]")
		if closing < 0 || closing+1 >= len(value) || value[closing+1] != ':' {
			return "", usagef("use [IPv6]:porta")
		}
		host, portText = value[1:closing], value[closing+2:]
		address, err := netip.ParseAddr(host)
		if err != nil || !address.Is6() {
			return "", usagef("endereço IPv6 inválido")
		}
		normalized = "[" + address.String() + "]"
	} else {
		if strings.Count(value, ":") != 1 {
			return "", usagef("IPv6 deve usar [IPv6]:porta")
		}
		host, portText, _ = strings.Cut(value, ":")
		if host == "" {
			return "", usagef("host ausente")
		}
		if address, err := netip.ParseAddr(host); err == nil && address.Is4() {
			normalized = address.String()
		} else {
			if len(host) > 253 || strings.HasSuffix(host, ".") {
				return "", usagef("hostname inválido")
			}
			for _, label := range strings.Split(host, ".") {
				if label == "" || len(label) > 63 || strings.HasPrefix(label, "-") || strings.HasSuffix(label, "-") {
					return "", usagef("hostname inválido")
				}
				for _, r := range label {
					if r > 127 || !(unicode.IsLetter(r) || unicode.IsDigit(r) || r == '-') {
						return "", usagef("hostname inválido")
					}
				}
			}
			normalized = strings.ToLower(host)
		}
	}
	port, err := strconv.Atoi(strings.TrimSpace(portText))
	if err != nil {
		return "", usagef("porta inválida")
	}
	if port < 1 || port > 65535 {
		return "", usagef("porta deve estar entre 1 e 65535")
	}
	return fmt.Sprintf("%s:%d", normalized, port), nil
}

// Endpoint renders address:port with IPv6 brackets.
func Endpoint(address string, port int) string {
	if strings.Contains(address, ":") {
		return fmt.Sprintf("[%s]:%d", address, port)
	}
	return fmt.Sprintf("%s:%d", address, port)
}

// LocalServiceAddress maps wildcard binds to loopback.
func LocalServiceAddress(address string) string {
	switch address {
	case "0.0.0.0":
		return "127.0.0.1"
	case "::":
		return "::1"
	}
	return address
}

// IsExternalBind reports a wildcard or non-loopback bind.
func IsExternalBind(address string) bool {
	parsed, err := netip.ParseAddr(address)
	if err != nil {
		return true
	}
	return parsed.IsUnspecified() || !parsed.IsLoopback()
}

func q(value string) string { return `"` + value + `"` }
