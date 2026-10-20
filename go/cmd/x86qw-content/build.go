package main

import (
	"flag"
	"time"
)

func runBuild(env *environment, args []string) (int, error) {
	flags := flag.NewFlagSet("build", flag.ContinueOnError)
	register := flags.Bool("register", false, "atualiza o catalogo com os builds")
	projectRef := flags.String("project-ref", "", "SHA-1 do commit x86QW ligado ao ownership do candidato")
	if err := parseFlags("build", flags, args, env.stderr); err != nil {
		return 0, err
	}
	if err := env.project.Build(env.stdout, *register, *projectRef, time.Now()); err != nil {
		return exitError, err
	}
	return exitOK, nil
}

func init() {
	register("build", "monta pacotes deterministicos a partir do dist", runBuild)
}
