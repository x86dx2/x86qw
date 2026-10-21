package content

import (
	"errors"
	"fmt"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
	"golang.org/x/text/unicode/norm"
)

// splitURL is a URL split into its five parts.
type splitURL struct {
	Scheme, Netloc, Path, Query, Fragment string
}

const schemeChars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-."

// urlsplit splits a URL already free of control characters into scheme,
// netloc, path, query and fragment.
func urlsplit(url string) (splitURL, error) {
	var result splitURL
	if index := strings.Index(url, ":"); index > 0 && url[0] < utf8.RuneSelf && isASCIIAlpha(url[0]) {
		valid := true
		for _, c := range url[:index] {
			if !strings.ContainsRune(schemeChars, c) {
				valid = false
				break
			}
		}
		if valid {
			result.Scheme, url = strings.ToLower(url[:index]), url[index+1:]
		}
	}
	if strings.HasPrefix(url, "//") {
		rest := url[2:]
		end := len(rest)
		if index := strings.IndexAny(rest, "/?#"); index >= 0 {
			end = index
		}
		result.Netloc, url = rest[:end], rest[end:]
		open, closed := strings.Contains(result.Netloc, "["), strings.Contains(result.Netloc, "]")
		if open != closed {
			return result, errors.New("Invalid IPv6 URL")
		}
		if open {
			_, bracketed, _ := strings.Cut(result.Netloc, "[")
			host, _, _ := strings.Cut(bracketed, "]")
			if !validBracketedHost(host) {
				return result, errors.New("Invalid IPv6 URL")
			}
		}
	}
	if before, fragment, ok := strings.Cut(url, "#"); ok {
		url, result.Fragment = before, fragment
	}
	if before, query, ok := strings.Cut(url, "?"); ok {
		url, result.Query = before, query
	}
	result.Path = url
	if err := checkNetloc(result.Netloc); err != nil {
		return result, err
	}
	return result, nil
}

