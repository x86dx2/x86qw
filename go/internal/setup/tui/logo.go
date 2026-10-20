package tui

import (
	_ "embed"
	"math"
	"strings"
	"time"

	"charm.land/lipgloss/v2"
)

//go:embed assets/logo-full.txt
var logoFull string

//go:embed assets/logo-compact.txt
var logoCompact string

// Logo is the braille x86QW wordmark.
type Logo struct {
	rows  [][]rune
	width int
}

func newLogo(art string) Logo {
	var logo Logo
	for _, line := range strings.Split(strings.TrimRight(art, "\n"), "\n") {
		runes := []rune(line)
		logo.rows = append(logo.rows, runes)
		logo.width = max(logo.width, len(runes))
	}
	return logo
}

// Height is the number of rows the logo takes.
func (l Logo) Height() int { return len(l.rows) }

// Width is the number of columns the logo takes.
func (l Logo) Width() int { return l.width }

// shimmer timing: a soft band crosses the logo from the bottom left to the
// top right once every cycle, and a faint breath keeps it alive between
// sweeps.
const (
	shimmerCycle = 4.0
	shimmerSweep = 0.32
	shimmerBand  = 0.38
)

// Render draws the logo at time now; a zero time draws it still.
func (l Logo) Render(theme Theme, now time.Time) []string {
	levels := 24
	palette := make([]lipgloss.Style, levels+1)
	for index := range palette {
		intensity := float64(index) / float64(levels)
		fg := Blend(Blend(theme.Gray, theme.Text, 0.35+0.65*intensity), theme.Brand, 0.45*intensity*intensity)
		palette[index] = lipgloss.NewStyle().Foreground(fg).Background(theme.Base)
	}
	center, breath := -10.0, 0.0
	if !now.IsZero() {
		seconds := float64(now.UnixNano()%int64(shimmerCycle*float64(time.Second))) / float64(time.Second)
		phase := seconds / shimmerCycle
		if phase < shimmerSweep {
			center = -shimmerBand + phase/shimmerSweep*(1+2*shimmerBand)
		}
		breath = 0.06 * math.Sin(float64(now.UnixNano())/float64(time.Second)*2*math.Pi/5)
	}
	out := make([]string, len(l.rows))
	height := float64(len(l.rows))
	for y, row := range l.rows {
		var line strings.Builder
		runLevel, run := -1, strings.Builder{}
		flush := func() {
			if run.Len() > 0 {
				line.WriteString(palette[runLevel].Render(run.String()))
				run.Reset()
			}
		}
		for x, r := range row {
			// Diagonal position: left to right, bottom rows a little ahead.
			position := (float64(x)/float64(l.width))*0.8 + (height-1-float64(y))/height*0.2
			intensity := 0.0
			if distance := math.Abs(position - center); distance < shimmerBand {
				intensity = 0.5 * (1 + math.Cos(math.Pi*distance/shimmerBand))
			}
			intensity = math.Max(0, math.Min(1, intensity+breath+0.06))
			level := int(intensity*float64(levels) + 0.5)
			if r == ' ' {
				level = 0
			}
			if level != runLevel {
				flush()
				runLevel = level
			}
			run.WriteRune(r)
		}
		flush()
		out[y] = line.String()
	}
	return out
}
