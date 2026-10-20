package menu

import (
	"fmt"
	"strconv"
	"strings"
	"unicode"
)

const (
	clearScreen = "\033[2J\033[H"
	hideCursor  = "\033[?25l"
	showCursor  = "\033[?25h"
)

func (u *UI) rowBudget() int {
	lines := u.Lines
	if lines == 0 {
		lines = 24
	}
	reserved := 11
	if lines < 22 {
		reserved = 8
	}
	if lines-reserved < 3 {
		return 3
	}
	return lines - reserved
}

func (u *UI) compact() bool { return u.Lines > 0 && u.Lines < 22 }

func (u *UI) rule() string {
	width := u.width()
	if width > 42 {
		width = 42
	}
	return u.paint(strings.Repeat("─", width), colorMuted)
}

func selectable(options []Option, matches []int) []int {
	var positions []int
	for position, index := range matches {
		if options[index].enabled() {
			positions = append(positions, position)
		}
	}
	return positions
}

func defaultPosition(options []Option, matches []int, def int) int {
	positions := selectable(options, matches)
	if len(positions) == 0 {
		return 0
	}
	if def > len(positions)-1 {
		def = len(positions) - 1
	}
	return positions[def]
}

func moveSelection(selected int, positions []int, key string, page int) int {
	if len(positions) == 0 {
		return selected
	}
	current := 0
	for index, position := range positions {
		if position == selected {
			current = index
		}
	}
	switch key {
	case "up", "k":
		return positions[(current-1+len(positions))%len(positions)]
	case "down", "j":
		return positions[(current+1)%len(positions)]
	case "home":
		return positions[0]
	case "end":
		return positions[len(positions)-1]
	case "pageup":
		if current-page < 0 {
			return positions[0]
		}
		return positions[current-page]
	case "pagedown":
		if current+page > len(positions)-1 {
			return positions[len(positions)-1]
		}
		return positions[current+page]
	}
	return selected
}

func (u *UI) header(title string, s Settings, query string, searching bool, numeric, numericLabel string) {
	if s.Breadcrumb != "" {
		u.emitText(s.Breadcrumb, colorMuted)
		if !u.compact() {
			u.printf("%s\n", u.rule())
		}
	}
	u.emitText(title, colorAccent+"m\033[1")
	if s.Subtitle != "" {
		u.emitText(s.Subtitle, "")
	}
	if s.Searchable {
		if searching || query != "" {
			field := query
			if searching {
				field += "_"
			}
			u.printf("\n%s %s\n", u.paint("/", colorMuted), u.paint(field, colorWarning+"m\033[1"))
		} else {
			u.printf("%s\n", u.paint("\n/  buscar", colorMuted))
		}
	}
	if numeric != "" {
		u.printf("\n%s: %s\n", numericLabel, u.paint(numeric+"_", colorWarning+"m\033[1"))
	}
	u.printf("\n")
}

func (u *UI) rows(options []Option, matches []int, selected int, marker func(int, Option) string) {
	if len(matches) == 0 {
		u.printf("%s\n", u.paint("  Nenhum resultado para esta busca.", colorMuted))
		return
	}
	labelWidth, descriptionWidth := 0, 0
	for _, index := range matches {
		if runeLen(options[index].Label) > labelWidth {
			labelWidth = runeLen(options[index].Label)
		}
		if runeLen(options[index].Description) > descriptionWidth {
			descriptionWidth = runeLen(options[index].Description)
		}
	}
	budget := u.rowBudget()
	start := selected - budget/2
	if start < 0 {
		start = 0
	}
	end := start + budget
	if end > len(matches) {
		end = len(matches)
		if end-budget >= 0 {
			start = end - budget
		} else {
			start = 0
		}
	}
	previousGroup := ""
	for position := start; position < end; position++ {
		option := options[matches[position]]
		if option.Group != "" && option.Group != previousGroup {
			if position > start {
				u.printf("\n")
			}
			u.printf("%s\n", u.paint(option.Group, colorMuted))
			previousGroup = option.Group
		}
		line := marker(position, option) + " " + pad(option.Label, labelWidth)
		if descriptionWidth > 0 {
			line += " · " + pad(option.Description, descriptionWidth)
		}
		explanation := ""
		if position == selected && option.Detail != "" {
			explanation = option.Detail
		}
		if !option.enabled() {
			explanation = "indisponível"
			if option.DisabledReason != "" {
				explanation += ": " + option.DisabledReason
			}
		}
		switch {
		case !option.enabled():
			line = u.paint(line, colorMuted)
		case position == selected:
			line = u.paint(line, colorSuccess+"m\033[1")
		}
		if explanation != "" {
			line += u.paint("  < "+explanation, colorMuted)
		}
		u.printf("%s\n", line)
	}
	if len(matches) > end-start {
		u.printf("%s\n", u.paint(fmt.Sprintf("\n%d–%d de %d", start+1, end, len(matches)), colorMuted))
	}
}

