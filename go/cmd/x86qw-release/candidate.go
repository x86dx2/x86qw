package main

import (
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/x86dx2/x86qw/go/internal/content"
	contentpy "github.com/x86dx2/x86qw/go/internal/content/pyjson"
	"github.com/x86dx2/x86qw/go/internal/pyjson"
)

// runVerifyCandidate: x86qw-release verify-candidate DIR
// (release_candidate.py verify). The positional may come before or after
// the flags, as with argparse.
func runVerifyCandidate(args []string) int {
	flags := flag.NewFlagSet("verify-candidate", flag.ExitOnError)
	var options content.VerifyCandidateOptions
	flags.BoolVar(&options.AllowPendingEvidence, "allow-pending-evidence", false, "não suportado no verify nativo")
	flags.StringVar(&options.TrustRoot, "trust-root", "", "não suportado no verify nativo")
	candidate := ""
	if len(args) > 0 && !strings.HasPrefix(args[0], "-") {
		candidate, args = args[0], args[1:]
	}
	flags.Parse(args)
	if candidate == "" && flags.NArg() == 1 {
		candidate = flags.Arg(0)
	} else if flags.NArg() != 0 {
		candidate = ""
	}
	if candidate == "" {
		fmt.Println("uso: x86qw-release verify-candidate DIR")
		return 2
	}
	if _, err := content.VerifyCandidate(candidate, options); err != nil {
		fmt.Printf("[ERRO] %v\n", err)
		return 1
	}
	fmt.Println("[OK] Candidato verify validado.")
	return 0
}

// plain converts a decoded document to the values pyjson.Dumps renders.
func plain(value any) any {
	switch typed := value.(type) {
	case *contentpy.Object:
		result := map[string]any{}
		for _, key := range typed.Keys() {
			result[key] = plain(typed.Value(key))
		}
		return result
	case []any:
		result := make([]any, len(typed))
		for index, item := range typed {
			result[index] = plain(item)
		}
		return result
	case contentpy.Number:
		if number, ok := typed.Int(); ok && typed.IsInt() {
			return number
		}
		return typed.Float()
	}
	return value
}

// runVerifyTUFOperation: x86qw-release verify-tuf-operation --candidate DIR
// --report FILE (verify_tuf_operation_report.py); the verified report goes
// to stdout as one JSON line.
func runVerifyTUFOperation(args []string) int {
	flags := flag.NewFlagSet("verify-tuf-operation", flag.ExitOnError)
	candidate := flags.String("candidate", "", "candidato verificado")
	report := flags.String("report", "", "relatório do drill TUF")
	flags.Parse(args)
	if *candidate == "" || *report == "" {
		flags.Usage()
		return 2
	}
	verified, err := content.VerifyTUFOperationReport(*candidate, *report, time.Now().UTC())
	if err == nil {
		var line string
		if line, err = pyjson.Dumps(plain(verified), -1); err == nil {
			fmt.Println(line)
			return 0
		}
	}
	fmt.Fprintf(os.Stderr, "[ERRO] Relatório TUF inválido: %v\n", err)
	return 1
}