func isASCIIAlpha(c byte) bool { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') }

func validBracketedHost(host string) bool {
	if strings.HasPrefix(host, "v") {
		// IPvFuture: v<hex>.<chars>
		version, rest, ok := strings.Cut(host[1:], ".")
		if !ok || version == "" || rest == "" {
			return false
		}
		for _, c := range version {
			if !strings.ContainsRune("0123456789abcdefABCDEF", c) {
				return false
			}
		}
		return true
	}
	address, _, _ := strings.Cut(host, "%")
	return parseIPv6(address)
}

func parseIPv6(address string) bool {
	if address == "" || strings.Count(address, "::") > 1 {
		return false
	}
	groups := strings.Split(address, ":")
	if strings.Contains(address, ".") {
		last := groups[len(groups)-1]
		octets := strings.Split(last, ".")
		if len(octets) != 4 {
			return false
		}
		for _, octet := range octets {
			value, err := strconv.Atoi(octet)
			if err != nil || value > 255 || (len(octet) > 1 && octet[0] == '0') {
				return false
			}
		}
		groups = append(groups[:len(groups)-1], "0", "0")
	}
	compressed := strings.Contains(address, "::")
	count := 0
	for index, group := range groups {
		if group == "" {
			if compressed && (index == 0 || index == len(groups)-1 || groups[index-1] == "" || (index+1 < len(groups) && groups[index+1] == "")) {
				continue
			}
			return false
		}
		if len(group) > 4 {
			return false
		}
		if _, err := strconv.ParseUint(group, 16, 16); err != nil {
			return false
		}
		count++
	}
	if compressed {
		return count < 8
	}
	return count == 8
}

func checkNetloc(netloc string) error {
	ascii := true
	for i := 0; i < len(netloc); i++ {
		if netloc[i] >= utf8.RuneSelf {
			ascii = false
			break
		}
	}
	if ascii {
		return nil
	}
	n := strings.NewReplacer("@", "", ":", "", "#", "", "?", "").Replace(netloc)
	normalized := norm.NFKC.String(n)
	if normalized == n {
		return nil
	}
	for _, c := range "/?#@:" {
		if strings.ContainsRune(normalized, c) {
			return fmt.Errorf("netloc '%s' contains invalid characters under NFKC normalization", netloc)
		}
	}
	return nil
}

func (u splitURL) hostinfo() (userinfo string, hasUser bool, host string, port string, hasPort bool) {
	userinfo, host, hasUser = cutLast(u.Netloc, "@")
	if !hasUser {
		host, userinfo = u.Netloc, ""
	}
	if _, bracketed, ok := strings.Cut(host, "["); ok {
		hostname, after, _ := strings.Cut(bracketed, "]")
		_, port, _ = strings.Cut(after, ":")
		return userinfo, hasUser, hostname, port, port != ""
	}
	hostname, port, _ := strings.Cut(host, ":")
	return userinfo, hasUser, hostname, port, port != ""
}

func cutLast(value, separator string) (string, string, bool) {
	index := strings.LastIndex(value, separator)
	if index < 0 {
		return "", value, false
	}
	return value[:index], value[index+len(separator):], true
}

// Hostname is the lowercased host without user info or port, "" when
// there is none.
func (u splitURL) Hostname() string {
	_, _, host, _, _ := u.hostinfo()
	return strings.ToLower(host)
}

func (u splitURL) port() error {
	_, _, _, port, hasPort := u.hostinfo()
	if !hasPort {
		return nil
	}
	for _, c := range port {
		if c < '0' || c > '9' {
			return fmt.Errorf("Port could not be cast to integer value as %s", pyRepr(port))
		}
	}
	value, err := strconv.Atoi(port)
	if err != nil || value > 65535 {
		return errors.New("Port out of range 0-65535")
	}
	return nil
}

// policyError is a URL the download policy refuses.
type policyError struct{ msg string }

func (e *policyError) Error() string { return e.msg }

// validateHTTPSURL checks a URL against the download policy: HTTPS, a host,
// no credentials, query, fragment, spaces or control characters.
func validateHTTPSURL(value any, label string) (splitURL, error) {
	url, ok := value.(string)
	if !ok || url == "" {
		return splitURL{}, &policyError{label + " is not a valid URL."}
	}
	for _, r := range url {
		if r <= 32 || r == 127 {
			return splitURL{}, &policyError{label + " contains a space or control character."}
		}
	}
	parsed, err := urlsplit(url)
	if err == nil {
		err = parsed.port()
	}
	if err != nil {
		return splitURL{}, &policyError{label + " is not a valid URL."}
	}
	_, hasUser, _, _, _ := parsed.hostinfo()
	if Casefold(parsed.Scheme) != "https" || parsed.Hostname() == "" || hasUser || parsed.Fragment != "" {
		return splitURL{}, &policyError{label + " must be an absolute HTTPS URL."}
	}
	return parsed, nil
}

// unquote decodes percent escapes as UTF-8, replacing invalid sequences.
func unquote(value string) string {
	if !strings.Contains(value, "%") {
		return value
	}
	var raw []byte
	for index := 0; index < len(value); index++ {
		if value[index] == '%' && index+2 < len(value) {
			if decoded, err := strconv.ParseUint(value[index+1:index+3], 16, 8); err == nil {
				raw = append(raw, byte(decoded))
				index += 2
				continue
			}
		}
		raw = append(raw, value[index])
	}
	return strings.ToValidUTF8(string(raw), "�")
}

// safeURLForLog keeps only a URL's scheme and host, for logs.
func safeURLForLog(value string) string {
	for _, r := range value {
		if r <= 32 || r == 127 {
			return "<invalid HTTPS URL>"
		}
	}
	parsed, err := urlsplit(value)
	if err == nil {
		err = parsed.port()
	}
	if err != nil || Casefold(parsed.Scheme) != "https" || parsed.Hostname() == "" {
		return "<invalid HTTPS URL>"
	}
	_, _, _, port, hasPort := parsed.hostinfo()
	host := parsed.Hostname()
	if strings.Contains(host, ":") {
		host = "[" + host + "]"
	}
	if hasPort {
		if value, _ := strconv.Atoi(port); value != 443 {
			host += ":" + strconv.Itoa(value)
		}
	}
	return "https://" + host + "/<redacted>"
}

func reprScalar(value any) string {
	switch value.(type) {
	case []any:
		return "[...]"
	case *pyjson.Object:
		return "{...}"
	}
	return pyjson.Repr(value)
}
