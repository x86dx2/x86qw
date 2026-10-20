//go:build windows

package menu

import (
	"os"
	"unicode/utf16"
	"unsafe"

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

var procReadConsoleInput = windows.NewLazySystemDLL("kernel32.dll").NewProc("ReadConsoleInputW")

// inputRecord is INPUT_RECORD restricted to its KEY_EVENT_RECORD member.
type inputRecord struct {
	eventType       uint16
	_               uint16
	keyDown         int32
	repeatCount     uint16
	virtualKeyCode  uint16
	virtualScanCode uint16
	unicodeChar     uint16
	controlKeyState uint32
}

// INPUT_RECORD is 20 bytes on every Windows architecture.
var _ [20]byte = [unsafe.Sizeof(inputRecord{})]byte{}

// keyDown returns the next key-down record, skipping key-up, focus, mouse
// and resize events.
func keyDown(handle windows.Handle) (inputRecord, error) {
	for {
		var record inputRecord
		var read uint32
		ok, _, err := procReadConsoleInput.Call(uintptr(handle), uintptr(unsafe.Pointer(&record)), 1, uintptr(unsafe.Pointer(&read)))
		if ok == 0 {
			return record, err
		}
		if read == 1 && record.eventType == windows.KEY_EVENT && record.keyDown != 0 {
			return record, nil
		}
	}
}

var virtualKeys = map[uint16]string{
	windows.VK_UP: "up", windows.VK_DOWN: "down", windows.VK_LEFT: "left", windows.VK_RIGHT: "right",
	windows.VK_HOME: "home", windows.VK_END: "end", windows.VK_PRIOR: "pageup", windows.VK_NEXT: "pagedown",
	windows.VK_ESCAPE: "escape", windows.VK_RETURN: "enter", windows.VK_BACK: "backspace",
}

// modifierKeys produce no character on their own (getwch ignores them).
var modifierKeys = map[uint16]bool{
	windows.VK_SHIFT: true, windows.VK_CONTROL: true, windows.VK_MENU: true, windows.VK_CAPITAL: true,
	windows.VK_LWIN: true, windows.VK_RWIN: true, windows.VK_NUMLOCK: true, windows.VK_SCROLL: true,
}

// readTerminalKey mirrors _read_windows_key: one key-down record read
// with line input, echo and Ctrl-C processing disabled.
func readTerminalKey() (string, error) {
	handle := windows.Handle(os.Stdin.Fd())
	var mode uint32
	if err := windows.GetConsoleMode(handle, &mode); err != nil {
		return "", err
	}
	raw := mode &^ (windows.ENABLE_LINE_INPUT | windows.ENABLE_ECHO_INPUT | windows.ENABLE_PROCESSED_INPUT | windows.ENABLE_VIRTUAL_TERMINAL_INPUT)
	if err := windows.SetConsoleMode(handle, raw); err != nil {
		return "", err
	}
	defer windows.SetConsoleMode(handle, mode)
	for {
		record, err := keyDown(handle)
		if err != nil {
			return "", err
		}
		if name, ok := virtualKeys[record.virtualKeyCode]; ok {
			return name, nil
		}
		char := record.unicodeChar
		switch {
		case char == 0x03:
			return "interrupt", nil
		case char == '\r':
			return "enter", nil
		case char == 0 && modifierKeys[record.virtualKeyCode]:
			continue
		case char == 0:
			return "unknown", nil
		}
		units := []uint16{char}
		if utf16.IsSurrogate(rune(char)) {
			low, err := keyDown(handle)
			if err != nil {
				return "", err
			}
			units = append(units, low.unicodeChar)
		}
		text := string(utf16.Decode(units))
		if !printable(text) {
			return "unknown", nil
		}
		return text, nil
	}
}
