package main

import (
	"bytes"
	"fmt"
	"io"
	"path/filepath"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/doctor"
	"github.com/x86dx2/x86qw/go/internal/download"
	"github.com/x86dx2/x86qw/go/internal/output"
)

func runDoctor(args []string, stdout, stderr io.Writer) int {
	bundle := ""
	var rest []string
	for index := 0; index < len(args); index++ {
		switch {
		case args[index] == "--bundle":
			bundle = "x86qw-doctor.zip"
			if index+1 < len(args) && !strings.HasPrefix(args[index+1], "-") && strings.HasSuffix(args[index+1], ".zip") {
				bundle = args[index+1]
				index++
			}
		case strings.HasPrefix(args[index], "--bundle="):
			bundle = strings.TrimPrefix(args[index], "--bundle=")
		default:
			rest = append(rest, args[index])
		}
	}
	target, asJSON, ok := targetFlags("doctor", rest, stderr)
	if !ok {
		return output.ExitUsage
	}
	var timestamps []string
	if dir := defaultCacheDir(); dir != "" {
		timestamps = append(timestamps, filepath.Join(dir, "metadata", "timestamp.json"))
	}
	if dir, err := download.CacheDir(); err == nil {
		timestamps = append(timestamps, filepath.Join(dir, "trust", "metadata", "timestamp.json"))
	}
	report := doctor.Diagnose(target, publicCommandNames(), timestamps, time.Now().UTC())
	if bundle != "" {
		var envelope bytes.Buffer
		output.Success("doctor", report).Write(&envelope)
		written, err := doctor.WriteBundle(envelope.Bytes(), bundle, target)
		if err != nil {
			fmt.Fprintf(stderr, "✗ %v\n", err)
			return output.ExitFailure
		}
		fmt.Fprintf(stderr, "Bundle de diagnóstico gravado em %s\n", written)
	}
	if asJSON {
		if err := output.Success("doctor", report).Write(stdout); err != nil {
			return output.ExitFailure
		}
		return output.ExitSuccess
	}
	fmt.Fprint(stdout, doctor.Render(report))
	return output.ExitSuccess
}

// publicCommandNames lists the registered public commands.
func publicCommandNames() []string {
	var names []string
	for name, cmd := range commands {
		if cmd.order >= 0 {
			names = append(names, name)
		}
	}
	return names
}

func init() {
	register("doctor", 90, "doctor [--bundle]    diagnostica a instalação sem alterar arquivos", noName(runDoctor))
}
