// Package pyjson encodes values the way the x86QW documents are written:
// sorted keys, no ASCII escaping, ", " and ": " on one line or N-space
// indent (what Python's json.dumps(ensure_ascii=False, sort_keys=True)
// writes, which the published files keep).
package pyjson

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
)

// Dumps renders value. indent < 0 means the single-line form, with ", " and
// ": " separators.
func Dumps(value any, indent int) (string, error) {
	var builder strings.Builder
	if err := write(&builder, value, indent, 0); err != nil {
		return "", err
	}
	return builder.String(), nil
}

func write(b *strings.Builder, value any, indent, depth int) error {
	switch v := value.(type) {
	case nil:
		b.WriteString("null")
	case bool:
		if v {
			b.WriteString("true")
		} else {
			b.WriteString("false")
		}
	case string:
		writeString(b, v)
	case int:
		b.WriteString(strconv.Itoa(v))
	case int64:
		b.WriteString(strconv.FormatInt(v, 10))
	case uint64:
		b.WriteString(strconv.FormatUint(v, 10))
	case float64:
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return fmt.Errorf("valor JSON não finito")
		}
		if v == math.Trunc(v) && math.Abs(v) < 1e16 {
			b.WriteString(strconv.FormatFloat(v, 'f', 1, 64))
		} else {
			b.WriteString(strconv.FormatFloat(v, 'g', -1, 64))
		}
	case []string:
		items := make([]any, len(v))
		for index, item := range v {
			items[index] = item
		}
		return writeList(b, items, indent, depth)
	case []any:
		return writeList(b, v, indent, depth)
	case []map[string]any:
		items := make([]any, len(v))
		for index, item := range v {
			items[index] = item
		}
		return writeList(b, items, indent, depth)
	case map[string]string:
		items := map[string]any{}
		for key, item := range v {
			items[key] = item
		}
		return writeMap(b, items, indent, depth)
	case map[string]any:
		return writeMap(b, v, indent, depth)
	case json.Number:
		b.WriteString(v.String())
	default:
		// Any other value goes through encoding/json first, keeping its
		// numbers exact.
		payload, err := json.Marshal(value)
		if err != nil {
			return fmt.Errorf("tipo JSON não suportado: %T", value)
		}
		decoder := json.NewDecoder(bytes.NewReader(payload))
		decoder.UseNumber()
		var generic any
		if err := decoder.Decode(&generic); err != nil {
			return err
		}
		return write(b, generic, indent, depth)
	}
	return nil
}

// Decode parses JSON keeping numbers as json.Number, so Dumps writes them
// back unchanged.
func Decode(payload []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	return value, nil
}

func newline(b *strings.Builder, indent, depth int) {
	b.WriteByte('\n')
	b.WriteString(strings.Repeat(" ", indent*depth))
}

func writeList(b *strings.Builder, items []any, indent, depth int) error {
	if len(items) == 0 {
		b.WriteString("[]")
		return nil
	}
	b.WriteByte('[')
	for index, item := range items {
		if index > 0 {
			b.WriteByte(',')
			if indent < 0 {
				b.WriteByte(' ')
			}
		}
		if indent >= 0 {
			newline(b, indent, depth+1)
		}
		if err := write(b, item, indent, depth+1); err != nil {
			return err
		}
	}
	if indent >= 0 {
		newline(b, indent, depth)
	}
	b.WriteByte(']')
	return nil
}

func writeMap(b *strings.Builder, items map[string]any, indent, depth int) error {
	if len(items) == 0 {
		b.WriteString("{}")
		return nil
	}
	keys := make([]string, 0, len(items))
	for key := range items {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(a, c int) bool { return lessCodepoints(keys[a], keys[c]) })
	b.WriteByte('{')
	for index, key := range keys {
		if index > 0 {
			b.WriteByte(',')
			if indent < 0 {
				b.WriteByte(' ')
			}
		}
		if indent >= 0 {
			newline(b, indent, depth+1)
		}
		writeString(b, key)
		b.WriteString(": ")
		if err := write(b, items[key], indent, depth+1); err != nil {
			return err
		}
	}
	if indent >= 0 {
		newline(b, indent, depth)
	}
	b.WriteByte('}')
	return nil
}

// lessCodepoints orders strings by code point.
func lessCodepoints(a, b string) bool {
	ra, rb := []rune(a), []rune(b)
	for index := 0; index < len(ra) && index < len(rb); index++ {
		if ra[index] != rb[index] {
			return ra[index] < rb[index]
		}
	}
	return len(ra) < len(rb)
}

func writeString(b *strings.Builder, value string) {
	b.WriteByte('"')
	for _, r := range value {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		default:
			if r < 0x20 {
				fmt.Fprintf(b, `\u%04x`, r)
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
}
