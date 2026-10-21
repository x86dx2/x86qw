package main

import (
	"flag"
	"fmt"

	"github.com/x86dx2/x86qw/go/internal/content"
)

func runVerify(env *environment, args []string) (int, error) {
	flags := flag.NewFlagSet("verify", flag.ContinueOnError)
	noGit := flags.Bool("no-git", false, "skips the Git LFS and committed diff checks")
	if err := parseFlags("verify", flags, args, env.stderr); err != nil {
		return 0, err
	}
	if err := env.project.Verify(env.stdout, content.VerifyOptions{SkipGit: *noGit}); err != nil {
		return exitError, err
	}
	return exitOK, nil
}

func runProduct(env *environment, args []string) (int, error) {
	flags := flag.NewFlagSet("product", flag.ContinueOnError)
	write := flags.Bool("write", false, "updates site/public/api/v1/product.json")
	if err := parseFlags("product", flags, args, env.stderr); err != nil {
		return 0, err
	}
	if *write {
		if err := env.project.WriteProduct(); err != nil {
			return exitError, err
		}
		fmt.Fprintf(env.stdout, "[OK] %s regenerated.\n", content.ProductPath)
		return exitOK, nil
	}
	if err := env.project.CheckProduct(); err != nil {
		return exitError, err
	}
	fmt.Fprintf(env.stdout, "[OK] %s matches the canonical inventories.\n", content.ProductPath)
	return exitOK, nil
}

func init() {
	register("verify", "validates dist, inventories, catalog, TUF, Git LFS and structure", runVerify)
	register("product", "validates or regenerates (--write) site/public/api/v1/product.json", runProduct)
}
