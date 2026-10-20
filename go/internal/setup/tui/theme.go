// Package tui is the x86QW installer in the terminal: fullscreen, quiet,
// keyboard and mouse driven.
package tui

import (
	"image/color"
	"os"
	"runtime"

	"charm.land/lipgloss/v2"
)

// Theme holds every colour the interface uses, by role. Hierarchy comes from
// the grey tiers and bold; hue only carries meaning, and the x86QW red marks
// the brand and the one thing in focus.
type Theme struct {
	Dark bool

	Base     color.Color // canvas
	Sunken   color.Color // code, recipe, log
	Band     color.Color // selected row, input field
	Hover    color.Color // row under the mouse
	Visual   color.Color // focused tab, strongest band
	Border   color.Color // dialogs and fields, idle
	BorderOn color.Color // focused field
	Text     color.Color
	Text2    color.Color
	Bright   color.Color // greys, in order of strength
	Gray     color.Color
	Dim      color.Color
	Brand    color.Color
	Success  color.Color
	Warning  color.Color
	Error    color.Color
	Info     color.Color
}

// Night is the default dark theme.
func Night() Theme {
	t := Theme{
		Dark: true, Base: hex("#141414"), Sunken: hex("#101010"), Band: hex("#222224"), Hover: hex("#2a2a2c"), Visual: hex("#343437"),
		Border: hex("#323237"), BorderOn: hex("#55555d"), Text: hex("#e4e2df"), Text2: hex("#c8c6c3"),
		Bright: hex("#8a8a8f"), Gray: hex("#6c6c70"), Dim: hex("#4e4e53"),
		Brand: hex("#ff4d4d"), Success: hex("#6fd3b8"), Warning: hex("#e0af68"), Error: hex("#f7768e"), Info: hex("#7aa2f7"),
	}
	if runtime.GOOS == "windows" && os.Getenv("WT_SESSION") == "" {
		// The classic console crushes close greys; spread the structural ones.
		t.Band, t.Hover, t.Border, t.BorderOn, t.Dim = hex("#2e2e30"), hex("#3a3a3c"), hex("#4a4a50"), hex("#77777f"), hex("#6a6a70")
	}
	return t
}

// Day is the light theme: the same ramp inverted, hues deepened.
func Day() Theme {
	return Theme{
		Base: hex("#eeedeb"), Sunken: hex("#e6e5e2"), Band: hex("#dfdedb"), Hover: hex("#d4d3d0"), Visual: hex("#c8c7c4"),
		Border: hex("#c6c5c9"), BorderOn: hex("#9e9da6"), Text: hex("#1f1f22"), Text2: hex("#3d3d42"),
		Bright: hex("#5e5e64"), Gray: hex("#78787e"), Dim: hex("#a4a4aa"),
		Brand: hex("#c8102e"), Success: hex("#0f8a6d"), Warning: hex("#9a6a10"), Error: hex("#c4314b"), Info: hex("#2f64d2"),
	}
}

func hex(s string) color.Color { return lipgloss.Color(s) }

// Blend mixes a towards b by t (0..1) in RGB.
func Blend(a, b color.Color, t float64) color.Color {
	ar, ag, ab, _ := a.RGBA()
	br, bg, bb, _ := b.RGBA()
	mix := func(x, y uint32) uint8 { return uint8((float64(x>>8)*(1-t) + float64(y>>8)*t) + 0.5) }
	return color.RGBA{mix(ar, br), mix(ag, bg), mix(ab, bb), 0xff}
}

// Faint is quieter than Dim: for rules and the softest frames.
func (t Theme) Faint() color.Color { return Blend(t.Base, t.Dim, 0.6) }

// Style is a text style on the canvas background.
func (t Theme) Style(fg color.Color) lipgloss.Style {
	return lipgloss.NewStyle().Foreground(fg).Background(t.Base)
}
