package setup

import (
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/install"
)

// The interface languages.
const (
	LangPT = "pt"
	LangEN = "en"
)

// Text is something the backend says and the interface translates: a key
// of messages and the values its format takes. A value that is a Text, a
// Size or a Date is rendered in the same language.
type Text struct {
	Key  string
	Args []any
}

// Say builds a Text.
func Say(key string, args ...any) Text { return Text{Key: key, Args: args} }

// Raw is text that needs no translation: a path, a version, an error from
// below.
func Raw(text string) Text {
	if text == "" {
		return Text{}
	}
	return Text{Key: "%s", Args: []any{text}}
}

// IsZero reports whether nothing was said.
func (t Text) IsZero() bool { return t.Key == "" }

// String is the English, for logs and the command line.
func (t Text) String() string { return t.In(LangEN) }

// In renders the text in lang: Portuguese for "pt", English otherwise.
func (t Text) In(lang string) string {
	if t.Key == "" {
		return ""
	}
	format := t.Key
	if pair, ok := messages[t.Key]; ok {
		format = pair[1]
		if lang == LangPT {
			format = pair[0]
		}
	}
	if len(t.Args) == 0 {
		return format
	}
	args := make([]any, len(t.Args))
	for index, arg := range t.Args {
		switch value := arg.(type) {
		case Text:
			args[index] = value.In(lang)
		case Size:
			args[index] = FormatSize(int64(value), lang)
		case Date:
			layout := "2006-01-02 15:04"
			if lang == LangPT {
				layout = "02/01/2006 15:04"
			}
			args[index] = time.Time(value).Local().Format(layout)
		default:
			args[index] = arg
		}
	}
	return fmt.Sprintf(format, args...)
}

// Size is a number of bytes a Text shows the way its language writes it.
type Size int64

// Date is a moment a Text shows the way its language writes it.
type Date time.Time

// Problem is an error the interface can translate. Field names the choice
// it is about (nick, server.name, server.rcon…), so the interface can
// point there.
type Problem struct {
	Text  Text
	Field string
}

func (p *Problem) Error() string { return p.Text.String() }

func problem(field, key string, args ...any) error {
	return &Problem{Text: Say(key, args...), Field: field}
}

// ContentUpdated reports a failure that still left the content updated: an
// update whose new CLI could not be put in place.
func ContentUpdated(err error) bool { return errors.Is(err, install.ErrContentOnly) }

// Explain renders err in lang: a Problem translated, anything else as it
// came.
func Explain(err error, lang string) string {
	var p *Problem
	if errors.As(err, &p) {
		return p.Text.In(lang)
	}
	return err.Error()
}

// FieldOf is the choice an error is about, "" when it names none.
func FieldOf(err error) string {
	var p *Problem
	if errors.As(err, &p) {
		return p.Field
	}
	return ""
}

// FormatSize writes a size in bytes the way lang writes decimals.
func FormatSize(b int64, lang string) string {
	var text string
	switch {
	case b >= 1e9:
		text = fmt.Sprintf("%.2f GB", float64(b)/1e9)
	case b >= 1e8:
		text = fmt.Sprintf("%.0f MB", float64(b)/1e6)
	case b >= 1e6:
		text = fmt.Sprintf("%.1f MB", float64(b)/1e6)
	case b >= 1e3:
		text = fmt.Sprintf("%.0f KB", float64(b)/1e3)
	default:
		text = fmt.Sprintf("%d B", b)
	}
	if lang == LangPT {
		text = strings.Replace(text, ".", ",", 1)
	}
	return text
}

// OnDisk is what a download takes once extracted into the installation.
func OnDisk(download int64) int64 { return download * 108 / 100 }

// DiskNeeded is the free space an installation needs: the download stays
// in the cache while it is extracted.
func DiskNeeded(download int64) int64 { return download + OnDisk(download) }

