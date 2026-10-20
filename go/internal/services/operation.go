package services

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"golang.org/x/term"

	"github.com/x86dx2/x86qw/go/internal/lock"
	"github.com/x86dx2/x86qw/go/internal/play"
)

// Operation drives one `host`, `proxy` or `qtv` invocation like services.main.
type Operation struct {
	Action string
	Host   *HostOptions
	Proxy  *ProxyOptions
	Qtv    *QtvOptions
	// Args are the raw command-line arguments (re-used by --background).
	Args    []string
	Stdin   io.Reader
	Console *Console
	// Output receives the runtimes' stdout/stderr.
	Output *os.File
}

func (op *Operation) common() *Common {
	switch op.Action {
	case "proxy":
		return &op.Proxy.Common
	case "qtv":
		return &op.Qtv.Common
	}
	return &op.Host.Common
}

// passwordField is one password triplet (value, prompt, file).
type passwordField struct {
	value  *string
	prompt *bool
	file   *string
	label  string
	name   string
}

func (op *Operation) passwordFields() []passwordField {
	switch op.Action {
	case "host":
		o := op.Host
		return []passwordField{
			{&o.Password, &o.PromptPassword, &o.PasswordFile, "senha de jogador", "password"},
			{&o.SpectatorPassword, &o.PromptSpectatorPassword, &o.SpectatorPasswordFile, "senha de espectador", "spectator_password"},
			{&o.RconPassword, &o.PromptRconPassword, &o.RconPasswordFile, "senha RCON", "rcon_password"},
			{&o.QtvPassword, &o.PromptQtvPassword, &o.QtvPasswordFile, "senha QTV", "qtv_password"},
		}
	case "qtv":
		o := op.Qtv
		return []passwordField{{&o.QtvPassword, &o.PromptQtvPassword, &o.QtvPasswordFile, "senha QTV", "qtv_password"}}
	}
	return nil
}

// resolvePasswords mirrors services.resolve_passwords.
func (op *Operation) resolvePasswords() error {
	for _, field := range op.passwordFields() {
		if *field.file != "" {
			value, err := ReadPasswordFile(*field.file, field.label)
			if err != nil {
				return err
			}
			*field.value = value
		} else if *field.prompt && *field.value == "" {
			fmt.Fprintf(os.Stderr, "%s: ", strings.ToUpper(field.label[:1])+field.label[1:])
			raw, err := term.ReadPassword(int(os.Stdin.Fd()))
			fmt.Fprintln(os.Stderr)
			if err != nil {
				return errorf("Não foi possível ler a %s sem eco.", field.label)
			}
			if len(raw) > 0 {
				if _, err := SafeText(string(raw), field.label, 4096); err != nil {
					return err
				}
			}
			*field.value = string(raw)
		}
	}
	return nil
}

// warnExternalBind mirrors services.warn_external_bind.
func (op *Operation) warnExternalBind() {
	c := op.Console
	warnQtv := func(address, password string, upstream bool) {
		if IsExternalBind(address) {
			c.Warning("A interface HTTP/QTV será exposta à rede. A senha de upstream não autentica o acesso HTTP.")
		}
		if upstream && password == "" {
			c.Warning("Upstream QTV sem segredo configurado.")
		}
	}
	switch op.Action {
	case "host":
		o := op.Host
		if IsExternalBind(o.Bind) && o.Password == "" && o.SpectatorPassword == "" && o.RconPassword == "" {
			c.Warning("Bind externo sem senhas de jogador, espectador ou RCON; o servidor ficará acessível pela rede.")
		}
		if o.WithQTV {
			warnQtv(o.QtvBind, o.QtvPassword, true)
		}
		if o.WithProxy && IsExternalBind(o.ProxyBind) {
			c.Warning("QWFWD está ligado a uma interface externa.")
		}
	case "qtv":
		warnQtv(op.Qtv.Bind, op.Qtv.QtvPassword, op.Qtv.Upstream != "")
	case "proxy":
		if IsExternalBind(op.Proxy.ProxyBind) {
			c.Warning("QWFWD está ligado a uma interface externa.")
		}
	}
}

var backgroundSecretFields = []string{"password", "spectator_password", "rcon_password", "qtv_password"}

