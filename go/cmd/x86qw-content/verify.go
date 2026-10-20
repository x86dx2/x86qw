package main

import (
	"flag"
	"fmt"

	"github.com/x86dx2/x86qw/go/internal/content"
)

func runVerify(env *environment, args []string) (int, error) {
	flags := flag.NewFlagSet("verify", flag.ContinueOnError)
	noGit := flags.Bool("no-git", false, "pula as verificações de Git LFS e diff commitado")
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
	write := flags.Bool("write", false, "atualiza site/public/api/v1/product.json")
	if err := parseFlags("product", flags, args, env.stderr); err != nil {
		return 0, err
	}
	if *write {
		if err := env.project.WriteProduct(); err != nil {
			return exitError, err
		}
		fmt.Fprintf(env.stdout, "[OK] %s regenerado.\n", content.ProductPath)
		return exitOK, nil
	}
	if err := env.project.CheckProduct(); err != nil {
		return exitError, err
	}
	fmt.Fprintf(env.stdout, "[OK] %s corresponde aos inventarios canonicos.\n", content.ProductPath)
	return exitOK, nil
}

func init() {
	register("verify", "valida dist, inventarios, catalogo, TUF, Git LFS e estrutura", runVerify)
	register("product", "valida ou regenera (--write) site/public/api/v1/product.json", runProduct)
}