func (u *UI) footer(items []string) {
	u.printf("\n")
	if !u.compact() {
		u.printf("%s\n", u.rule())
	}
	current := ""
	for _, item := range items {
		candidate := item
		if current != "" {
			candidate = current + "   " + item
		}
		if current != "" && runeLen(candidate) > u.width() {
			u.printf("%s\n", u.paint(current, colorMuted))
			current = item
		} else {
			current = candidate
		}
	}
	if current != "" {
		u.printf("%s\n", u.paint(current, colorMuted))
	}
}

func printable(key string) bool {
	r := []rune(key)
	return len(r) == 1 && unicode.IsPrint(r[0])
}

func (u *UI) selectNavigation(title string, options []Option, s Settings) (string, bool, error) {
	query, searching, numeric := "", false, ""
	matches := matching(options, query)
	selected := defaultPosition(options, matches, s.Default)
	defer u.printf("%s", showCursor)
	for {
		u.printf("%s%s", clearScreen, hideCursor)
		u.header(title, s, query, searching, numeric, "Ir para o item")
		width := len(strconv.Itoa(max(len(matches), 1)))
		u.rows(options, matches, selected, func(position int, _ Option) string {
			caret := " "
			if position == selected {
				caret = "▸"
			}
			return fmt.Sprintf("%s %*d)", caret, width, position+1)
		})
		var items []string
		switch {
		case searching:
			items = []string{"Digite para buscar", "Enter aplicar busca", "Esc limpar busca"}
		case numeric != "":
			items = []string{"Digite o número completo", "→/Enter selecionar", "Esc limpar número"}
		default:
			items = []string{"↑↓ navegar", "→/Enter selecionar"}
			if s.Searchable {
				items = append(items, "/ buscar")
			}
			if s.AllowBack {
				items = append(items, "← voltar")
			} else {
				items = append(items, "← cancelar")
			}
			items = append(items, "esc sair")
		}
		u.footer(items)
		key, err := u.ReadKey()
		if err != nil {
			return "", false, ErrCancelled
		}
		if key == "interrupt" {
			return "", false, ErrInterrupt
		}
		if searching {
			switch {
			case key == "enter":
				searching = false
			case key == "escape":
				query, searching = "", false
			case key == "backspace":
				if r := []rune(query); len(r) > 0 {
					query = string(r[:len(r)-1])
				}
			case printable(key):
				query += key
			}
			matches = matching(options, query)
			selected = defaultPosition(options, matches, 0)
			continue
		}
		positions := selectable(options, matches)
		if numeric != "" {
			switch {
			case isDigits(key):
				if candidate, _ := strconv.Atoi(numeric + key); candidate <= len(matches) {
					numeric += key
				}
			case key == "backspace":
				numeric = numeric[:len(numeric)-1]
			case key == "enter" || key == "right":
				position, _ := strconv.Atoi(numeric)
				position--
				if position >= 0 && position < len(matches) && options[matches[position]].enabled() {
					return options[matches[position]].Key, false, nil
				}
				numeric = ""
			default:
				numeric = ""
			}
			continue
		}
		switch {
		case key == "up" || key == "down" || key == "k" || key == "j" || key == "home" || key == "end" || key == "pageup" || key == "pagedown":
			selected = moveSelection(selected, positions, key, max(1, u.rowBudget()/2))
		case (key == "enter" || key == "right") && len(positions) > 0:
			return options[matches[selected]].Key, false, nil
		case key == "left":
			if s.AllowBack {
				return "", true, nil
			}
			return "", false, ErrCancelled
		case key == "escape" || key == "q":
			return "", false, ErrExit
		case key == "/" && s.Searchable:
			searching = true
		case isDigits(key) && key != "0":
			if len(matches) > 9 {
				numeric = key
			} else if position, _ := strconv.Atoi(key); position-1 < len(matches) && options[matches[position-1]].enabled() {
				return options[matches[position-1]].Key, false, nil
			}
		}
	}
}

