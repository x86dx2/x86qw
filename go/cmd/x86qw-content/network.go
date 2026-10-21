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

// confirm asks for a yes on an interactive terminal.
func confirm(env *environment) content.Confirm {
	return func(message string) error {
		if env.stdin == nil || !term.IsTerminal(int(env.stdin.Fd())) {
			return errors.New("use --yes to confirm the operation without an interactive terminal")
		}
		fmt.Fprintf(env.stdout, "%s [y/N]: ", message)
		answer, _ := bufio.NewReader(env.stdin).ReadString('\n')
		switch strings.ToLower(strings.TrimSpace(answer)) {
		case "y", "yes":
			return nil
		}
		return errors.New("operation cancelled; no file was changed")
	}
}

func runCheck(env *environment, args []string) (int, error) {
	flags := flag.NewFlagSet("check", flag.ContinueOnError)
	offline := flags.Bool("offline", false, "validates declared versions only")
	asJSON := flags.Bool("json", false, "structured output")
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
	flags.IntVar(&options.Workers, "workers", 8, "parallel downloads (1-32)")
	flags.BoolVar(&options.DryRun, "dry-run", false, "only shows the plan")
	flags.BoolVar(&options.Yes, "yes", false, "confirms without an interactive terminal")
	flags.BoolVar(&options.Commit, "commit", false, "commits after updating")
	flags.BoolVar(&options.Push, "push", false, "pushes the commit to origin and gitlab")
	flags.StringVar(&options.Message, "message", "", "commit message")
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
	flags.BoolVar(&options.DryRun, "dry-run", false, "only validates the definition")
	flags.BoolVar(&options.Yes, "yes", false, "confirms without an interactive terminal")
	// The positional definition may come before or after the flags.
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
	githubOnly := flags.Bool("github-only", false, "publishes to GitHub only")
	gitlabOnly := flags.Bool("gitlab-only", false, "publishes to GitLab only")
	dryRun := flags.Bool("dry-run", false, "only verifies")
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
	message := flags.String("message", "", "commit message")
	push := flags.Bool("push", false, "pushes to origin and gitlab")
	if err := parseFlags("commit", flags, args, env.stderr); err != nil {
		return 0, err
	}
	if err := env.project.Commit(env.stdout, *message, *push); err != nil {
		return exitError, err
	}
	return exitOK, nil
}

func init() {
	register("check", "checks upstreams without changing files; [COMPONENT...] (exit 2 = updates, 1 = unavailable)", runCheck)
	register("update", "updates each component independently; [COMPONENT...]", runUpdate)
	register("add", "incorporates a package/configuration from a reviewed definition", runAdd)
	register("publish", "publishes the artifacts to the GitHub and GitLab mirrors", runPublish)
	register("commit", "records only distribution changes in Git", runCommit)
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
