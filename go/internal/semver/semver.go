// Package semver implements strict SemVer 2.0.0 parsing and precedence: no
// "v" prefix, no leading zeroes, build metadata ignored for ordering.
package semver

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

const (
	numeric      = `(?:0|[1-9][0-9]*)`
	alphanumeric = `(?:[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)`
	prerelease   = `(?:` + numeric + `|` + alphanumeric + `)`
	build        = `[0-9A-Za-z-]+`
)

var pattern = regexp.MustCompile(`^(` + numeric + `)\.(` + numeric + `)\.(` + numeric + `)` +
	`(?:-(` + prerelease + `(?:\.` + prerelease + `)*))?` +
	`(?:\+(` + build + `(?:\.` + build + `)*))?$`)

// Version is a parsed SemVer value.
type Version struct {
	Major, Minor, Patch uint64
	Prerelease          []string
	Build               []string
}

// Parse parses one strict SemVer string.
func Parse(value string) (Version, error) {
	match := pattern.FindStringSubmatch(value)
	if match == nil {
		return Version{}, fmt.Errorf("SemVer inválido: %q", value)
	}
	var version Version
	var err error
	for index, target := range []*uint64{&version.Major, &version.Minor, &version.Patch} {
		if *target, err = strconv.ParseUint(match[index+1], 10, 64); err != nil {
			return Version{}, fmt.Errorf("SemVer inválido: %q", value)
		}
	}
	if match[4] != "" {
		version.Prerelease = strings.Split(match[4], ".")
	}
	if match[5] != "" {
		version.Build = strings.Split(match[5], ".")
	}
	return version, nil
}

// Valid reports whether value is strict SemVer.
func Valid(value string) bool {
	_, err := Parse(value)
	return err == nil
}

func (v Version) String() string {
	result := fmt.Sprintf("%d.%d.%d", v.Major, v.Minor, v.Patch)
	if len(v.Prerelease) > 0 {
		result += "-" + strings.Join(v.Prerelease, ".")
	}
	if len(v.Build) > 0 {
		result += "+" + strings.Join(v.Build, ".")
	}
	return result
}

// Compare returns -1, 0 or 1 by SemVer precedence.
func Compare(a, b Version) int {
	for _, pair := range [][2]uint64{{a.Major, b.Major}, {a.Minor, b.Minor}, {a.Patch, b.Patch}} {
		if pair[0] != pair[1] {
			if pair[0] < pair[1] {
				return -1
			}
			return 1
		}
	}
	switch {
	case len(a.Prerelease) == 0 && len(b.Prerelease) == 0:
		return 0
	case len(a.Prerelease) == 0:
		return 1
	case len(b.Prerelease) == 0:
		return -1
	}
	for index := 0; index < len(a.Prerelease) && index < len(b.Prerelease); index++ {
		if result := compareIdentifier(a.Prerelease[index], b.Prerelease[index]); result != 0 {
			return result
		}
	}
	switch {
	case len(a.Prerelease) < len(b.Prerelease):
		return -1
	case len(a.Prerelease) > len(b.Prerelease):
		return 1
	}
	return 0
}

func compareIdentifier(a, b string) int {
	aNumber, aErr := strconv.ParseUint(a, 10, 64)
	bNumber, bErr := strconv.ParseUint(b, 10, 64)
	aNumeric, bNumeric := aErr == nil && isDigits(a), bErr == nil && isDigits(b)
	switch {
	case aNumeric && bNumeric:
		switch {
		case aNumber < bNumber:
			return -1
		case aNumber > bNumber:
			return 1
		}
		return 0
	case aNumeric:
		return -1
	case bNumeric:
		return 1
	}
	return strings.Compare(a, b)
}

func isDigits(value string) bool {
	for _, r := range value {
		if r < '0' || r > '9' {
			return false
		}
	}
	return value != ""
}

// CompareStrings parses and compares two versions.
func CompareStrings(a, b string) (int, error) {
	left, err := Parse(a)
	if err != nil {
		return 0, err
	}
	right, err := Parse(b)
	if err != nil {
		return 0, err
	}
	return Compare(left, right), nil
}
