package setup

import (
	"context"
	"os"
	"testing"
	"time"
)

func TestDetectNetworkLive(t *testing.T) {
	if os.Getenv("X86QW_NETWORK_TESTS") != "1" {
		t.Skip("defina X86QW_NETWORK_TESTS=1 para consultar a rede")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	network := DetectNetwork(ctx)
	t.Logf("privados: %v", network.Private)
	t.Logf("público v4: %v (%v) v6: %v (%v)", network.PublicV4, network.PublicV4Err, network.PublicV6, network.PublicV6Err)
	if !network.PublicV4.IsValid() {
		t.Fatalf("IPv4 público não detectado: %v", network.PublicV4Err)
	}
	check := CheckHostname(ctx, "qw.x86.com.br", network)
	t.Logf("qw.x86.com.br → %v confere=%v err=%v", check.Resolved, check.Matches, check.Err)
}

func TestValidateRejectsPortCollisions(t *testing.T) {
	options := Defaults(Both, "/tmp/x86qw")
	options.Nick = "x86dx2"
	options.Server.QWFWDPort = options.Server.BasePort + 1
	if err := options.Validate(); err == nil {
		t.Fatal("QWFWD na porta de uma instância deveria ser recusado")
	}
	options.Server.QWFWDPort = 30000
	if err := options.Validate(); err != nil {
		t.Fatal(err)
	}
	if got := FakeName("x86dx2"); got != "x86" {
		t.Fatalf("fakename: %s", got)
	}
}

func TestDemoInstallFinishes(t *testing.T) {
	demo := &Demo{Speed: 20}
	options := Defaults(Both, "/tmp/x86qw")
	options.Nick = "x86dx2"
	var last Progress
	for progress := range demo.Install(context.Background(), options, nil) {
		last = progress
	}
	if !last.Finished || last.Done != last.Total || len(last.Log) == 0 {
		t.Fatalf("instalação simulada não terminou: %+v", last.Done)
	}
}
