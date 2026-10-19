package main

import (
	"fmt"
	"io"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
)

func runProfile(args []string, stdout, stderr io.Writer) int {
	parsed, ok := parseArgs("profile", args, []string{"--restore"}, nil, []optionalValue{{"--backup", "x86qw-profile.zip"}}, stderr)
	if !ok {
		return output.ExitUsage
	}
	backup, hasBackup := parsed.values["--backup"]
	restore, hasRestore := parsed.values["--restore"]
	if hasBackup && hasRestore {
		fmt.Fprintln(stderr, "x86qw profile: erro: --backup e --restore não podem ser combinados")
		return output.ExitUsage
	}
	fmt.Fprint(stdout, maint.RenderProfile(maint.ClassifyInstallData(parsed.target)))
	if hasBackup {
		destination, err := maint.ResolveBundleDestination(backup, parsed.target)
		if err == nil {
			destination, err = maint.BackupProfile(parsed.target, destination)
		}
		if err != nil {
			fmt.Fprintf(stderr, "✗ %v\n", err)
			return output.ExitFailure
		}
		fmt.Fprintf(stdout, "Perfil gravado: %s\n", destination)
	}
	if hasRestore {
		restored, err := maint.RestoreProfile(expandHome(restore), parsed.target)
		if err != nil {
			fmt.Fprintf(stderr, "✗ %v\n", err)
			return output.ExitFailure
		}
		list := "(nenhum)"
		if len(restored) > 0 {
			list = strings.Join(restored, ", ")
		}
		fmt.Fprintf(stdout, "Arquivos restaurados: %s\n", list)
	}
	return output.ExitSuccess
}

func init() {
	register("profile", 120, "profile [--backup|--restore] configurações pessoais", noName(runProfile))
}