// readBackgroundRequest mirrors read_background_request (secrets via stdin).
func (op *Operation) readBackgroundRequest() error {
	invalid := errorf("O controlador em segundo plano não recebeu uma configuração privada válida.")
	payload, err := io.ReadAll(io.LimitReader(op.Stdin, 65537))
	if err != nil || len(payload) > 65536 {
		return invalid
	}
	var document struct {
		Format  int               `json:"format"`
		Project string            `json:"project"`
		Secrets map[string]string `json:"secrets"`
	}
	var keys map[string]json.RawMessage
	if json.Unmarshal(payload, &keys) != nil || len(keys) != 3 || json.Unmarshal(payload, &document) != nil ||
		document.Format != 1 || document.Project != "x86qw" || len(document.Secrets) != len(backgroundSecretFields) {
		return invalid
	}
	for _, name := range backgroundSecretFields {
		if value, ok := document.Secrets[name]; !ok || len(value) > 4096 {
			return invalid
		}
	}
	for _, field := range op.passwordFields() {
		*field.value = document.Secrets[field.name]
		*field.prompt, *field.file = false, ""
	}
	op.common().Background = true
	return nil
}

// Run executes the operation and returns the process exit code.
func (op *Operation) Run() (code int) {
	c := op.Console
	common := op.common()
	if common.Menu {
		c.Error("O menu interativo de serviços ainda não foi portado para a CLI nativa; informe os parâmetros na linha de comando.")
		return 2
	}
	target, err := filepath.Abs(common.Target)
	if err == nil {
		target = lock.Resolve(target)
	}
	common.Target = target
	var temporary []*TemporaryConfig
	var materialized []*Materialized
	var journal *Journal
	var held *lock.Lock
	session := &Session{}
	fail := func(err error) int {
		var usage *UsageError
		if errors.As(err, &usage) {
			c.Error(err.Error())
			return 2
		}
		c.Error(err.Error())
		return 1
	}
	defer func() {
		temporary, materialized = session.Temporary, session.Materialized
		if journal == nil && len(temporary) == 0 && len(materialized) == 0 && held == nil {
			return
		}
		var cleanupErrors []string
		if journal != nil {
			if err := journal.SetStatus("stopping"); err != nil {
				cleanupErrors = append(cleanupErrors, "journal em stopping: "+err.Error())
			}
		}
		cleanupOK := true
		if err := cleanupSession(journal, temporary, materialized, c); err != nil {
			cleanupOK = false
			cleanupErrors = append(cleanupErrors, "conteúdo efêmero: "+err.Error())
		}
		if journal != nil {
			status := "clean"
			if !cleanupOK {
				status = "interrupted"
			}
			if err := journal.SetStatus(status); err != nil {
				cleanupErrors = append(cleanupErrors, "estado final do journal: "+err.Error())
			}
		}
		if held != nil {
			if err := held.Release(); err != nil {
				cleanupErrors = append(cleanupErrors, "lock da instalação: "+err.Error())
			}
		}
		for _, message := range cleanupErrors {
			c.Warning("Falha durante a finalização de " + message)
		}
		if len(cleanupErrors) > 0 && code == 0 {
			c.Error("A sessão terminou com falha crítica de finalização.")
			code = 1
		}
	}()

	if common.BackgroundChild {
		if err := op.readBackgroundRequest(); err != nil {
			return fail(err)
		}
	}
	if err := validateTarget(target); err != nil {
		return fail(err)
	}
	if op.Action == "host" && op.Host.SavePreset != "" {
		saved, err := SavePreset(target, op.Host.SavePreset, op.Host)
		if err != nil {
			return fail(errorf("%s", err.Error()))
		}
		c.Success(fmt.Sprintf("Preset de hospedagem gravado: %v", saved["name"]))
		return 0
	}
	var hosted *Hosted
	if op.Action == "host" {
		if hosted, err = SelectHosted(target, op.Host); err != nil {
			return fail(err)
		}
	}
	if err := op.resolvePasswords(); err != nil {
		return fail(err)
	}
	op.warnExternalBind()
	if common.Background && !common.BackgroundChild {
		return op.launchBackground(hosted)
	}

	if held, err = lock.Acquire(target, op.Action); err != nil {
		held = nil
		return fail(errorf("%s", capitalize(err.Error())))
	}
	if common.BackgroundChild {
		if common.BackgroundLog == "" {
			return fail(errorf("Controlador em segundo plano sem log privado."))
		}
		log, err := activateBackgroundLog(target, common.BackgroundLog)
		if err != nil {
			return fail(err)
		}
		defer log.Close()
		c.Out, c.Err, op.Output = log, log, log
	}
	if err := RecoverSessions(target, c.Warning); err != nil {
		return fail(err)
	}
	if err := PreflightPorts(RequestedPorts(op.Action, op.Host, op.Proxy, op.Qtv)); err != nil {
		return fail(err)
	}
	newJournal := func() error {
		journal, err = NewJournal(target, &held.Owner, common.BackgroundChild, common.BackgroundLog)
		return err
	}
	var specs []*Spec
	switch op.Action {
	case "proxy":
		c.Banner("iniciar QWFWD", target)
		spec, err := ProxySpec(target, op.Proxy.ProxyBind, op.Proxy.ProxyPort)
		if err != nil {
			return fail(err)
		}
		if err := newJournal(); err != nil {
			return fail(err)
		}
		c.Info(fmt.Sprintf("Proxy local: %s:%d/UDP", op.Proxy.ProxyBind, op.Proxy.ProxyPort))
		specs = []*Spec{spec}
	case "qtv":
		c.Banner("iniciar QTV", target)
		if err := newJournal(); err != nil {
			return fail(err)
		}
		o := op.Qtv
		spec, err := QtvSpec(target, o.Bind, o.Port, o.Hostname, o.Upstream, o.QtvPassword, session, journal)
		if err != nil {
			return fail(err)
		}
		c.Info(fmt.Sprintf("QTV HTTP: http://%s/", Endpoint(o.Bind, o.Port)))
		specs = []*Spec{spec}
	default:
		c.Banner("hospedar jogo com MVDSV", target)
		o := op.Host
		hostname := o.Hostname
		if hostname == "" {
			hostname = "x86QW - " + hosted.Game.Label
		}
		for _, item := range [][2]string{{o.Password, "senha de jogador"}, {o.SpectatorPassword, "senha de espectador"},
			{o.RconPassword, "senha RCON"}, {o.QtvPassword, "senha QTV"}} {
			if item[0] != "" {
				if _, err := SafeText(item[0], item[1], 96); err != nil {
					return fail(err)
				}
			}
		}
		if err := newJournal(); err != nil {
			return fail(err)
		}
		host, err := HostSpec(target, o, hosted, session, journal)
		if err != nil {
			return fail(err)
		}
		specs = []*Spec{host}
		if o.WithQTV {
			spec, err := QtvSpec(target, o.QtvBind, o.QtvPort, hostname+" QTV",
				Endpoint(LocalServiceAddress(o.Bind), o.Port), o.QtvPassword, session, journal)
			if err != nil {
				return fail(err)
			}
			specs = append(specs, spec)
		}
		if o.WithProxy {
			spec, err := ProxySpec(target, o.ProxyBind, o.ProxyPort)
			if err != nil {
				return fail(err)
			}
			specs = append(specs, spec)
		}
		label := hosted.Game.Label
		if hosted.Mode != nil {
			label += " · " + hosted.Mode.Label
		}
		c.Info(fmt.Sprintf("Servidor: connect %s:%d · %s em %s", o.Bind, o.Port, label, hosted.Map))
		if o.WithQTV {
			c.Info(fmt.Sprintf("QTV HTTP: http://%s/", Endpoint(o.QtvBind, o.QtvPort)))
		}
	}
	if capture := os.Getenv("X86QW_SERVICES_ORACLE"); capture != "" {
		if err := writeOracleCapture(capture, op.Action, specs, journal, session); err != nil {
			return fail(err)
		}
		return 0
	}
	result, err := RunProcesses(specs, journal, c, op.Output)
	if err != nil {
		return fail(err)
	}
	return result
}

