package main

import (
	"fmt"
	"io"
	"strings"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/doctor"
	"github.com/x86dx2/x86qw/go/internal/maint"
	"github.com/x86dx2/x86qw/go/internal/output"
)

type profileArgs struct{ target, backup, restore string }

func (a *profileArgs) parser() *cliargs.Parser {
	p := cliargs.New()
	targetFlag(p, &a.target)
	p.Optional(&a.backup, "x86qw-profile.zip", "FILE.zip", "writes a backup of the personal settings", "--backup")
	p.String(&a.restore, "FILE.zip", "restores a backup", "--restore")
	p.Exclusive("--backup", "--restore")
	return p
}

func runProfile(args []string, stdout, stderr io.Writer) int {
	var a profileArgs
	p := a.parser()
	if code, ok := parse("profile", p, args, stdout, stderr); !ok {
		return code
	}
	target, err := targetOf(p, a.target)
	if err != nil {
		return usageFailure("profile", err, stderr)
	}
	fmt.Fprint(stdout, maint.RenderProfile(maint.ClassifyInstallData(target)))
	if a.backup != "" {
		destination, err := doctor.BundleDestination(expandHome(a.backup), target)
		if err == nil {
			destination, err = maint.BackupProfile(target, destination)
		}
		if err != nil {
			fmt.Fprintf(stderr, "✗ %v\n", err)
			return output.ExitFailure
		}
		fmt.Fprintf(stdout, "✓ Perfil gravado: %s\n", destination)
	}
	if a.restore != "" {
		restored, err := maint.RestoreProfile(expandHome(a.restore), target)
		if err != nil {
			fmt.Fprintf(stderr, "✗ %v\n", err)
			return output.ExitFailure
		}
		list := "(none)"
		if len(restored) > 0 {
			list = strings.Join(restored, ", ")
		}
		fmt.Fprintf(stdout, "✓ Arquivos restaurados: %s\n", list)
	}
	return output.ExitSuccess
}

func init() {
	register(command{name: "profile", group: "Your data", order: 20, synopsis: "[TARGET]",
		summary: "lists, saves and restores the personal settings",
		flags:   (&profileArgs{}).parser, run: noName(runProfile)})
}
