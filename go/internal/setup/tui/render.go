package tui

import (
	"image/color"
	"strings"

	"charm.land/lipgloss/v2"
	"github.com/charmbracelet/x/ansi"
)

// page is one column of lines, each exactly width columns wide, with the
// regions the mouse can hit and where the caret sits.
type page struct {
	theme  Theme
	glyphs Glyphs
	width  int
	lines  []string
	hits   []hit
	caret  *[2]int // x, y inside the page
	// focus is the line range that must stay visible when the page scrolls.
	focusTop, focusBottom int
}

// keep marks lines from..to as the part that must stay on screen.
func (p *page) keep(from, to int) { p.focusTop, p.focusBottom = from, to }

// hit is a clickable region; y and x are page coordinates until the frame
// places the page.
type hit struct {
	y, x0, x1 int
	id        string
}

func newPage(theme Theme, glyphs Glyphs, width int) *page {
	return &page{theme: theme, glyphs: glyphs, width: width}
}

// fill pads or cuts s to exactly width columns on background bg.
func fill(s string, width int, bg color.Color) string {
	w := lipgloss.Width(s)
	if w > width {
		return ansi.Truncate(s, width, "…")
	}
	return s + lipgloss.NewStyle().Background(bg).Render(strings.Repeat(" ", width-w))
}

func (p *page) add(s string) int {
	p.lines = append(p.lines, fill(s, p.width, p.theme.Base))
	return len(p.lines) - 1
}

func (p *page) gap(n int) {
	for range n {
		p.add("")
	}
}

func (p *page) st(fg color.Color) lipgloss.Style { return p.theme.Style(fg) }

// title is the one question a screen asks.
func (p *page) title(text string) {
	p.add(p.st(p.theme.Text).Bold(true).Render(text))
}

// lead is quiet explanatory text, wrapped.
func (p *page) lead(text string) {
	for _, line := range strings.Split(lipgloss.Wrap(text, p.width, " "), "\n") {
		p.add(p.st(p.theme.Gray).Render(line))
	}
}

// band draws a full-width row whose background marks selection or hover.
func (p *page) band(content string, bg color.Color, id string) {
	y := p.add("")
	p.lines[y] = fill(content, p.width, bg)
	if id != "" {
		p.hits = append(p.hits, hit{y: y, x0: 0, x1: p.width, id: id})
	}
}

// rowBG picks the band for a row that is focused, hovered or neither.
func (p *page) rowBG(focused, hovered bool) color.Color {
	switch {
	case focused:
		return p.theme.Band
	case hovered:
		return p.theme.Hover
	}
	return p.theme.Base
}

// spread places left and right on one line of width w over bg.
func spread(left, right string, w int, bg color.Color) string {
	space := w - lipgloss.Width(left) - lipgloss.Width(right)
	if space < 2 {
		return fill(left, w, bg)
	}
	return left + lipgloss.NewStyle().Background(bg).Render(strings.Repeat(" ", space)) + right
}

// rule is a faint horizontal line with an optional label.
func (p *page) rule(label string) {
	faint := p.st(p.theme.Faint())
	if label == "" {
		p.add(faint.Render(strings.Repeat("─", p.width)))
		return
	}
	text := p.st(p.theme.Gray).Render(label) + faint.Render(" ")
	p.add(text + faint.Render(strings.Repeat("─", max(0, p.width-lipgloss.Width(text)))))
}

// heading opens a part of a screen: a bold label and a faint rule.
func (p *page) heading(label string) {
	text := p.st(p.theme.Bright).Bold(true).Render(label) + p.st(p.theme.Base).Render("  ")
	p.add(text + p.st(p.theme.Faint()).Render(strings.Repeat("─", max(0, p.width-lipgloss.Width(text)))))
	p.gap(1)
}

// bar draws a progress bar with eighth-block resolution.
func bar(theme Theme, glyphs Glyphs, width int, fraction float64, fg color.Color) string {
	fraction = max(0, min(1, fraction))
	cells := fraction * float64(width)
	full := int(cells)
	partial := int((cells - float64(full)) * 8)
	track := lipgloss.NewStyle().Foreground(fg).Background(Blend(theme.Base, theme.Band, 0.9))
	s := strings.Repeat(glyphs.Eighths[8], full)
	if full < width {
		s += glyphs.Eighths[partial] + strings.Repeat(" ", width-full-1)
	}
	return track.Render(s)
}

// box frames lines with a rounded border, a title on the top edge and an
// optional caption on the bottom edge.
func box(theme Theme, lines []string, width int, title, caption string, border color.Color, bg color.Color) []string {
	edge := lipgloss.NewStyle().Foreground(border).Background(bg)
	top := edge.Render("╭─")
	if title != "" {
		top += lipgloss.NewStyle().Foreground(theme.Text).Background(bg).Bold(true).Render(" "+title+" ") + edge.Render("")
	}
	top += edge.Render(strings.Repeat("─", max(0, width-lipgloss.Width(top)-1)) + "╮")
	out := []string{top}
	for _, line := range lines {
		out = append(out, edge.Render("│ ")+fill(line, width-4, bg)+edge.Render(" │"))
	}
	bottom := edge.Render("╰")
	if caption != "" {
		text := lipgloss.NewStyle().Foreground(theme.Gray).Background(bg).Render(" " + caption + " ")
		bottom += edge.Render(strings.Repeat("─", max(0, width-lipgloss.Width(text)-3))) + text + edge.Render("─╯")
	} else {
		bottom += edge.Render(strings.Repeat("─", width-2) + "╯")
	}
	return append(out, bottom)
}

// hint renders the footer: key in bold, label muted, dim separators.
func hints(theme Theme, items [][2]string, width int, hover string) (string, []hit) {
	var parts []string
	var hits []hit
	x := 0
	sep := lipgloss.NewStyle().Foreground(theme.Dim).Background(theme.Base).Render("   ")
	for index, item := range items {
		bg := theme.Base
		if hover == "hint:"+item[0] {
			bg = theme.Hover
		}
		key := lipgloss.NewStyle().Foreground(theme.Text2).Background(bg).Bold(true).Render(item[0])
		label := lipgloss.NewStyle().Foreground(theme.Gray).Background(bg).Render(" " + item[1])
		part := key + label
		if index > 0 {
			parts = append(parts, sep)
			x += 3
		}
		w := lipgloss.Width(part)
		hits = append(hits, hit{x0: x, x1: x + w, id: "hint:" + item[0]})
		parts = append(parts, part)
		x += w
	}
	line := strings.Join(parts, "")
	if lipgloss.Width(line) > width {
		line = ansi.Truncate(line, width, "…")
	}
	return line, hits
}
