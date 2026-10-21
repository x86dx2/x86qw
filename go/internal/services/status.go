package services

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode"

	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/pyjson"
)

// Human `status` and `status --stop`, ported from services.py.

var statusParameterLabels = [][2]string{
	{"game", "Jogo"}, {"mode", "Modo"}, {"map", "Mapa"}, {"bots", "Bots"}, {"bot_skill", "Habilidade"},
	{"bot_names", "Nomes"}, {"bot_team", "Equipe"}, {"bot_weapon", "Arma"}, {"bot_health", "Vida"},
	{"ctf_hook", "Gancho CTF"}, {"ctf_runes", "Runas CTF"}, {"race_style", "Estilo Race"},
	{"race_scoring", "Pontuação Race"}, {"hostname", "Nome"}, {"bind", "Bind"}, {"port", "Porta"},
	{"maxclients", "Clientes"}, {"mvd", "Gravação MVD"}, {"memory", "Memória"}, {"secrets", "Segredos"},
	{"protocol", "Protocolo"}, {"http", "HTTP"}, {"upstream", "Upstream"}, {"upstream_secret", "Segredo upstream"},
}

var sessionStatusLabels = map[string]string{
	"starting": "inicializando", "running": "ativa", "stopping": "encerrando",
	"interrupted": "interrompida", "clean": "finalizada",
}

func statusValue(value any) string {
	var text string
	switch typed := value.(type) {
	case string:
		text = typed
	case nil:
		text = "None"
	case bool:
		text = map[bool]string{true: "True", false: "False"}[typed]
	default:
		text = fmt.Sprint(typed)
	}
	var builder strings.Builder
	count := 0
	for _, character := range text {
		if count == 512 {
			break
		}
		if unicode.IsPrint(character) {
			builder.WriteRune(character)
		} else {
			builder.WriteRune('?')
		}
		count++
	}
	return builder.String()
}

func processStatusLabel(status string) string {
	labels := map[string]string{
		"alive": "ativo", "dead": "encerrado",
		"identity_mismatch": "PID reutilizado; processo preservado", "inconclusive": "identidade inconclusiva",
	}
	if label, ok := labels[status]; ok {
		return label
	}
	return statusValue(status)
}

func statusExecutable(target string, value any) string {
	text := statusValue(value)
	executable, _ := value.(string)
	resolved := executable
	if real, err := filepath.EvalSymlinks(executable); err == nil {
		resolved = real
	}
	if relative, err := filepath.Rel(target, resolved); err == nil && relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator)) && !filepath.IsAbs(relative) {
		return filepath.ToSlash(relative)
	}
	return text
}

func (c *Console) row(label string, value any) {
	padding := 18 - len([]rune(label))
	if padding < 0 {
		padding = 0
	}
	fmt.Fprintf(c.Out, "  %s%s| %s\n", label, strings.Repeat(" ", padding), statusValue(value))
}

func printSession(c *Console, target string, data map[string]any, ownsLock bool) {
	c.Section(fmt.Sprintf("Sessão %v", data["session_id"]))
	status := fmt.Sprint(data["status"])
	if label, ok := sessionStatusLabels[status]; ok {
		c.row("Estado", label)
	} else {
		c.row("Estado", data["status"])
	}
	c.row("Lock", map[bool]string{true: "ativo", false: "ausente"}[ownsLock])
	c.row("Iniciada", data["created_at"])
	if background, _ := data["background"].(bool); background {
		c.row("Execução", "segundo plano")
	} else {
		c.row("Execução", "primeiro plano")
	}
	if log, ok := data["background_log"].(string); ok && log != "" {
		c.row("Log", log)
	}
	if controller, ok := data["controller"].(map[string]any); ok {
		c.row("Controlador", fmt.Sprintf("PID %v · %s", controller["pid"], processStatusLabel(controllerProbe(data))))
		c.row("Comando", controller["command"])
	} else {
		c.row("Controlador", "identidade não registrada")
	}
	processes, _ := data["processes"].([]any)
	if len(processes) == 0 {
		c.Info("Nenhum processo foi registrado; a stack pode ainda estar inicializando.")
		return
	}
	for _, item := range processes {
		entry, _ := item.(map[string]any)
		labelValue, ok := entry["label"]
		if !ok {
			labelValue = "Serviço"
		}
		label := statusValue(labelValue)
		fmt.Fprintf(c.Out, "\n  %s\n", c.paint(label, "1;36"))
		c.row("Estado", processStatusLabel(processProbe(entry)))
		c.row("PID", entry["pid"])
		if runtime, ok := entry["runtime"]; ok {
			c.row("Runtime", runtime)
		} else {
			c.row("Runtime", strings.ToLower(label))
		}
		address, okAddress := entry["address"].(string)
		port, okPort := intField(entry["port"])
		if okAddress && okPort {
			c.row("Endpoint local", Endpoint(address, port))
		}
		if executable, ok := entry["executable"].(string); ok && executable != "" {
			c.row("Executável", statusExecutable(target, executable))
		}
		parameters, _ := entry["parameters"].(map[string]any)
		displayed := false
		for _, item := range statusParameterLabels {
			if value, ok := parameters[item[0]]; ok {
				c.row(item[1], value)
				displayed = true
			}
		}
		if !displayed {
			c.row("Parâmetros", "não registrados nesta sessão")
		}
	}
}

