package maint

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestUninstallRemovesManagedFilesAndKeepsPersonalOnes(t *testing.T) {
	isolate(t)
	root := managedInstall(t)
	write(t, filepath.Join(root, "qw", "maps", "x86qw-b.bsp"), "mapa b editado")
	report, err := Uninstall(root)
	if err != nil {
		t.Fatal(err)
	}
	for _, gone := range []string{"ezquake-nightly-x86_64.AppImage", "qw/maps/x86qw-a.bsp", "x86qw.sh", ".x86qw"} {
		if exists(filepath.Join(root, filepath.FromSlash(gone))) {
			t.Errorf("%s deveria ter sido removido", gone)
		}
	}
	for _, kept := range []string{"id1/pak0.pak", "id1/pak1.pak", "ezquake/configs/config.cfg", "qw/demos/partida.mvd"} {
		if !exists(filepath.Join(root, filepath.FromSlash(kept))) {
			t.Errorf("%s deveria ter sido preservado", kept)
		}
	}
	if read(t, filepath.Join(root, "qw", "maps", "x86qw-b.bsp")) != "mapa b editado" {
		t.Error("o arquivo modificado pelo usuário não foi preservado")
	}
	if len(report.Warnings) != 1 || !strings.Contains(report.Warnings[0], "x86qw-b.bsp") {
		t.Errorf("avisos: %v", report.Warnings)
	}
	lines := strings.Join(report.Lines, "\n")
	if !strings.Contains(lines, "CLI permanente x86QW removida") || !strings.Contains(lines, "Componentes gerenciados removidos") {
		t.Errorf("relatório: %s", lines)
	}
}

func TestUninstallRefusesWhatItDoesNotManage(t *testing.T) {
	isolate(t)
	plain := t.TempDir()
	write(t, filepath.Join(plain, "notas.txt"), "minhas")
	if _, err := Uninstall(plain); err == nil || !strings.Contains(err.Error(), "identidade gerenciada") {
		t.Fatalf("pasta sem x86QW deveria ser recusada: %v", err)
	}
	if !exists(filepath.Join(plain, "notas.txt")) {
		t.Fatal("a recusa apagou arquivos")
	}
	legacy := managedInstall(t)
	write(t, filepath.Join(legacy, filepath.FromSlash(nquakeReceipt)), "format=1\n")
	if _, err := Uninstall(legacy); err == nil || !strings.Contains(err.Error(), "anteriores ao 1.0") {
		t.Fatalf("layout pré-1.0 deveria ser recusado: %v", err)
	}
	if !exists(filepath.Join(legacy, "ezquake-nightly-x86_64.AppImage")) {
		t.Fatal("a recusa removeu o runtime")
	}
}

func TestUninstallOfACLIOnlyFolderKeepsPersonalFiles(t *testing.T) {
	isolate(t)
	root := filepath.Join(t.TempDir(), "x86qw")
	write(t, filepath.Join(root, ".x86qw", "cli", "receipt"), `{"version":"2.3.2"}`)
	write(t, filepath.Join(root, "x86qw.sh"), "#!/bin/sh\n")
	write(t, filepath.Join(root, "qw", "demos", "partida.mvd"), "demo")
	report, err := Uninstall(root)
	if err != nil {
		t.Fatal(err)
	}
	if exists(filepath.Join(root, ".x86qw")) || exists(filepath.Join(root, "x86qw.sh")) {
		t.Fatal("a CLI e os metadados deveriam ter saído")
	}
	if !exists(filepath.Join(root, "qw", "demos", "partida.mvd")) {
		t.Fatal("a demo foi apagada")
	}
	if !strings.Contains(strings.Join(report.Lines, "\n"), "CLI permanente x86QW removida") {
		t.Fatalf("relatório: %v", report.Lines)
	}
}

func TestPurgeRemovesTheInstallationAndTheCache(t *testing.T) {
	cache := isolate(t)
	ownedCache(t, cache)
	root := managedInstall(t)
	report, err := Purge(root)
	if err != nil {
		t.Fatal(err)
	}
	if exists(root) || exists(cache) {
		t.Fatalf("restou instalação (%v) ou cache (%v)", exists(root), exists(cache))
	}
	lines := strings.Join(report.Lines, "\n")
	if !strings.Contains(lines, "Cache removido") || !strings.Contains(lines, "Diretório da instalação removido") {
		t.Fatalf("relatório: %s", lines)
	}
}

// A cache holding files the installer does not own stops purge before it
// touches anything, the installation included.
func TestPurgeStopsOnAForeignCache(t *testing.T) {
	cache := isolate(t)
	write(t, filepath.Join(cache, "alheio.txt"), "não é do x86QW")
	root := managedInstall(t)
	if _, err := Purge(root); err == nil || !strings.Contains(err.Error(), "não pertencem ao instalador") {
		t.Fatalf("cache alheio deveria parar o purge: %v", err)
	}
	if !exists(filepath.Join(root, "id1", "pak0.pak")) || !exists(filepath.Join(cache, "alheio.txt")) {
		t.Fatal("o purge recusado apagou arquivos")
	}
}

func TestPurgeOfAMissingFolderStillClearsTheCache(t *testing.T) {
	cache := isolate(t)
	ownedCache(t, cache)
	missing := filepath.Join(t.TempDir(), "nunca-instalado")
	report, err := Purge(missing)
	if err != nil {
		t.Fatal(err)
	}
	if exists(cache) || !strings.Contains(strings.Join(report.Lines, "\n"), "Nenhum diretório de instalação") {
		t.Fatalf("cache %v, relatório %v", exists(cache), report.Lines)
	}
}

func TestQuarantineRestoresEverythingWhenOneMoveFails(t *testing.T) {
	root := t.TempDir()
	first := filepath.Join(root, "primeiro")
	write(t, filepath.Join(first, "a.txt"), "a")
	missing := filepath.Join(root, "nao-existe")
	if err := quarantineRemove([]string{first, missing}); err == nil {
		t.Fatal("mover um caminho inexistente deveria falhar")
	}
	if read(t, filepath.Join(first, "a.txt")) != "a" {
		t.Fatal("o primeiro caminho não voltou do recolhimento")
	}
	entries, _ := os.ReadDir(root)
	if len(entries) != 1 {
		t.Fatalf("sobrou quarentena: %v", entries)
	}
}

func TestMinimalRemovalPathsDropsDescendants(t *testing.T) {
	sep := string(filepath.Separator)
	got := minimalRemovalPaths([]string{"a" + sep + "b" + sep + "c", "a" + sep + "b", "a" + sep + "bc", "a" + sep + "b"})
	if strings.Join(got, ",") != "a"+sep+"b,a"+sep+"bc" {
		t.Fatalf("%v", got)
	}
}
