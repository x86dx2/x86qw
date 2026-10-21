package main

import (
	"context"
	"flag"
	"fmt"

	"github.com/x86dx2/x86qw/go/internal/content"
)

func runLFSMaterialize(env *environment, args []string) (int, error) {
	flags := flag.NewFlagSet("lfs-materialize", flag.ContinueOnError)
	repository := flags.String("repository", "", "GitHub repository (owner/name)")
	commit := flags.String("commit", "", "commit whose LFS objects are downloaded")
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
	fmt.Fprintf(env.stdout, "[OK] LFS: %d new objects verified in the cache.\n", created)
	return exitOK, nil
}

func init() {
	register("lfs-materialize", "downloads and verifies the commit's LFS objects through GitHub's media endpoint", runLFSMaterialize)
}