func (u *UI) selectManyNavigation(title string, options, enabled []Option, s Settings, checked map[string]bool, allowEmpty bool) ([]string, bool, error) {
	query, searching, numeric, message := "", false, "", ""
	matches := matching(options, query)
	position := defaultPosition(options, matches, 0)
	defer u.printf("%s", showCursor)
	toggle := func(key string) {
		if checked[key] {
			delete(checked, key)
		} else {
			checked[key] = true
		}
	}
	for {
		u.printf("%s%s", clearScreen, hideCursor)
		u.header(title, s, query, searching, numeric, "Marcar item")
		width := len(strconv.Itoa(max(len(matches), 1)))
		u.rows(options, matches, position, func(index int, option Option) string {
			caret, box := " ", "[ ]"
			if index == position {
				caret = "▸"
			}
			if checked[option.Key] {
				box = "[✓]"
			}
			return fmt.Sprintf("%s %*d) %s", caret, width, index+1, box)
		})
		if message != "" {
			u.printf("\n%s\n", u.paint(message, colorWarning))
		}
		items := []string{"↑↓ navegar", "Espaço marcar", "→/Enter concluir", "A marcar tudo", "D desmarcar tudo"}
		if s.Searchable {
			items = append(items, "/ buscar")
		}
		if s.AllowBack {
			items = append(items, "← voltar")
		} else {
			items = append(items, "← cancelar")
		}
		u.footer(append(items, "esc sair"))
		key, err := u.ReadKey()
		if err != nil {
			return nil, false, ErrCancelled
		}
		if key == "interrupt" {
			return nil, false, ErrInterrupt
		}
		if searching {
			switch {
			case key == "enter":
				searching = false
			case key == "escape":
				query, searching = "", false
			case key == "backspace":
				if r := []rune(query); len(r) > 0 {
					query = string(r[:len(r)-1])
				}
			case printable(key):
				query += key
			}
			matches = matching(options, query)
			position = defaultPosition(options, matches, 0)
			message = ""
			continue
		}
		positions := selectable(options, matches)
		if numeric != "" {
			switch {
			case isDigits(key):
				if candidate, _ := strconv.Atoi(numeric + key); candidate <= len(matches) {
					numeric += key
				}
			case key == "backspace":
				numeric = numeric[:len(numeric)-1]
			case key == " " || key == "enter":
				index, _ := strconv.Atoi(numeric)
				if index-1 >= 0 && index-1 < len(matches) && options[matches[index-1]].enabled() {
					toggle(options[matches[index-1]].Key)
				}
				numeric, message = "", ""
			default:
				numeric = ""
			}
			continue
		}
		switch {
		case key == "up" || key == "down" || key == "k" || key == "j" || key == "home" || key == "end" || key == "pageup" || key == "pagedown":
			position = moveSelection(position, positions, key, max(1, u.rowBudget()/2))
		case key == " " && len(positions) > 0:
			toggle(options[matches[position]].Key)
			message = ""
		case strings.EqualFold(key, "a"):
			for _, option := range enabled {
				checked[option.Key] = true
			}
			message = ""
		case strings.EqualFold(key, "d"):
			for key := range checked {
				delete(checked, key)
			}
			message = ""
		case key == "enter" || key == "right":
			result := orderedChecked(enabled, checked)
			if len(result) > 0 || allowEmpty {
				return result, false, nil
			}
			message = "Selecione ao menos um componente."
		case key == "left":
			if s.AllowBack {
				return nil, true, nil
			}
			return nil, false, ErrCancelled
		case key == "escape" || key == "q":
			return nil, false, ErrExit
		case key == "/" && s.Searchable:
			searching = true
		case isDigits(key) && key != "0":
			if len(matches) > 9 {
				numeric = key
			} else if index, _ := strconv.Atoi(key); index-1 < len(matches) && options[matches[index-1]].enabled() {
				toggle(options[matches[index-1]].Key)
				message = ""
			}
		}
	}
}
