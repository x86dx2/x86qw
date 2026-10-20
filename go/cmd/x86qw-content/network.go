package main

import (
	"bufio"
	"errors"
	"flag"
	"fmt"
	"os"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/content"
	"golang.org/x/term"
)

// confirm mirrors manage.confirm for interactive terminals.
func confirm(env *environment) content.Confirm {
	return func(message string) error {
		if env.stdin == nil || !term.IsTerminal(int(env.stdin.Fd())) {
			return errors.New("use --yes para confirmar a operacao sem terminal interativo")
		}
		fmt.Fprintf(env.stdout, "%s [s/N]: ", message)
		answer, _ := bufio.NewReader(env.stdin).ReadString('\n')
		switch strings.ToLower(strings.TrimSpace(answer)) {
		case "s", "sim":
			return nil
		}
		return errors.New("operacao cancelada; nenhum arquivo foi alterado")
	}
}

func runCheck(env *environment, args []string) (int, error) {
	flags := flag.NewFlagSet("check", flag.ContinueOnError)
	offline := flags.Bool("offline", false, "valida somente versoes declaradas")
	asJSON := flags.Bool("json", false, "saida estruturada")
	components, rest := splitPositional(args)
	if err := parseFlags("check", flags, rest, env.stderr); err != nil {
		return 0, err
	}
	code, err := env.project.Check(env.stdout, env.stderr, *offline, *asJSON, components)
	if err != nil {
		return exitError, err
	}
	return code, nil
}

func runUpdate(env *environment, args []string) (int, error) {
	flags := flag.NewFlagSet("update", flag.ContinueOnError)
	options := content.UpdateOptions{Confirm: confirm(env)}
	flags.IntVar(&options.Workers, "workers", 8, "downloads paralelos (1-32)")
	flags.BoolVar(&options.DryRun, "dry-run", false, "somente mostra o plano")
	flags.BoolVar(&options.Yes, "yes", false, "confirma sem terminal interativo")
	flags.BoolVar(&options.Commit, "commit", false, "cria commit apos atualizar")
	flags.BoolVar(&options.Push, "push", false, "envia o commit para origin e gitlab")
	flags.StringVar(&options.Message, "message", "", "mensagem do commit")
	components, rest := splitPositional(args)
	if err := parseFlags("update", flags, rest, env.stderr); err != nil {
		return 0, err
	}
	options.Components = components
	if err := env.project.Update(env.stdout, options); err != nil {
		return exitError, err
	}
	return exitOK, nil
}

func runAdd(env *environment, args []string) (int, error) {
	flags := flag.NewFlagSet("add", flag.ContinueOnError)
	options := content.AddOptions{Confirm: confirm(env)}
	flags.BoolVar(&options.DryRun, "dry-run", false, "somente valida a definicao")
	flags.BoolVar(&options.Yes, "yes", false, "confirma sem terminal interativo")
	// argparse accepts the positional definition before or after flags.
	var positional []string
	var rest []string
	for _, arg := range args {
		if strings.HasPrefix(arg, "-") {
			rest = append(rest, arg)
		} else {
			positional = append(positional, arg)
		}
	}
	if err := parseFlags("add", flags, rest, env.stderr); err != nil {
		return 0, err
	}
	positional = append(positional, flags.Args()...)
	if len(positional) != 1 {
		return 0, &usageError{msg: "the following arguments are required: definition"}
	}
	if err := env.project.Add(env.stdout, positional[0], options); err != nil {
		return exitError, err
	}
	return exitOK, nil
}

func runPublish(env *environment, args []string) (int, error) {
	flags := flag.NewFlagSet("publish", flag.ContinueOnError)
	githubOnly := flags.Bool("github-only", false, "publica somente no GitHub")
	gitlabOnly := flags.Bool("gitlab-only", false, "publica somente no GitLab")
	dryRun := flags.Bool("dry-run", false, "somente verifica")
	if err := parseFlags("publish", flags, args, env.stderr); err != nil {
		return 0, err
	}
	if *githubOnly && *gitlabOnly {
		return 0, &usageError{msg: "argument --gitlab-only: not allowed with argument --github-only"}
	}
	if err := env.project.Publish(env.stdout, *githubOnly, *gitlabOnly, *dryRun); err != nil {
		return exitError, err
	}
	return exitOK, nil
}

func runCommit(env *environment, args []string) (int, error) {
	flags := flag.NewFlagSet("commit", flag.ContinueOnError)
	message := flags.String("message", "", "mensagem do commit")
	push := flags.Bool("push", false, "envia para origin e gitlab")
	if err := parseFlags("commit", flags, args, env.stderr); err != nil {
		return 0, err
	}
	if err := env.project.Commit(env.stdout, *message, *push); err != nil {
		return exitError, err
	}
	return exitOK, nil
}

func init() {
	register("check", "consulta upstreams sem alterar arquivos; [COMPONENTE...] (exit 2 = atualizacoes, 1 = indisponivel)", runCheck)
	register("update", "atualiza cada componente de forma independente; [COMPONENTE...]", runUpdate)
	register("add", "incorpora pacote/configuracao por definicao revisada", runAdd)
	register("publish", "publica os artefatos nos mirrors GitHub e GitLab", runPublish)
	register("commit", "registra no Git somente mudancas da distribuicao", runCommit)
	_ = os.Stdin
}

// splitPositional separates component names from flags, which may come in
// any order; a flag that takes a value keeps it.
func splitPositional(args []string) (positional, flags []string) {
	valued := map[string]bool{"--workers": true, "-workers": true, "--message": true, "-message": true}
	for index := 0; index < len(args); index++ {
		arg := args[index]
		switch {
		case strings.HasPrefix(arg, "-"):
			flags = append(flags, arg)
			if valued[arg] && index+1 < len(args) {
				index++
				flags = append(flags, args[index])
			}
		default:
			positional = append(positional, arg)
		}
	}
	return positional, flags
}
