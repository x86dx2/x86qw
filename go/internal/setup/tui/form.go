package tui

import (
	"strings"

	"charm.land/lipgloss/v2"
)

type rowKind int

const (
	rowRadio   rowKind = iota // one of a group; enter/space picks it
	rowToggle                 // on/off
	rowStepper                // ← → change the value
	rowText                   // typing edits Field
	rowAction                 // enter runs it
	rowInfo                   // read-only label and value
	rowHeading                // group title, not focusable
	rowCustom                 // pre-rendered lines, not focusable
)

// formRow is one line of an editor. Rows are rebuilt from the state on
// every frame, so the closures always see current values.
type formRow struct {
	id          string
	kind        rowKind
	label, desc string
	value       string
	on          bool
	tone        string // "", "ok", "warn", "error", "dim" for rowInfo values
	field       *Field
	placeholder string
	lines       []string
	activate    func()          // radio pick, toggle, action
	step        func(delta int) // stepper
	coarse      bool            // a number: shift+← → steps by ten
}

func (r formRow) focusable() bool {
	return r.kind != rowInfo && r.kind != rowHeading && r.kind != rowCustom
}

// renderRows draws rows into p and returns the caret position of the
// focused text row, if any.
func (p *page) renderRows(rows []formRow, focus int, hover string) {
	t := p.theme
	g := p.glyphs
	focusIndex := -1
	for index, row := range rows {
		if row.focusable() {
			focusIndex++
		}
		focused := row.focusable() && focusIndex == focus
		hovered := hover == "row:"+row.id
		bg := p.rowBG(focused, hovered)
		base := lipgloss.NewStyle().Background(bg)
		label := base.Foreground(t.Text2)
		if focused {
			label = label.Foreground(t.Text).Bold(true)
		}
		desc := base.Foreground(t.Gray)
		switch row.kind {
		case rowHeading:
			if index > 0 {
				p.gap(1)
			}
			p.add(p.st(t.Bright).Bold(true).Render(row.label))
			continue
		case rowCustom:
			for _, line := range row.lines {
				p.add(line)
			}
			continue
		case rowInfo:
			tone := map[string]lipgloss.Style{"ok": p.st(t.Success), "warn": p.st(t.Warning), "error": p.st(t.Error), "dim": p.st(t.Gray)}[row.tone]
			if row.tone == "" {
				tone = p.st(t.Text2)
			}
			// Info lines up with the labels of the rows around it.
			p.add(spread(p.st(t.Gray).Render("     "+padRight(row.label, 12)), tone.Render(row.value)+p.st(t.Base).Render("  "), p.width, t.Base))
			if row.desc != "" {
				for _, line := range strings.Split(lipgloss.Wrap(row.desc, p.width-16, " "), "\n") {
					p.add(p.st(t.Gray).Render(strings.Repeat(" ", 16) + line))
				}
			}
			continue
		}
		mark := "   "
		switch row.kind {
		case rowRadio:
			glyph, style := g.RadioOff, base.Foreground(t.Dim)
			if row.on {
				glyph, style = g.Radio, base.Foreground(t.Brand)
			}
			mark = base.Render("  ") + style.Render(glyph) + base.Render(" ")
		case rowToggle:
			glyph, style := g.CheckOff, base.Foreground(t.Dim)
			if row.on {
				glyph, style = g.Check, base.Foreground(t.Brand)
			}
			mark = base.Render("  ") + style.Render(glyph) + base.Render(" ")
		case rowAction:
			mark = base.Render("  ") + base.Foreground(t.Brand).Render(g.Chevron) + base.Render(" ")
		default:
			mark = base.Render("    ")
		}
		left := mark + base.Render(" ") + label.Render(row.label)
		right := ""
		switch row.kind {
		case rowStepper:
			arrows := base.Foreground(t.Dim)
			if focused {
				arrows = base.Foreground(t.Bright)
			}
			right = arrows.Render("‹ ") + base.Foreground(t.Text).Bold(focused).Render(row.value) + arrows.Render(" ›") + base.Render("  ")
		case rowText:
			fieldWidth := min(36, p.width-lipgloss.Width(left)-4)
			fieldBG := t.Sunken
			if focused {
				fieldBG = t.Visual
			}
			text := row.field.String()
			style := lipgloss.NewStyle().Background(fieldBG).Foreground(t.Text)
			if text == "" {
				text, style = row.placeholder, style.Foreground(t.Dim)
			}
			shown := []rune(text)
			offset := 0
			if focused && row.field.Caret >= fieldWidth-1 {
				offset = row.field.Caret - fieldWidth + 2
			}
			if offset < len(shown) {
				shown = shown[offset:]
			} else {
				shown = nil
			}
			right = style.Render(" "+fill(string(shown), fieldWidth-1, fieldBG)) + base.Render("  ")
			if focused {
				x := p.width - fieldWidth - 2 + 1 + (row.field.Caret - offset)
				p.caret = &[2]int{x, len(p.lines)}
			}
		default:
			if row.desc != "" {
				right = desc.Render(row.desc) + base.Render("  ")
			}
		}
		p.band(spread(left, right, p.width, bg), bg, "row:"+row.id)
		if row.kind == rowText && row.desc != "" {
			p.add(p.st(t.Gray).Render("      " + row.desc))
		}
	}
}

// focusable rows, in order, for keyboard navigation.
func focusables(rows []formRow) []formRow {
	var out []formRow
	for _, row := range rows {
		if row.focusable() {
			out = append(out, row)
		}
	}
	return out
}

func padRight(s string, n int) string {
	if w := lipgloss.Width(s); w < n {
		return s + strings.Repeat(" ", n-w)
	}
	return s
}
