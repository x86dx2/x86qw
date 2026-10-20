package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Read-only provenance gate for protected GitHub Actions handoffs, like
// maintenance/tools/verify_external_handoff.py: the supplied run must be a
// successful run of the expected workflow at the candidate commit, and the
// named artifacts (with their immutable IDs) must have been published by it.

var (
	handoffSHA1      = regexp.MustCompile(`^[0-9a-f]{40}$`)
	handoffRunID     = regexp.MustCompile(`^[1-9][0-9]{0,19}$`)
	handoffName      = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$`)
	handoffDigest    = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
	handoffWorkflow  = regexp.MustCompile(`^\.github/workflows/[A-Za-z0-9._/-]+\.ya?ml$`)
	handoffEvent     = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
	handoffBranch    = regexp.MustCompile(`^[A-Za-z0-9._/-]{1,255}$`)
	handoffRepoPart  = regexp.MustCompile(`^[A-Za-z0-9_.-]+$`)
	handoffAPIBase   = "https://api.github.com"
	handoffAPIClient = &http.Client{Timeout: 15 * time.Second}
)

const (
	handoffMaxResponse = 2 << 20
	handoffMaxPages    = 10
)

type handoffRequest struct {
	Repository  string
	RunID       string
	Commit      string
	Artifacts   []string
	ArtifactIDs map[string]int64 // nil when no --artifact-id was given
	Token       string
	Workflow    string
	Event       string
	HeadBranch  *string
}

// verifiedArtifact is one entry of the --output document.
type verifiedArtifact struct {
	CreatedAt string `json:"created_at"`
	Digest    string `json:"digest"`
	ID        int64  `json:"id"`
	UpdatedAt string `json:"updated_at"`
}

func handoffGet(url, token string) (map[string]any, error) {
	request, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return nil, errors.New("não foi possível consultar a API de Actions")
	}
	request.Header.Set("Accept", "application/vnd.github+json")
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set("User-Agent", "x86qw-release-gate/1")
	response, err := handoffAPIClient.Do(request)
	if err != nil {
		return nil, errors.New("não foi possível consultar a API de Actions")
	}
	defer response.Body.Close()
	payload, err := io.ReadAll(io.LimitReader(response.Body, handoffMaxResponse+1))
	if err != nil || response.StatusCode/100 != 2 || len(payload) > handoffMaxResponse {
		return nil, errors.New("não foi possível consultar a API de Actions")
	}
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil || decoder.More() {
		return nil, errors.New("resposta da API de Actions inválida")
	}
	object, ok := value.(map[string]any)
	if !ok {
		return nil, errors.New("resposta da API de Actions não é um objeto")
	}
	return object, nil
}

// pyStr is Python's str() for the JSON scalars the API returns.
func pyStr(value any) string {
	switch v := value.(type) {
	case string:
		return v
	case json.Number:
		return v.String()
	case bool:
		if v {
			return "True"
		}
		return "False"
	case nil:
		return "None"
	}
	return fmt.Sprint(value)
}

func fullName(value any) (any, bool) {
	object, ok := value.(map[string]any)
	if !ok {
		return nil, false
	}
	return object["full_name"], true
}

func verifyHandoff(r handoffRequest) (map[string]verifiedArtifact, error) {
	parts := strings.Split(r.Repository, "/")
	if len(parts) != 2 || !handoffRepoPart.MatchString(parts[0]) || !handoffRepoPart.MatchString(parts[1]) || strings.ContainsAny(r.Repository, "\r\n\x00") {
		return nil, errors.New("repositório GitHub inválido")
	}
	if !handoffRunID.MatchString(r.RunID) {
		return nil, errors.New("run_id externo inválido")
	}
	if !handoffSHA1.MatchString(r.Commit) {
		return nil, errors.New("commit externo inválido")
	}
	if r.Token == "" {
		return nil, errors.New("GITHUB_TOKEN é obrigatório para validar o run externo")
	}
	if !handoffWorkflow.MatchString(r.Workflow) {
		return nil, errors.New("workflow externo inválido ou não fixado")
	}
	if !handoffEvent.MatchString(r.Event) {
		return nil, errors.New("evento do workflow externo inválido")
	}
	if r.HeadBranch != nil && !handoffBranch.MatchString(*r.HeadBranch) {
		return nil, errors.New("ref do workflow externo inválida")
	}
	requested := map[string]bool{}
	for _, name := range r.Artifacts {
		requested[name] = true
	}
	if len(r.Artifacts) == 0 || len(requested) != len(r.Artifacts) {
		return nil, errors.New("artefatos externos devem ser únicos e não vazios")
	}
	for _, name := range r.Artifacts {
		if !handoffName.MatchString(name) {
			return nil, errors.New("nome do artefato externo inválido")
		}
	}
	if r.ArtifactIDs != nil {
		if len(r.ArtifactIDs) != len(requested) {
			return nil, errors.New("IDs de artefatos externos não correspondem aos nomes")
		}
		for name := range r.ArtifactIDs {
			if !requested[name] {
				return nil, errors.New("IDs de artefatos externos não correspondem aos nomes")
			}
		}
		for _, name := range r.Artifacts {
			if r.ArtifactIDs[name] <= 0 {
				return nil, fmt.Errorf("ID do artefato externo inválido: %s", name)
			}
		}
	}
	base := handoffAPIBase + "/repos/" + r.Repository + "/actions/runs/" + r.RunID
	run, err := handoffGet(base, r.Token)
	if err != nil {
		return nil, err
	}
	if run["head_sha"] != r.Commit || run["status"] != "completed" || run["conclusion"] != "success" {
		return nil, errors.New("run externo não está concluído com sucesso no commit candidato")
	}
	if run["path"] != r.Workflow || run["event"] != r.Event {
		return nil, errors.New("run externo não pertence ao workflow/evento esperado")
	}
	if r.HeadBranch != nil && run["head_branch"] != *r.HeadBranch {
		return nil, errors.New("run externo não pertence à ref esperada")
	}
	if name, ok := fullName(run["repository"]); !ok || name != r.Repository {
		return nil, errors.New("run externo pertence a outro repositório")
	}
	if name, ok := fullName(run["head_repository"]); !ok || name != r.Repository {
		return nil, errors.New("run externo tem head repository divergente")
	}
	var entries []map[string]any
	complete := false
	for page := 1; page <= handoffMaxPages; page++ {
		listing, err := handoffGet(fmt.Sprintf("%s/artifacts?per_page=100&page=%d", base, page), r.Token)
		if err != nil {
			return nil, err
		}
		pageEntries, ok := listing["artifacts"].([]any)
		if !ok {
			return nil, errors.New("lista de artefatos do run externo inválida")
		}
		for _, value := range pageEntries {
			entry, ok := value.(map[string]any)
			if !ok {
				return nil, errors.New("artefato do run externo inválido")
			}
			if _, ok := entry["name"].(string); !ok {
				return nil, errors.New("artefato do run externo inválido")
			}
			if entry["expired"] != false {
				return nil, errors.New("artefato do run externo expirado ou sem estado explícito")
			}
			entries = append(entries, entry)
		}
		if len(pageEntries) < 100 {
			complete = true
			break
		}
	}
	if !complete {
		return nil, errors.New("lista de artefatos do run externo excede o limite de páginas")
	}
	byName := map[string]map[string]any{}
	for _, entry := range entries {
		byName[entry["name"].(string)] = entry
	}
	if len(byName) != len(entries) {
		return nil, errors.New("run externo publicou artefatos duplicados")
	}
	for _, entry := range entries {
		name := entry["name"].(string)
		idText := ""
		if value, present := entry["id"]; present {
			idText = pyStr(value)
		}
		if !handoffRunID.MatchString(idText) {
			return nil, fmt.Errorf("artefato externo sem ID imutável: %s", name)
		}
		id, err := strconv.ParseInt(idText, 10, 64)
		if err != nil {
			return nil, fmt.Errorf("artefato externo sem ID imutável: %s", name)
		}
		if expected, listed := r.ArtifactIDs[name]; r.ArtifactIDs != nil && listed && id != expected {
			return nil, fmt.Errorf("ID do artefato externo diverge do nome verificado: %s", name)
		}
		if digest, ok := entry["digest"].(string); !ok || !handoffDigest.MatchString(digest) {
			return nil, fmt.Errorf("artefato externo sem digest SHA-256: %s", name)
		}
		_, created := entry["created_at"].(string)
		_, updated := entry["updated_at"].(string)
		if !created || !updated {
			return nil, fmt.Errorf("artefato externo sem timestamps: %s", name)
		}
	}
	var missing []string
	for _, name := range r.Artifacts {
		if byName[name] == nil {
			missing = append(missing, name)
		}
	}
	if len(missing) > 0 {
		sort.Strings(missing)
		return nil, fmt.Errorf("run externo não publicou os artefatos esperados: %s", strings.Join(missing, ", "))
	}
	verified := map[string]verifiedArtifact{}
	for _, name := range r.Artifacts {
		entry := byName[name]
		id, _ := strconv.ParseInt(pyStr(entry["id"]), 10, 64)
		verified[name] = verifiedArtifact{
			CreatedAt: entry["created_at"].(string),
			Digest:    entry["digest"].(string),
			ID:        id,
			UpdatedAt: entry["updated_at"].(string),
		}
	}
	return verified, nil
}

// writeHandoffOutput writes the verified identities as a private JSON file
// that must not exist yet.
func writeHandoffOutput(destination string, verified map[string]verifiedArtifact) error {
	if _, err := os.Lstat(destination); err == nil {
		return fmt.Errorf("destino de handoff já existe: %s", destination)
	}
	var payload bytes.Buffer
	encoder := json.NewEncoder(&payload)
	encoder.SetEscapeHTML(false)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(verified); err != nil {
		return err
	}
	directory := filepath.Dir(destination)
	if err := os.MkdirAll(directory, 0o755); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(directory, "."+filepath.Base(destination)+"-")
	if err != nil {
		return err
	}
	defer os.Remove(temporary.Name())
	if _, err := temporary.Write(payload.Bytes()); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Sync(); err != nil {
		temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	if err := os.Chmod(temporary.Name(), 0o600); err != nil {
		return err
	}
	return os.Rename(temporary.Name(), destination)
}

type stringList []string

func (s *stringList) String() string     { return strings.Join(*s, ",") }
func (s *stringList) Set(v string) error { *s = append(*s, v); return nil }

// runVerifyHandoff: x86qw-release verify-handoff --run-id N --commit SHA
// --artifact NAME [--artifact-id ID] ... --workflow PATH [--event E]
// [--head-branch B] [--output FILE]
func runVerifyHandoff(args []string) int {
	flags := flag.NewFlagSet("verify-handoff", flag.ExitOnError)
	repository := flags.String("repository", os.Getenv("GITHUB_REPOSITORY"), "repositório GitHub (owner/nome)")
	runID := flags.String("run-id", "", "run protegido de origem")
	commit := flags.String("commit", "", "commit do run de origem")
	var artifacts, artifactIDs stringList
	flags.Var(&artifacts, "artifact", "nome exato de um artefato (repetível)")
	flags.Var(&artifactIDs, "artifact-id", "ID imutável correspondente a cada --artifact, na mesma ordem")
	workflow := flags.String("workflow", "", "caminho do workflow de origem")
	event := flags.String("event", "workflow_dispatch", "evento do run de origem")
	var headBranch *string
	flags.Func("head-branch", "ref do run de origem", func(value string) error { headBranch = &value; return nil })
	output := flags.String("output", "", "grava as identidades verificadas dos artefatos em JSON privado")
	flags.Parse(args)
	if *runID == "" || *commit == "" || len(artifacts) == 0 || *workflow == "" {
		flags.Usage()
		return 2
	}
	request := handoffRequest{
		Repository: *repository, RunID: *runID, Commit: *commit, Artifacts: artifacts,
		Token: os.Getenv("GITHUB_TOKEN"), Workflow: *workflow, Event: *event, HeadBranch: headBranch,
	}
	err := func() error {
		if artifactIDs != nil {
			if len(artifactIDs) != len(artifacts) {
				return errors.New("cada --artifact precisa de um --artifact-id correspondente")
			}
			request.ArtifactIDs = map[string]int64{}
			for index, text := range artifactIDs {
				id, err := strconv.ParseInt(text, 10, 64)
				if err != nil {
					return fmt.Errorf("--artifact-id inválido: %s", text)
				}
				request.ArtifactIDs[artifacts[index]] = id
			}
		}
		verified, err := verifyHandoff(request)
		if err != nil || *output == "" {
			return err
		}
		return writeHandoffOutput(*output, verified)
	}()
	if err != nil {
		fmt.Printf("[ERRO] %v\n", err)
		return 1
	}
	fmt.Printf("[OK] Run externo %s pertence ao candidato e publicou os handoffs.\n", *runID)
	return 0
}
