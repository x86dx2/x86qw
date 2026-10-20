package menu

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
