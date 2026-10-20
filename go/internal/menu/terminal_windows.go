//go:build windows

package menu

import (
	"os"
	"time"
	"unicode/utf8"

	"golang.org/x/sys/windows"
	"golang.org/x/term"
)

func isTerminal(file *os.File) bool { return term.IsTerminal(int(file.Fd())) }

func terminalSize() (int, int) {
	width, height, err := term.GetSize(int(os.Stdout.Fd()))
	if err != nil {
		return 88, 24
	}
	return width, height
}

// enableVirtualTerminal turns on ANSI processing for the console output, so
// colors and redraws work in conhost as well as Windows Terminal. Without it
// the menu falls back to numbered lines.
func enableVirtualTerminal() bool {
	handle := windows.Handle(os.Stdout.Fd())
	var mode uint32
	if windows.GetConsoleMode(handle, &mode) != nil {
		return false
	}
	if mode&windows.ENABLE_VIRTUAL_TERMINAL_PROCESSING != 0 {
		return true
	}
	return windows.SetConsoleMode(handle, mode|windows.ENABLE_VIRTUAL_TERMINAL_PROCESSING) == nil
}

// pending holds console bytes read past the current key (a VT sequence or
// pasted text arrives as one burst).
var pending []byte

// fill reads the next console burst, waiting at most timeout (0 = forever).
func fill(timeout time.Duration) bool {
	if len(pending) > 0 {
		return true
	}
	if timeout > 0 {
		event, err := windows.WaitForSingleObject(windows.Handle(os.Stdin.Fd()), uint32(timeout/time.Millisecond))
		if err != nil || event != windows.WAIT_OBJECT_0 {
			return false
		}
	}
	buffer := make([]byte, 64)
	n, err := os.Stdin.Read(buffer)
	if err != nil || n == 0 {
		return false
	}
	pending = append(pending, buffer[:n]...)
	return true
}

func next(timeout time.Duration) (byte, bool) {
	if !fill(timeout) {
		return 0, false
	}
	b := pending[0]
	pending = pending[1:]
	return b, true
}

// readTerminalKey reads one key in raw mode with VT input enabled, so arrows
// and navigation keys arrive as the same escape sequences as on POSIX
// (the getwch mapping of _read_windows_key).
func readTerminalKey() (string, error) {
	fd := int(os.Stdin.Fd())
	previous, err := term.MakeRaw(fd)
	if err != nil {
		return "", err
	}
	defer term.Restore(fd, previous)
	first, ok := next(0)
	if !ok {
		return "", os.ErrClosed
	}
	switch first {
	case 0x1b:
		var sequence []byte
		timeout := 120 * time.Millisecond
		for len(sequence) < 16 {
			b, ok := next(timeout)
			if !ok {
				break
			}
			sequence = append(sequence, b)
			timeout = 40 * time.Millisecond
			if sequence[0] == '[' {
				if len(sequence) >= 2 && b >= 0x40 && b <= 0x7e {
					break
				}
			} else if sequence[0] == 'O' {
				if len(sequence) >= 2 {
					break
				}
			} else {
				break
			}
		}
		if len(sequence) == 0 {
			return "escape", nil
		}
		return decodeEscape(sequence), nil
	case '\r', '\n':
		return "enter", nil
	case 0x7f, 0x08:
		return "backspace", nil
	case 0x03:
		return "interrupt", nil
	}
	data := []byte{first}
	need := 1
	switch {
	case first >= 0xC2 && first <= 0xDF:
		need = 2
	case first >= 0xE0 && first <= 0xEF:
		need = 3
	case first >= 0xF0 && first <= 0xF4:
		need = 4
	case first >= 0x80:
		return "unknown", nil
	}
	for len(data) < need {
		b, ok := next(40 * time.Millisecond)
		if !ok {
			return "unknown", nil
		}
		data = append(data, b)
	}
	r, _ := utf8.DecodeRune(data)
	if r == utf8.RuneError || !printable(string(r)) {
		return "unknown", nil
	}
	return string(r), nil
}
