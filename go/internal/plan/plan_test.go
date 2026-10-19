package plan

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/x86dx2/x86qw/go/internal/catalog"
	"github.com/x86dx2/x86qw/go/internal/state"
)

func sum(data string) string {
	digest := sha256.Sum256([]byte(data))
	return hex.EncodeToString(digest[:])
}

func write(t *testing.T, root, name, content string) {
	t.Helper()
	path := filepath.Join(root, filepath.FromSlash(name))
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func component(t *testing.T, root, id, selection string) {
	inventory := "qw/" + id + ".txt\t" + sum(id) + "\n"
	write(t, root, ".x86qw/components/"+id+"/inventory", inventory)
	write(t, root, ".x86qw/components/"+id+"/receipt",
		"format\t1\ncomponent\t"+id+"\nselection\t"+selection+"\nsource\tx\ninventory_sha256\t"+sum(inventory)+"\n")
}

func fixture(t *testing.T) Input {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("..", "..", "..", "site", "public", "api", "v1", "catalog.json"))
	if err != nil {
		t.Fatal(err)
	}
	cat, err := catalog.Parse(raw)
	if err != nil {
		t.Fatal(err)
	}
	inventory, err := catalog.LoadInventory()
	if err != nil {
		t.Fatal(err)
	}
	root := t.TempDir()
	stable, _ := cat.ClientReleases(catalog.ClientSpecs[0], "stable")
	release := stable[0]
	write(t, root, ".x86qw/clients/ezquake/macos/stable.receipt", strings.Join([]string{
		"format\t1", "platform\tmacos", "architecture\tuniversal", "channel\tstable",
		"selection\t" + release.Version, "install_name\tezQuake Stable.app", "bundle_version\t" + release.Version,
		"artifact_name\t" + release.Filename, "artifact_url\t" + release.URLs[0],
		"artifact_sha256\t" + release.SHA256, "binary_sha256\t" + strings.Repeat("a", 64),
	}, "\n")+"\n")
	essential, _ := inventory.Desired("essential", nil)
	for _, id := range essential {
		pkg, err := cat.ComponentPackage(id, inventory.ContentNamespaces)
		if err != nil {
			t.Fatal(err)
		}
		selection := pkg.Version
		if id == "ktx" {
			selection = "0-old"
		}
		component(t, root, id, selection)
	}
	write(t, root, state.Path, `{"capabilities":[],"component_fingerprint":"`+state.Fingerprint(essential)+
		`","format":2,"known_components":[],"min_cli_version":"0.7.0","profile":"recommended","project":"x86qw",`+
		`"recorded_components":["`+strings.Join(essential, `","`)+`"],"requested_components":[],"state_version":2}`)
	return Input{Root: root, Catalog: cat, Inventory: inventory}
}

func TestUpdatePlansOutdatedComponent(t *testing.T) {
	result, err := Update(fixture(t))
	if err != nil {
		t.Fatal(err)
	}
	if result.Status != "planned" || len(result.Operations) != 1 {
		t.Fatalf("plano: %+v", result)
	}
	row := result.Operations[0]
	if row.Kind != "Componente" || row.Installed != "0-old" || row.Action != "Atualizar" || row.Size == nil {
		t.Fatalf("linha: %+v", row)
	}
}

func TestUpgradeAddsMissingProfileComponents(t *testing.T) {
	result, err := Upgrade(fixture(t))
	if err != nil {
		t.Fatal(err)
	}
	added := 0
	for _, row := range result.Operations {
		if row.Action == "Adicionar" {
			added++
			if row.Installed != "não instalado" {
				t.Fatalf("linha: %+v", row)
			}
		}
	}
	if added != 9 {
		t.Fatalf("recommended deveria adicionar 9 componentes, adicionou %d", added)
	}
}

func TestOverlayNewerThanCatalogIsKept(t *testing.T) {
	if !newerOverlay("1.47+x86qw.20", "1.47+x86qw.19") || newerOverlay("1.47+x86qw.18", "1.47+x86qw.19") ||
		newerOverlay("1.48+x86qw.20", "1.47+x86qw.19") {
		t.Fatal("regra de overlay +x86qw.N")
	}
}

func TestUpdateRequiresAManagedClient(t *testing.T) {
	input := fixture(t)
	os.RemoveAll(filepath.Join(input.Root, ".x86qw", "clients"))
	if _, err := Update(input); err == nil {
		t.Fatal("instalação sem ezQuake deveria falhar")
	}
}
