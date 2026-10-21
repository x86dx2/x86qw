package content

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
)

// Repository hygiene checks: Git LFS and whitespace in the committed diff.

const maxDirectBinary = 1024 * 1024

// IsGitWorkTree reports whether root is inside a Git work tree.
func IsGitWorkTree(root string) bool {
	command := exec.Command("git", "rev-parse", "--is-inside-work-tree")
	command.Dir = root
	output, err := command.Output()
	return err == nil && strings.TrimSpace(string(output)) == "true"
}

func git(root string, stdin []byte, arguments ...string) ([]byte, error) {
	command := exec.Command("git", arguments...)
	command.Dir = root
	if stdin != nil {
		command.Stdin = bytes.NewReader(stdin)
	}
	var stderr bytes.Buffer
	command.Stderr = &stderr
	output, err := command.Output()
	if err != nil {
		return nil, fmt.Errorf("git %s: %v: %s", strings.Join(arguments, " "), err, strings.TrimSpace(stderr.String()))
	}
	return output, nil
}

// CheckLFS requires every LFS-tracked file to be
// materialized and no tracked binary above 1 MiB may bypass LFS. It returns
// the number of LFS objects and one message per problem.
func CheckLFS(root string) (int, []string, error) {
	listing, err := git(root, nil, "ls-files", "-z")
	if err != nil {
		return 0, nil, err
	}
	var paths []string
	for _, value := range bytes.Split(listing, []byte{0}) {
		if len(value) > 0 {
			paths = append(paths, string(value))
		}
	}
	attributes := map[string]bool{}
	for offset := 0; offset < len(paths); offset += 100 {
		end := min(offset+100, len(paths))
		payload := []byte(strings.Join(paths[offset:end], "\x00") + "\x00")
		output, err := git(root, payload, "check-attr", "-z", "--stdin", "filter")
		if err != nil {
			return 0, nil, err
		}
		fields := bytes.Split(output, []byte{0})
		for index := 0; index+2 < len(fields); index += 3 {
			attributes[string(fields[index])] = string(fields[index+2]) == "lfs"
		}
	}
	var problems []string
	count := 0
	for _, relative := range paths {
		path := joinPosix(root, relative)
		if !isFile(path) || isSymlink(path) {
			continue
		}
		if attributes[relative] {
			count++
			file, err := os.Open(path)
			if err != nil {
				problems = append(problems, fmt.Sprintf("objeto LFS ilegível: %s: %v", relative, err))
				continue
			}
			head := make([]byte, len(GitLFSPointerPrefix))
			n, _ := io.ReadFull(file, head)
			file.Close()
			if bytes.Equal(head[:n], GitLFSPointerPrefix) {
				problems = append(problems, "objeto LFS não materializado: "+relative)
			}
			continue
		}
		info, err := os.Stat(path)
		if err != nil {
			problems = append(problems, fmt.Sprintf("arquivo rastreado ilegível: %s: %v", relative, err))
			continue
		}
		if info.Size() > maxDirectBinary && looksBinary(path) {
			problems = append(problems, fmt.Sprintf("binário grande fora do Git LFS (%d bytes): %s", info.Size(), relative))
		}
	}
	return count, problems, nil
}

func looksBinary(path string) bool {
	file, err := os.Open(path)
	if err != nil {
		return false
	}
	defer file.Close()
	sample := make([]byte, 8192)
	n, _ := io.ReadFull(file, sample)
	return bytes.IndexByte(sample[:n], 0) >= 0
}

const zeroSHA = "0000000000000000000000000000000000000000"

func checkedSHA(value any, label string) (string, error) {
	text, ok := value.(string)
	valid := ok && len(text) == 40
	for _, c := range text {
		valid = valid && strings.ContainsRune("0123456789abcdefABCDEF", c)
	}
	if !valid {
		return "", fmt.Errorf("SHA inválido para %s: %s", label, pyRepr(value))
	}
	return text, nil
}

// CommittedDiffCommand is the git command that checks the diff a CI event
// introduces.
func CommittedDiffCommand(eventName string, event *Object) ([]string, error) {
	switch eventName {
	case "pull_request":
		pull, ok := event.Value("pull_request").(*Object)
		if !ok {
			return nil, errors.New("Evento de pull request sem metadados do PR.")
		}
		base, baseOK := pull.Value("base").(*Object)
		head, headOK := pull.Value("head").(*Object)
		if !baseOK || !headOK {
			return nil, errors.New("Evento de pull request sem base/head.")
		}
		baseSHA, err := checkedSHA(base.Value("sha"), "base")
		if err != nil {
			return nil, err
		}
		headSHA, err := checkedSHA(head.Value("sha"), "head")
		if err != nil {
			return nil, err
		}
		return []string{"git", "diff", "--check", baseSHA, headSHA}, nil
	case "push":
		before, err := checkedSHA(event.Value("before"), "before")
		if err != nil {
			return nil, err
		}
		after, err := checkedSHA(event.Value("after"), "after")
		if err != nil {
			return nil, err
		}
		if before != zeroSHA {
			return []string{"git", "diff", "--check", before, after}, nil
		}
	}
	return []string{"git", "show", "--check", "--format=", "HEAD"}, nil
}

// CommittedDiffFromEnvironment selects the committed range like the CI
// script: the GitHub event when GITHUB_EVENT_PATH is set, else HEAD.
func CommittedDiffFromEnvironment() ([]string, error) {
	path := os.Getenv("GITHUB_EVENT_PATH")
	if path == "" {
		return []string{"git", "show", "--check", "--format=", "HEAD"}, nil
	}
	value, err := ReadJSON(path)
	if err != nil {
		return nil, fmt.Errorf("Erro ao determinar o diff commitado: %v", err)
	}
	event, ok := value.(*Object)
	if !ok {
		return nil, errors.New("Evento do GitHub precisa ser um objeto JSON.")
	}
	return CommittedDiffCommand(os.Getenv("GITHUB_EVENT_NAME"), event)
}

// CheckCommittedDiff runs the whitespace check and returns its report.
func CheckCommittedDiff(root string, command []string) (string, error) {
	process := exec.Command(command[0], command[1:]...)
	process.Dir = root
	output, err := process.CombinedOutput()
	if err != nil {
		return string(output), fmt.Errorf("%s falhou: %s", strings.Join(command, " "), strings.TrimSpace(string(output)))
	}
	return string(output), nil
}