func capitalize(message string) string {
	if message == "" {
		return message
	}
	runes := []rune(message)
	return strings.ToUpper(string(runes[0])) + string(runes[1:])
}

// validateTarget is the subset of validate_target("verify") services need.
func validateTarget(target string) error {
	info, err := os.Lstat(target)
	if err != nil || !info.IsDir() {
		return errorf("Diretório de destino não existe: %s", target)
	}
	control := filepath.Join(target, ".x86qw")
	if info, err := os.Lstat(control); err != nil || !info.IsDir() {
		return errorf("Instalação x86QW não encontrada em %s.", target)
	}
	return nil
}

// cleanupSession mirrors cleanup_current_session for this controller.
func cleanupSession(journal *Journal, temporary []*TemporaryConfig, materialized []*Materialized, c *Console) error {
	var failure error
	for _, config := range temporary {
		if _, err := os.Lstat(config.Path); errors.Is(err, os.ErrNotExist) {
			continue
		}
		if !cleanupSensitive(config.Path, config.Identity) {
			c.Warning("Temporário sensível substituído foi preservado: " + config.Path)
			failure = errorf("Temporário sensível sem identidade original confirmável foi preservado: %s", config.Path)
			break
		}
	}
	for index := len(materialized) - 1; index >= 0; index-- {
		package_ := materialized[index]
		if journal != nil {
			for _, entry := range package_.Files {
				if !entry.CreatedBySession {
					continue
				}
				if _, err := os.Lstat(entry.Path); err != nil {
					continue
				}
				if digest, size, err := fileSHA256(entry.Path); err != nil || digest != entry.ExpectedHash || size != entry.Size {
					if relative, err := journal.Relative(entry.Path); err == nil {
						journal.MarkModified(relative)
					}
				}
			}
		}
		package_.Cleanup(c.Warning)
	}
	return failure
}

