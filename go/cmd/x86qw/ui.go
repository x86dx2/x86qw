package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
)

type uiArgs struct{ target, output string }

func (a *uiArgs) parser() *cliargs.Parser {
	p := cliargs.New()
	targetFlag(p, &a.target)
	p.String(&a.output, "ARQUIVO.html", "onde gravar (padrão: um arquivo temporário)", "--output")
	return p
}

func runUI(args []string, stdout, stderr io.Writer) int {
	var a uiArgs
	p := a.parser()
	if code, ok := parse("ui", p, args, stdout, stderr); !ok {
		return code
	}
	target, err := targetOf(p, a.target)
	if err != nil {
		return usageFailure("ui", err, stderr)
	}
	destination := expandHome(a.output)
	if destination == "" {
		// A fresh folder of our own, so the page goes where no one else
		// can have put anything first.
		dir, err := os.MkdirTemp("", "x86qw-ui-")
		if err != nil {
			fmt.Fprintf(stderr, "✗ %v\n", err)
			return output.ExitFailure
		}
		destination = filepath.Join(dir, "x86qw.html")
	}
	written, err := maint.WriteLocalUI(target, destination)
	if err != nil {
		fmt.Fprintf(stderr, "✗ %v\n", err)
		return output.ExitFailure
	}
	fmt.Fprintln(stdout, filepath.ToSlash(written))
	return output.ExitSuccess
}

func init() {
	register(command{name: "ui", group: "Seus dados", order: 40, synopsis: "[DESTINO]",
		summary: "gera um painel HTML local, só de leitura",
		flags:   (&uiArgs{}).parser, run: noName(runUI)})
}
