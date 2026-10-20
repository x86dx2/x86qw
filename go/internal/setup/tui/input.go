package tui

import (
	"strings"
	"unicode"

	tea "charm.land/bubbletea/v2"
)

// Field is a one-line text input drawn by the screens; the terminal's own
// cursor marks the caret.
type Field struct {
	Value  []rune
	Caret  int
	Limit  int
	Accept func(r rune) bool // nil accepts any printable rune
}

// NewField starts a field holding value.
func NewField(value string, limit int) Field {
	runes := []rune(value)
	return Field{Value: runes, Caret: len(runes), Limit: limit}
}

// String is the current value.
func (f Field) String() string { return string(f.Value) }

// Set replaces the value and moves the caret to the end.
func (f *Field) Set(value string) { f.Value, f.Caret = []rune(value), len([]rune(value)) }

// Key edits the field; it reports whether the key was used.
func (f *Field) Key(msg tea.KeyPressMsg) bool {
	switch msg.String() {
	case "left", "ctrl+b":
		f.Caret = max(0, f.Caret-1)
	case "right", "ctrl+f":
		f.Caret = min(len(f.Value), f.Caret+1)
	case "home", "ctrl+a":
		f.Caret = 0
	case "end", "ctrl+e":
		f.Caret = len(f.Value)
	case "backspace", "ctrl+h":
		if f.Caret > 0 {
			f.Value = append(f.Value[:f.Caret-1], f.Value[f.Caret:]...)
			f.Caret--
		}
	case "delete", "ctrl+d":
		if f.Caret < len(f.Value) {
			f.Value = append(f.Value[:f.Caret], f.Value[f.Caret+1:]...)
		}
	case "ctrl+u":
		f.Value, f.Caret = f.Value[f.Caret:], 0
	case "ctrl+w", "alt+backspace":
		start := f.Caret
		for start > 0 && unicode.IsSpace(f.Value[start-1]) {
			start--
		}
		for start > 0 && !unicode.IsSpace(f.Value[start-1]) {
			start--
		}
		f.Value, f.Caret = append(f.Value[:start], f.Value[f.Caret:]...), start
	default:
		if msg.Text == "" {
			return false
		}
		f.Insert(msg.Text)
	}
	return true
}

// Insert types text at the caret, dropping what the field refuses.
func (f *Field) Insert(text string) {
	for _, r := range strings.ReplaceAll(text, "\n", " ") {
		if !unicode.IsPrint(r) || (f.Accept != nil && !f.Accept(r)) || (f.Limit > 0 && len(f.Value) >= f.Limit) {
			continue
		}
		f.Value = append(f.Value[:f.Caret], append([]rune{r}, f.Value[f.Caret:]...)...)
		f.Caret++
	}
}
