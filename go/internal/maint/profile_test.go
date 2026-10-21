package maint

import (
	"archive/zip"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// profiled is an installation with personal configs and things a profile
// must never carry (cache, demos, managed files).
func profiled(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	write(t, filepath.Join(root, "ezquake", "configs", "config.cfg"), "sensitivity 3\n")
	write(t, filepath.Join(root, "qw", "x86qw-ktx-user.cfg"), "// ktx\n")
	write(t, filepath.Join(root, "qtv", "qtv.cfg"), "port 28000\n")
	write(t, filepath.Join(root, "qw", "demos", "partida.mvd"), "demo")
	write(t, filepath.Join(root, "ezquake", "sb", "cache", "x"), "cache")
	write(t, filepath.Join(root, "qw", "ktx.pk3"), "gerenciado")
	return root
}

func TestProfileClassifiesOnlyUserOwnedFiles(t *testing.T) {
	report := ClassifyInstallData(profiled(t))
	if got := strings.Join(report.Profile, ","); got != "ezquake/configs/config.cfg,qtv/qtv.cfg,qw/x86qw-ktx-user.cfg" {
		t.Fatalf("perfil: %s", got)
	}
	if strings.Join(report.Cache, ",") != "ezquake/sb/cache" || strings.Join(report.Personal, ",") != "qw/demos" {
		t.Fatalf("cache %v, pessoal %v", report.Cache, report.Personal)
	}
	for _, path := range []string{"../qw/x86qw-a.cfg", "qw/x86qw-a.pk3", "qw/sub/x86qw-a.cfg", "/qw/x86qw-a.cfg", `qw\x86qw-a.cfg`} {
		if IsUserProfilePath(path) {
			t.Errorf("%s não é perfil", path)
		}
	}
}

func TestProfileBackupRestoresByteForByte(t *testing.T) {
	root := profiled(t)
	archive := filepath.Join(t.TempDir(), "perfil.zip")
	if _, err := BackupProfile(root, archive); err != nil {
		t.Fatal(err)
	}
	if _, err := BackupProfile(root, archive); err == nil {
		t.Fatal("um backup não pode sobrescrever outro")
	}
	write(t, filepath.Join(root, "ezquake", "configs", "config.cfg"), "sensitivity 9\n")
	os.Remove(filepath.Join(root, "qtv", "qtv.cfg"))
	restored, err := RestoreProfile(archive, root)
	if err != nil {
		t.Fatal(err)
	}
	if len(restored) != 3 || read(t, filepath.Join(root, "ezquake", "configs", "config.cfg")) != "sensitivity 3\n" ||
		read(t, filepath.Join(root, "qtv", "qtv.cfg")) != "port 28000\n" {
		t.Fatalf("restaurados %v", restored)
	}
	// The same backup restores into a fresh folder.
	fresh := t.TempDir()
	if _, err := RestoreProfile(archive, fresh); err != nil || read(t, filepath.Join(fresh, "qw", "x86qw-ktx-user.cfg")) != "// ktx\n" {
		t.Fatalf("pasta nova: %v", err)
	}
}

// forged writes a profile zip from raw members, manifest included.
func forged(t *testing.T, members map[string]string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "forjado.zip")
	file, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	writer := zip.NewWriter(file)
	for name, body := range members {
		out, err := writer.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		out.Write([]byte(body))
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	file.Close()
	return path
}

func TestProfileRestoreRejectsTamperedBackups(t *testing.T) {
	cfg := "sensitivity 3\n"
	manifest := `{"format": 1, "kind": "profile", "files": [{"path": "ezquake/configs/config.cfg", "sha256": "` + sha(cfg) + `", "size": 14}]}`
	cases := map[string]map[string]string{
		"hash":        {"manifest.json": manifest, "ezquake/configs/config.cfg": "sensitivity 9\n"},
		"extra":       {"manifest.json": manifest, "ezquake/configs/config.cfg": cfg, "qw/ktx.pk3": "x"},
		"kind":        {"manifest.json": strings.Replace(manifest, `"profile"`, `"library"`, 1), "ezquake/configs/config.cfg": cfg},
		"non-profile": {"manifest.json": `{"format": 1, "kind": "profile", "files": [{"path": "qw/ktx.pk3", "sha256": "` + sha("x") + `", "size": 1}]}`, "qw/ktx.pk3": "x"},
		"missing":     {"ezquake/configs/config.cfg": cfg},
	}
	for name, members := range cases {
		t.Run(name, func(t *testing.T) {
			root := t.TempDir()
			write(t, filepath.Join(root, "ezquake", "configs", "config.cfg"), "original\n")
			if _, err := RestoreProfile(forged(t, members), root); err == nil {
				t.Fatal("backup adulterado aceito")
			}
			if read(t, filepath.Join(root, "ezquake", "configs", "config.cfg")) != "original\n" {
				t.Fatal("a recusa alterou a configuração")
			}
		})
	}
}

// A destination that is a symlink stops the restore and rolls back what
// it already wrote.
func TestProfileRestoreRollsBackOnASymlinkedDestination(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("links simbólicos exigem privilégio no Windows")
	}
	root := profiled(t)
	archive := filepath.Join(t.TempDir(), "perfil.zip")
	if _, err := BackupProfile(root, archive); err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(root, "ezquake", "configs", "config.cfg"), "sensitivity 9\n")
	outside := filepath.Join(t.TempDir(), "fora.cfg")
	write(t, outside, "fora\n")
	os.Remove(filepath.Join(root, "qw", "x86qw-ktx-user.cfg"))
	if err := os.Symlink(outside, filepath.Join(root, "qw", "x86qw-ktx-user.cfg")); err != nil {
		t.Fatal(err)
	}
	if _, err := RestoreProfile(archive, root); err == nil || !strings.Contains(err.Error(), "symlink") {
		t.Fatalf("link deveria parar a restauração: %v", err)
	}
	if read(t, filepath.Join(root, "ezquake", "configs", "config.cfg")) != "sensitivity 9\n" {
		t.Fatal("o rollback não devolveu o arquivo já restaurado")
	}
	if read(t, outside) != "fora\n" {
		t.Fatal("a restauração escreveu através do link")
	}
}