// messages are the backend's texts, Portuguese and English.
var messages = map[string][2]string{
	"err.platform":    {"plataforma desconhecida: %s", "unknown platform: %s"},
	"err.game":        {"jogo não instalado: %s", "game not installed: %s"},
	"err.run":         {"o comando x86qw não está disponível aqui", "the x86qw command is not available here"},
	"err.choices":     {"escolhas do setup ilegíveis: %s", "the setup's choices are unreadable: %s"},
	"err.ruleset":     {"ruleset desconhecido: %s", "unknown ruleset: %s"},
	"err.catalog":     {"o catálogo publicado não respondeu: %s", "the published catalog did not answer: %s"},
	"err.noaddress":   {"resposta sem endereço", "the answer has no address"},
	"err.nohostname":  {"hostname vazio", "empty hostname"},
	"check.folder":    {"Pasta de instalação", "Install folder"},
	"folder.relative": {"O caminho precisa ser absoluto.", "The path must be absolute."},
	"folder.existing": {"Já tem uma instalação x86QW: ela recebe as mudanças.", "It already holds an x86QW installation: it gets the changes."},
	"folder.busy.fix": {"Espere a outra operação terminar ou encerre o servidor com x86qw status --stop.", "Wait for the other operation to end, or stop the server with x86qw status --stop."},
	"folder.new":      {"Será criada.", "It will be created."},
	"folder.plus":     {"O nome da pasta tem \" +\", que o ezQuake e o MVDSV leem como comando; escolha outra pasta.", "The folder name holds \" +\", which ezQuake and MVDSV read as a command; pick another folder."},
	"folder.full":     {"não está vazia", "not empty"},
	"folder.full.d":   {"%s tem %d itens que não são do x86QW.", "%s holds %d items that are not x86QW's."},
	"folder.full.fix": {"Os arquivos são preservados; prefira uma pasta vazia em Pasta.", "They are kept; an empty folder, picked in Folder, is better."},
	"folder.empty":    {"Vazia.", "Empty."},
	"folder.denied":   {"sem permissão", "no permission"},
	"folder.denied.d": {"Não dá para escrever em %s.", "%s is not writable."},
	"folder.other":    {"Escolha outra pasta em Pasta.", "Pick another folder in Folder."},
	"check.path":      {"Caminho do servidor", "Server path"},
	"path.chars":      {"%d caracteres", "%d characters"},
	"path.long.d":     {"O MVDSV não enxerga arquivos em caminhos tão longos e o servidor não sobe.", "MVDSV cannot see files on paths this long, and the server will not start."},
	"path.long.fix":   {"Escolha uma pasta com até %d caracteres em Pasta.", "Pick a folder of at most %d characters in Folder."},
	"path.ok.d":       {"Curto o bastante para o MVDSV.", "Short enough for MVDSV."},
	"check.disk":      {"Espaço em disco", "Disk space"},
	"disk.unknown":    {"desconhecido", "unknown"},
	"disk.free":       {"%s livres", "%s free"},
	"disk.short":      {"A instalação precisa de %s.", "The installation needs %s."},
	"disk.short.fix":  {"Libere espaço ou escolha uma pasta em outro disco.", "Free some space or pick a folder on another disk."},
	"disk.tight":      {"Cabe, mas sobra pouco: a instalação precisa de %s.", "It fits, with little to spare: the installation needs %s."},
	"disk.ok":         {"A instalação precisa de %s.", "The installation needs %s."},
	"check.net":       {"Conexão", "Connection"},
	"net.down":        {"sem acesso", "no access"},
	"net.down.d":      {"qw.x86.com.br não respondeu: %s", "qw.x86.com.br did not answer: %s"},
	"net.down.fix":    {"Confira a conexão e tente de novo.", "Check the connection and try again."},
	"net.ms":          {"%d ms", "%d ms"},
	"net.ok.d":        {"Tempo para autenticar o catálogo em qw.x86.com.br.", "Time to authenticate the catalog at qw.x86.com.br."},
	"check.catalog":   {"Catálogo assinado", "Signed catalog"},
	"catalog.bad":     {"inválido", "invalid"},
	"catalog.count":   {"%d pacotes", "%d packages"},
	"catalog.tuf":     {"TUF v%d, válido até %s.", "TUF v%d, valid until %s."},
	"catalog.local":   {"Lido de um arquivo local.", "Read from a local file."},
	"check.ports":     {"Portas do servidor", "Server ports"},
	"ports.free":      {"livres", "free"},
	"ports.busy":      {"em uso", "in use"},
	"ports.busy.fix":  {"Mude a porta inicial em Servidor, ou instale mesmo assim e libere-a antes de iniciar.", "Change the first port in Server, or install anyway and free it before starting."},
	"ports.ok.d":      {"UDP %d–%d livres.", "UDP %d–%d free."},
	"warn.of":         {"%s: %s", "%s: %s"},
	"warn.choices":    {"escolhas", "choices"},
	"warn.client":     {"configuração do cliente", "client settings"},
	"warn.presets":    {"presets do servidor", "server presets"},
	"warn.cli":        {"comando x86qw", "x86qw command"},
	"warn.integ":      {"integração", "integration"},
	"warn.profile":    {"escolha de conteúdo", "content choice"},
	"v.intent":        {"intenção inválida: %q", "invalid intent: %q"},
	"v.nick":          {"escolha um nick", "pick a nick"},
	"v.nick.chars":    {"o nick aceita só letras sem acento, números e sinais comuns: nada de aspas, barras invertidas nem ponto e vírgula", "the nick takes letters without accents, digits and plain signs: no quotes, backslashes or semicolons"},
	"v.ruleset":       {"ruleset desconhecido: %q", "unknown ruleset: %q"},
	"v.key":           {"%q não é uma tecla do jogo", "%q is not a game key"},
	"v.name":          {"dê um nome ao servidor", "give the server a name"},
	"v.name.chars":    {"o nome do servidor aceita só letras sem acento, números e sinais comuns: nada de aspas, barras invertidas nem ponto e vírgula", "the server name takes letters without accents, digits and plain signs: no quotes, backslashes or semicolons"},
	"v.instances":     {"o servidor precisa de 1 a 8 instâncias", "the server needs 1 to 8 instances"},
	"v.mode":          {"modo desconhecido: %q", "unknown mode: %q"},
	"v.bots":          {"o modo %s não tem bots", "mode %s has no bots"},
	"v.component":     {"componente desconhecido: %q", "unknown component: %q"},
	"v.slots":         {"vagas: de 2 a 32, não %d", "slots: 2 to 32, not %d"},
	"v.secrets":       {"senhas não aceitam aspas, barras invertidas, ponto e vírgula nem quebras de linha", "passwords take no quotes, backslashes, semicolons or line breaks"},
	"v.port.range":    {"%s: porta %d fora de 1025–65535", "%s: port %d outside 1025–65535"},
	"v.port.clash":    {"%s e %s usam a mesma porta %s %d", "%s and %s use the same %s port %d"},
	"v.path":          {"a pasta tem %d caracteres; o MVDSV só enxerga até %d", "the folder is %d characters long; MVDSV sees at most %d"},
	"instance":        {"instância #%d", "instance #%d"},
	"plan.component":  {"Componente", "Component"},
	"plan.client":     {"Cliente", "Client"},
	"plan.system":     {"Sistema", "System"},
	"plan.missing":    {"não instalado", "not installed"},
	"plan.safe":       {"área segura", "safe area"},
	"plan.legacy":     {"ausente ou formato histórico", "missing or an old format"},
	"plan.resigned":   {"bundle re-assinado localmente", "bundle re-signed locally"},
}

// planWords are what the update plan writes, as texts.
var planWords = map[string]string{
	"Component": "plan.component", "Client": "plan.client", "System": "plan.system",
	"not installed": "plan.missing", "safe area": "plan.safe", "missing or an old format": "plan.legacy",
	"bundle re-signed locally": "plan.resigned",
}

// planText translates a word of the update plan; versions stay as they are.
func planText(word string) Text {
	if key, ok := planWords[word]; ok {
		return Say(key)
	}
	return Raw(word)
}
