package tui

import "os"

// Glyphs are the symbols the interface draws with; every one is a single
// column. The legacy Windows console gets plain fallbacks.
type Glyphs struct {
	Radio, RadioOff, Check, CheckOff, Ok, Fail, Warn string
	Diamond, DiamondOff, Chevron, Ellipsis, Rail     string
	Focus                                            string // marks the open section
	Spinner                                          []string
	Eighths                                          []string // progress bar partial blocks, emptiest first
	Spark                                            []string
	Braille                                          bool // the logo can be drawn
}

var unicodeGlyphs = Glyphs{
	Radio: "●", RadioOff: "○", Check: "■", CheckOff: "□", Ok: "✓", Fail: "✗", Warn: "!",
	Diamond: "◆", DiamondOff: "◇", Chevron: "›", Ellipsis: "…", Rail: "▏", Focus: "▌",
	Spinner: []string{"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧"},
	Eighths: []string{" ", "▏", "▎", "▍", "▌", "▋", "▊", "▉", "█"},
	Spark:   []string{"▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"},
	Braille: true,
}

var legacyGlyphs = Glyphs{
	Radio: "•", RadioOff: "o", Check: "x", CheckOff: " ", Ok: "√", Fail: "x", Warn: "!",
	Diamond: "♦", DiamondOff: "o", Chevron: ">", Ellipsis: "...", Rail: "|", Focus: "▌",
	Spinner: []string{"|", "/", "-", "\\"},
	Eighths: []string{" ", "░", "░", "▒", "▒", "▓", "▓", "█", "█"},
	Spark:   []string{"_", "_", ".", ".", "-", "-", "=", "#"},
}

// DetectGlyphs picks the legacy set only on the classic console of
// Windows 10, whose default font has no braille. Windows 11 hands
// consoles to Windows Terminal without setting WT_SESSION, so the system
// version decides, not the variable. X86QW_GLYPHS=unicode or ascii
// overrides both ways.
func DetectGlyphs() Glyphs {
	switch os.Getenv("X86QW_GLYPHS") {
	case "unicode":
		return unicodeGlyphs
	case "ascii":
		return legacyGlyphs
	}
	if legacyConsole() {
		return legacyGlyphs
	}
	return unicodeGlyphs
}