func TestInsideUsesTheOSSeparator(t *testing.T) {
	root := filepath.Join(t.TempDir(), "x86qw")
	for name, want := range map[string]bool{
		root:                                       true,
		filepath.Join(root, "qw", "a.cfg"):         true,
		filepath.Join(root, "..", "fora.zip"):      false,
		filepath.Join(root+"-irmao", "a.zip"):      false,
		filepath.Join(filepath.Dir(root), "a.zip"): false,
	} {
		if inside(name, root) != want {
			t.Errorf("inside(%s) deveria ser %v", name, want)
		}
	}
}

// Same rule for the local UI snapshot, through a folder that does not
// exist yet.
func TestLocalUINeverLandsInsideTheInstallation(t *testing.T) {
	root := t.TempDir()
	if _, err := WriteLocalUI(root, filepath.Join(root, "novo", "ui.html")); err == nil || !strings.Contains(err.Error(), "outside") {
		t.Fatalf("snapshot dentro da instalação aceito: %v", err)
	}
	if exists(filepath.Join(root, "novo")) {
		t.Fatal("a recusa criou a pasta")
	}
}

func TestBundleDestinationNeverLandsInsideTheInstallation(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	root := t.TempDir()
	outside := filepath.Join(t.TempDir(), "bundle.zip")
	if got, err := ResolveBundleDestination(outside, root); err != nil || got != filepath.Join(resolve(filepath.Dir(outside)), "bundle.zip") {
		t.Fatalf("fora da instalação: %s %v", got, err)
	}
	if _, err := ResolveBundleDestination(filepath.Join(root, "sub", "bundle.zip"), root); err == nil {
		t.Fatal("caminho dentro da instalação aceito")
	}
	previous, _ := os.Getwd()
	defer os.Chdir(previous)
	os.Chdir(root)
	if got, err := ResolveBundleDestination("bundle.zip", root); err != nil || got != filepath.Join(resolve(home), "bundle.zip") {
		t.Fatalf("nome solto dentro da instalação deveria ir para a home: %s %v", got, err)
	}
}