// ShowStatus mirrors show_service_status; it reports whether a service
// controller is confirmed alive.
func ShowStatus(c *Console, target string) (bool, error) {
	c.Banner("visualizar serviços ativos", target)
	lockPath := filepath.Join(target, ".x86qw", "sessions", "active.lock")
	var owner *lock.Owner
	active := false
	if _, err := os.Lstat(lockPath); err == nil {
		if owner, err = lock.ReadOwner(lockPath); err != nil {
			return false, errorf("%v", err)
		}
		probe := lock.Probe(owner.ControllerPID, owner.ControllerStartToken, owner.ControllerExecutable)
		c.Section("Operação ativa")
		kind := "serviços"
		if owner.OperationKind == "maintenance" {
			kind = "manutenção"
		}
		c.row("Tipo", kind)
		c.row("Comando", owner.Command)
		c.row("Sessão", owner.SessionID)
		c.row("Controlador", fmt.Sprintf("PID %d · %s", owner.ControllerPID, processStatusLabel(probe)))
		active = owner.OperationKind != "maintenance" && probe == "alive"
	}
	paths, err := SessionJournalPaths(target, "")
	if err != nil {
		return false, err
	}
	type unfinished struct{ data map[string]any }
	var pending []unfinished
	for _, path := range paths {
		data, err := LoadJournal(path)
		if err != nil {
			return false, err
		}
		if data["status"] != "clean" {
			pending = append(pending, unfinished{data})
		}
	}
	hint := func() {
		if active {
			c.Info("Para encerrar: x86qw status --stop.")
		}
	}
	if len(pending) == 0 {
		switch {
		case owner != nil && owner.OperationKind == "service":
			c.Info("A stack está inicializando; nenhum processo foi registrado ainda.")
		case owner != nil:
			c.Info("Não há serviços registrados durante esta operação de manutenção.")
		default:
			c.Success("Nenhum serviço x86QW está ativo nesta instalação.")
		}
		hint()
		return active, nil
	}
	lockSession := ""
	if owner != nil {
		lockSession = owner.SessionID
	}
	for _, item := range pending {
		ownsLock := owner != nil && fmt.Sprint(item.data["session_id"]) == lockSession
		if !ownsLock {
			c.Warning(fmt.Sprintf("Sessão sem lock ativo preservada para inspeção: %v", item.data["session_id"]))
		}
		printSession(c, target, item.data, ownsLock)
	}
	hint()
	return active, nil
}

