package main

import (
	"flag"
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/buildinfo"
	"github.com/x86dx2/x86qw/go/internal/output"
)

func runVersion(args []string, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("version", flag.ContinueOnError)
	flags.SetOutput(stderr)
	asJSON := flags.Bool("json", false, "saída estruturada")
	if err := flags.Parse(args); err != nil {
		return output.ExitUsage
	}
	if *asJSON {
		data := map[string]any{"project": "x86qw", "version": buildinfo.Version}
		if err := output.Success("version", data).Write(stdout); err != nil {
			return output.ExitFailure
		}
		return output.ExitSuccess
	}
	fmt.Fprintf(stdout, "x86QW %s\n", buildinfo.Version)
	return output.ExitSuccess
}

func init() {
	register("version", 10, "version              mostra a versão", noName(runVersion), "--version")
}
