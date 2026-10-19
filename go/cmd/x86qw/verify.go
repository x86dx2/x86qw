package main

import (
	"fmt"
	"io"

	"github.com/x86dx2/x86qw/go/internal/install"
	"github.com/x86dx2/x86qw/go/internal/output"
)

func runVerify(args []string, stdout, stderr io.Writer) int {
	target, asJSON, ok := targetFlags("verify", args, stderr)
	if !ok {
		return output.ExitUsage
	}
	report, err := install.Verify(target)
	if err != nil {
		if asJSON {
			output.Failure("verify", output.ExitFailure, "operation", err.Error()).Write(stdout)
		} else {
			fmt.Fprintf(stderr, "✗ %v\n", err)
		}
		return output.ExitFailure
	}
	if asJSON {
		data := map[string]any{"target": target, "verified": true}
		if err := output.Success("verify", data).Write(stdout); err != nil {
			return output.ExitFailure
		}
		return output.ExitSuccess
	}
	fmt.Fprintf(stdout, "✓ ezQuake (%d) + %d componentes íntegros · %d arquivos\n", report.Clients, len(report.Components), report.Files)
	return output.ExitSuccess
}

func init() {
	register("verify", 30, "verify               verifica a integridade da instalação", noName(runVerify))
}