// RequestStop mirrors request_service_stop: publish stop.request for the
// confirmed controller and wait until it releases the lock.
func RequestStop(target string, timeout time.Duration) error {
	lockPath := filepath.Join(target, ".x86qw", "sessions", "active.lock")
	if _, err := os.Lstat(lockPath); err != nil {
		return errorf("Nenhuma stack de serviços x86QW está ativa.")
	}
	owner, err := lock.ReadOwner(lockPath)
	if err != nil {
		return errorf("%v", err)
	}
	if owner.OperationKind != "" && owner.OperationKind != "service" {
		return errorf("A operação ativa é %s, não uma stack de serviços.", owner.Command)
	}
	if lock.Probe(owner.ControllerPID, owner.ControllerStartToken, owner.ControllerExecutable) != "alive" {
		return errorf("O controlador ativo não pôde ser confirmado; nenhum processo foi encerrado.")
	}
	paths, err := SessionJournalPaths(target, owner.SessionID)
	if err != nil {
		return err
	}
	if len(paths) != 1 {
		return errorf("O journal da stack ativa não está disponível; nenhum processo foi encerrado.")
	}
	journal, err := LoadJournal(paths[0])
	if err != nil {
		return err
	}
	controller, _ := journal["controller"].(map[string]any)
	pid, _ := intField(controller["pid"])
	executable, _ := controller["executable"].(string)
	if controller == nil || pid != owner.ControllerPID || controller["creation_token"] != owner.ControllerStartToken ||
		realpath(executable) != realpath(owner.ControllerExecutable) {
		return errorf("Lock e journal não identificam o mesmo controlador; nada foi encerrado.")
	}
	request := filepath.Join(filepath.Dir(paths[0]), "stop.request")
	payload, err := pyjson.Dumps(map[string]any{
		"format": 1, "project": "x86qw", "session_id": owner.SessionID, "requested_at": isoNow(),
	}, -1)
	if err != nil {
		return err
	}
	identity, err := publishStopRequest(request, []byte(payload+"\n"))
	if err != nil {
		return err
	}
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if _, err := os.Lstat(lockPath); errors.Is(err, os.ErrNotExist) {
			unlinkStopRequest(request, identity)
			return nil
		}
		current, err := lock.ReadOwner(lockPath)
		if err != nil {
			if _, statErr := os.Lstat(lockPath); errors.Is(statErr, os.ErrNotExist) {
				unlinkStopRequest(request, identity)
				return nil
			}
			return errorf("%v", err)
		}
		if current.SessionID != owner.SessionID {
			return errorf("O lock mudou de proprietário durante o encerramento.")
		}
		time.Sleep(100 * time.Millisecond)
	}
	return errorf("A stack recebeu o pedido, mas não concluiu o encerramento dentro do limite.")
}

func realpath(path string) string {
	if resolved, err := filepath.EvalSymlinks(path); err == nil {
		return resolved
	}
	if absolute, err := filepath.Abs(path); err == nil {
		return absolute
	}
	return path
}

// publishStopRequest stages a private ".stop-*.request" and links it into
// place (create-only), like sessions.publish_stop_request.
func publishStopRequest(request string, payload []byte) ([2]int64, error) {
	var file *os.File
	var temporary string
	for attempt := 0; attempt < 128 && file == nil; attempt++ {
		temporary = filepath.Join(filepath.Dir(request), ".stop-"+tokenHex(4)+".request")
		created, err := os.OpenFile(temporary, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
		if errors.Is(err, os.ErrExist) {
			continue
		}
		if err != nil {
			return [2]int64{}, errorf("Não foi possível criar o pedido privado de encerramento.")
		}
		file = created
	}
	if file == nil {
		return [2]int64{}, errorf("Não foi possível criar o pedido privado de encerramento.")
	}
	defer os.Remove(temporary)
	identity, err := identityOfFile(file)
	if err != nil {
		file.Close()
		return [2]int64{}, errorf("Não foi possível criar o pedido privado de encerramento.")
	}
	_, writeErr := file.Write(payload)
	if err := errors.Join(writeErr, file.Sync(), file.Close()); err != nil {
		return [2]int64{}, errorf("Não foi possível publicar o pedido privado de encerramento.")
	}
	if err := os.Link(temporary, request); err != nil {
		if errors.Is(err, os.ErrExist) {
			return [2]int64{}, errorf("Já existe um pedido de encerramento para esta stack.")
		}
		return [2]int64{}, errorf("Não foi possível publicar o pedido privado de encerramento.")
	}
	return identity, nil
}

func unlinkStopRequest(request string, identity [2]int64) {
	if info, err := os.Lstat(request); err == nil && info.Mode().IsRegular() && identityAt(request, info) == identity {
		os.Remove(request)
	}
}
