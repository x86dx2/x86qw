package tui

import (
	"os"
	"runtime"
)

// Glyphs are the symbols the interface draws with; every one is a single
// column. The legacy Windows console gets plain fallbacks.
type Glyphs struct {
	Radio, RadioOff, Check, CheckOff, Ok, Fail, Warn string
	Diamond, DiamondOff, Chevron, Ellipsis, Rail     string
	Spinner                                          []string
	Eighths                                          []string // progress bar partial blocks, emptiest first
	Spark                                            []string
	Braille                                          bool // the logo can be drawn
}

var unicodeGlyphs = Glyphs{
	Radio: "●", RadioOff: "○", Check: "■", CheckOff: "□", Ok: "✓", Fail: "✗", Warn: "!",
	Diamond: "◆", DiamondOff: "◇", Chevron: "›", Ellipsis: "…", Rail: "▏",
	Spinner: []string{"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧"},
	Eighths: []string{" ", "▏", "▎", "▍", "▌", "▋", "▊", "▉", "█"},
	Spark:   []string{"▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"},
	Braille: true,
}

var legacyGlyphs = Glyphs{
	Radio: "•", RadioOff: "o", Check: "x", CheckOff: " ", Ok: "√", Fail: "x", Warn: "!",
	Diamond: "♦", DiamondOff: "o", Chevron: ">", Ellipsis: "...", Rail: "|",
	Spinner: []string{"|", "/", "-", "\\"},
	Eighths: []string{" ", "░", "░", "▒", "▒", "▓", "▓", "█", "█"},
	Spark:   []string{"_", "_", ".", ".", "-", "-", "=", "#"},
}

// DetectGlyphs picks the legacy set only on the classic Windows console.
func DetectGlyphs() Glyphs {
	if runtime.GOOS == "windows" && os.Getenv("WT_SESSION") == "" && os.Getenv("TERM_PROGRAM") == "" {
		return legacyGlyphs
	}
	return unicodeGlyphs
}
