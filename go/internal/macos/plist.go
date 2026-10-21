// Package macos prepares and checks the ezQuake app bundle: Info.plist
// editing that keeps the exact XML form already on disk, entitlement and
// safe-area probes, nightly preparation and the process check.
package macos

import (
	"bytes"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"strings"
)

// Value is one plist node. Kind is dict, array, string, integer, real,
// true or false; dates and data are rejected (the ezQuake plist has none).
type Value struct {
	Kind  string
	Text  string
	Keys  []string
	Dict  map[string]*Value
	Items []*Value
}

// Bool returns (value, true) for true/false nodes.
func (v *Value) Bool() (bool, bool) {
	switch v.Kind {
	case "true":
		return true, true
	case "false":
		return false, true
	}
	return false, false
}

// Set stores key in a dict; a new key goes last, so the order on disk is
// kept.
func (v *Value) Set(key string, value *Value) {
	if _, ok := v.Dict[key]; !ok {
		v.Keys = append(v.Keys, key)
	}
	v.Dict[key] = value
}

// ParsePlist decodes an XML property list.
func ParsePlist(payload []byte) (*Value, error) {
	if bytes.HasPrefix(payload, []byte("bplist00")) {
		return nil, errors.New("binary Info.plist is not supported")
	}
	decoder := xml.NewDecoder(bytes.NewReader(payload))
	decoder.Strict = true
	for {
		token, err := decoder.Token()
		if err != nil {
			return nil, fmt.Errorf("invalid Info.plist: %w", err)
		}
		if start, ok := token.(xml.StartElement); ok {
			if start.Name.Local != "plist" {
				return nil, errors.New("invalid Info.plist")
			}
			value, err := parseNext(decoder)
			if err != nil {
				return nil, err
			}
			if value == nil {
				return nil, errors.New("empty Info.plist")
			}
			return value, nil
		}
	}
}

// parseNext returns the next value element, or nil at a closing tag.
func parseNext(decoder *xml.Decoder) (*Value, error) {
	for {
		token, err := decoder.Token()
		if err != nil {
			return nil, fmt.Errorf("invalid Info.plist: %w", err)
		}
		switch t := token.(type) {
		case xml.EndElement:
			return nil, nil
		case xml.StartElement:
			return parseElement(decoder, t)
		}
	}
}

func elementText(decoder *xml.Decoder) (string, error) {
	var builder strings.Builder
	for {
		token, err := decoder.Token()
		if err != nil {
			return "", err
		}
		switch t := token.(type) {
		case xml.CharData:
			builder.Write(t)
		case xml.EndElement:
			return builder.String(), nil
		case xml.StartElement:
			return "", errors.New("invalid Info.plist: unexpected nested element")
		}
	}
}

func parseElement(decoder *xml.Decoder, start xml.StartElement) (*Value, error) {
	switch start.Name.Local {
	case "dict":
		value := &Value{Kind: "dict", Dict: map[string]*Value{}}
		for {
			token, err := decoder.Token()
			if err != nil {
				return nil, err
			}
			switch t := token.(type) {
			case xml.EndElement:
				return value, nil
			case xml.StartElement:
				if t.Name.Local != "key" {
					return nil, errors.New("invalid Info.plist: missing key")
				}
				key, err := elementText(decoder)
				if err != nil {
					return nil, err
				}
				item, err := parseNext(decoder)
				if err != nil || item == nil {
					return nil, errors.New("invalid Info.plist: missing value")
				}
				value.Set(key, item)
			}
		}
	case "array":
		value := &Value{Kind: "array"}
		for {
			item, err := parseNext(decoder)
			if err != nil {
				return nil, err
			}
			if item == nil {
				return value, nil
			}
			value.Items = append(value.Items, item)
		}
	case "true", "false":
		if err := decoder.Skip(); err != nil {
			return nil, err
		}
		return &Value{Kind: start.Name.Local}, nil
	case "string", "integer", "real":
		text, err := elementText(decoder)
		if err != nil {
			return nil, err
		}
		return &Value{Kind: start.Name.Local, Text: text}, nil
	}
	return nil, fmt.Errorf("Info.plist uses an unsupported type: %s", start.Name.Local)
}

// MarshalPlist writes plistlib.dumps(fmt=FMT_XML, sort_keys=False) bytes.
func MarshalPlist(root *Value) []byte {
	var buffer bytes.Buffer
	buffer.WriteString("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" +
		"<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n" +
		"<plist version=\"1.0\">\n")
	writeValue(&buffer, root, 0)
	buffer.WriteString("</plist>\n")
	return buffer.Bytes()
}

func escape(text string) string {
	var buffer bytes.Buffer
	xml.EscapeText(&buffer, []byte(text))
	// plistlib escapes only &, < and > (and normalizes \r\n to \n).
	out := buffer.String()
	out = strings.NewReplacer("&#34;", "\"", "&#39;", "'", "&#x9;", "\t", "&#xA;", "\n", "&#xD;", "\r").Replace(out)
	return out
}

func writeValue(w io.Writer, value *Value, depth int) {
	indent := strings.Repeat("\t", depth)
	switch value.Kind {
	case "dict":
		if len(value.Keys) == 0 {
			fmt.Fprintf(w, "%s<dict/>\n", indent)
			return
		}
		fmt.Fprintf(w, "%s<dict>\n", indent)
		for _, key := range value.Keys {
			fmt.Fprintf(w, "%s\t<key>%s</key>\n", indent, escape(key))
			writeValue(w, value.Dict[key], depth+1)
		}
		fmt.Fprintf(w, "%s</dict>\n", indent)
	case "array":
		if len(value.Items) == 0 {
			fmt.Fprintf(w, "%s<array/>\n", indent)
			return
		}
		fmt.Fprintf(w, "%s<array>\n", indent)
		for _, item := range value.Items {
			writeValue(w, item, depth+1)
		}
		fmt.Fprintf(w, "%s</array>\n", indent)
	case "true", "false":
		fmt.Fprintf(w, "%s<%s/>\n", indent, value.Kind)
	default:
		fmt.Fprintf(w, "%s<%s>%s</%s>\n", indent, value.Kind, escape(value.Text), value.Kind)
	}
}
