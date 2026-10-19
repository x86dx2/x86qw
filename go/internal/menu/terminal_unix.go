//go:build !windows

package menu

import (
	"os"
	"unicode/utf8"

	"golang.org/x/sys/unix"
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

// waitReadable mirrors select.select with a timeout in milliseconds.
func waitReadable(fd int, millis int) bool {
	var set unix.FdSet
	set.Set(fd)
	timeout := unix.NsecToTimeval(int64(millis) * 1_000_000)
	n, err := unix.Select(fd+1, &set, nil, nil, &timeout)
	return err == nil && n > 0
}

func readByte(fd int) (byte, bool) {
	buffer := make([]byte, 1)
	n, err := unix.Read(fd, buffer)
	return buffer[0], err == nil && n == 1
}

// decodeEscape mirrors _decode_posix_escape.
func decodeEscape(sequence []byte) string {
	if len(sequence) == 0 || (sequence[0] != '[' && sequence[0] != 'O') {
		return "unknown"
	}
	last := sequence[len(sequence)-1]
	letters := map[byte]string{'A': "up", 'B': "down", 'C': "right", 'D': "left", 'H': "home", 'F': "end"}
	if sequence[0] == 'O' {
		if name, ok := letters[last]; ok {
			return name
		}
		return "unknown"
	}
	body := sequence[1:]
	if len(body) > 0 && body[len(body)-1] == '~' {
		code := string(body[:len(body)-1])
		for index, r := range code {
			if r == ';' {
				code = code[:index]
				break
			}
		}
		switch code {
		case "1":
			return "home"
		case "4":
			return "end"
		case "5":
			return "pageup"
		case "6":
			return "pagedown"
		}
		return "unknown"
	}
	if name, ok := letters[last]; ok {
		return name
	}
	return "unknown"
}

// readTerminalKey mirrors _read_posix_key in raw mode.
func readTerminalKey() (string, error) {
	fd := int(os.Stdin.Fd())
	previous, err := term.MakeRaw(fd)
	if err != nil {
		return "", err
	}
	defer term.Restore(fd, previous)
	first, ok := readByte(fd)
	if !ok {
		return "", os.ErrClosed
	}
	switch first {
	case 0x1b:
		var sequence []byte
		timeout := 120
		for len(sequence) < 16 {
			if !waitReadable(fd, timeout) {
				break
			}
			b, ok := readByte(fd)
			if !ok {
				break
			}
			sequence = append(sequence, b)
			timeout = 40
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
		if !waitReadable(fd, 40) {
			return "unknown", nil
		}
		b, ok := readByte(fd)
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
