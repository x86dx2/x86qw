// Package menu ports x86qw_runtime/ui/menu.py: keyboard navigation on a
// terminal and the line-based fallback (same Portuguese prompts) otherwise.
package menu

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"unicode"

	"golang.org/x/text/runes"
	"golang.org/x/text/transform"
	"golang.org/x/text/unicode/norm"
)

// Option is one menu entry (MenuOption).
type Option struct {
	Key, Label, Description, Detail string
	Disabled                        bool
	Aliases                         []string
	DisabledReason                  string
	Group                           string
}

func (o Option) enabled() bool { return !o.Disabled }

var (
	// ErrCancelled is MenuCancelled: EOF on the fallback or ← without back.
	ErrCancelled = errors.New("menu cancelado")
	// ErrExit is MenuExit: q/sair or Esc.
	ErrExit = errors.New("menu encerrado")
	// ErrInterrupt is Ctrl-C inside navigation (KeyboardInterrupt).
	ErrInterrupt = errors.New("interrompido")
)

const (
	colorAccent  = "38;2;255;77;77"
	colorSuccess = "38;2;0;229;204"
	colorWarning = "38;2;255;176;32"
	colorMuted   = "38;2;90;100;128"
)

// UI holds the streams and mode of one interactive session.
type UI struct {
	In         *bufio.Reader
	Out        io.Writer
	Navigation bool // keyboard navigation (TTY) instead of the fallback
	Color      bool
	ReadKey    func() (string, error)
	Width      int
	Lines      int
}

// Settings mirror select_one's keyword arguments.
type Settings struct {
	Breadcrumb, Subtitle string
	Default              int
	Searchable           bool
	AllowBack            bool
	InvalidMessage       string
}

// Fold mirrors _fold: casefold and strip combining marks.
func Fold(value string) string {
	chain := transform.Chain(norm.NFD, runes.Remove(runes.In(unicode.Mn)))
	folded, _, _ := transform.String(chain, strings.ToLower(value))
	return folded
}

func searchText(o Option) string {
	return strings.Join(append([]string{o.Key, o.Label, o.Description, o.Detail, o.DisabledReason, o.Group}, o.Aliases...), " ")
}

func matching(options []Option, query string) []int {
	var result []int
	folded := Fold(query)
	for index, option := range options {
		if query == "" || strings.Contains(Fold(searchText(option)), folded) {
			result = append(result, index)
		}
	}
	return result
}

func (u *UI) printf(format string, args ...any) { fmt.Fprintf(u.Out, format, args...) }

func (u *UI) paint(value, code string) string {
	if !u.Color || code == "" {
		return value
	}
	return "\033[" + code + "m" + value + "\033[0m"
}

func (u *UI) width() int {
	width := u.Width
	if width == 0 {
		width = 88
	}
	if width < 20 {
		width = 20
	}
	if width > 110 {
		width = 110
	}
	return width
}

func runeLen(value string) int { return len([]rune(value)) }

func pad(value string, width int) string {
	if missing := width - runeLen(value); missing > 0 {
		return value + strings.Repeat(" ", missing)
	}
	return value
}

// wrap mirrors textwrap.wrap(break_long_words=False, break_on_hyphens=False).
func wrap(value string, width int) []string {
	if width < 8 {
		width = 8
	}
	words := strings.Fields(value)
	if len(words) == 0 {
		return []string{""}
	}
	var lines []string
	current := ""
	for _, word := range words {
		switch {
		case current == "":
			current = word
		case runeLen(current)+1+runeLen(word) <= width:
			current += " " + word
		default:
			lines = append(lines, current)
			current = word
		}
	}
	return append(lines, current)
}

func (u *UI) emitText(value, code string) {
	lines := strings.Split(value, "\n")
	for _, line := range lines {
		if line == "" {
			u.printf("\n")
			continue
		}
		for _, part := range wrap(line, u.width()) {
			u.printf("%s\n", u.paint(part, code))
		}
	}
}

