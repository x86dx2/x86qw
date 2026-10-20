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

// runRenewTimestampHandoff: x86qw-release renew-timestamp-handoff
// --repository DIR --root FILE --catalog FILE --timestamp-key PEM --key-id ID
// --output DIR --report FILE --lease-hours N (tuf_timestamp_renewal.py)
func runRenewTimestampHandoff(args []string) int {
	flags := flag.NewFlagSet("renew-timestamp-handoff", flag.ExitOnError)
	var h tufsign.TimestampHandoff
	flags.StringVar(&h.Repository, "repository", "", "repositório TUF corrente")
	flags.StringVar(&h.Root, "root", "", "root TUF incorporada")
	flags.StringVar(&h.Catalog, "catalog", "", "catálogo autenticado pelo repositório")
	flags.StringVar(&h.Key.Path, "timestamp-key", "", "chave PEM da role timestamp (0600)")
	flags.StringVar(&h.Key.ID, "key-id", "", "key id timestamp")
	flags.StringVar(&h.Output, "output", "", "repositório renovado (novo)")
	flags.StringVar(&h.Report, "report", "", "relatório da renovação (novo)")
	flags.IntVar(&h.LeaseHours, "lease-hours", 0, "duração da lease em horas")
	flags.Parse(args)
	if h.Repository == "" || h.Root == "" || h.Catalog == "" || h.Key.Path == "" || h.Key.ID == "" || h.Output == "" || h.Report == "" || h.LeaseHours == 0 {
		flags.Usage()
		return 2
	}
	h.Now = time.Now()
	report, err := tufsign.RenewTimestampHandoff(h)
	if err != nil {
		fmt.Fprintf(os.Stderr, "[ERRO] Renovação TUF timestamp falhou: %v\n", err)
		return 1
	}
	fmt.Println(report)
	return 0
}

// runRenewSnapshotHandoff: x86qw-release renew-snapshot-handoff
// --repository DIR --root FILE --catalog FILE --snapshot-key PEM
// --snapshot-key-id ID --timestamp-key PEM --timestamp-key-id ID
// --output DIR --report FILE [--snapshot-lease-days N]
// [--timestamp-lease-days N] (tuf_snapshot_renewal.py)
func runRenewSnapshotHandoff(args []string) int {
	flags := flag.NewFlagSet("renew-snapshot-handoff", flag.ExitOnError)
	var h tufsign.SnapshotHandoff
	flags.StringVar(&h.Repository, "repository", "", "repositório TUF corrente")
	flags.StringVar(&h.Root, "root", "", "root TUF incorporada")
	flags.StringVar(&h.Catalog, "catalog", "", "catálogo autenticado pelo repositório")
	flags.StringVar(&h.Snapshot.Path, "snapshot-key", "", "chave PEM da role snapshot (0600)")
	flags.StringVar(&h.Snapshot.ID, "snapshot-key-id", "", "key id snapshot")
	flags.StringVar(&h.Timestamp.Path, "timestamp-key", "", "chave PEM da role timestamp (0600)")
	flags.StringVar(&h.Timestamp.ID, "timestamp-key-id", "", "key id timestamp")
	flags.StringVar(&h.Output, "output", "", "repositório renovado (novo)")
	flags.StringVar(&h.Report, "report", "", "relatório da renovação (novo)")
	flags.IntVar(&h.SnapshotLeaseDays, "snapshot-lease-days", tufsign.RoleExpiryDays["snapshot"], "lease snapshot em dias")
	flags.IntVar(&h.TimestampLeaseDays, "timestamp-lease-days", tufsign.RoleExpiryDays["timestamp"], "lease timestamp em dias")
	flags.Parse(args)
	if h.Repository == "" || h.Root == "" || h.Catalog == "" || h.Snapshot.Path == "" || h.Snapshot.ID == "" || h.Timestamp.Path == "" || h.Timestamp.ID == "" || h.Output == "" || h.Report == "" {
		flags.Usage()
		return 2
	}
	h.Now = time.Now()
	report, err := tufsign.RenewSnapshotHandoff(h)
	if err != nil {
		fmt.Fprintf(os.Stderr, "[ERRO] Renovação TUF snapshot falhou: %v\n", err)
		return 1
	}
	fmt.Println(report)
	return 0
}
