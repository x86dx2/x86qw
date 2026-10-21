package main

import (
	"bytes"
	"fmt"
	"io"
	"path/filepath"
	"time"

	"github.com/x86dx2/x86qw/go/internal/cliargs"
	"github.com/x86dx2/x86qw/go/internal/doctor"
	"github.com/x86dx2/x86qw/go/internal/output"
)

type doctorArgs struct {
	targetArgs
	bundle string
}

func (a *doctorArgs) parser() *cliargs.Parser {
	p := a.targetArgs.parser()
	p.Optional(&a.bundle, "x86qw-doctor.zip", "ARQUIVO.zip", "grava também um bundle de diagnóstico", "--bundle")
	return p
}

func runDoctor(args []string, stdout, stderr io.Writer) int {
	var a doctorArgs
	p := a.parser()
	if code, ok := parse("doctor", p, args, stdout, stderr); !ok {
		return code
	}
	target, err := targetOf(p, a.target)
	if err != nil {
		return usageFailure("doctor", err, stderr)
	}
	var timestamps []string
	if dir := defaultCacheDir(); dir != "" {
		timestamps = append(timestamps, filepath.Join(dir, "metadata", "timestamp.json"))
	}
	report := doctor.Diagnose(target, timestamps, time.Now().UTC())
	if a.bundle != "" {
		var envelope bytes.Buffer
		output.Success("doctor", report).Write(&envelope)
		written, err := doctor.WriteBundle(envelope.Bytes(), expandHome(a.bundle), target)
		if err != nil {
			fmt.Fprintf(stderr, "✗ %v\n", err)
			return output.ExitFailure
		}
		fmt.Fprintf(stderr, "Bundle de diagnóstico gravado em %s\n", written)
	}
	if a.json {
		if err := output.Success("doctor", report).Write(stdout); err != nil {
			return output.ExitFailure
		}
		return output.ExitSuccess
	}
	fmt.Fprint(stdout, doctor.Render(report))
	return output.ExitSuccess
}

func init() {
	register(command{name: "doctor", group: "Diagnóstico", order: 10, synopsis: "[DESTINO]",
		summary: "diagnostica a instalação sem alterar arquivos",
		flags:   (&doctorArgs{}).parser, run: noName(runDoctor)})
}
