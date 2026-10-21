package main

import (
	"flag"
	"time"
)

func runBuild(env *environment, args []string) (int, error) {
	flags := flag.NewFlagSet("build", flag.ContinueOnError)
	register := flags.Bool("register", false, "registers the builds in the catalog")
	projectRef := flags.String("project-ref", "", "SHA-1 of the x86QW commit tied to the candidate's ownership")
	if err := parseFlags("build", flags, args, env.stderr); err != nil {
		return 0, err
	}
	if err := env.project.Build(env.stdout, *register, *projectRef, time.Now()); err != nil {
		return exitError, err
	}
	return exitOK, nil
}

func init() {
	register("build", "builds deterministic packages from dist", runBuild)
}
