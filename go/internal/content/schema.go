package content

import (
	"errors"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/content/pyjson"
	"github.com/x86dx2/x86qw/go/internal/semver"
)

// errContract is a contract violation; callers wrap it with their own
// message.
var errContract = errors.New("invalid contract")

func isDigits(value string) bool {
	if value == "" {
		return false
	}
	for _, c := range value {
		if c < '0' || c > '9' {
			return false
		}
	}
	return true
}

// validateCatalogVersions checks a catalog's format and version fields,
// accepting the legacy ones only with allowLegacy.
func validateCatalogVersions(document *Object, allowLegacy bool) error {
	has := func(field string) bool {
		return document.Has(field) || document.Has(strings.Replace(field, "_version", "_schema_version", 1))
	}
	for _, field := range []string{"state_version", "receipt_version"} {
		if has(field) {
			return errContract
		}
	}
	const selected, alias = "catalog_version", "catalog_schema_version"
	explicit := has(selected)
	if document.Has(selected) && document.Has(alias) && !pyjson.Eq(document.Value(selected), document.Value(alias)) {
		return errContract
	}
	var value any
	if document.Has(selected) {
		value = document.Value(selected)
	} else {
		value = document.Value(alias)
	}
	if value == nil && allowLegacy && !explicit {
		if document.Has("format") {
			value = document.Value("format")
		} else {
			value = pyjson.Number("1")
		}
		if text, ok := value.(string); ok && isDigits(text) {
			value = pyjson.Number(text)
		}
	} else if value != nil && allowLegacy && document.Has("format") {
		legacy := document.Value("format")
		if text, ok := legacy.(string); ok && isDigits(text) {
			legacy = pyjson.Number(text)
		} else if !pyjson.IsExactInt(legacy) {
			return errContract
		}
		if pyjson.IsExactInt(legacy) && !pyjson.Eq(legacy, value) {
			return errContract
		}
	}
	if value == nil {
		return errContract
	}
	if explicit && !document.Has("min_cli_version") {
		return errContract
	}
	if document.Has("max_cli_version") && !document.Has("min_cli_version") {
		return errContract
	}
	if !document.Has("min_cli_version") && !allowLegacy {
		return errContract
	}
	// ContractVersions.__post_init__: catalog_version must be exactly int 1.
	if !pyjson.IsExactInt(value) || !pyjson.EqInt(value, 1) {
		return errContract
	}
	minimum := any("0.7.0")
	if document.Has("min_cli_version") {
		minimum = document.Value("min_cli_version")
	}
	minText, ok := minimum.(string)
	if !ok {
		return errContract
	}
	low, err := semver.Parse(minText)
	if err != nil {
		return errContract
	}
	if maximum := document.Value("max_cli_version"); maximum != nil {
		maxText, ok := maximum.(string)
		if !ok {
			return errContract
		}
		high, err := semver.Parse(maxText)
		if err != nil || semver.Compare(high, low) < 0 {
			return errContract
		}
	}
	return nil
}
