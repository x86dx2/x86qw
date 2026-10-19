package services

import (
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"strings"
)

// pyDumps renders value exactly like Python's
// json.dumps(value, ensure_ascii=False, sort_keys=True[, indent=2]).
func pyDumps(value any, indent bool) string {
	var builder strings.Builder
	writePy(&builder, value, indent, 0)
	return builder.String()
}

func pyString(value string) string {
	var builder strings.Builder
	builder.WriteByte('"')
	for _, r := range value {
		switch r {
		case '"':
			builder.WriteString(`\"`)
		case '\\':
			builder.WriteString(`\\`)
		case '\n':
			builder.WriteString(`\n`)
		case '\r':
			builder.WriteString(`\r`)
		case '\t':
			builder.WriteString(`\t`)
		case '\b':
			builder.WriteString(`\b`)
		case '\f':
			builder.WriteString(`\f`)
		default:
			if r < 0x20 {
				fmt.Fprintf(&builder, `\u%04x`, r)
			} else {
				builder.WriteRune(r)
			}
		}
	}
	builder.WriteByte('"')
	return builder.String()
}

func writePy(builder *strings.Builder, value any, indent bool, level int) {
	newline := func(depth int) {
		if indent {
			builder.WriteString("\n" + strings.Repeat("  ", depth))
		}
	}
	itemSeparator := ", "
	if indent {
		itemSeparator = ","
	}
	switch typed := value.(type) {
	case nil:
		builder.WriteString("null")
	case bool:
		builder.WriteString(strconv.FormatBool(typed))
	case string:
		builder.WriteString(pyString(typed))
	case int:
		builder.WriteString(strconv.Itoa(typed))
	case int64:
		builder.WriteString(strconv.FormatInt(typed, 10))
	case uint64:
		builder.WriteString(strconv.FormatUint(typed, 10))
	case json.Number:
		builder.WriteString(typed.String())
	case float64:
		if typed == float64(int64(typed)) {
			builder.WriteString(strconv.FormatInt(int64(typed), 10))
		} else {
			builder.WriteString(strconv.FormatFloat(typed, 'g', -1, 64))
		}
	case []any:
		if len(typed) == 0 {
			builder.WriteString("[]")
			return
		}
		builder.WriteByte('[')
		for index, item := range typed {
			if index > 0 {
				builder.WriteString(itemSeparator)
			}
			newline(level + 1)
			writePy(builder, item, indent, level+1)
		}
		newline(level)
		builder.WriteByte(']')
	case []string:
		items := make([]any, len(typed))
		for index, item := range typed {
			items[index] = item
		}
		writePy(builder, items, indent, level)
	case map[string]any:
		if len(typed) == 0 {
			builder.WriteString("{}")
			return
		}
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		builder.WriteByte('{')
		for index, key := range keys {
			if index > 0 {
				builder.WriteString(itemSeparator)
			}
			newline(level + 1)
			builder.WriteString(pyString(key))
			builder.WriteString(": ")
			writePy(builder, typed[key], indent, level+1)
		}
		newline(level)
		builder.WriteByte('}')
	case map[string]string:
		converted := map[string]any{}
		for key, item := range typed {
			converted[key] = item
		}
		writePy(builder, converted, indent, level)
	default:
		payload, _ := json.Marshal(typed)
		var generic any
		decoder := json.NewDecoder(strings.NewReader(string(payload)))
		decoder.UseNumber()
		if decoder.Decode(&generic) == nil {
			writePy(builder, generic, indent, level)
			return
		}
		builder.Write(payload)
	}
}

// decodeGeneric parses JSON keeping integers as json.Number.
func decodeGeneric(payload []byte) (any, error) {
	decoder := json.NewDecoder(strings.NewReader(string(payload)))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	return value, nil
}