func (u *UI) readLine(prompt string) (string, error) {
	u.printf("%s", prompt)
	line, err := u.In.ReadString('\n')
	if err != nil && line == "" {
		return "", io.EOF
	}
	return strings.TrimRight(line, "\r\n"), nil
}

// Pause mirrors pause_menu_result: false on EOF.
func (u *UI) Pause(message string) bool {
	_, err := u.readLine(message)
	return err == nil
}

// SelectOne returns the chosen key; back=true when the user went back.
func (u *UI) SelectOne(title string, options []Option, settings Settings) (string, bool, error) {
	if len(options) == 0 {
		return "", false, errors.New("menu options must be non-empty")
	}
	seen := map[string]bool{}
	anyEnabled := false
	for _, option := range options {
		if seen[option.Key] {
			return "", false, errors.New("menu options must have unique keys")
		}
		seen[option.Key] = true
		anyEnabled = anyEnabled || option.enabled()
	}
	if !anyEnabled {
		return "", false, errors.New("menu has no enabled options")
	}
	if settings.InvalidMessage == "" {
		settings.InvalidMessage = "Seleção inválida; tente novamente."
	}
	if u.Navigation {
		return u.selectNavigation(title, options, settings)
	}
	return u.selectFallback(title, options, settings)
}

func (u *UI) selectFallback(title string, options []Option, s Settings) (string, bool, error) {
	var enabled []Option
	for _, option := range options {
		if option.enabled() {
			enabled = append(enabled, option)
		}
	}
	clampDefault := func() string {
		index := s.Default
		if index > len(enabled)-1 {
			index = len(enabled) - 1
		}
		if index < 0 {
			index = 0
		}
		return enabled[index].Key
	}
	defaultKey := clampDefault()
	visible := append([]Option(nil), options...)
	for {
		if s.Breadcrumb != "" {
			u.printf("\n")
			u.emitText(s.Breadcrumb, "")
		}
		u.printf("\n")
		u.emitText(title, "")
		if s.Subtitle != "" {
			u.emitText(s.Subtitle, "")
		}
		labels := make([]string, len(visible))
		labelWidth, descriptionWidth := 0, 0
		for index, option := range visible {
			labels[index] = option.Label
			if option.Key == defaultKey {
				labels[index] += " (padrão)"
			}
			if runeLen(labels[index]) > labelWidth {
				labelWidth = runeLen(labels[index])
			}
			if runeLen(option.Description) > descriptionWidth {
				descriptionWidth = runeLen(option.Description)
			}
		}
		indexWidth := len(strconv.Itoa(len(visible)))
		previousGroup := ""
		for index, option := range visible {
			if option.Group != "" && option.Group != previousGroup {
				u.printf("  %s\n", option.Group)
				previousGroup = option.Group
			}
			line := fmt.Sprintf("  %*d) %s", indexWidth, index+1, pad(labels[index], labelWidth))
			if descriptionWidth > 0 {
				line += " · " + pad(option.Description, descriptionWidth)
			}
			if option.Detail != "" {
				line += "  < " + option.Detail
			}
			if !option.enabled() {
				line += "  < indisponível"
				if option.DisabledReason != "" {
					line += ": " + option.DisabledReason
				}
			}
			u.printf("%s\n", line)
		}
		prompt := fmt.Sprintf("Escolha [1-%d]", len(visible))
		if s.Searchable {
			prompt += " ou digite para buscar"
			if len(visible) != len(options) {
				prompt += "; / para limpar a busca"
			}
		}
		if s.AllowBack {
			prompt += "; b para voltar"
		}
		prompt += "; q para sair"
		answer, err := u.readLine(prompt + ": ")
		if err != nil {
			return "", false, ErrCancelled
		}
		answer = strings.TrimSpace(answer)
		folded := strings.ToLower(answer)
		switch {
		case answer == "":
			return defaultKey, false, nil
		case s.AllowBack && (folded == "b" || folded == "voltar"):
			return "", true, nil
		case folded == "q" || folded == "sair":
			return "", false, ErrExit
		case s.Searchable && (folded == "/" || folded == "limpar"):
			visible = append([]Option(nil), options...)
			defaultKey = clampDefault()
			continue
		}
		if number, err := strconv.Atoi(answer); err == nil && isDigits(answer) && number >= 1 && number <= len(visible) {
			if visible[number-1].enabled() {
				return visible[number-1].Key, false, nil
			}
			u.printf("[ATENÇÃO] Esta opção não está disponível neste contexto.\n")
			continue
		}
		foldedAnswer := Fold(answer)
		var exact []Option
		for _, option := range enabled {
			names := append([]string{option.Key, option.Label}, option.Aliases...)
			for _, name := range names {
				if Fold(name) == foldedAnswer {
					exact = append(exact, option)
					break
				}
			}
		}
		if len(exact) == 1 {
			return exact[0].Key, false, nil
		}
		if s.Searchable {
			var found []Option
			for _, option := range visible {
				if strings.Contains(Fold(searchText(option)), foldedAnswer) {
					found = append(found, option)
				}
			}
			if len(found) == 1 && found[0].enabled() {
				return found[0].Key, false, nil
			}
			if len(found) > 0 {
				visible = found
				for _, option := range visible {
					if option.enabled() {
						defaultKey = option.Key
						break
					}
				}
				continue
			}
		}
		u.printf("[ATENÇÃO] %s\n", s.InvalidMessage)
	}
}

