package main

import (
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/output"
)

func runStatus(args []string, stdout, stderr io.Writer) int {
	target, asJSON, ok := targetFlags("status", args, stderr)
	if !ok {
		return output.ExitUsage
	}
	status := install.ReadStatus(target)
	if asJSON {
		if err := output.Success("status", status).Write(stdout); err != nil {
			return output.ExitFailure
		}
		return output.ExitSuccess
	}
	fmt.Fprintf(stdout, "Instalação: %s (%s)\nEstado: %s\n", target, status.Installation, status.State)
	if len(status.Sessions) == 0 {
		fmt.Fprintln(stdout, "Nenhum serviço registrado.")
	}
	for _, session := range status.Sessions {
		fmt.Fprintf(stdout, "  %s  %-10s %s\n", session.SessionID, session.Status, session.Command)
	}
	return output.ExitSuccess
}

func init() {
	register("status", 20, "status               mostra instalação, estado e serviços registrados", noName(runStatus))
}
