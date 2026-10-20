// Package pyjson decodes and encodes JSON exactly like CPython's json module
// as used by the maintenance tooling: objects keep insertion order, and
// Encode reproduces json.dumps(value, ensure_ascii=False, indent=2) byte for
// byte, optionally with sort_keys=True.
package pyjson

import (
	"bytes"
	"errors"
	"fmt"
	"math"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

// Number is a JSON number kept as its source literal.
type Number string

// IsInt reports whether the literal decodes to a Python int.
func (n Number) IsInt() bool { return !strings.ContainsAny(string(n), ".eE") && !n.special() }

func (n Number) special() bool {
	return n == "NaN" || n == "Infinity" || n == "-Infinity"
}

// Int returns the integer value (ok=false for floats or overflow).
func (n Number) Int() (int64, bool) {
	if !n.IsInt() {
		return 0, false
	}
	value, err := strconv.ParseInt(string(n), 10, 64)
	return value, err == nil
}

// Rat returns the exact numeric value (nil for NaN/Infinity).
func (n Number) Rat() *big.Rat {
	if n.special() {
		return nil
	}
	value, ok := new(big.Rat).SetString(string(n))
	if !ok {
		return nil
	}
	return value
}

// Float returns the Python float value.
func (n Number) Float() float64 {
	switch n {
	case "NaN":
		return math.NaN()
	case "Infinity":
		return math.Inf(1)
	case "-Infinity":
		return math.Inf(-1)
	}
	value, _ := strconv.ParseFloat(string(n), 64)
	return value
}

// Object is a JSON object that preserves key insertion order.
type Object struct {
	keys   []string
	values map[string]any
}

// NewObject returns an empty object.
func NewObject() *Object { return &Object{values: map[string]any{}} }

// Get returns the value under key.
func (o *Object) Get(key string) (any, bool) {
	value, ok := o.values[key]
	return value, ok
}

// Value returns the value under key or nil (Python dict.get).
func (o *Object) Value(key string) any { return o.values[key] }

// Has reports whether key exists.
func (o *Object) Has(key string) bool {
	_, ok := o.values[key]
	return ok
}

// Set inserts or replaces key, keeping the first insertion position.
func (o *Object) Set(key string, value any) {
	if _, ok := o.values[key]; !ok {
		o.keys = append(o.keys, key)
	}
	o.values[key] = value
}

// Delete removes key.
func (o *Object) Delete(key string) {
	if _, ok := o.values[key]; !ok {
		return
	}
	delete(o.values, key)
	for index, existing := range o.keys {
		if existing == key {
			o.keys = append(o.keys[:index:index], o.keys[index+1:]...)
			break
		}
	}
}

// Keys returns the keys in insertion order.
func (o *Object) Keys() []string { return append([]string(nil), o.keys...) }

// Len returns the number of keys.
func (o *Object) Len() int { return len(o.keys) }

// Clone returns a deep copy.
func (o *Object) Clone() *Object { return Clone(o).(*Object) }

// Clone deep-copies a decoded value.
func Clone(value any) any {
	switch typed := value.(type) {
	case *Object:
		result := NewObject()
		for _, key := range typed.keys {
			result.Set(key, Clone(typed.values[key]))
		}
		return result
	case []any:
		result := make([]any, len(typed))
		for index, item := range typed {
			result[index] = Clone(item)
		}
		return result
	}
	return value
}

// DuplicateMode selects how repeated object keys are handled.
type DuplicateMode int

const (
	// LastWins mirrors json.loads: the key keeps its first position and the
	// last value.
	LastWins DuplicateMode = iota
	// RejectDuplicates mirrors object_pairs_hook=_reject_duplicate_pairs.
	RejectDuplicates
)

// DuplicateKeyError reports a repeated key under RejectDuplicates.
type DuplicateKeyError struct{ Key string }

func (e *DuplicateKeyError) Error() string { return "duplicate catalog field: " + e.Key }

// Decode parses one JSON document like json.loads(text).
func Decode(data []byte, mode DuplicateMode) (any, error) {
	if !utf8.Valid(data) {
		return nil, errors.New("JSON não é UTF-8 válido")
	}
	if bytes.HasPrefix(data, []byte("\xef\xbb\xbf")) {
		return nil, errors.New("Unexpected UTF-8 BOM (decode using utf-8-sig)")
	}
	parser := &decoder{data: data, mode: mode}
	parser.skip()
	value, err := parser.value()
	if err != nil {
		return nil, err
	}
	parser.skip()
	if parser.pos != len(data) {
		return nil, parser.errorf("Extra data")
	}
	return value, nil
}

type decoder struct {
	data []byte
	pos  int
	mode DuplicateMode
}

func (d *decoder) errorf(message string) error {
	return fmt.Errorf("%s: char %d", message, d.pos)
}

func (d *decoder) skip() {
	for d.pos < len(d.data) {
		switch d.data[d.pos] {
		case ' ', '\t', '\n', '\r':
			d.pos++
		default:
			return
		}
	}
}

func (d *decoder) literal(word string) bool {
	if bytes.HasPrefix(d.data[d.pos:], []byte(word)) {
		d.pos += len(word)
		return true
	}
	return false
}

func (d *decoder) value() (any, error) {
	if d.pos >= len(d.data) {
		return nil, d.errorf("Expecting value")
	}
	switch c := d.data[d.pos]; {
	case c == '{':
		return d.object()
	case c == '[':
		return d.array()
	case c == '"':
		return d.string()
	case d.literal("null"):
		return nil, nil
	case d.literal("true"):
		return true, nil
	case d.literal("false"):
		return false, nil
	case d.literal("NaN"):
		return Number("NaN"), nil
	case d.literal("Infinity"):
		return Number("Infinity"), nil
	case d.literal("-Infinity"):
		return Number("-Infinity"), nil
	case c == '-' || (c >= '0' && c <= '9'):
		return d.number()
	}
	return nil, d.errorf("Expecting value")
}

func (d *decoder) number() (any, error) {
	start := d.pos
	digits := func() int {
		begin := d.pos
		for d.pos < len(d.data) && d.data[d.pos] >= '0' && d.data[d.pos] <= '9' {
			d.pos++
		}
		return d.pos - begin
	}
	if d.data[d.pos] == '-' {
		d.pos++
	}
	if d.pos < len(d.data) && d.data[d.pos] == '0' {
		d.pos++
	} else if digits() == 0 {
		d.pos = start
		return nil, d.errorf("Expecting value")
	}
	if d.pos+1 < len(d.data) && d.data[d.pos] == '.' && d.data[d.pos+1] >= '0' && d.data[d.pos+1] <= '9' {
		d.pos++
		digits()
	}
	if d.pos < len(d.data) && (d.data[d.pos] == 'e' || d.data[d.pos] == 'E') {
		mark := d.pos
		d.pos++
		if d.pos < len(d.data) && (d.data[d.pos] == '+' || d.data[d.pos] == '-') {
			d.pos++
		}
		if digits() == 0 {
			d.pos = mark
		}
	}
	return Number(d.data[start:d.pos]), nil
}

func (d *decoder) string() (string, error) {
	d.pos++ // opening quote
	var builder strings.Builder
	for {
		if d.pos >= len(d.data) {
			return "", d.errorf("Unterminated string starting at")
		}
		c := d.data[d.pos]
		switch {
		case c == '"':
			d.pos++
			return builder.String(), nil
		case c < 0x20:
			return "", d.errorf("Invalid control character at")
		case c == '\\':
			d.pos++
			if d.pos >= len(d.data) {
				return "", d.errorf("Unterminated string starting at")
			}
			escape := d.data[d.pos]
			d.pos++
			switch escape {
			case '"', '\\', '/':
				builder.WriteByte(escape)
			case 'b':
				builder.WriteByte('\b')
			case 'f':
				builder.WriteByte('\f')
			case 'n':
				builder.WriteByte('\n')
			case 'r':
				builder.WriteByte('\r')
			case 't':
				builder.WriteByte('\t')
			case 'u':
				r, err := d.hex4()
				if err != nil {
					return "", err
				}
				if r >= 0xd800 && r < 0xdc00 && d.pos+6 <= len(d.data) && d.data[d.pos] == '\\' && d.data[d.pos+1] == 'u' {
					saved := d.pos
					d.pos += 2
					low, err := d.hex4()
					if err == nil && low >= 0xdc00 && low < 0xe000 {
						r = 0x10000 + (r-0xd800)<<10 + (low - 0xdc00)
					} else {
						d.pos = saved
					}
				}
				builder.WriteRune(r)
			default:
				return "", d.errorf("Invalid \\escape")
			}
		default:
			_, size := utf8.DecodeRune(d.data[d.pos:])
			builder.Write(d.data[d.pos : d.pos+size])
			d.pos += size
		}
	}
}

func (d *decoder) hex4() (rune, error) {
	if d.pos+4 > len(d.data) {
		return 0, d.errorf("Invalid \\uXXXX escape")
	}
	value, err := strconv.ParseUint(string(d.data[d.pos:d.pos+4]), 16, 32)
	if err != nil {
		return 0, d.errorf("Invalid \\uXXXX escape")
	}
	d.pos += 4
	return rune(value), nil
}

func (d *decoder) array() (any, error) {
	d.pos++
	result := []any{}
	d.skip()
	if d.pos < len(d.data) && d.data[d.pos] == ']' {
		d.pos++
		return result, nil
	}
	for {
		d.skip()
		item, err := d.value()
		if err != nil {
			return nil, err
		}
		result = append(result, item)
		d.skip()
		if d.pos >= len(d.data) {
			return nil, d.errorf("Expecting ',' delimiter")
		}
		switch d.data[d.pos] {
		case ',':
			d.pos++
		case ']':
			d.pos++
			return result, nil
		default:
			return nil, d.errorf("Expecting ',' delimiter")
		}
	}
}

func (d *decoder) object() (any, error) {
	d.pos++
	result := NewObject()
	d.skip()
	if d.pos < len(d.data) && d.data[d.pos] == '}' {
		d.pos++
		return result, nil
	}
	for {
		d.skip()
		if d.pos >= len(d.data) || d.data[d.pos] != '"' {
			return nil, d.errorf("Expecting property name enclosed in double quotes")
		}
		key, err := d.string()
		if err != nil {
			return nil, err
		}
		d.skip()
		if d.pos >= len(d.data) || d.data[d.pos] != ':' {
			return nil, d.errorf("Expecting ':' delimiter")
		}
		d.pos++
		d.skip()
		value, err := d.value()
		if err != nil {
			return nil, err
		}
		if d.mode == RejectDuplicates && result.Has(key) {
			return nil, &DuplicateKeyError{Key: key}
		}
		result.Set(key, value)
		d.skip()
		if d.pos >= len(d.data) {
			return nil, d.errorf("Expecting ',' delimiter")
		}
		switch d.data[d.pos] {
		case ',':
			d.pos++
		case '}':
			d.pos++
			return result, nil
		default:
			return nil, d.errorf("Expecting ',' delimiter")
		}
	}
}

// Encode renders json.dumps(value, ensure_ascii=False, indent=2[, sort_keys]).
func Encode(value any, sortKeys bool) ([]byte, error) {
	var buffer bytes.Buffer
	if err := encode(&buffer, value, 0, sortKeys); err != nil {
		return nil, err
	}
	return buffer.Bytes(), nil
}

// EncodeFile renders Encode plus the trailing newline every writer appends.
func EncodeFile(value any, sortKeys bool) ([]byte, error) {
	payload, err := Encode(value, sortKeys)
	if err != nil {
		return nil, err
	}
	return append(payload, '\n'), nil
}

func newline(buffer *bytes.Buffer, level int) {
	buffer.WriteByte('\n')
	for range level {
		buffer.WriteString("  ")
	}
}

func encode(buffer *bytes.Buffer, value any, level int, sortKeys bool) error {
	switch typed := value.(type) {
	case nil:
		buffer.WriteString("null")
	case bool:
		if typed {
			buffer.WriteString("true")
		} else {
			buffer.WriteString("false")
		}
	case Number:
		text, err := numberText(typed)
		if err != nil {
			return err
		}
		buffer.WriteString(text)
	case int:
		buffer.WriteString(strconv.Itoa(typed))
	case int64:
		buffer.WriteString(strconv.FormatInt(typed, 10))
	case string:
		writeString(buffer, typed)
	case []string:
		items := make([]any, len(typed))
		for index, item := range typed {
			items[index] = item
		}
		return encode(buffer, items, level, sortKeys)
	case []any:
		if len(typed) == 0 {
			buffer.WriteString("[]")
			return nil
		}
		buffer.WriteByte('[')
		for index, item := range typed {
			if index > 0 {
				buffer.WriteByte(',')
			}
			newline(buffer, level+1)
			if err := encode(buffer, item, level+1, sortKeys); err != nil {
				return err
			}
		}
		newline(buffer, level)
		buffer.WriteByte(']')
	case *Object:
		if typed.Len() == 0 {
			buffer.WriteString("{}")
			return nil
		}
		keys := typed.Keys()
		if sortKeys {
			sort.Strings(keys)
		}
		buffer.WriteByte('{')
		for index, key := range keys {
			if index > 0 {
				buffer.WriteByte(',')
			}
			newline(buffer, level+1)
			writeString(buffer, key)
			buffer.WriteString(": ")
			if err := encode(buffer, typed.values[key], level+1, sortKeys); err != nil {
				return err
			}
		}
		newline(buffer, level)
		buffer.WriteByte('}')
	default:
		return fmt.Errorf("Object of type %T is not JSON serializable", value)
	}
	return nil
}

func numberText(n Number) (string, error) {
	if n.special() {
		return string(n), nil
	}
	if n.IsInt() {
		value, ok := new(big.Int).SetString(string(n), 10)
		if !ok {
			return "", fmt.Errorf("número JSON inválido: %s", n)
		}
		return value.String(), nil
	}
	return FloatRepr(n.Float()), nil
}

// FloatRepr renders float.__repr__.
func FloatRepr(value float64) string {
	switch {
	case math.IsNaN(value):
		return "NaN"
	case math.IsInf(value, 1):
		return "Infinity"
	case math.IsInf(value, -1):
		return "-Infinity"
	}
	digits := strconv.FormatFloat(value, 'e', -1, 64) // d.ddde±XX
	mantissa, exponentText, _ := strings.Cut(digits, "e")
	exponent, _ := strconv.Atoi(exponentText)
	sign := ""
	if strings.HasPrefix(mantissa, "-") {
		sign, mantissa = "-", mantissa[1:]
	}
	significant := strings.Replace(mantissa, ".", "", 1)
	if exponent >= -4 && exponent < 16 {
		point := exponent + 1
		switch {
		case point <= 0:
			return sign + "0." + strings.Repeat("0", -point) + significant
		case point >= len(significant):
			return sign + significant + strings.Repeat("0", point-len(significant)) + ".0"
		default:
			return sign + significant[:point] + "." + significant[point:]
		}
	}
	result := significant[:1]
	if len(significant) > 1 {
		result += "." + significant[1:]
	}
	exponentSign := "+"
	if exponent < 0 {
		exponentSign, exponent = "-", -exponent
	}
	return fmt.Sprintf("%s%se%s%02d", sign, result, exponentSign, exponent)
}

func writeString(buffer *bytes.Buffer, value string) {
	buffer.WriteByte('"')
	for _, r := range value {
		switch r {
		case '"':
			buffer.WriteString(`\"`)
		case '\\':
			buffer.WriteString(`\\`)
		case '\n':
			buffer.WriteString(`\n`)
		case '\r':
			buffer.WriteString(`\r`)
		case '\t':
			buffer.WriteString(`\t`)
		case '\b':
			buffer.WriteString(`\b`)
		case '\f':
			buffer.WriteString(`\f`)
		default:
			if r < 0x20 {
				fmt.Fprintf(buffer, `\u%04x`, r)
			} else {
				buffer.WriteRune(r)
			}
		}
	}
	buffer.WriteByte('"')
}