func isDigits(value string) bool {
	if value == "" {
		return false
	}
	for _, r := range value {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

// Confirm mirrors confirm(); back=true when the user went back.
func (u *UI) Confirm(title string, settings Settings, description string, defaultYes bool) (bool, bool, error) {
	if description == "" {
		description = "confirmar esta escolha"
	}
	if settings.InvalidMessage == "" {
		settings.InvalidMessage = "Resposta inválida. Digite sim ou não."
	}
	settings.Default = 1
	if defaultYes {
		settings.Default = 0
	}
	key, back, err := u.SelectOne(title, []Option{
		{Key: "yes", Label: "Sim", Description: description, Aliases: []string{"s", "sim", "y", "yes"}},
		{Key: "no", Label: "Não", Description: "não executar esta ação", Aliases: []string{"n", "nao", "não", "no"}},
	}, settings)
	return key == "yes", back, err
}

// PromptText mirrors prompt_text in its line form.
func (u *UI) PromptText(title, defaultValue, description string) (string, error) {
	u.printf("\n%s\n", title)
	if defaultValue != "" {
		u.printf("Sugestão: %s\n", defaultValue)
	}
	if description != "" {
		u.printf("%s\n", description)
	}
	answer, err := u.readLine("Diretório de instalação: ")
	if err != nil {
		return "", ErrCancelled
	}
	if answer = strings.TrimSpace(answer); answer == "" {
		return defaultValue, nil
	}
	return answer, nil
}

// SelectMany mirrors select_many's fallback (comma-separated choices).
func (u *UI) SelectMany(title string, options []Option, settings Settings, selected []string, allowEmpty bool) ([]string, bool, error) {
	checked := map[string]bool{}
	var enabled []Option
	for _, option := range options {
		if option.enabled() {
			enabled = append(enabled, option)
		}
	}
	for _, key := range selected {
		for _, option := range enabled {
			if option.Key == key {
				checked[key] = true
			}
		}
	}
	if u.Navigation {
		return u.selectManyNavigation(title, options, enabled, settings, checked, allowEmpty)
	}
	for {
		labelWidth, descriptionWidth := 0, 0
		for _, option := range options {
			if runeLen(option.Label) > labelWidth {
				labelWidth = runeLen(option.Label)
			}
		}
		for _, option := range enabled {
			if runeLen(option.Description) > descriptionWidth {
				descriptionWidth = runeLen(option.Description)
			}
		}
		u.printf("\n")
		if settings.Breadcrumb != "" {
			u.emitText(settings.Breadcrumb, "")
		}
		u.emitText(title, "")
		if settings.Subtitle != "" {
			u.emitText(settings.Subtitle, "")
		}
		indexWidth := len(strconv.Itoa(len(options)))
		previousGroup := ""
		for index, option := range options {
			if option.Group != "" && option.Group != previousGroup {
				u.printf("  %s\n", option.Group)
				previousGroup = option.Group
			}
			mark := " "
			if checked[option.Key] {
				mark = "x"
			}
			line := fmt.Sprintf("  %*d) [%s] %s", indexWidth, index+1, mark, pad(option.Label, labelWidth))
			if descriptionWidth > 0 {
				line += " · " + pad(option.Description, descriptionWidth)
			}
			if option.Detail != "" {
				line += "  < " + option.Detail
			}
			if !option.enabled() {
				line += "  < indisponível"
				if option.DisabledReason != "" {
					line += ": " + option.DisabledReason
				}
			}
			u.printf("%s\n", line)
		}
		prompt := "Informe números ou identificadores separados por vírgula"
		if settings.AllowBack {
			prompt += "; b para voltar"
		}
		prompt += "; q para sair"
		answer, err := u.readLine(prompt + ": ")
		if err != nil {
			return nil, false, ErrCancelled
		}
		answer = strings.TrimSpace(answer)
		folded := strings.ToLower(answer)
		if settings.AllowBack && (folded == "b" || folded == "voltar") {
			return nil, true, nil
		}
		if folded == "q" || folded == "sair" {
			return nil, false, ErrExit
		}
		if answer == "" {
			result := orderedChecked(enabled, checked)
			if len(result) > 0 || allowEmpty {
				return result, false, nil
			}
			return nil, false, ErrCancelled
		}
		chosen := map[string]bool{}
		invalid := false
		for _, token := range strings.Split(answer, ",") {
			token = strings.TrimSpace(token)
			if number, err := strconv.Atoi(token); err == nil && isDigits(token) && number >= 1 && number <= len(options) {
				if options[number-1].enabled() {
					chosen[options[number-1].Key] = true
				} else {
					invalid = true
				}
				continue
			}
			var hits []Option
			for _, option := range enabled {
				for _, name := range append([]string{option.Key, option.Label}, option.Aliases...) {
					if Fold(name) == Fold(token) {
						hits = append(hits, option)
						break
					}
				}
			}
			if len(hits) != 1 {
				invalid = true
				break
			}
			chosen[hits[0].Key] = true
		}
		if invalid {
			u.printf("[ATENÇÃO] Seleção inválida; tente novamente.\n")
			continue
		}
		return orderedChecked(enabled, chosen), false, nil
	}
}

func orderedChecked(enabled []Option, checked map[string]bool) []string {
	result := []string{}
	for _, option := range enabled {
		if checked[option.Key] {
			result = append(result, option.Key)
		}
	}
	return result
}

// Stdio builds a UI on the process streams: navigation only when stdin and
// stdout are terminals and TERM is not dumb (supports_navigation).
func Stdio(noColor bool) *UI {
	ui := &UI{In: bufio.NewReader(os.Stdin), Out: os.Stdout}
	ui.Navigation = isTerminal(os.Stdin) && isTerminal(os.Stdout) && !strings.EqualFold(os.Getenv("TERM"), "dumb")
	_, noColorEnv := os.LookupEnv("NO_COLOR")
	ui.Color = !noColor && !noColorEnv && isTerminal(os.Stdout)
	ui.Width, ui.Lines = terminalSize()
	if ui.Navigation {
		ui.ReadKey = readTerminalKey
	}
	return ui
}