// activateBackgroundLog mirrors sessions.activate_background_log.
func activateBackgroundLog(target, relative string) (*os.File, error) {
	parts := strings.Split(relative, "/")
	if len(parts) != 3 || parts[0] != ".x86qw" || parts[1] != "logs" || !strings.HasPrefix(parts[2], "service-") ||
		filepath.Ext(parts[2]) != ".log" || strings.ContainsAny(parts[2], `\`) {
		return nil, errorf("Caminho interno do log em segundo plano é inválido.")
	}
	directory := filepath.Join(target, ".x86qw", "logs")
	if err := ensurePrivateDirectory(directory); err != nil {
		return nil, err
	}
	path := filepath.Join(directory, parts[2])
	if info, err := os.Lstat(path); err == nil && !info.Mode().IsRegular() {
		return nil, errorf("Log em segundo plano ausente ou inseguro: %s", path)
	}
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_APPEND|os.O_CREATE, 0o600)
	if err != nil {
		return nil, errorf("Log em segundo plano não pôde ser protegido: %s", path)
	}
	return file, nil
}

// backgroundArguments rebuilds the child command line without secrets,
// like background_controller_arguments.
func (op *Operation) backgroundArguments(hosted *Hosted, logRelative string) []string {
	var arguments []string
	switch op.Action {
	case "host":
		o := op.Host
		arguments = []string{"host", hosted.Game.ID}
		if hosted.Mode != nil {
			arguments = append(arguments, "--mode", hosted.Mode.Key)
			arguments = append(arguments, ktxCLIArguments(hosted.Options)...)
		}
		arguments = append(arguments, "--map", hosted.Map, "--bind", o.Bind, "--port", fmt.Sprint(o.Port),
			"--maxclients", fmt.Sprint(o.Maxclients))
		if o.Hostname != "" {
			arguments = append(arguments, "--hostname", o.Hostname)
		}
		if o.NoMVD {
			arguments = append(arguments, "--no-mvd")
		}
		if o.WithQTV {
			arguments = append(arguments, "--with-qtv", "--qtv-bind", o.QtvBind, "--qtv-port", fmt.Sprint(o.QtvPort))
		}
		if o.WithProxy {
			arguments = append(arguments, "--with-proxy", "--proxy-bind", o.ProxyBind, "--proxy-port", fmt.Sprint(o.ProxyPort))
		}
	case "proxy":
		arguments = []string{"proxy", "--bind", op.Proxy.ProxyBind, "--port", fmt.Sprint(op.Proxy.ProxyPort)}
	case "qtv":
		o := op.Qtv
		arguments = []string{"qtv", "--bind", o.Bind, "--port", fmt.Sprint(o.Port), "--hostname", o.Hostname}
		if o.Upstream != "" {
			arguments = append(arguments, "--upstream", o.Upstream)
		}
	}
	common := op.common()
	arguments = append(arguments, "--target", common.Target, "--background-child", "--background-log", logRelative)
	if common.Verbose {
		arguments = append(arguments, "--verbose")
	}
	if common.NoColor {
		arguments = append(arguments, "--no-color")
	}
	return arguments
}

func (op *Operation) secrets() map[string]any {
	values := map[string]any{}
	for _, name := range backgroundSecretFields {
		values[name] = ""
	}
	for _, field := range op.passwordFields() {
		values[field.name] = *field.value
	}
	return values
}

// launchBackground mirrors launch_background_controller.
func (op *Operation) launchBackground(hosted *Hosted) int {
	c := op.Console
	target := op.common().Target
	logRelative := ".x86qw/logs/service-" + tokenHex(8) + ".log"
	arguments := op.backgroundArguments(hosted, logRelative)
	secrets := op.secrets()
	request := pyDumps(map[string]any{"format": 1, "project": "x86qw", "secrets": secrets}, false) + "\n"
	if capture := os.Getenv("X86QW_SERVICES_ORACLE"); capture != "" {
		document := map[string]any{"action": op.Action, "background": map[string]any{"arguments": toAny(arguments), "request": request}}
		if err := os.WriteFile(capture, []byte(pyDumps(document, true)+"\n"), 0o600); err != nil {
			c.Error(err.Error())
			return 1
		}
		return 0
	}
	executable, err := os.Executable()
	if err != nil {
		c.Error("Não foi possível criar o controlador em segundo plano: " + err.Error())
		return 1
	}
	process, err := spawnBackgroundController(executable, arguments, []byte(request))
	if err != nil {
		c.Error(err.Error())
		return 1
	}
	exited := make(chan int, 1)
	go func() {
		process.Wait()
		exited <- exitCode(process.ProcessState)
	}()
	lockPath := filepath.Join(target, ".x86qw", "sessions", "active.lock")
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		select {
		case code := <-exited:
			detail := backgroundLogTail(filepath.Join(target, filepath.FromSlash(logRelative)), secrets)
			if detail != "" {
				detail = "\n" + detail
			}
			c.Error(fmt.Sprintf("O controlador em segundo plano encerrou com código %d.%s", code, detail))
			return 1
		default:
		}
		if owner, err := lock.ReadOwner(lockPath); err == nil && owner.ControllerPID == process.Process.Pid {
			if paths, err := SessionJournalPaths(target, owner.SessionID); err == nil && len(paths) == 1 {
				if data, err := LoadJournal(paths[0]); err == nil && data["status"] == "running" {
					c.Success("Stack iniciada em segundo plano; consulte em Serviços › Visualizar serviços ativos.")
					c.Info(fmt.Sprintf("Controlador: PID %d", process.Process.Pid))
					c.Info("Log: " + logRelative)
					return 0
				}
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	c.Warning("A stack continua inicializando em segundo plano; use status para acompanhar.")
	c.Info(fmt.Sprintf("Controlador: PID %d", process.Process.Pid))
	return 0
}

func backgroundLogTail(path string, secrets map[string]any) string {
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() > 1<<20 {
		return ""
	}
	payload, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	lines := strings.Split(strings.TrimRight(strings.ToValidUTF8(string(payload), "�"), "\n"), "\n")
	if len(lines) > 8 {
		lines = lines[len(lines)-8:]
	}
	for index, line := range lines {
		lines[index] = statusValue(line)
	}
	rendered := strings.Join(lines, "\n")
	for _, secret := range secrets {
		if text, _ := secret.(string); text != "" {
			rendered = strings.ReplaceAll(rendered, text, "[REDIGIDO]")
		}
	}
	return rendered
}

func ktxCLIArguments(o play.Options) []string {
	var arguments []string
	if o.FillBots {
		arguments = append(arguments, "--fill-bots")
	} else if o.Bots > 0 {
		arguments = append(arguments, "--bots", fmt.Sprint(o.Bots))
	}
	if o.Bots > 0 || o.FillBots {
		arguments = append(arguments, "--bot-skill", o.BotSkill)
		if o.BotNamesProfile != "default" {
			arguments = append(arguments, "--bot-names", o.BotNamesProfile)
		}
		if o.BotTeam != nil {
			arguments = append(arguments, "--bot-team", *o.BotTeam)
		}
		if o.BotWeapon != nil {
			arguments = append(arguments, "--bot-weapon", *o.BotWeapon)
		}
		if o.BotHealth != nil {
			arguments = append(arguments, "--bot-health", fmt.Sprint(*o.BotHealth))
		}
		if o.BotBreakOnDeath != nil {
			if *o.BotBreakOnDeath {
				arguments = append(arguments, "--bot-break-on-death")
			} else {
				arguments = append(arguments, "--no-bot-break-on-death")
			}
		}
	}
	for _, item := range []struct {
		flag  string
		value *string
	}{{"--ctf-hook", o.CTFHook}, {"--ctf-runes", o.CTFRunes}} {
		if item.value != nil {
			arguments = append(arguments, item.flag, *item.value)
		}
	}
	if o.CTFBasedSpawn {
		arguments = append(arguments, "--ctf-based-spawn")
	}
	for _, item := range []struct {
		flag  string
		value *string
	}{{"--race-style", o.RaceStyle}, {"--race-scoring", o.RaceScoring}} {
		if item.value != nil {
			arguments = append(arguments, item.flag, *item.value)
		}
	}
	return arguments
}

func toAny(values []string) []any {
	result := make([]any, len(values))
	for index, value := range values {
		result[index] = value
	}
	return result
}
