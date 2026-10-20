package main

import (
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/x86dx2/x86qw/go/internal/tufsign"
)

// runSign: x86qw-release sign --keys DIR --root FILE --catalog FILE --out DIR --version N
func runSign(args []string) int {
	flags := flag.NewFlagSet("sign", flag.ExitOnError)
	keys := flags.String("keys", ".tuf-production/keys", "diretório das chaves offline")
	root := flags.String("root", "maintenance/trust/root.json", "root TUF")
	catalog := flags.String("catalog", "site/public/api/v1/catalog.json", "catálogo a assinar")
	out := flags.String("out", "", "repositório de saída (novo)")
	version := flags.Int64("version", 0, "versão de targets/snapshot/timestamp")
	flags.Parse(args)
	if *out == "" || *version <= 0 {
		flags.Usage()
		return 2
	}
	if err := tufsign.Generate(*keys, *root, *catalog, *out, *version, time.Now().UTC()); err != nil {
		fmt.Fprintln(os.Stderr, "x86qw-release sign:", err)
		return 1
	}
	fmt.Printf("repositório v%d assinado em %s\n", *version, *out)
	return 0
}

// runRenew: x86qw-release renew-timestamp --current FILE --key PEM --out FILE [--lease-hours 720]
func runRenew(args []string) int {
	flags := flag.NewFlagSet("renew-timestamp", flag.ExitOnError)
	current := flags.String("current", "site/public/api/v1/trust/metadata/timestamp.json", "timestamp atual")
	key := flags.String("key", "", "chave PEM da role timestamp")
	root := flags.String("root", "maintenance/trust/root.json", "root TUF")
	out := flags.String("out", "", "timestamp renovado")
	lease := flags.Int("lease-hours", 720, "duração da lease")
	flags.Parse(args)
	if *key == "" || *out == "" {
		flags.Usage()
		return 2
	}
	payload, err := os.ReadFile(*current)
	if err == nil {
		payload, err = tufsign.RenewTimestamp(payload, *key, *root, time.Duration(*lease)*time.Hour, time.Now().UTC())
	}
	if err == nil {
		err = os.WriteFile(*out, payload, 0o644)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "x86qw-release renew-timestamp:", err)
		return 1
	}
	return 0
}
