package main

import (
	"context"
	"flag"
	"fmt"

	"github.com/x86dx2/x86qw/go/internal/content"
)

func runLFSMaterialize(env *environment, args []string) (int, error) {
	flags := flag.NewFlagSet("lfs-materialize", flag.ContinueOnError)
	repository := flags.String("repository", "", "repositório GitHub (owner/nome)")
	commit := flags.String("commit", "", "commit cujos objetos LFS serão baixados")
	if err := parseFlags("lfs-materialize", flags, args, env.stderr); err != nil {
		return 0, err
	}
	if *repository == "" || *commit == "" {
		return 0, &usageError{msg: "the following arguments are required: --repository, --commit"}
	}
	created, err := content.MaterializeLFS(context.Background(), env.project.Root, *repository, *commit, content.FetchLFSObject)
	if err != nil {
		return exitError, err
	}
	fmt.Fprintf(env.stdout, "[OK] LFS: %d objetos novos verificados no cache.\n", created)
	return exitOK, nil
}

func init() {
	register("lfs-materialize", "baixa e verifica os objetos LFS do commit pelo endpoint de mídia do GitHub", runLFSMaterialize)
}
